import shutil
from datetime import timedelta

from flask import current_app

from app.utils.files import safe_upload_path
from app.utils.security import now


CONTENT_COLLECTIONS = (
    ("posts", "author_id"),
    ("submissions", "user_id"),
)


def bytes_to_mb(value):
    return round((value or 0) / 1024 / 1024, 2)


def upload_url_to_path(url):
    if not url:
        return None
    parts = str(url).split("?", 1)[0].strip("/").split("/")
    if len(parts) != 3 or parts[0] != "uploads":
        return None
    return safe_upload_path(parts[1], parts[2])


def _quality_user_ids(db):
    return {
        user["_id"]
        for user in db.users.find({"quality_photographer": True}, {"_id": 1})
    }


def _mark_query(author_field, protected_user_ids, cutoff):
    query = {
        "created_at": {"$lte": cutoff},
        "images.0": {"$exists": True},
        "storage_status": {"$nin": ["deletable", "cleaned"]},
    }
    if protected_user_ids:
        query[author_field] = {"$nin": list(protected_user_ids)}
    return query


def mark_deletable_content(db, older_than_days=30):
    cutoff = now() - timedelta(days=older_than_days)
    protected = _quality_user_ids(db)
    result = {"cutoff": cutoff, "older_than_days": older_than_days, "posts": 0, "submissions": 0}
    for collection_name, author_field in CONTENT_COLLECTIONS:
        query = _mark_query(author_field, protected, cutoff)
        if collection_name == "posts":
            query["status"] = {"$ne": "deleted"}
        update = {
            "$set": {
                "storage_status": "deletable",
                "storage_marked_at": now(),
                "storage_reason": f"普通用户内容超过 {older_than_days} 天，进入可清理范围。",
                "updated_at": now(),
            }
        }
        write_result = db[collection_name].update_many(query, update)
        result[collection_name] = write_result.modified_count
    return result


def collect_deletable_files(db):
    protected = _quality_user_ids(db)
    candidates = []
    for collection_name, author_field in CONTENT_COLLECTIONS:
        projection = {"images": 1, "created_at": 1, author_field: 1}
        docs = db[collection_name].find({"storage_status": "deletable", "images.0": {"$exists": True}}, projection)
        for doc in docs:
            if doc.get(author_field) in protected:
                continue
            for url in doc.get("images", []):
                path = upload_url_to_path(url)
                if not path:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                candidates.append(
                    {
                        "collection": collection_name,
                        "document_id": doc["_id"],
                        "url": url,
                        "path": path,
                        "size": size,
                        "created_at": doc.get("created_at") or now(),
                    }
                )
    return sorted(candidates, key=lambda item: item["created_at"])


def upload_storage_usage():
    root = current_app.config["UPLOAD_ROOT"]
    root.mkdir(parents=True, exist_ok=True)
    upload_bytes = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                upload_bytes += path.stat().st_size
            except OSError:
                continue
    disk = shutil.disk_usage(root)
    return {
        "upload_bytes": upload_bytes,
        "total_bytes": disk.total,
        "used_bytes": disk.used,
        "free_bytes": disk.free,
        "upload_mb": bytes_to_mb(upload_bytes),
        "total_mb": bytes_to_mb(disk.total),
        "used_mb": bytes_to_mb(disk.used),
        "free_mb": bytes_to_mb(disk.free),
    }


def storage_summary(db):
    candidates = collect_deletable_files(db)
    usage = upload_storage_usage()
    usage.update(
        {
            "deletable_posts": db.posts.count_documents({"storage_status": "deletable"}),
            "deletable_submissions": db.submissions.count_documents({"storage_status": "deletable"}),
            "candidate_files": len(candidates),
            "candidate_bytes": sum(item["size"] for item in candidates),
            "candidate_mb": bytes_to_mb(sum(item["size"] for item in candidates)),
            "cleaned_posts": db.posts.count_documents({"storage_status": "cleaned"}),
            "cleaned_submissions": db.submissions.count_documents({"storage_status": "cleaned"}),
        }
    )
    return usage


def _refresh_document_storage_status(db, collection_name, document_id):
    doc = db[collection_name].find_one({"_id": document_id}, {"images": 1})
    if not doc:
        return
    next_status = "deletable" if doc.get("images") else "cleaned"
    db[collection_name].update_one(
        {"_id": document_id},
        {
            "$set": {
                "storage_status": next_status,
                "storage_last_cleaned_at": now(),
                "updated_at": now(),
            }
        },
    )


def cleanup_deletable_files(db, target_free_mb):
    usage_before = upload_storage_usage()
    target_bytes = max(0, int(target_free_mb * 1024 * 1024))
    bytes_needed = target_bytes - usage_before["free_bytes"]
    result = {
        "target_free_mb": target_free_mb,
        "before_free_mb": usage_before["free_mb"],
        "after_free_mb": usage_before["free_mb"],
        "deleted_files": 0,
        "freed_bytes": 0,
        "freed_mb": 0,
        "needed_mb": bytes_to_mb(bytes_needed if bytes_needed > 0 else 0),
        "skipped": bytes_needed <= 0,
    }
    if bytes_needed <= 0:
        return result

    touched_docs = set()
    for item in collect_deletable_files(db):
        if result["freed_bytes"] >= bytes_needed:
            break
        try:
            item["path"].unlink()
        except FileNotFoundError:
            pass
        except OSError:
            continue
        db[item["collection"]].update_one(
            {"_id": item["document_id"]},
            {
                "$pull": {"images": item["url"]},
                "$addToSet": {"deleted_files": item["url"]},
                "$set": {"storage_last_cleaned_at": now(), "updated_at": now()},
            },
        )
        touched_docs.add((item["collection"], item["document_id"]))
        result["deleted_files"] += 1
        result["freed_bytes"] += item["size"]

    for collection_name, document_id in touched_docs:
        _refresh_document_storage_status(db, collection_name, document_id)

    usage_after = upload_storage_usage()
    result["freed_mb"] = bytes_to_mb(result["freed_bytes"])
    result["after_free_mb"] = usage_after["free_mb"]
    result["remaining_needed_mb"] = bytes_to_mb(max(target_bytes - usage_after["free_bytes"], 0))
    return result
