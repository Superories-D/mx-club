from bson import ObjectId
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from pymongo.errors import DuplicateKeyError

from app.decorators import active_required, login_required
from app.extensions import mongo
from app.utils.files import UploadError, save_upload
from app.utils.security import hash_password, now, to_object_id, verify_password

bp = Blueprint("profile", __name__)


@bp.route("/users/<user_id>")
def user_home(user_id):
    user = mongo.db.users.find_one({"_id": to_object_id(user_id), "status": {"$ne": "deleted"}})
    if not user:
        abort(404)
    posts = list(mongo.db.posts.find({"author_id": user["_id"], "status": "normal"}).sort("created_at", -1))
    follower_count = mongo.db.follows.count_documents({"following_id": user["_id"]})
    following_count = mongo.db.follows.count_documents({"follower_id": user["_id"]})
    post_ids = [post["_id"] for post in posts]
    like_total = mongo.db.likes.count_documents({"post_id": {"$in": post_ids}}) if post_ids else 0
    is_following = False
    if getattr(g, "user", None):
        is_following = mongo.db.follows.find_one({"follower_id": g.user["_id"], "following_id": user["_id"]}) is not None
    return render_template(
        "profile/home.html",
        profile_user=user,
        posts=posts,
        follower_count=follower_count,
        following_count=following_count,
        like_total=like_total,
        is_following=is_following,
    )


@bp.route("/profile/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        action = request.form.get("action", "profile")
        if action == "profile":
            update = {
                "username": request.form.get("username", "").strip() or g.user["username"],
                "bio": request.form.get("bio", "").strip(),
                "contact": request.form.get("contact", "").strip(),
                "updated_at": now(),
            }
            avatar = request.files.get("avatar")
            if avatar and avatar.filename:
                try:
                    update["avatar_url"] = save_upload(avatar, "avatars")
                except UploadError as exc:
                    flash(str(exc), "danger")
                    return redirect(url_for("profile.settings"))
            try:
                mongo.db.users.update_one({"_id": g.user["_id"]}, {"$set": update})
                flash("资料已更新。", "success")
            except DuplicateKeyError:
                flash("该昵称 / 用户名已被使用。", "danger")
        elif action == "password":
            old_password = request.form.get("old_password", "")
            new_password = request.form.get("new_password", "")
            if not verify_password(g.user["password_hash"], old_password):
                flash("旧密码不正确。", "danger")
            elif len(new_password) < 6:
                flash("新密码至少需要 6 位。", "danger")
            else:
                mongo.db.users.update_one(
                    {"_id": g.user["_id"]},
                    {"$set": {"password_hash": hash_password(new_password), "updated_at": now()}},
                )
                flash("密码已修改。", "success")
        elif action == "delete_account":
            mongo.db.users.update_one(
                {"_id": g.user["_id"]},
                {"$set": {"status": "deleted", "updated_at": now()}},
            )
            session.clear()
            flash("账号已注销。", "info")
            return redirect(url_for("main.index"))
        return redirect(url_for("profile.settings"))
    return render_template("profile/settings.html")


@bp.route("/users/<user_id>/follow", methods=["POST"])
@active_required
def toggle_follow(user_id):
    target_id = to_object_id(user_id)
    if target_id == g.user["_id"]:
        flash("不能关注自己。", "warning")
        return redirect(url_for("profile.user_home", user_id=user_id))
    target = mongo.db.users.find_one({"_id": target_id, "status": "active"})
    if not target:
        abort(404)
    existing = mongo.db.follows.find_one({"follower_id": g.user["_id"], "following_id": target_id})
    if existing:
        mongo.db.follows.delete_one({"_id": existing["_id"]})
        flash("已取消关注。", "info")
    else:
        mongo.db.follows.insert_one({"follower_id": g.user["_id"], "following_id": target_id, "created_at": now()})
        flash("已关注作者。", "success")
    return redirect(request.referrer or url_for("profile.user_home", user_id=user_id))
