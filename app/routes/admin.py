import csv
import io
import re
import secrets
import string
import unicodedata
from datetime import datetime
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from bson import ObjectId
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, session, url_for
from pymongo.errors import DuplicateKeyError

from app.decorators import admin_required, login_required, permission_required, super_admin_required
from app.extensions import mongo
from app.utils.audit import log_action
from app.utils.files import UploadError, delete_upload_url, safe_upload_path, save_many, save_upload
from app.utils.permissions import PERMISSIONS, PERMISSION_KEYS, normalize_permissions
from app.utils.security import can_manage_user, hash_password, now, parse_int, safe_redirect_url, to_object_id, verify_password
from app.utils.storage import cleanup_deletable_files, mark_deletable_content, storage_summary
from app.utils.titles import attach_equipped_titles, awarded_titles_for_user
from app.utils.validation import ValidationError, clean_text, clean_theme_color, clean_username, validate_password

bp = Blueprint("admin", __name__, url_prefix="/admin")
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 5000
MAX_ZIP_FILES = 5000


def _random_password(length=12):
    alphabet = string.ascii_letters + string.digits + "!@#$%&"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _regex(value):
    return re.compile(re.escape(str(value)[:120]), re.IGNORECASE)


def _validated_text(value, label, max_length, required=False):
    try:
        return clean_text(value, label, max_length, required=required)
    except ValidationError as exc:
        abort(400, description=str(exc))


def _delete_upload_urls(urls):
    for url in urls:
        delete_upload_url(url)


def _paginate(collection, query, sort=("created_at", -1), per_page=20):
    page = parse_int(request.args.get("page"), default=1)
    total = collection.count_documents(query)
    docs = list(collection.find(query).sort(*sort).skip((page - 1) * per_page).limit(per_page))
    return docs, page, total, per_page


def _user_map(user_ids):
    ids = list({uid for uid in user_ids if uid})
    users = list(mongo.db.users.find({"_id": {"$in": ids}}))
    attach_equipped_titles(users, mongo.db)
    return {user["_id"]: user for user in users}


def _activity_map(activity_ids):
    ids = list({aid for aid in activity_ids if aid})
    return {item["_id"]: item for item in mongo.db.activities.find({"_id": {"$in": ids}})}


def _member_titles(active_only=False):
    query = {"is_active": True} if active_only else {}
    return list(mongo.db.member_titles.find(query).sort([("sort_order", 1), ("name", 1), ("created_at", 1)]))


def _decorate_managed_users(users):
    for user in users:
        user["_can_manage"] = can_manage_user(g.user, user)
        user["_peer_super_admin_locked"] = user.get("role") == "super_admin" and not user.get(
            "allow_peer_super_admin_management"
        )
    attach_equipped_titles(users, mongo.db)
    return users


def _role_permissions(role, submitted_permissions):
    if role == "super_admin":
        return PERMISSION_KEYS
    if role == "admin":
        return normalize_permissions(submitted_permissions)
    return []


@bp.route("/")
@admin_required
def dashboard():
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    stats = {
        "users": mongo.db.users.count_documents({"status": {"$ne": "deleted"}}),
        "today_users": mongo.db.users.count_documents({"created_at": {"$gte": today}}),
        "posts": mongo.db.posts.count_documents({"status": {"$ne": "deleted"}}),
        "today_posts": mongo.db.posts.count_documents({"created_at": {"$gte": today}}),
        "activities": mongo.db.activities.count_documents({}),
        "pending_submissions": mongo.db.submissions.count_documents({"status": "pending"}),
        "pending_reports": mongo.db.reports.count_documents({"status": "pending"}),
        "likes": mongo.db.likes.count_documents({}),
        "favorites": mongo.db.favorites.count_documents({}),
    }
    latest_users = list(mongo.db.users.find({}).sort("created_at", -1).limit(6))
    latest_posts = list(mongo.db.posts.find({}).sort("created_at", -1).limit(6))
    latest_submissions = list(mongo.db.submissions.find({}).sort("created_at", -1).limit(6))
    users = _user_map([post.get("author_id") for post in latest_posts] + [sub.get("user_id") for sub in latest_submissions])
    activities = _activity_map([sub.get("activity_id") for sub in latest_submissions])
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        latest_users=latest_users,
        latest_posts=latest_posts,
        latest_submissions=latest_submissions,
        users=users,
        activities=activities,
    )


@bp.route("/force-profile", methods=["GET", "POST"])
@login_required
def force_profile():
    if not g.user.get("must_change_password"):
        return redirect(url_for("admin.dashboard" if g.user.get("role") in ("admin", "super_admin") else "main.index"))
    if request.method == "POST":
        try:
            real_name = clean_text(request.form.get("real_name"), "真实姓名", 80, required=True)
            username = clean_username(request.form.get("username"))
            new_password = validate_password(request.form.get("new_password"), "新密码")
        except ValidationError as exc:
            flash(str(exc), "danger")
            return render_template("admin/force_profile.html")
        old_password = request.form.get("old_password", "")
        if not old_password:
            flash("请完整填写首次登录信息。", "danger")
        elif not verify_password(g.user["password_hash"], old_password):
            flash("初始密码不正确。", "danger")
        else:
            try:
                mongo.db.users.update_one(
                    {"_id": g.user["_id"]},
                    {
                        "$set": {
                            "real_name": real_name,
                            "username": username,
                            "password_hash": hash_password(new_password),
                            "must_change_password": False,
                            "updated_at": now(),
                        },
                        "$inc": {"session_version": 1},
                    },
                )
                session["session_version"] = g.user.get("session_version", 0) + 1
                flash("初始化信息已更新。", "success")
                return redirect(url_for("admin.dashboard"))
            except DuplicateKeyError:
                flash("该用户名已被使用。", "danger")
    return render_template("admin/force_profile.html")


