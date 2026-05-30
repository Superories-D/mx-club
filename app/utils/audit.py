from flask import g, request

from app.extensions import mongo
from app.utils.security import now


def log_action(action, target_type="", target_id="", detail=""):
    admin_id = g.user["_id"] if getattr(g, "user", None) else None
    mongo.db.audit_logs.insert_one(
        {
            "admin_id": admin_id,
            "action": action,
            "target_type": target_type,
            "target_id": str(target_id or ""),
            "detail": detail,
            "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
            "created_at": now(),
        }
    )
