from bson import ObjectId


def _normalize_object_ids(values):
    ids = []
    for value in values or []:
        if isinstance(value, ObjectId):
            ids.append(value)
            continue
        try:
            ids.append(ObjectId(str(value)))
        except Exception:
            continue
    return ids


def collect_title_ids(users, include_awarded=False):
    title_ids = []
    for user in users or []:
        if not user:
            continue
        if user.get("equipped_title_id"):
            title_ids.append(user.get("equipped_title_id"))
        if include_awarded:
            title_ids.extend(user.get("awarded_title_ids", []))
    return _normalize_object_ids(title_ids)


def title_map(db, title_ids=None, active_only=False):
    query = {}
    if title_ids is not None:
        ids = list({item for item in _normalize_object_ids(title_ids)})
        if not ids:
            return {}
        query["_id"] = {"$in": ids}
    if active_only:
        query["is_active"] = True
    docs = list(db.member_titles.find(query).sort([("sort_order", 1), ("name", 1), ("created_at", 1)]))
    return {doc["_id"]: doc for doc in docs}


def attach_equipped_titles(users, db, titles=None):
    docs = [user for user in users or [] if user]
    title_docs = titles or title_map(db, collect_title_ids(docs), active_only=True)
    for user in docs:
        user["_equipped_title"] = title_docs.get(user.get("equipped_title_id"))
    return title_docs


def awarded_titles_for_user(db, user, active_only=False):
    title_ids = _normalize_object_ids(user.get("awarded_title_ids", []))
    if not title_ids:
        return []
    titles = title_map(db, title_ids, active_only=active_only)
    return [titles[title_id] for title_id in title_ids if title_id in titles]


def normalize_equipped_title_id(db, user, raw_value):
    value = str(raw_value or "").strip()
    if not value:
        return None
    ids = _normalize_object_ids([value])
    if not ids:
        raise ValueError("头衔不存在。")
    title_id = ids[0]
    awarded_ids = set(_normalize_object_ids(user.get("awarded_title_ids", [])))
    if title_id not in awarded_ids:
        raise ValueError("只能佩戴自己已经获得的头衔。")
    title = db.member_titles.find_one({"_id": title_id, "is_active": True})
    if not title:
        raise ValueError("该头衔已停用或不存在。")
    return title_id