@bp.route("/users")
@permission_required("manage_users")
def users():
    q = request.args.get("q", "").strip()
    query = {}
    if q:
        rgx = _regex(q)
        query["$or"] = [{"username": rgx}, {"real_name": rgx}, {"contact": rgx}, {"cohort_tag": rgx}]
    role = request.args.get("role", "")
    status = request.args.get("status", "")
    cohort_tag = request.args.get("cohort_tag", "").strip()
    quality = request.args.get("quality", "")
    if role in {"user", "admin", "super_admin"}:
        query["role"] = role
    if status in {"active", "restricted", "banned", "deleted"}:
        query["status"] = status
    if cohort_tag:
        query["cohort_tag"] = cohort_tag
    if quality == "yes":
        query["quality_photographer"] = True
    elif quality == "no":
        query["quality_photographer"] = {"$ne": True}
    docs, page, total, per_page = _paginate(mongo.db.users, query, sort=("created_at", -1))
    _decorate_managed_users(docs)
    cohorts = mongo.db.users.distinct("cohort_tag", {"cohort_tag": {"$nin": ["", None]}})
    return render_template(
        "admin/users.html",
        users=docs,
        member_titles=_member_titles(active_only=True),
        page=page,
        total=total,
        per_page=per_page,
        q=q,
        role=role,
        status=status,
        cohort_tag=cohort_tag,
        quality=quality,
        cohorts=sorted(cohorts),
    )


@bp.route("/users/<user_id>")
@permission_required("manage_users")
def user_detail(user_id):
    user = mongo.db.users.find_one({"_id": to_object_id(user_id)})
    if not user:
        abort(404)
    _decorate_managed_users([user])
    posts = list(mongo.db.posts.find({"author_id": user["_id"]}).sort("created_at", -1).limit(20))
    submissions = list(mongo.db.submissions.find({"user_id": user["_id"]}).sort("created_at", -1).limit(20))
    return render_template(
        "admin/user_detail.html",
        target=user,
        posts=posts,
        submissions=submissions,
        member_titles=_member_titles(),
        awarded_titles=awarded_titles_for_user(mongo.db, user),
    )


@bp.route("/users/create-admin", methods=["POST"])
@super_admin_required
def create_admin():
    try:
        username = clean_username(request.form.get("username"))
        real_name = clean_text(request.form.get("real_name"), "真实姓名", 80) or "新管理员"
        contact = clean_text(request.form.get("contact"), "联系方式", 120)
    except ValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.users"))
    role = request.form.get("role", "admin")
    if role not in {"admin", "super_admin"}:
        flash("角色类型不支持。", "danger")
        return redirect(url_for("admin.users"))
    password = _random_password()
    try:
        mongo.db.users.insert_one(
            {
                "real_name": real_name,
                "username": username,
                "password_hash": hash_password(password),
                "contact": contact,
                "avatar_url": "",
                "bio": "",
                "default_post_visibility": "public",
                "default_follow_delay_days": 0,
                "role": role,
                "permissions": _role_permissions(role, request.form.getlist("permissions")),
                "status": "active",
                "cohort_tag": "",
                "quality_photographer": False,
                "restricted_reason": "",
                "allow_peer_super_admin_management": request.form.get("allow_peer_super_admin_management") == "on"
                if role == "super_admin"
                else False,
                "awarded_title_ids": [],
                "equipped_title_id": None,
                "session_version": 0,
                "must_change_password": True,
                "created_at": now(),
                "updated_at": now(),
                "last_login_at": None,
            }
        )
        action = "create_super_admin" if role == "super_admin" else "create_admin"
        log_action(action, "user", username, role)
        flash(f"{role} 已创建，初始密码：{password}", "success")
    except DuplicateKeyError:
        flash("用户名已存在。", "danger")
    return redirect(url_for("admin.users"))


@bp.route("/users/<user_id>/permissions", methods=["GET", "POST"])
@super_admin_required
def user_permissions(user_id):
    target = mongo.db.users.find_one({"_id": to_object_id(user_id)})
    if not target:
        abort(404)
    if target.get("role") == "super_admin":
        flash("super_admin 默认拥有全部权限，不需要单独配置。", "info")
        return redirect(url_for("admin.users"))
    if target.get("role") != "admin":
        flash("只有管理员账号可以配置后台权限。", "warning")
        return redirect(url_for("admin.users"))
    if request.method == "POST":
        permissions = normalize_permissions(request.form.getlist("permissions"))
        mongo.db.users.update_one(
            {"_id": target["_id"]},
            {"$set": {"permissions": permissions, "updated_at": now()}},
        )
        log_action("update_admin_permissions", "user", target["_id"], ",".join(permissions))
        flash("管理员权限已更新。", "success")
        return redirect(url_for("admin.users"))
    current_permissions = target.get("permissions")
    if current_permissions is None:
        current_permissions = PERMISSION_KEYS
    return render_template(
        "admin/permissions.html",
        target=target,
        permission_options=PERMISSIONS,
        current_permissions=current_permissions,
    )


@bp.route("/users/<user_id>/action", methods=["POST"])
@permission_required("manage_users")
def user_action(user_id):
    target = mongo.db.users.find_one({"_id": to_object_id(user_id)})
    if not target:
        abort(404)
    if not can_manage_user(g.user, target):
        abort(403)
    action = request.form.get("action")
    update = {"updated_at": now()}
    if action == "ban":
        update["status"] = "banned"
    elif action == "unban":
        update["status"] = "active"
        update["restricted_reason"] = ""
    elif action == "restrict":
        update["status"] = "restricted"
        update["restricted_at"] = now()
        update["restricted_reason"] = _validated_text(request.form.get("restricted_reason"), "暂停原因", 200) or "管理员暂停了互动、发布和投稿功能。"
    elif action == "activate":
        update["status"] = "active"
        update["restricted_reason"] = ""
    elif action == "mark_quality":
        update["quality_photographer"] = True
        update["quality_marked_at"] = now()
    elif action == "unmark_quality":
        update["quality_photographer"] = False
    elif action == "delete":
        update["status"] = "deleted"
    elif action == "role":
        role = request.form.get("role")
        if g.user.get("role") != "super_admin" or role not in {"user", "admin", "super_admin"}:
            abort(403)
        previous_role = target.get("role")
        update["role"] = role
        update["permissions"] = _role_permissions(role, request.form.getlist("permissions"))
        if role == "super_admin" and previous_role != "super_admin":
            update.setdefault("allow_peer_super_admin_management", False)
        if previous_role == "super_admin" and role != "super_admin":
            update["allow_peer_super_admin_management"] = False
    else:
        abort(400)
    write_update = {"$set": update}
    if action in {"ban", "delete"}:
        write_update["$inc"] = {"session_version": 1}
    mongo.db.users.update_one({"_id": target["_id"]}, write_update)
    if action == "mark_quality":
        protect_update = {
            "$set": {
                "storage_status": "active",
                "storage_reason": "用户已标记为优质摄影，内容从清理池中移出。",
                "updated_at": now(),
            }
        }
        mongo.db.posts.update_many({"author_id": target["_id"], "storage_status": "deletable"}, protect_update)
        mongo.db.submissions.update_many({"user_id": target["_id"], "storage_status": "deletable"}, protect_update)
    if action == "role" and update.get("role") == "super_admin" and target.get("role") != "super_admin":
        log_action("promote_super_admin", "user", target["_id"], target.get("username", ""))
    log_action(f"user_{action}", "user", target["_id"], update.get("role", target.get("username", "")))
    flash("用户状态已更新。", "success")
    return redirect(safe_redirect_url(request.referrer, url_for("admin.users")))


