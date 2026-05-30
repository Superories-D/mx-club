from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from pymongo.errors import DuplicateKeyError

from app.extensions import mongo
from app.utils.security import hash_password, now, verify_password

bp = Blueprint("auth", __name__)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        code = request.form.get("invite_code", "").strip()
        real_name = request.form.get("real_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        contact = request.form.get("contact", "").strip()

        if not all([code, real_name, username, password, contact]):
            flash("请完整填写注册信息。", "danger")
            return render_template("auth/register.html")
        if len(password) < 6:
            flash("密码至少需要 6 位。", "danger")
            return render_template("auth/register.html")

        invite = mongo.db.invite_codes.find_one({"code": code, "real_name": real_name})
        if not invite:
            code_only = mongo.db.invite_codes.find_one({"code": code})
            if code_only and not code_only.get("real_name"):
                flash("该邀请码还没有绑定真实姓名，请联系管理员上传填写后的注册表。", "danger")
                return render_template("auth/register.html")
            flash("邀请码错误或真实姓名不匹配。", "danger")
            return render_template("auth/register.html")
        if invite.get("used") and not invite.get("allow_reuse"):
            flash("该邀请码已使用，不能再次注册。", "danger")
            return render_template("auth/register.html")

        user_doc = {
            "real_name": real_name,
            "username": username,
            "password_hash": hash_password(password),
            "contact": contact,
            "avatar_url": "",
            "bio": "",
            "role": "user",
            "permissions": [],
            "status": "active",
            "must_change_password": False,
            "created_at": now(),
            "updated_at": now(),
            "last_login_at": None,
        }
        try:
            result = mongo.db.users.insert_one(user_doc)
        except DuplicateKeyError:
            flash("该昵称 / 用户名已被使用。", "danger")
            return render_template("auth/register.html")

        mongo.db.invite_codes.update_one(
            {"_id": invite["_id"]},
            {"$set": {"used": True, "used_by": result.inserted_id, "used_at": now()}},
        )
        flash("注册成功，请登录。", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = mongo.db.users.find_one({"username": username})
        if not user or not verify_password(user.get("password_hash", ""), password):
            flash("登录失败，用户名或密码错误。", "danger")
            return render_template("auth/login.html")
        if user.get("status") == "banned":
            flash("账号已被封禁，无法登录。", "danger")
            return render_template("auth/login.html")
        if user.get("status") == "deleted":
            flash("账号已注销，无法登录。", "danger")
            return render_template("auth/login.html")
        session.clear()
        session["user_id"] = str(user["_id"])
        mongo.db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login_at": now()}})
        flash("欢迎回来。", "success")
        next_url = request.args.get("next")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        if user.get("role") in ("admin", "super_admin"):
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("main.index"))
    return render_template("auth/login.html")


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    flash("已退出登录。", "info")
    return redirect(url_for("main.index"))
