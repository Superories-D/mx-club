import re

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from pymongo.errors import DuplicateKeyError

from app.decorators import active_required, login_required
from app.extensions import mongo
from app.utils.files import UploadError, save_many
from app.utils.security import now, parse_int, to_object_id

bp = Blueprint("community", __name__, url_prefix="/community")


def _post_query():
    query = {"status": "normal"}
    keyword = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip()
    if keyword:
        regex = re.compile(re.escape(keyword), re.IGNORECASE)
        query["$or"] = [{"title": regex}, {"description": regex}, {"tags": regex}]
    if tag:
        query["tags"] = tag
    return query, keyword, tag


def _author_map(posts):
    ids = list({post.get("author_id") for post in posts if post.get("author_id")})
    return {user["_id"]: user for user in mongo.db.users.find({"_id": {"$in": ids}})}


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
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        tags = [item.strip() for item in request.form.get("tags", "").replace("，", ",").split(",") if item.strip()]
        if not title:
            flash("请填写作品标题。", "danger")
            return render_template("community/form.html", post=None)
        try:
            images = save_many(request.files.getlist("images"), "posts", required=True)
        except UploadError as exc:
            flash(str(exc), "danger")
            return render_template("community/form.html", post=None)

        mongo.db.posts.insert_one(
            {
                "author_id": g.user["_id"],
                "title": title,
                "description": description,
                "images": images,
                "location": request.form.get("location", "").strip(),
                "shoot_time": request.form.get("shoot_time", "").strip(),
                "device": request.form.get("device", "").strip(),
                "tags": tags,
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
        flash("作品发布成功。", "success")
        return redirect(url_for("community.list_posts"))
    return render_template("community/form.html", post=None)


@bp.route("/<post_id>")
def detail(post_id):
    post = mongo.db.posts.find_one({"_id": to_object_id(post_id), "status": {"$ne": "deleted"}})
    if not post or (post.get("status") != "normal" and not getattr(g, "user", None)):
        abort(404)
    if post.get("status") != "normal" and g.user.get("role") not in ("admin", "super_admin"):
        abort(404)
    mongo.db.posts.update_one({"_id": post["_id"]}, {"$inc": {"view_count": 1}})
    post["view_count"] = post.get("view_count", 0) + 1
    author = mongo.db.users.find_one({"_id": post["author_id"]})
    comments = list(mongo.db.comments.find({"post_id": post["_id"], "status": "normal"}).sort("created_at", 1))
    comment_authors = _author_map([{"author_id": item["author_id"]} for item in comments])
    liked = favorited = followed = False
    if getattr(g, "user", None):
        liked = mongo.db.likes.find_one({"user_id": g.user["_id"], "post_id": post["_id"]}) is not None
        favorited = mongo.db.favorites.find_one({"user_id": g.user["_id"], "post_id": post["_id"]}) is not None
        followed = (
            author
            and mongo.db.follows.find_one({"follower_id": g.user["_id"], "following_id": author["_id"]}) is not None
        )
    return render_template(
        "community/detail.html",
        post=post,
        author=author,
        comments=comments,
        comment_authors=comment_authors,
        liked=liked,
        favorited=favorited,
        followed=followed,
    )


@bp.route("/<post_id>/edit", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    post = mongo.db.posts.find_one({"_id": to_object_id(post_id), "status": {"$ne": "deleted"}})
    if not post:
        abort(404)
    if post["author_id"] != g.user["_id"] and g.user.get("role") not in ("admin", "super_admin"):
        abort(403)
    if request.method == "POST":
        update = {
            "title": request.form.get("title", "").strip(),
            "description": request.form.get("description", "").strip(),
            "location": request.form.get("location", "").strip(),
            "shoot_time": request.form.get("shoot_time", "").strip(),
            "device": request.form.get("device", "").strip(),
            "tags": [item.strip() for item in request.form.get("tags", "").replace("，", ",").split(",") if item.strip()],
            "updated_at": now(),
        }
        if not update["title"]:
            flash("请填写作品标题。", "danger")
            return render_template("community/form.html", post=post)
        try:
            new_images = save_many(request.files.getlist("images"), "posts")
        except UploadError as exc:
            flash(str(exc), "danger")
            return render_template("community/form.html", post=post)
        if new_images:
            update["images"] = post.get("images", []) + new_images
        mongo.db.posts.update_one({"_id": post["_id"]}, {"$set": update})
        flash("作品已更新。", "success")
        return redirect(url_for("community.detail", post_id=post_id))
    return render_template("community/form.html", post=post)


@bp.route("/<post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    post = mongo.db.posts.find_one({"_id": to_object_id(post_id), "status": {"$ne": "deleted"}})
    if not post:
        abort(404)
    if post["author_id"] != g.user["_id"] and g.user.get("role") not in ("admin", "super_admin"):
        abort(403)
    mongo.db.posts.update_one({"_id": post["_id"]}, {"$set": {"status": "deleted", "updated_at": now()}})
    flash("作品已删除。", "success")
    return redirect(url_for("community.list_posts"))


@bp.route("/<post_id>/like", methods=["POST"])
@active_required
def toggle_like(post_id):
    post = mongo.db.posts.find_one({"_id": to_object_id(post_id), "status": "normal"})
    if not post:
        abort(404)
    existing = mongo.db.likes.find_one({"user_id": g.user["_id"], "post_id": post["_id"]})
    if existing:
        mongo.db.likes.delete_one({"_id": existing["_id"]})
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
    post = mongo.db.posts.find_one({"_id": to_object_id(post_id), "status": "normal"})
    if not post:
        abort(404)
    existing = mongo.db.favorites.find_one({"user_id": g.user["_id"], "post_id": post["_id"]})
    if existing:
        mongo.db.favorites.delete_one({"_id": existing["_id"]})
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
    post = mongo.db.posts.find_one({"_id": to_object_id(post_id), "status": "normal"})
    if not post:
        abort(404)
    content = request.form.get("content", "").strip()
    if not content:
        flash("评论不能为空。", "danger")
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
    if comment["author_id"] != g.user["_id"] and g.user.get("role") not in ("admin", "super_admin"):
        abort(403)
    mongo.db.comments.update_one({"_id": comment["_id"]}, {"$set": {"status": "deleted", "updated_at": now()}})
    mongo.db.posts.update_one({"_id": comment["post_id"]}, {"$inc": {"comment_count": -1}})
    flash("评论已删除。", "success")
    return redirect(request.referrer or url_for("community.list_posts"))


def _create_report(target_type, target_id, reason, detail=""):
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
    flash("举报已提交，感谢你帮助维护社区秩序。", "success")


@bp.route("/<post_id>/report", methods=["POST"])
@active_required
def report_post(post_id):
    post = mongo.db.posts.find_one({"_id": to_object_id(post_id), "status": {"$ne": "deleted"}})
    if not post:
        abort(404)
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
    _create_report(
        "comment",
        comment["_id"],
        request.form.get("reason", "").strip() or "其他",
        request.form.get("detail", "").strip(),
    )
    return redirect(request.referrer or url_for("community.detail", post_id=comment["post_id"]))