@bp.route("/users/batch-status", methods=["POST"])
@permission_required("manage_users")
def users_batch_status():
    cohort_tag = _validated_text(request.form.get("cohort_tag"), "用户标签/届别", 40, required=True)
    action = request.form.get("action", "")
    if not cohort_tag or action not in {"restrict", "activate"}:
        flash("请选择用户标签/届别和批量操作。", "danger")
        return redirect(url_for("admin.users"))
    query = {"role": "user", "cohort_tag": cohort_tag, "status": {"$ne": "deleted"}}
    if action == "restrict":
        update = {
            "$set": {
                "status": "restricted",
                "restricted_at": now(),
                "restricted_reason": _validated_text(request.form.get("restricted_reason"), "暂停原因", 200) or f"{cohort_tag} 批量暂停部分功能。",
                "updated_at": now(),
            }
        }
        label = "暂停"
    else:
        update = {"$set": {"status": "active", "restricted_reason": "", "updated_at": now()}}
        label = "恢复"
    result = mongo.db.users.update_many(query, update)
    log_action(f"batch_user_{action}", "user", cohort_tag, f"{result.modified_count} users")
    flash(f"已按 {cohort_tag} 批量{label} {result.modified_count} 个普通用户账号。", "success")
    return redirect(url_for("admin.users", cohort_tag=cohort_tag))


@bp.route("/users/batch-award-title", methods=["POST"])
@permission_required("manage_users")
def users_batch_award_title():
    cohort_tag = _validated_text(request.form.get("cohort_tag"), "用户标签/届别", 40, required=True)
    title = mongo.db.member_titles.find_one({"_id": to_object_id(request.form.get("title_id")), "is_active": True})
    if not title:
        flash("头衔不存在或已停用。", "danger")
        return redirect(url_for("admin.users", cohort_tag=cohort_tag))
    result = mongo.db.users.update_many(
        {
            "cohort_tag": cohort_tag,
            "role": {"$in": ["user", "admin"]},
            "status": {"$ne": "deleted"},
            "awarded_title_ids": {"$ne": title["_id"]},
        },
        {
            "$addToSet": {"awarded_title_ids": title["_id"]},
            "$set": {"updated_at": now()},
        },
    )
    log_action("batch_award_title", "member_title", title["_id"], f"{cohort_tag}:{result.modified_count}")
    flash(f"已向 {result.modified_count} 个成员授予头衔：{title['name']}。", "success")
    return redirect(url_for("admin.users", cohort_tag=cohort_tag))


@bp.route("/users/<user_id>/award-title", methods=["POST"])
@permission_required("manage_users")
def award_title(user_id):
    target = mongo.db.users.find_one({"_id": to_object_id(user_id)})
    if not target:
        abort(404)
    if not can_manage_user(g.user, target):
        abort(403)
    title = mongo.db.member_titles.find_one({"_id": to_object_id(request.form.get("title_id")), "is_active": True})
    if not title:
        flash("头衔不存在或已停用。", "danger")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    result = mongo.db.users.update_one(
        {"_id": target["_id"], "awarded_title_ids": {"$ne": title["_id"]}},
        {
            "$addToSet": {"awarded_title_ids": title["_id"]},
            "$set": {"updated_at": now()},
        },
    )
    if not result.modified_count:
        flash(f"{target.get('username')} 已经拥有头衔：{title['name']}。", "info")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    log_action("award_title", "member_title", title["_id"], target.get("username", ""))
    flash(f"已向 {target.get('username')} 授予头衔：{title['name']}。", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@bp.route("/users/<user_id>/reset-password", methods=["POST"])
@permission_required("manage_users")
def reset_password(user_id):
    target = mongo.db.users.find_one({"_id": to_object_id(user_id)})
    if not target:
        abort(404)
    if not can_manage_user(g.user, target):
        abort(403)
    password = _random_password()
    mongo.db.users.update_one(
        {"_id": target["_id"]},
        {
            "$set": {"password_hash": hash_password(password), "must_change_password": True, "updated_at": now()},
            "$inc": {"session_version": 1},
        },
    )
    log_action("reset_password", "user", target["_id"], target.get("username", ""))
    flash(f"密码已重置，新密码：{password}", "success")
    return redirect(safe_redirect_url(request.referrer, url_for("admin.users")))


@bp.route("/member-titles", methods=["GET", "POST"])
@permission_required("manage_users")
def member_titles():
    if request.method == "POST":
        try:
            payload = {
                "name": clean_text(request.form.get("name"), "头衔名称", 40, required=True),
                "description": clean_text(request.form.get("description"), "头衔说明", 200),
                "sort_order": parse_int(request.form.get("sort_order"), default=100, minimum=0, maximum=9999),
                "is_active": True,
                "created_by": g.user["_id"],
                "created_at": now(),
                "updated_at": now(),
            }
        except ValidationError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("admin.member_titles"))
        try:
            mongo.db.member_titles.insert_one(payload)
        except DuplicateKeyError:
            flash("头衔名称已存在。", "danger")
            return redirect(url_for("admin.member_titles"))
        log_action("create_member_title", "member_title", payload["name"], "")
        flash("头衔已创建。", "success")
        return redirect(url_for("admin.member_titles"))
    return render_template("admin/member_titles.html", titles=_member_titles())


@bp.route("/member-titles/<title_id>/edit", methods=["POST"])
@permission_required("manage_users")
def edit_member_title(title_id):
    title = mongo.db.member_titles.find_one({"_id": to_object_id(title_id)})
    if not title:
        abort(404)
    try:
        payload = {
            "name": clean_text(request.form.get("name"), "头衔名称", 40, required=True),
            "description": clean_text(request.form.get("description"), "头衔说明", 200),
            "sort_order": parse_int(request.form.get("sort_order"), default=100, minimum=0, maximum=9999),
            "updated_at": now(),
        }
    except ValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.member_titles"))
    try:
        mongo.db.member_titles.update_one({"_id": title["_id"]}, {"$set": payload})
    except DuplicateKeyError:
        flash("头衔名称已存在。", "danger")
        return redirect(url_for("admin.member_titles"))
    log_action("edit_member_title", "member_title", title["_id"], payload["name"])
    flash("头衔已更新。", "success")
    return redirect(url_for("admin.member_titles"))


