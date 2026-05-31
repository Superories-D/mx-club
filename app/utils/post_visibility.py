import math
from datetime import timedelta

from app.extensions import mongo
from app.utils.permissions import has_permission
from app.utils.security import now


POST_VISIBILITIES = {"public", "private", "followers"}


def normalize_post_visibility(value, default="public"):
    visibility = str(value or "").strip()
    return visibility if visibility in POST_VISIBILITIES else default


def normalize_follow_delay_days(value, default=0):
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = default
    return max(0, min(7, days))


def can_moderate_posts(user):
    return bool(user and has_permission(user, "moderate_community"))


def post_access_state(post, user):
    if user and (post.get("author_id") == user.get("_id") or can_moderate_posts(user)):
        return _state(True)

    raw_visibility = post.get("visibility")
    visibility = "public" if raw_visibility in (None, "") else normalize_post_visibility(raw_visibility, "private")
    if visibility == "public":
        return _state(True)
    if visibility == "private":
        return _state(False, list_visible=False, detail_visible=False, message="仅作者自己可见")

    delay_days = normalize_follow_delay_days(post.get("follow_delay_days"))
    if not user:
        return _state(False, message=_follow_prompt(delay_days))
    follow = mongo.db.follows.find_one(
        {"follower_id": user["_id"], "following_id": post.get("author_id")},
        {"created_at": 1},
    )
    if not follow:
        return _state(False, message=_follow_prompt(delay_days))
    unlock_at = (follow.get("created_at") or now()) + timedelta(days=delay_days)
    if unlock_at <= now():
        return _state(True)
    remaining = max(1, math.ceil((unlock_at - now()).total_seconds() / 86400))
    return _state(False, message=f"还需关注作者 {remaining} 天后可查看")


def visible_post_query(user, extra_clause=None):
    query = {"status": "normal"}
    clauses = []
    if not can_moderate_posts(user):
        visibility_choices = [
            {"visibility": {"$exists": False}},
            {"visibility": "public"},
            {"visibility": "followers"},
        ]
        if user:
            visibility_choices.append({"author_id": user["_id"]})
        clauses.append({"$or": visibility_choices})
    if extra_clause:
        clauses.append(extra_clause)
    if clauses:
        query["$and"] = clauses
    return query


def attach_post_access(posts, user):
    for post in posts:
        post["_access"] = post_access_state(post, user)
    return posts


def _state(image_visible, list_visible=True, detail_visible=True, message=""):
    return {
        "image_visible": image_visible,
        "list_visible": list_visible,
        "detail_visible": detail_visible,
        "locked": not image_visible and list_visible,
        "message": message,
    }


def _follow_prompt(delay_days):
    if delay_days:
        return f"关注作者 {delay_days} 天后可查看"
    return "关注作者后即可查看"
