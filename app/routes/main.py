from flask import Blueprint, current_app, g, jsonify, render_template
from pymongo.errors import PyMongoError

from app.extensions import mongo
from app.utils.post_visibility import attach_post_access, visible_post_query

bp = Blueprint("main", __name__)


def _author_map(posts):
    user_ids = list({post.get("author_id") for post in posts if post.get("author_id")})
    users = mongo.db.users.find({"_id": {"$in": user_ids}})
    return {user["_id"]: user for user in users}


@bp.route("/")
def index():
    query = visible_post_query(getattr(g, "user", None))
    featured_posts = list(mongo.db.posts.find(query).sort("like_count", -1).limit(6))
    latest_posts = list(mongo.db.posts.find(query).sort("created_at", -1).limit(8))
    attach_post_access(featured_posts, getattr(g, "user", None))
    attach_post_access(latest_posts, getattr(g, "user", None))
    active_activities = list(
        mongo.db.activities.find({"status": {"$in": ["active", "showcased"]}}).sort("created_at", -1).limit(4)
    )
    authors = _author_map(featured_posts + latest_posts)
    return render_template(
        "index.html",
        featured_posts=featured_posts,
        latest_posts=latest_posts,
        active_activities=active_activities,
        authors=authors,
    )


@bp.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@bp.route("/readyz")
def readyz():
    try:
        mongo.db.command("ping")
    except PyMongoError as exc:
        current_app.logger.warning("readyz MongoDB ping failed: %s", exc)
        return jsonify({"status": "error", "database": "unavailable"}), 503
    return jsonify({"status": "ok", "database": "ok"})


@bp.route("/showcase")
def showcase():
    submissions = list(mongo.db.submissions.find({"status": "selected"}).sort("created_at", -1).limit(60))
    activity_ids = list({sub.get("activity_id") for sub in submissions if sub.get("activity_id")})
    activities = {item["_id"]: item for item in mongo.db.activities.find({"_id": {"$in": activity_ids}})}
    return render_template("activities/showcase.html", submissions=submissions, activities=activities)