@bp.route("/member-titles/<title_id>/toggle", methods=["POST"])
@permission_required("manage_users")
def toggle_member_title(title_id):
    title = mongo.db.member_titles.find_one({"_id": to_object_id(title_id)})
    if not title:
        abort(404)
    is_active = not title.get("is_active", True)
    mongo.db.member_titles.update_one(
        {"_id": title["_id"]},
        {"$set": {"is_active": is_active, "updated_at": now()}},
    )
    if not is_active:
        mongo.db.users.update_many(
            {"equipped_title_id": title["_id"]},
            {"$set": {"equipped_title_id": None, "updated_at": now()}},
        )
    log_action("toggle_member_title", "member_title", title["_id"], "active" if is_active else "inactive")
    flash("头衔状态已更新。", "success")
    return redirect(url_for("admin.member_titles"))


@bp.route("/invites")
@permission_required("manage_invites")
def invites():
    q = request.args.get("q", "").strip()
    query = {}
    if q:
        rgx = _regex(q)
        query["$or"] = [{"code": rgx}, {"real_name": rgx}, {"cohort_tag": rgx}, {"batch_id": rgx}]
    cohort_tag = request.args.get("cohort_tag", "").strip()
    if cohort_tag:
        query["cohort_tag"] = cohort_tag
    docs, page, total, per_page = _paginate(mongo.db.invite_codes, query, sort=("created_at", -1))
    cohorts = mongo.db.invite_codes.distinct("cohort_tag", {"cohort_tag": {"$nin": ["", None]}})
    return render_template(
        "admin/invites.html",
        invites=docs,
        page=page,
        total=total,
        per_page=per_page,
        q=q,
        cohort_tag=cohort_tag,
        cohorts=sorted(cohorts),
    )


@bp.route("/invites/new", methods=["POST"])
@permission_required("manage_invites")
def invite_new():
    code = _validated_text(request.form.get("code"), "邀请码", 80, required=True)
    real_name = _validated_text(request.form.get("real_name"), "真实姓名", 80, required=True)
    cohort_tag = _sanitize_cohort_tag(request.form.get("cohort_tag"))
    try:
        if mongo.db.invite_codes.find_one({"code": code}):
            raise DuplicateKeyError("duplicate invite code")
        mongo.db.invite_codes.insert_one(
            {
                "code": code,
                "real_name": real_name,
                "used": False,
                "allow_reuse": request.form.get("allow_reuse") == "on",
                "used_by": None,
                "cohort_tag": cohort_tag,
                "created_at": now(),
                "used_at": None,
            }
        )
        log_action("create_invite", "invite_code", code, real_name)
        flash("邀请码已创建。", "success")
    except DuplicateKeyError:
        flash("重复的邀请码 + 姓名组合已跳过。", "warning")
    return redirect(url_for("admin.invites"))


def _invite_code(prefix="MUXI"):
    return f"{prefix}{secrets.token_hex(4).upper()}"


def _csv_download(output, filename):
    data = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    return send_file(data, as_attachment=True, download_name=filename, mimetype="text/csv")


def _sanitize_invite_prefix(raw_prefix):
    prefix = (raw_prefix or "MUXI").strip().upper()
    return re.sub(r"[^A-Z0-9_-]", "", prefix)[:12] or "MUXI"


def _sanitize_cohort_tag(raw_value):
    value = (raw_value or "").strip()
    value = "".join(" " if unicodedata.category(char).startswith("C") else char for char in value)
    return re.sub(r"\s+", " ", value)[:40]


def _row_value(row, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _csv_cell(value):
    text = str(value or "")
    return f"'{text}" if text.lstrip().startswith(("=", "+", "-", "@")) else text


def _csv_rows(file):
    if not file or not file.filename:
        raise ValidationError("请选择 CSV 文件。")
    if not file.filename.lower().endswith(".csv"):
        raise ValidationError("仅支持 CSV 文件。")
    stream = file.stream
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    if size > MAX_CSV_BYTES:
        raise ValidationError("CSV 文件不能超过 2MB。")
    try:
        text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        for row_number, row in enumerate(reader, start=2):
            if row_number > MAX_CSV_ROWS + 1:
                raise ValidationError(f"CSV 最多允许 {MAX_CSV_ROWS} 行数据。")
            yield row_number, row
    except UnicodeDecodeError as exc:
        raise ValidationError("CSV 必须使用 UTF-8 编码。") from exc


@bp.route("/invites/generate-sheet", methods=["POST"])
@permission_required("manage_invites")
def invites_generate_sheet():
    count = parse_int(request.form.get("count"), default=30, minimum=1, maximum=500)
    prefix = _sanitize_invite_prefix(request.form.get("prefix"))
    cohort_tag = _sanitize_cohort_tag(request.form.get("cohort_tag"))
    allow_reuse = request.form.get("allow_reuse") == "on"
    batch_id = secrets.token_hex(6)

    rows = []
    failures = 0
    for sequence in range(1, count + 1):
        inserted = False
        for _ in range(12):
            code = _invite_code(prefix)
            try:
                if mongo.db.invite_codes.find_one({"code": code}):
                    continue
                mongo.db.invite_codes.insert_one(
                    {
                        "code": code,
                        "real_name": "",
                        "used": False,
                        "allow_reuse": allow_reuse,
                        "used_by": None,
                        "cohort_tag": cohort_tag,
                        "created_at": now(),
                        "used_at": None,
                        "batch_id": batch_id,
                        "sequence": sequence,
                        "sheet_status": "waiting_name",
                        "name_bound_at": None,
                    }
                )
                rows.append([sequence, "", _csv_cell(cohort_tag), code, "右侧邀请码可剪下分发；填好姓名后上传本表自动配对"])
                inserted = True
                break
            except DuplicateKeyError:
                continue
        if not inserted:
            failures += 1

    if not rows:
        flash("生成失败，请更换前缀后重试。", "danger")
        return redirect(url_for("admin.invites"))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["序号", "真实姓名（填写）", "用户标签/届别", "邀请码（剪下发放）", "备注"])
    writer.writerows(rows)
    log_action("generate_invite_sheet", "invite_code", batch_id, f"生成 {len(rows)} 条，失败 {failures}")
    if failures:
        flash(f"已生成 {len(rows)} 条，另有 {failures} 条因随机冲突未生成。", "warning")
    return _csv_download(output, f"muxi_invite_sheet_{batch_id}.csv")


