import re

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from pymongo.errors import DuplicateKeyError

from app.decorators import active_required, login_required
from app.extensions import mongo
from app.utils.files import UploadError, delete_upload_url, save_many
from app.utils.post_visibility import (
    attach_post_access,
    can_moderate_posts,
    normalize_follow_delay_days,
    normalize_post_visibility,
    post_access_state,
    visible_post_query,
)
from app.utils.rate_limit import consume_rate_limit
from app.utils.security import now, parse_int, safe_redirect_url, to_object_id
from app.utils.validation import ValidationError, clean_text

bp = Blueprint("community", __name__, url_prefix="/community")


def _post_query():
    keyword = request.args.get("q", "").strip()[:120]
    tag = request.args.get("tag", "").strip()[:30]
    keyword_clause = None
    if keyword:
        regex = re.compile(re.escape(keyword), re.IGNORECASE)
        keyword_clause = {"$or": [{"title": regex}, {"description": regex}, {"tags": regex}]}
    query = visible_post_query(getattr(g, "user", None), keyword_clause)
    if tag:
        query["tags"] = tag
    return query, keyword, tag


def _author_map(posts):
    ids = list({post.get("author_id") for post in posts if post.get("author_id")})
    return {user["_id"]: user for user in mongo.db.users.find({"_id": {"$in": ids}})}


def _post_fields():
    user = getattr(g, "user", None) or {}
    return {
        "title": clean_text(request.form.get("title"), "作品标题", 120, required=True),
        "description": clean_text(request.form.get("description"), "作品描述", 5000),
        "location": clean_text(request.form.get("location"), "拍摄地点", 120),
        "shoot_time": clean_text(request.form.get("shoot_time"), "拍摄时间", 40),
        "device": clean_text(request.form.get("device"), "拍摄设备", 120),
        "tags": [
            clean_text(item, "标签", 30)
            for item in request.form.get("tags", "").replace("，", ",").split(",")
            if item.strip()
        ][:12],
        "visibility": normalize_post_visibility(
            request.form.get("visibility"),
            normalize_post_visibility(user.get("default_post_visibility")),
        ),
        "follow_delay_days": normalize_follow_delay_days(
            request.form.get("follow_delay_days"),
            normalize_follow_delay_days(user.get("default_follow_delay_days")),
        ),
    }


