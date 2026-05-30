from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from pymongo.errors import DuplicateKeyError

from app.decorators import active_required, login_required
from app.extensions import mongo
from app.utils.files import UploadError, delete_upload_url, save_upload
from app.utils.rate_limit import consume_rate_limit
from app.utils.security import hash_password, now, safe_redirect_url, to_object_id, verify_password
from app.utils.validation import ValidationError, clean_text, clean_username, validate_password

bp = Blueprint("profile", __name__)


@bp.route("/users/<user_id>")
def user_home(user_id):
    user = mongo.db.users.find_one({"_id": to_object_id(user_id), "status": {"$ne": "deleted"}})
    if not user:
        abort(404)
    posts = list(mongo.db.posts.find({"author_id": user["_id"], "status": "normal"}).sort("created_at", -1).limit(120))
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
            try:
                update = {
                    "username": clean_username(request.form.get("username")),
                    "bio": clean_text(request.form.get("bio"), "简介", 500),
                    "contact": clean_text(request.form.get("contact"), "联系方式", 120),
                    "updated_at": now(),
                }
            except ValidationError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("profile.settings"))
            avatar = request.files.get("avatar")
            if avatar and avatar.filename:
                if not consume_rate_limit("profile.avatar", 20, 3600, str(g.user["_id"])):
                    abort(429)
                try:
                    update["avatar_url"] = save_upload(avatar, "avatars")
                except UploadError as exc:
                    flash(str(exc), "danger")
                    return redirect(url_for("profile.settings"))
            try:
                mongo.db.users.update_one({"_id": g.user["_id"]}, {"$set": update})
                if update.get("avatar_url") and g.user.get("avatar_url") != update["avatar_url"]:
                    delete_upload_url(g.user.get("avatar_url"))
                flash("资料已更新。", "success")
            except DuplicateKeyError:
                delete_upload_url(update.get("avatar_url"))
                flash("该昵称 / 用户名已被使用。", "danger")
            except Exception:
                delete_upload_url(update.get("avatar_url"))
                raise
        elif action == "password":
            old_password = request.form.get("old_password", "")
            new_password = request.form.get("new_password", "")
            if not verify_password(g.user["password_hash"], old_password):
                flash("旧密码不正确。", "danger")
            else:
                try:
                    validate_password(new_password, "新密码")
                except ValidationError as exc:
                    flash(str(exc), "danger")
                    return redirect(url_for("profile.settings"))
                mongo.db.users.update_one(
                    {"_id": g.user["_id"]},
                    {
                        "$set": {"password_hash": hash_password(new_password), "updated_at": now()},
                        "$inc": {"session_version": 1},
                    },
                )
                session["session_version"] = g.user.get("session_version", 0) + 1
                flash("密码已修改。", "success")
        elif action == "delete_account":
            password = request.form.get("current_password", "")
            if not verify_password(g.user["password_hash"], password):
                flash("当前密码不正确，账号未注销。", "danger")
                return redirect(url_for("profile.settings"))
            mongo.db.users.update_one(
                {"_id": g.user["_id"]},
                {"$set": {"status": "deleted", "updated_at": now()}, "$inc": {"session_version": 1}},
            )
            session.clear()
            flash("账号已注销。", "info")
            return redirect(url_for("main.index"))
        else:
            abort(400)
        return redirect(url_for("profile.settings"))
    return render_template("profile/settings.html")


@bp.route("/users/<user_id>/follow", methods=["POST"])
@active_required
def toggle_follow(user_id):
    if not consume_rate_limit("profile.follow", 120, 3600, str(g.user["_id"])):
        abort(429)
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
        try:
            mongo.db.follows.insert_one({"follower_id": g.user["_id"], "following_id": target_id, "created_at": now()})
            flash("已关注作者。", "success")
        except DuplicateKeyError:
            pass
    return redirect(safe_redirect_url(request.referrer, url_for("profile.user_home", user_id=user_id)))