@bp.route("/invites/bind-sheet", methods=["POST"])
@permission_required("manage_invites")
def invites_bind_sheet():
    file = request.files.get("sheet_file")
    success = 0
    skipped = 0
    failures = []
    try:
        rows = _csv_rows(file)
        for index, row in rows:
            code = _row_value(row, "邀请码（剪下发放）", "邀请码", "code")
            real_name = _row_value(row, "真实姓名（填写）", "真实姓名", "real_name")
            cohort_tag = _sanitize_cohort_tag(_row_value(row, "用户标签/届别", "用户标签", "届别", "cohort_tag"))
            if not code:
                failures.append(f"第 {index} 行缺少邀请码")
                continue
            if not real_name:
                skipped += 1
                continue
            try:
                code = clean_text(code, "邀请码", 80, required=True)
                real_name = clean_text(real_name, "真实姓名", 80, required=True)
            except ValidationError as exc:
                failures.append(f"第 {index} 行：{exc}")
                continue
            invite = mongo.db.invite_codes.find_one({"code": code})
            if not invite:
                failures.append(f"第 {index} 行邀请码不存在：{code}")
                continue
            if invite.get("used"):
                failures.append(f"第 {index} 行邀请码已使用：{code}")
                continue
            if invite.get("real_name") and invite.get("real_name") != real_name:
                failures.append(f"第 {index} 行邀请码已绑定其他姓名：{code}")
                continue
            try:
                set_fields = {
                    "real_name": real_name,
                    "sheet_status": "bound",
                    "name_bound_at": now(),
                    "updated_at": now(),
                }
                if cohort_tag:
                    set_fields["cohort_tag"] = cohort_tag
                result = mongo.db.invite_codes.update_one(
                    {"_id": invite["_id"], "used": False},
                    {"$set": set_fields},
                )
                if result.modified_count:
                    success += 1
                else:
                    skipped += 1
            except DuplicateKeyError:
                failures.append(f"第 {index} 行重复绑定：{code} / {real_name}")
    except ValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.invites"))

    log_action("bind_invite_sheet", "invite_code", "", f"绑定 {success}，跳过 {skipped}，失败 {len(failures)}")
    message = f"注册表配对完成：成功绑定 {success} 条，空姓名跳过 {skipped} 条，失败 {len(failures)} 条。"
    if failures:
        message += "；".join(failures[:6])
    flash(message, "success" if success else "warning")
    return redirect(url_for("admin.invites"))


@bp.route("/invites/bulk-generate", methods=["POST"])
@permission_required("manage_invites")
def invites_bulk_generate():
    prefix = _sanitize_invite_prefix(request.form.get("prefix"))
    cohort_tag = _sanitize_cohort_tag(request.form.get("cohort_tag"))
    names = []
    for line in request.form.get("real_names", "").splitlines():
        name = _validated_text(line, "真实姓名", 80)
        if name and name not in names:
            names.append(name)
        if len(names) > 500:
            abort(400, description="单次最多生成 500 个邀请码。")
    if not names:
        flash("请至少填写一个真实姓名。", "danger")
        return redirect(url_for("admin.invites"))

    success = 0
    failures = []
    for real_name in names:
        inserted = False
        for _ in range(8):
            code = _invite_code(prefix)
            try:
                if mongo.db.invite_codes.find_one({"code": code}):
                    continue
                mongo.db.invite_codes.insert_one(
                    {
                        "code": code,
                        "real_name": real_name,
                        "used": False,
                        "allow_reuse": request.form.get("allow_reuse") == "on",
                        "used_by": None,
                        "cohort_tag": cohort_tag,
                        "created_at": now(),
                        "used_at": None,
                        "sheet_status": "bound",
                    }
                )
                success += 1
                inserted = True
                break
            except DuplicateKeyError:
                continue
        if not inserted:
            failures.append(real_name)

    log_action("bulk_generate_invites", "invite_code", "", f"成功 {success}，失败 {len(failures)}")
    message = f"批量生成完成：成功 {success} 条，失败 {len(failures)} 条。"
    if failures:
        message += "失败姓名：" + "、".join(failures[:10])
    flash(message, "success" if success else "warning")
    return redirect(url_for("admin.invites"))


@bp.route("/invites/import", methods=["POST"])
@permission_required("manage_invites")
def invites_import():
    file = request.files.get("csv_file")
    success = 0
    failures = []
    try:
        for index, row in _csv_rows(file):
            try:
                code = clean_text(row.get("邀请码") or row.get("code"), "邀请码", 80, required=True)
                real_name = clean_text(row.get("真实姓名") or row.get("real_name"), "真实姓名", 80, required=True)
            except ValidationError as exc:
                failures.append(f"第 {index} 行：{exc}")
                continue
            cohort_tag = _sanitize_cohort_tag(row.get("用户标签/届别") or row.get("用户标签") or row.get("届别") or row.get("cohort_tag"))
            used_value = (row.get("是否已使用") or "").strip().lower()
            used = used_value in {"是", "true", "1", "yes"}
            try:
                if mongo.db.invite_codes.find_one({"code": code}):
                    raise DuplicateKeyError("duplicate invite code")
                mongo.db.invite_codes.insert_one(
                    {
                        "code": code,
                        "real_name": real_name,
                        "used": used,
                        "allow_reuse": False,
                        "used_by": None,
                        "cohort_tag": cohort_tag,
                        "created_at": now(),
                        "used_at": now() if used else None,
                    }
                )
                success += 1
            except DuplicateKeyError:
                failures.append(f"第 {index} 行重复：{code} / {real_name}")
    except ValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.invites"))
    log_action("import_invites", "invite_code", "", f"成功 {success}，失败 {len(failures)}")
    flash(f"导入完成：成功 {success} 条，失败 {len(failures)} 条。{'；'.join(failures[:5])}", "success" if success else "warning")
    return redirect(url_for("admin.invites"))


@bp.route("/invites/template")
@permission_required("manage_invites")
def invites_template():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["序号", "真实姓名（填写）", "用户标签/届别", "邀请码（剪下发放）", "备注"])
    writer.writerow([1, "张三", "2026届", "MUXI2026A001", "右侧邀请码可剪下分发；填好姓名后上传本表自动配对"])
    return _csv_download(output, "muxi_invite_sheet_template.csv")