@bp.route("/")
def list_posts():
    page = parse_int(request.args.get("page"), default=1)
    per_page = 12
    query, keyword, tag = _post_query()
    total = mongo.db.posts.count_documents(query)
    posts = list(
        mongo.db.posts.find(query)
        .sort("created_at", -1)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    attach_post_access(posts, getattr(g, "user", None))
    authors = _author_map(posts)
    return render_template(
        "community/list.html",
        posts=posts,
        authors=authors,
        page=page,
        per_page=per_page,
        total=total,
        keyword=keyword,
        tag=tag,
    )


@bp.route("/new", methods=["GET", "POST"])
@active_required
def new_post():
    if request.method == "POST":
        if not consume_rate_limit("community.new_post", 20, 3600, str(g.user["_id"])):
            abort(429)
        try:
            fields = _post_fields()
        except ValidationError as exc:
            flash(str(exc), "danger")
            return render_template("community/form.html", post=None)
        try:
            images = save_many(request.files.getlist("images"), "posts", required=True)
        except UploadError as exc:
            flash(str(exc), "danger")
            return render_template("community/form.html", post=None)

        try:
            mongo.db.posts.insert_one(
                {
                    "author_id": g.user["_id"],
                    "title": fields["title"],
                    "description": fields["description"],
                    "images": images,
                    "location": fields["location"],
                    "shoot_time": fields["shoot_time"],
                    "device": fields["device"],
                    "tags": fields["tags"],
                    "visibility": fields["visibility"],
                    "follow_delay_days": fields["follow_delay_days"],
                    "view_count": 0,
                    "like_count": 0,
                    "favorite_count": 0,
                    "comment_count": 0,
                    "status": "normal",
                    "storage_status": "active",
                    "storage_marked_at": None,
                    "storage_reason": "",
                    "deleted_files": [],
                    "created_at": now(),
                    "updated_at": now(),
                }
            )
        except Exception:
            for url in images:
                delete_upload_url(url)
            raise
        flash("作品发布成功。", "success")
        return redirect(url_for("community.list_posts"))
    return render_template("community/form.html", post=None)


@bp.route("/<post_id>")
def detail(post_id):
    post = mongo.db.posts.find_one({"_id": to_object_id(post_id), "status": {"$ne": "deleted"}})
    if not post:
        abort(404)
    user = getattr(g, "user", None)
    access = post_access_state(post, user)
    if post.get("status") != "normal" and not (
        user and (post.get("author_id") == user["_id"] or can_moderate_posts(user))
    ):
        abort(404)
    if post.get("status") == "normal" and not access["detail_visible"]:
        abort(404)
    mongo.db.posts.update_one({"_id": post["_id"]}, {"$inc": {"view_count": 1}})
    post["view_count"] = post.get("view_count", 0) + 1
    author = mongo.db.users.find_one({"_id": post["author_id"]})
    comments = []
    if access["image_visible"]:
        comments = list(mongo.db.comments.find({"post_id": post["_id"], "status": "normal"}).sort("created_at", -1).limit(300))
    comments.reverse()
    comment_authors = _author_map([{"author_id": item["author_id"]} for item in comments])
    liked = favorited = followed = False
    if user:
        followed = (
            author
            and mongo.db.follows.find_one({"follower_id": user["_id"], "following_id": author["_id"]}) is not None
        )
    if user and access["image_visible"]:
        liked = mongo.db.likes.find_one({"user_id": g.user["_id"], "post_id": post["_id"]}) is not None
        favorited = mongo.db.favorites.find_one({"user_id": g.user["_id"], "post_id": post["_id"]}) is not None
    return render_template(
        "community/detail.html",
        post=post,
        author=author,
        comments=comments,
        comment_authors=comment_authors,
        liked=liked,
        favorited=favorited,
        followed=followed,
        access=access,
    )


@bp.route("/<post_id>/edit", methods=["GET", "POST"])
@active_required
def edit_post(post_id):
    post = mongo.db.posts.find_one({"_id": to_object_id(post_id), "status": {"$ne": "deleted"}})
    if not post:
        abort(404)
    if post["author_id"] != g.user["_id"] and not can_moderate_posts(g.user):
        abort(403)
    if request.method == "POST":
        try:
            update = {**_post_fields(), "updated_at": now()}
        except ValidationError as exc:
            flash(str(exc), "danger")
            return render_template("community/form.html", post=post)
        try:
            new_images = save_many(request.files.getlist("images"), "posts")
        except UploadError as exc:
            flash(str(exc), "danger")
            return render_template("community/form.html", post=post)
        if new_images:
            if len(post.get("images", [])) + len(new_images) > current_app.config["MAX_FILES_PER_UPLOAD"]:
                for url in new_images:
                    delete_upload_url(url)
                flash(f"每篇作品最多保留 {current_app.config['MAX_FILES_PER_UPLOAD']} 张图片。", "danger")
                return render_template("community/form.html", post=post)
            update["images"] = post.get("images", []) + new_images
        try:
            mongo.db.posts.update_one({"_id": post["_id"]}, {"$set": update})
        except Exception:
            for url in new_images:
                delete_upload_url(url)
            raise
        flash("作品已更新。", "success")
        return redirect(url_for("community.detail", post_id=post_id))
    return render_template("community/form.html", post=post)


@bp.route("/<post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    post = mongo.db.posts.find_one({"_id": to_object_id(post_id), "status": {"$ne": "deleted"}})
    if not post:
        abort(404)
    if post["author_id"] != g.user["_id"] and not can_moderate_posts(g.user):
        abort(403)
    mongo.db.posts.update_one({"_id": post["_id"]}, {"$set": {"status": "deleted", "updated_at": now()}})
    flash("作品已删除。", "success")
    return redirect(url_for("community.list_posts"))


@bp.route("/<post_id>/like", methods=["POST"])
@active_required
def toggle_like(post_id):
    if not consume_rate_limit("community.like", 300, 3600, str(g.user["_id"])):
        abort(429)
    post = _post_for_interaction(post_id)
    existing = mongo.db.likes.find_one({"user_id": g.user["_id"], "post_id": post["_id"]})
    if existing:
        result = mongo.db.likes.delete_one({"_id": existing["_id"]})
        if result.deleted_count:
            mongo.db.posts.update_one({"_id": post["_id"]}, {"$inc": {"like_count": -1}})
        flash("已取消点赞。", "info")
    else:
        try:
            mongo.db.likes.insert_one({"user_id": g.user["_id"], "post_id": post["_id"], "created_at": now()})
            mongo.db.posts.update_one({"_id": post["_id"]}, {"$inc": {"like_count": 1}})
            flash("已点赞。", "success")
        except DuplicateKeyError:
            pass
    return redirect(url_for("community.detail", post_id=post_id))


@bp.route("/<post_id>/favorite", methods=["POST"])
@active_required
def toggle_favorite(post_id):
    if not consume_rate_limit("community.favorite", 300, 3600, str(g.user["_id"])):
        abort(429)
    post = _post_for_interaction(post_id)
    existing = mongo.db.favorites.find_one({"user_id": g.user["_id"], "post_id": post["_id"]})
    if existing:
        result = mongo.db.favorites.delete_one({"_id": existing["_id"]})
        if result.deleted_count:
            mongo.db.posts.update_one({"_id": post["_id"]}, {"$inc": {"favorite_count": -1}})
        flash("已取消收藏。", "info")
    else:
        try:
            mongo.db.favorites.insert_one({"user_id": g.user["_id"], "post_id": post["_id"], "created_at": now()})
            mongo.db.posts.update_one({"_id": post["_id"]}, {"$inc": {"favorite_count": 1}})
            flash("已收藏。", "success")
        except DuplicateKeyError:
            pass
    return redirect(url_for("community.detail", post_id=post_id))


@bp.route("/<post_id>/comments", methods=["POST"])
@active_required
def add_comment(post_id):
    if not consume_rate_limit("community.comment", 60, 3600, str(g.user["_id"])):
        abort(429)
    post = _post_for_interaction(post_id)
    try:
        content = clean_text(request.form.get("content"), "评论", 1000, required=True)
    except ValidationError as exc:
        flash(str(exc), "danger")
    else:
        mongo.db.comments.insert_one(
            {
                "post_id": post["_id"],
                "author_id": g.user["_id"],
                "content": content,
                "status": "normal",
                "created_at": now(),
                "updated_at": now(),
            }
        )
        mongo.db.posts.update_one({"_id": post["_id"]}, {"$inc": {"comment_count": 1}})
        flash("评论已发布。", "success")
    return redirect(url_for("community.detail", post_id=post_id))


@bp.route("/comments/<comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    comment = mongo.db.comments.find_one({"_id": to_object_id(comment_id), "status": "normal"})
    if not comment:
        abort(404)
    if comment["author_id"] != g.user["_id"] and not can_moderate_posts(g.user):
        abort(403)
    result = mongo.db.comments.update_one(
        {"_id": comment["_id"], "status": "normal"},
        {"$set": {"status": "deleted", "updated_at": now()}},
    )
    if result.modified_count:
        mongo.db.posts.update_one({"_id": comment["post_id"]}, {"$inc": {"comment_count": -1}})
    flash("评论已删除。", "success")
    return redirect(safe_redirect_url(request.referrer, url_for("community.list_posts")))


def _create_report(target_type, target_id, reason, detail=""):
    if not consume_rate_limit("community.report", 20, 3600, str(g.user["_id"])):
        abort(429)
    try:
        reason = clean_text(reason, "举报原因", 120, required=True)
        detail = clean_text(detail, "举报说明", 1000)
    except ValidationError as exc:
        flash(str(exc), "danger")
        return
    existing = mongo.db.reports.find_one(
        {
            "reporter_id": g.user["_id"],
            "target_type": target_type,
            "target_id": target_id,
            "status": "pending",
        }
    )
    if existing:
        flash("你已经举报过该内容，管理员会尽快处理。", "info")
        return
    try:
        mongo.db.reports.insert_one(
            {
                "reporter_id": g.user["_id"],
                "target_type": target_type,
                "target_id": target_id,
                "reason": reason,
                "detail": detail,
                "status": "pending",
                "handled_by": None,
                "handled_at": None,
                "admin_note": "",
                "created_at": now(),
                "updated_at": now(),
            }
        )
    except DuplicateKeyError:
        flash("你已经举报过该内容，管理员会尽快处理。", "info")
        return
    flash("举报已提交，感谢你帮助维护社区秩序。", "success")


def _post_for_interaction(post_id):
    post = mongo.db.posts.find_one({"_id": to_object_id(post_id), "status": "normal"})
    if not post or not post_access_state(post, getattr(g, "user", None))["image_visible"]:
        abort(404)
    return post


@bp.route("/<post_id>/report", methods=["POST"])
@active_required
def report_post(post_id):
    post = _post_for_interaction(post_id)
    _create_report(
        "post",
        post["_id"],
        request.form.get("reason", "").strip() or "其他",
        request.form.get("detail", "").strip(),
    )
    return redirect(url_for("community.detail", post_id=post_id))


@bp.route("/comments/<comment_id>/report", methods=["POST"])
@active_required
def report_comment(comment_id):
    comment = mongo.db.comments.find_one({"_id": to_object_id(comment_id), "status": "normal"})
    if not comment:
        abort(404)
    _post_for_interaction(comment["post_id"])
    _create_report(
        "comment",
        comment["_id"],
        request.form.get("reason", "").strip() or "其他",
        request.form.get("detail", "").strip(),
    )
    return redirect(safe_redirect_url(request.referrer, url_for("community.detail", post_id=comment["post_id"])))
