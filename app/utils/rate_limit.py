import hashlib
import time
from datetime import timedelta

from flask import request
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.extensions import mongo
from app.utils.security import now


def _bucket_key(scope, window_seconds, subject=""):
    bucket = int(time.time() // window_seconds)
    remote_addr = request.remote_addr or "unknown"
    raw = f"{scope}:{remote_addr}:{subject}:{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def consume_rate_limit(scope, limit, window_seconds, subject=""):
    key = _bucket_key(scope, window_seconds, subject)
    expires_at = now() + timedelta(seconds=window_seconds * 2)
    try:
        result = mongo.db.rate_limits.find_one_and_update(
            {"_id": key},
            {
                "$inc": {"count": 1},
                "$setOnInsert": {
                    "scope": scope,
                    "expires_at": expires_at,
                    "created_at": now(),
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        result = mongo.db.rate_limits.find_one_and_update(
            {"_id": key},
            {"$inc": {"count": 1}},
            return_document=ReturnDocument.AFTER,
        )
    return bool(result and result.get("count", 0) <= limit)