@bp.route("/invites/export")
@permission_required("manage_invites")
def invites_export():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["邀请码", "真实姓名", "用户标签/届别", "是否已使用", "绑定用户ID", "创建时间", "使用时间"])
    for item in mongo.db.invite_codes.find({}).sort("created_at", -1):
        writer.writerow(
            [
                _csv_cell(item.get("code", "")),
                _csv_cell(item.get("real_name", "")),
                _csv_cell(item.get("cohort_tag", "")),
                "是" if item.get("used") else "否",
                str(item.get("used_by") or ""),
                item.get("created_at", ""),
                item.get("used_at", ""),
            ]
        )
    return _csv_download(output, "muxi_invites.csv")


@bp.route("/invites/<invite_id>/delete", methods=["POST"])
@permission_required("manage_invites")
def invite_delete(invite_id):
    mongo.db.invite_codes.delete_one({"_id": to_object_id(invite_id)})
    log_action("delete_invite", "invite_code", invite_id, "")
    flash("邀请码已删除。", "success")
    return redirect(url_for("admin.invites"))


@bp.route("/posts")
@permission_required("moderate_community")
def posts():
    q = request.args.get("q", "").strip()
    query = {}
    if q:
        rgx = _regex(q)
        query["$or"] = [{"title": rgx}, {"description": rgx}, {"tags": rgx}]
    status = request.args.get("status", "")
    if status in {"normal", "hidden", "deleted"}:
        query["status"] = status
    docs, page, total, per_page = _paginate(mongo.db.posts, query, sort=("created_at", -1))
    users = _user_map([post.get("author_id") for post in docs])
    return render_template("admin/posts.html", posts=docs, users=users, page=page, total=total, per_page=per_page, q=q, status=status)


@bp.route("/comments")
@permission_required("moderate_community")
def comments():
    q = request.args.get("q", "").strip()
    query = {}
    if q:
        query["content"] = _regex(q)
    status = request.args.get("status", "")
    if status in {"normal", "deleted"}:
        query["status"] = status
    docs, page, total, per_page = _paginate(mongo.db.comments, query, sort=("created_at", -1))
    users = _user_map([comment.get("author_id") for comment in docs])
    post_ids = list({comment.get("post_id") for comment in docs if comment.get("post_id")})
    posts_map = {post["_id"]: post for post in mongo.db.posts.find({"_id": {"$in": post_ids}})}
    return render_template(
        "admin/comments.html",
        comments=docs,
        users=users,
        posts_map=posts_map,
        page=page,
        total=total,
        per_page=per_page,
        q=q,
        status=status,
    )


@bp.route("/posts/<post_id>/moderate", methods=["POST"])
@permission_required("moderate_community")
def post_moderate(post_id):
    post = mongo.db.posts.find_one({"_id": to_object_id(post_id)})
    if not post:
        abort(404)
    action = request.form.get("action")
    status_map = {"hide": "hidden", "restore": "normal", "delete": "deleted"}
    if action not in status_map:
        abort(400)
    mongo.db.posts.update_one({"_id": post["_id"]}, {"$set": {"status": status_map[action], "updated_at": now()}})
    log_action(f"post_{action}", "post", post["_id"], post.get("title", ""))
    flash("帖子状态已更新。", "success")
    return redirect(safe_redirect_url(request.referrer, url_for("admin.posts")))


@bp.route("/comments/<comment_id>/delete", methods=["POST"])
@permission_required("moderate_community")
def admin_delete_comment(comment_id):
    comment = mongo.db.comments.find_one({"_id": to_object_id(comment_id)})
    if not comment:
        abort(404)
    result = mongo.db.comments.update_one(
        {"_id": comment["_id"], "status": {"$ne": "deleted"}},
        {"$set": {"status": "deleted", "updated_at": now()}},
    )
    if result.modified_count:
        mongo.db.posts.update_one({"_id": comment["post_id"]}, {"$inc": {"comment_count": -1}})
    log_action("delete_comment", "comment", comment["_id"], "")
    flash("评论已删除。", "success")
    return redirect(safe_redirect_url(request.referrer, url_for("admin.posts")))


@bp.route("/reports")
@permission_required("moderate_community")
def reports():
    query = {}
    status = request.args.get("status", "")
    if status in {"pending", "resolved", "rejected"}:
        query["status"] = status
    target_type = request.args.get("target_type", "")
    if target_type in {"post", "comment"}:
        query["target_type"] = target_type
    docs, page, total, per_page = _paginate(mongo.db.reports, query, sort=("created_at", -1))
    users = _user_map([report.get("reporter_id") for report in docs] + [report.get("handled_by") for report in docs])
    post_ids = [report.get("target_id") for report in docs if report.get("target_type") == "post"]
    comment_ids = [report.get("target_id") for report in docs if report.get("target_type") == "comment"]
    comments_map = {item["_id"]: item for item in mongo.db.comments.find({"_id": {"$in": comment_ids}})}
    post_ids.extend([item.get("post_id") for item in comments_map.values() if item.get("post_id")])
    posts_map = {item["_id"]: item for item in mongo.db.posts.find({"_id": {"$in": post_ids}})}
    return render_template(
        "admin/reports.html",
        reports=docs,
        users=users,
        posts_map=posts_map,
        comments_map=comments_map,
        page=page,
        total=total,
        per_page=per_page,
        status=status,
        target_type=target_type,
    )


@bp.route("/reports/<report_id>/handle", methods=["POST"])
@permission_required("moderate_community")
def handle_report(report_id):
    report = mongo.db.reports.find_one({"_id": to_object_id(report_id)})
    if not report:
        abort(404)
    status = request.form.get("status")
    if status not in {"resolved", "rejected", "pending"}:
        abort(400)
    mongo.db.reports.update_one(
        {"_id": report["_id"]},
        {
            "$set": {
                "status": status,
                "admin_note": _validated_text(request.form.get("admin_note"), "处理备注", 1000),
                "handled_by": g.user["_id"] if status != "pending" else None,
                "handled_at": now() if status != "pending" else None,
                "updated_at": now(),
            }
        },
    )
    log_action("handle_report", "report", report["_id"], status)
    flash("举报处理状态已更新。", "success")
    return redirect(safe_redirect_url(request.referrer, url_for("admin.reports")))


