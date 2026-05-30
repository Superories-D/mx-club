import secrets
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from flask import abort, current_app, request, session
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash


def now():
    return datetime.utcnow()


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password_hash, password):
    return check_password_hash(password_hash, password)


def to_object_id(value):
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        abort(404)


def get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf():
    if request.method != "POST":
        return
    session_token = session.get("_csrf_token")
    form_token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
    if not session_token or not form_token or not secrets.compare_digest(session_token, form_token):
        abort(400, description="CSRF 校验失败，请刷新页面后重试。")


def csrf_field():
    return Markup(f'<input type="hidden" name="_csrf_token" value="{get_csrf_token()}">')


def mask_contact(contact):
    if not contact:
        return ""
    value = str(contact)
    if "@" in value:
        name, domain = value.split("@", 1)
        return f"{name[:2]}***@{domain}" if len(name) > 2 else f"{name[:1]}***@{domain}"
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) >= 7:
        return f"{digits[:3]}****{digits[-4:]}"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}****{value[-2:]}"


def can_manage_user(actor, target):
    if not actor or not target:
        return False
    if actor.get("role") == "super_admin":
        return True
    if actor.get("role") == "admin" and target.get("role") != "super_admin":
        return True
    return False


def parse_int(value, default=1, minimum=1, maximum=1000):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def public_asset(path, fallback):
    return path or fallback


def get_site_settings():
    from app.extensions import mongo

    defaults = {
        "site_name": current_app.config["SITE_NAME"],
        "logo": "",
        "home_banner": "/static/images/generated/home-banner.png",
        "auth_background": "/static/images/generated/auth-background.png",
        "community_cover": "/static/images/generated/community-cover.png",
        "activity_cover": "/static/images/generated/activity-cover.png",
        "default_avatar": "/static/images/generated/default-avatar.png",
        "empty_illustration": "/static/images/generated/empty-state.png",
        "club_intro": "用镜头记录校园里的光、风、树影和青春。",
        "contact": "请联系社团指导老师或管理员。",
        "footer": "© 木樨映像 Muxi Photo",
        "theme_color": "#b8864b",
    }
    doc = mongo.db.site_settings.find_one({"key": "default"}) or {}
    defaults.update({k: v for k, v in doc.items() if k != "_id"})
    return defaults
