import secrets

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.extensions import mongo
from app.utils.rate_limit import consume_rate_limit
from app.utils.security import hash_password, now, safe_redirect_url, set_login_session, verify_password
from app.utils.validation import ValidationError, clean_text, clean_username, validate_password

bp = Blueprint("auth", __name__)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        if not consume_rate_limit("register", limit=20, window_seconds=3600):
            flash("注册尝试过于频繁，请稍后再试。", "danger")
            return render_template("auth/register.html")
        try:
            code = clean_text(request.form.get("invite_code"), "邀请码", 80, required=True)
            real_name = clean_text(request.form.get("real_name"), "真实姓名", 80, required=True)
            username = clean_username(request.form.get("username"))
            password = validate_password(request.form.get("password"))
            contact = clean_text(request.form.get("contact"), "联系方式", 120, required=True)
        except ValidationError as exc:
            flash(str(exc), "danger")
            return render_template("auth/register.html")

        claim_token = secrets.token_urlsafe(24)
        invite = mongo.db.invite_codes.find_one_and_update(
            {
                "code": code,
                "real_name": real_name,
                "$or": [{"used": {"$ne": True}}, {"allow_reuse": True}],
            },
            {
                "$set": {
                    "used": True,
                    "used_at": now(),
                    "updated_at": now(),
                    "registration_claim_token": claim_token,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if not invite:
            code_only = mongo.db.invite_codes.find_one({"code": code})
            if code_only and not code_only.get("real_name"):
                flash("该邀请码还没有绑定真实姓名，请联系管理员上传填写后的注册表。", "danger")
                return render_template("auth/register.html")
            if code_only and code_only.get("used") and not code_only.get("allow_reuse"):
                flash("该邀请码已使用，不能再次注册。", "danger")
                return render_template("auth/register.html")
            flash("邀请码错误或真实姓名不匹配。", "danger")
            return render_template("auth/register.html")

        user_doc = {
            "real_name": real_name,
            "username": username,
            "password_hash": hash_password(password),
            "contact": contact,
            "avatar_url": "",
            "bio": "",
            "default_post_visibility": "public",
            "default_follow_delay_days": 0,
            "role": "user",
            "permissions": [],
            "status": "active",
            "cohort_tag": invite.get("cohort_tag", ""),
            "invite_code": invite.get("code", ""),
            "quality_photographer": False,
            "restricted_reason": "",
            "session_version": 0,
            "must_change_password": False,
            "created_at": now(),
            "updated_at": now(),
            "last_login_at": None,
        }
        try:
            result = mongo.db.users.insert_one(user_doc)
        except DuplicateKeyError:
            _release_invite_claim(invite, claim_token)
            flash("该昵称 / 用户名已被使用。", "danger")
            return render_template("auth/register.html")
        except Exception:
            _release_invite_claim(invite, claim_token)
            raise

        invite_query = {"_id": invite["_id"]}
        if not invite.get("allow_reuse"):
            invite_query["registration_claim_token"] = claim_token
        mongo.db.invite_codes.update_one(
            invite_query,
            {
                "$set": {"used_by": result.inserted_id, "updated_at": now()},
                "$addToSet": {"used_by_ids": result.inserted_id},
                "$unset": {"registration_claim_token": ""},
            },
        )
        flash("注册成功，请登录。", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not consume_rate_limit("login", limit=20, window_seconds=900):
            flash("登录尝试过于频繁，请 15 分钟后再试。", "danger")
            return render_template("auth/login.html"), 429
        username = str(request.form.get("username", "")).strip()[:80]
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
        set_login_session(user)
        mongo.db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login_at": now()}})
        flash("欢迎回来。", "success")
        next_url = request.args.get("next")
        if next_url:
            return redirect(safe_redirect_url(next_url, url_for("main.index")))
        if user.get("role") in ("admin", "super_admin"):
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("main.index"))
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("已退出登录。", "info")
    return redirect(url_for("main.index"))


def _release_invite_claim(invite, claim_token):
    if invite.get("allow_reuse"):
        return
    mongo.db.invite_codes.update_one(
        {"_id": invite["_id"], "registration_claim_token": claim_token, "used_by": None},
        {
            "$set": {"used": False, "used_at": None, "updated_at": now()},
            "$unset": {"registration_claim_token": ""},
        },
    )
