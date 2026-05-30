from flask import Blueprint, abort, g, send_file

from app.extensions import mongo
from app.utils.files import safe_upload_path
from app.utils.permissions import has_permission

bp = Blueprint("uploads", __name__, url_prefix="/uploads")


@bp.route("/<category>/<filename>")
def uploaded_file(category, filename):
    path = safe_upload_path(category, filename)
    if not path:
        abort(404)
    url = f"/uploads/{category}/{filename}"
    if category == "posts" and not _can_access_post_image(url):
        abort(404)
    if category == "submissions" and not _can_access_submission_image(url):
        abort(404)
    response = send_file(path)
    if category == "submissions":
        response.headers["Cache-Control"] = "private, no-store"
    return response


def _can_access_post_image(url):
    post = mongo.db.posts.find_one({"images": url}, {"author_id": 1, "status": 1})
    if not post:
        return False
    if post.get("status") == "normal":
        return True
    user = getattr(g, "user", None)
    return bool(
        user
        and (
            post.get("author_id") == user["_id"]
            or has_permission(user, "moderate_community")
        )
    )


def _can_access_submission_image(url):
    submission = mongo.db.submissions.find_one({"images": url}, {"user_id": 1, "status": 1})
    if not submission:
        return False
    if submission.get("status") == "selected":
        return True
    user = getattr(g, "user", None)
    return bool(
        user
        and (
            submission.get("user_id") == user["_id"]
            or has_permission(user, "review_submissions")
        )
    )