def _activity_payload(existing=None):
    payload = {
        "title": _validated_text(request.form.get("title"), "活动标题", 120, required=True),
        "intro": _validated_text(request.form.get("intro"), "活动简介", 3000),
        "requirements": _validated_text(request.form.get("requirements"), "投稿要求", 5000),
        "upload_instructions": _validated_text(request.form.get("upload_instructions"), "上传说明", 3000),
        "start_time": _validated_text(request.form.get("start_time"), "开始时间", 40),
        "end_time": _validated_text(request.form.get("end_time"), "结束时间", 40),
        "status": request.form.get("status", "draft"),
        "allow_public_submissions_view": request.form.get("allow_public_submissions_view") == "on",
        "show_selected_works": request.form.get("show_selected_works") == "on",
        "updated_at": now(),
    }
    if payload["status"] not in {"draft", "active", "closed", "showcased"}:
        payload["status"] = "draft"
    new_uploads = []
    try:
        cover = request.files.get("cover_image")
        if cover and cover.filename:
            payload["cover_image"] = save_upload(cover, "activities")
            new_uploads.append(payload["cover_image"])
        samples = save_many(request.files.getlist("sample_images"), "activities")
        new_uploads.extend(samples)
        if samples:
            combined = (existing or {}).get("sample_images", []) + samples
            if len(combined) > current_app.config["MAX_FILES_PER_UPLOAD"]:
                raise UploadError(f"活动样图最多保留 {current_app.config['MAX_FILES_PER_UPLOAD']} 张。")
            payload["sample_images"] = combined
    except UploadError:
        _delete_upload_urls(new_uploads)
        raise
    return payload, new_uploads


@bp.route("/activities")
@permission_required("manage_activities")
def admin_activities():
    q = request.args.get("q", "").strip()
    query = {}
    if q:
        query["title"] = _regex(q)
    status = request.args.get("status", "")
    if status in {"draft", "active", "closed", "showcased"}:
        query["status"] = status
    docs, page, total, per_page = _paginate(mongo.db.activities, query, sort=("created_at", -1))
    return render_template("admin/activities.html", activities=docs, page=page, total=total, per_page=per_page, q=q, status=status)


@bp.route("/activities/new", methods=["GET", "POST"])
@permission_required("manage_activities")
def activity_new():
    if request.method == "POST":
        try:
            payload, new_uploads = _activity_payload()
        except UploadError as exc:
            flash(str(exc), "danger")
            return render_template("admin/activity_form.html", activity=None)
        if not payload["title"]:
            flash("活动标题不能为空。", "danger")
            return render_template("admin/activity_form.html", activity=None)
        payload.update({"cover_image": payload.get("cover_image", ""), "sample_images": payload.get("sample_images", [])})
        payload.update({"created_by": g.user["_id"], "created_at": now()})
        try:
            result = mongo.db.activities.insert_one(payload)
        except Exception:
            _delete_upload_urls(new_uploads)
            raise
        log_action("create_activity", "activity", result.inserted_id, payload["title"])
        flash("活动已创建。", "success")
        return redirect(url_for("admin.admin_activities"))
    return render_template("admin/activity_form.html", activity=None)


@bp.route("/activities/<activity_id>/edit", methods=["GET", "POST"])
@permission_required("manage_activities")
def activity_edit(activity_id):
    activity = mongo.db.activities.find_one({"_id": to_object_id(activity_id)})
    if not activity:
        abort(404)
    if request.method == "POST":
        try:
            payload, new_uploads = _activity_payload(activity)
        except UploadError as exc:
            flash(str(exc), "danger")
            return render_template("admin/activity_form.html", activity=activity)
        if not payload["title"]:
            flash("活动标题不能为空。", "danger")
            return render_template("admin/activity_form.html", activity=activity)
        try:
            mongo.db.activities.update_one({"_id": activity["_id"]}, {"$set": payload})
        except Exception:
            _delete_upload_urls(new_uploads)
            raise
        if payload.get("cover_image") and payload["cover_image"] != activity.get("cover_image"):
            delete_upload_url(activity.get("cover_image"))
        log_action("edit_activity", "activity", activity["_id"], payload["title"])
        flash("活动已更新。", "success")
        return redirect(url_for("admin.admin_activities"))
    return render_template("admin/activity_form.html", activity=activity)


@bp.route("/activities/<activity_id>/delete", methods=["POST"])
@permission_required("manage_activities")
def activity_delete(activity_id):
    activity = mongo.db.activities.find_one({"_id": to_object_id(activity_id)})
    if not activity:
        abort(404)
    if mongo.db.submissions.find_one({"activity_id": activity["_id"]}):
        flash("该活动已有投稿，不能删除。请将活动状态改为关闭。", "warning")
        return redirect(url_for("admin.admin_activities"))
    mongo.db.activities.delete_one({"_id": activity["_id"]})
    _delete_upload_urls([activity.get("cover_image"), *activity.get("sample_images", [])])
    log_action("delete_activity", "activity", activity["_id"], activity.get("title", ""))
    flash("活动已删除。", "success")
    return redirect(url_for("admin.admin_activities"))


@bp.route("/submissions")
@permission_required("review_submissions")
def submissions():
    query = {}
    activity_id = request.args.get("activity_id", "").strip()
    if activity_id:
        query["activity_id"] = to_object_id(activity_id)
    status = request.args.get("status", "")
    if status in {"pending", "selected", "rejected", "returned"}:
        query["status"] = status
    docs, page, total, per_page = _paginate(mongo.db.submissions, query, sort=("created_at", -1))
    users = _user_map([sub.get("user_id") for sub in docs])
    activities = _activity_map([sub.get("activity_id") for sub in docs])
    all_activities = list(mongo.db.activities.find({}).sort("created_at", -1))
    return render_template(
        "admin/submissions.html",
        submissions=docs,
        users=users,
        activities=activities,
        all_activities=all_activities,
        page=page,
        total=total,
        per_page=per_page,
        status=status,
        activity_id=activity_id,
    )


@bp.route("/submissions/<submission_id>/review", methods=["POST"])
@permission_required("review_submissions")
def review_submission(submission_id):
    submission = mongo.db.submissions.find_one({"_id": to_object_id(submission_id)})
    if not submission:
        abort(404)
    status = request.form.get("status")
    if status not in {"pending", "selected", "rejected", "returned"}:
        abort(400)
    mongo.db.submissions.update_one(
        {"_id": submission["_id"]},
        {
            "$set": {
                "status": status,
                "admin_note": _validated_text(request.form.get("admin_note"), "审核备注", 1000),
                "reviewed_by": g.user["_id"],
                "reviewed_at": now(),
                "updated_at": now(),
            }
        },
    )
    log_action("review_submission", "submission", submission["_id"], status)
    flash("审核成功。", "success")
    return redirect(safe_redirect_url(request.referrer, url_for("admin.submissions")))


