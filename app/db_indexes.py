from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError


def create_indexes(db, logger=None):
    index_specs = [
        ("users", [("username", ASCENDING)], {"unique": True}),
        ("users", [("role", ASCENDING)], {}),
        ("users", [("status", ASCENDING)], {}),
        ("users", [("cohort_tag", ASCENDING)], {}),
        ("users", [("quality_photographer", ASCENDING)], {}),
        ("users", [("created_at", DESCENDING)], {}),
        ("invite_codes", [("code", ASCENDING), ("real_name", ASCENDING)], {"unique": True}),
        ("invite_codes", [("used", ASCENDING)], {}),
        ("invite_codes", [("used_by", ASCENDING)], {}),
        ("invite_codes", [("cohort_tag", ASCENDING)], {}),
        ("invite_codes", [("batch_id", ASCENDING)], {}),
        ("posts", [("author_id", ASCENDING)], {}),
        ("posts", [("created_at", DESCENDING)], {}),
        ("posts", [("status", ASCENDING)], {}),
        ("posts", [("storage_status", ASCENDING)], {}),
        ("posts", [("storage_marked_at", ASCENDING)], {}),
        ("comments", [("post_id", ASCENDING)], {}),
        ("comments", [("author_id", ASCENDING)], {}),
        ("comments", [("created_at", DESCENDING)], {}),
        ("likes", [("user_id", ASCENDING), ("post_id", ASCENDING)], {"unique": True}),
        ("likes", [("post_id", ASCENDING)], {}),
        ("favorites", [("user_id", ASCENDING), ("post_id", ASCENDING)], {"unique": True}),
        ("favorites", [("post_id", ASCENDING)], {}),
        ("follows", [("follower_id", ASCENDING), ("following_id", ASCENDING)], {"unique": True}),
        ("follows", [("following_id", ASCENDING)], {}),
        ("activities", [("status", ASCENDING)], {}),
        ("activities", [("created_at", DESCENDING)], {}),
        ("activities", [("start_time", ASCENDING)], {}),
        ("activities", [("end_time", ASCENDING)], {}),
        ("submissions", [("activity_id", ASCENDING)], {}),
        ("submissions", [("user_id", ASCENDING)], {}),
        ("submissions", [("status", ASCENDING)], {}),
        ("submissions", [("created_at", DESCENDING)], {}),
        ("submissions", [("storage_status", ASCENDING)], {}),
        ("submissions", [("storage_marked_at", ASCENDING)], {}),
        ("audit_logs", [("admin_id", ASCENDING)], {}),
        ("audit_logs", [("action", ASCENDING)], {}),
        ("audit_logs", [("created_at", DESCENDING)], {}),
        ("site_settings", [("key", ASCENDING)], {"unique": True}),
        ("system_locks", [("key", ASCENDING)], {"unique": True}),
        ("reports", [("target_type", ASCENDING), ("target_id", ASCENDING)], {}),
        ("reports", [("reporter_id", ASCENDING)], {}),
        ("reports", [("status", ASCENDING)], {}),
        ("reports", [("created_at", DESCENDING)], {}),
    ]
    for collection, keys, options in index_specs:
        try:
            db[collection].create_index(keys, **options)
        except PyMongoError as exc:
            if logger:
                logger.warning("创建索引失败 %s %s: %s", collection, keys, exc)

    try:
        db.posts.create_index([("title", "text"), ("description", "text"), ("tags", "text")])
    except PyMongoError as exc:
        if logger:
            logger.warning("创建帖子搜索索引失败: %s", exc)