@bp.route("/submissions/batch-review", methods=["POST"])
@permission_required("review_submissions")
def batch_review():
    ids = [to_object_id(item) for item in request.form.getlist("submission_ids")]
    status = request.form.get("status")
    if not ids or status not in {"selected", "rejected", "returned"}:
        flash("请选择投稿和审核状态。", "danger")
        return redirect(url_for("admin.submissions"))
    result = mongo.db.submissions.update_many(
        {"_id": {"$in": ids}},
        {
            "$set": {
                "status": status,
                "admin_note": _validated_text(request.form.get("admin_note"), "审核备注", 1000),
                "reviewed_by": g.user["_id"],
                "reviewed_at": now(),
                "updated_at": now(),
            }
        },
    )
    log_action("batch_review_submissions", "submission", "", f"{result.modified_count} => {status}")
    flash(f"批量审核完成，更新 {result.modified_count} 条。", "success")
    return redirect(url_for("admin.submissions"))


@bp.route("/activities/<activity_id>/download")
@permission_required("review_submissions")
def download_submissions(activity_id):
    activity = mongo.db.activities.find_one({"_id": to_object_id(activity_id)})
    if not activity:
        abort(404)
    mode = request.args.get("mode", "all")
    if mode not in {"all", "selected"}:
        abort(400)
    query = {"activity_id": activity["_id"]}
    if mode == "selected":
        query["status"] = "selected"
    entries = []
    total_bytes = 0
    max_bytes = current_app.config["MAX_ZIP_DOWNLOAD_MB"] * 1024 * 1024
    for sub in mongo.db.submissions.find(query, {"images": 1}):
        for index, url in enumerate(sub.get("images", []), start=1):
            parts = url.strip("/").split("/")
            if len(parts) != 3 or parts[0] != "uploads":
                continue
            path = safe_upload_path(parts[1], parts[2])
            if not path:
                continue
            try:
                total_bytes += path.stat().st_size
            except OSError:
                continue
            if len(entries) >= MAX_ZIP_FILES or total_bytes > max_bytes:
                abort(413, description="下载内容过多，请缩小范围后重试。")
            entries.append((sub["_id"], index, path))
    buffer = SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zip_file:
        for submission_id, index, path in entries:
            zip_file.write(path, f"{submission_id}/{index}-{path.name}")
    buffer.seek(0)
    log_action("download_submissions", "activity", activity["_id"], mode)
    name = f"muxi_{activity['_id']}_{mode}.zip"
    return send_file(buffer, as_attachment=True, download_name=name, mimetype="application/zip")


@bp.route("/storage", methods=["GET", "POST"])
@permission_required("manage_storage")
def storage():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "mark":
            older_than_days = parse_int(request.form.get("older_than_days"), default=30, minimum=1, maximum=3650)
            result = mark_deletable_content(mongo.db, older_than_days=older_than_days)
            log_action("mark_deletable_content", "storage", "", f"{older_than_days} days")
            flash(
                f"已标记 {older_than_days} 天前的普通内容：帖子 {result['posts']} 条，投稿 {result['submissions']} 条。",
                "success",
            )
        elif action == "cleanup":
            target_free_mb = parse_int(request.form.get("target_free_mb"), default=1024, minimum=1, maximum=1048576)
            result = cleanup_deletable_files(mongo.db, target_free_mb=target_free_mb)
            log_action("cleanup_deletable_files", "storage", "", f"{result['deleted_files']} files")
            if result["skipped"]:
                flash(f"当前磁盘可用空间 {result['before_free_mb']}MB，已高于目标 {target_free_mb}MB，未执行清理。", "info")
            else:
                flash(
                    f"已按时间从早到晚清理 {result['deleted_files']} 个可删除文件，约释放 {result['freed_mb']}MB。",
                    "success",
                )
        else:
            abort(400)
        return redirect(url_for("admin.storage"))
    return render_template("admin/storage.html", summary=storage_summary(mongo.db))


@bp.route("/settings", methods=["GET", "POST"])
@permission_required("manage_settings")
def settings():
    settings_doc = mongo.db.site_settings.find_one({"key": "default"}) or {"key": "default"}
    if request.method == "POST":
        update = {
            "site_name": _validated_text(request.form.get("site_name"), "网站名称", 120) or "泸州高中木樨映像",
            "club_intro": _validated_text(request.form.get("club_intro"), "社团介绍", 3000),
            "contact": _validated_text(request.form.get("contact"), "联系方式", 300),
            "footer": _validated_text(request.form.get("footer"), "页脚", 300),
            "theme_color": clean_theme_color(request.form.get("theme_color")),
            "updated_at": now(),
        }
        file_fields = {
            "logo": "logo",
            "home_banner": "home_banner",
            "auth_background": "auth_background",
            "community_cover": "community_cover",
            "activity_cover": "activity_cover",
            "default_avatar": "default_avatar",
            "empty_illustration": "empty_illustration",
        }
        new_uploads = []
        try:
            for field, key in file_fields.items():
                file = request.files.get(field)
                if file and file.filename:
                    update[key] = save_upload(file, "site_assets")
                    new_uploads.append(update[key])
        except UploadError as exc:
            _delete_upload_urls(new_uploads)
            flash(str(exc), "danger")
            return render_template("admin/settings.html", settings=settings_doc)
        try:
            mongo.db.site_settings.update_one({"key": "default"}, {"$set": update, "$setOnInsert": {"key": "default"}}, upsert=True)
        except Exception:
            _delete_upload_urls(new_uploads)
            raise
        retained_assets = {update.get(key, settings_doc.get(key)) for key in file_fields.values()}
        for key in file_fields.values():
            if update.get(key) and settings_doc.get(key) not in retained_assets:
                delete_upload_url(settings_doc.get(key))
        log_action("update_site_settings", "site_settings", "default", "")
        flash("网站设置已保存。", "success")
        return redirect(url_for("admin.settings"))
    return render_template("admin/settings.html", settings=settings_doc)


@bp.route("/audit-logs")
@permission_required("view_audit_logs")
def audit_logs():
    q = request.args.get("q", "").strip()
    query = {}
    if q:
        query["action"] = _regex(q)
    docs, page, total, per_page = _paginate(mongo.db.audit_logs, query, sort=("created_at", -1))
    users = _user_map([item.get("admin_id") for item in docs])
    return render_template("admin/audit_logs.html", logs=docs, users=users, page=page, total=total, per_page=per_page, q=q)
