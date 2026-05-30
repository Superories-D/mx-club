import csv
import io
import re
import secrets
import string
from datetime import datetime
from zipfile import ZIP_DEFLATED, ZipFile

from bson import ObjectId
from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for
from pymongo.errors import DuplicateKeyError

from app.decorators import admin_required, login_required, permission_required, super_admin_required
from app.extensions import mongo
from app.utils.audit import log_action
from app.utils.files import UploadError, safe_upload_path, save_many, save_upload
from app.utils.permissions import PERMISSIONS, PERMISSION_KEYS, normalize_permissions
from app.utils.security import can_manage_user, hash_password, now, parse_int, to_object_id, verify_password

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _random_password(length=12):
    alphabet = string.ascii_letters + string.digits + "!@#$%&"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _regex(value):
    return re.compile(re.escape(value), re.IGNORECASE)


def _paginate(collection, query, sort=("created_at", -1), per_page=20):
    page = parse_int(request.args.get("page"), default=1)
    total = collection.count_documents(query)
    docs = list(collection.find(query).sort(*sort).skip((page - 1) * per_page).limit(per_page))
    return docs, page, total, per_page


def _user_map(user_ids):
    ids = list({uid for uid in user_ids if uid})
    return {user["_id"]: user for user in mongo.db.users.find({"_id": {"$in": ids}})}


def _activity_map(activity_ids):
    ids = list({aid for aid in activity_ids if aid})
    return {item["_id"]: item for item in mongo.db.activities.find({"_id": {"$in": ids}})}


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
        real_name = request.form.get("real_name", "").strip()
        username = request.form.get("username", "").strip()
        old_password = request.form.get("old_password", "")
        new_password = request.form.get("new_password", "")
        if not all([real_name, username, old_password, new_password]):
            flash("请完整填写首次登录信息。", "danger")
        elif not verify_password(g.user["password_hash"], old_password):
            flash("初始密码不正确。", "danger")
        elif len(new_password) < 6:
            flash("新密码至少需要 6 位。", "danger")
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
                        }
                    },
                )
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
        query["$or"] = [{"username": rgx}, {"real_name": rgx}, {"contact": rgx}]
    role = request.args.get("role", "")
    status = request.args.get("status", "")
    if role in {"user", "admin", "super_admin"}:
        query["role"] = role
    if status in {"active", "banned", "deleted"}:
        query["status"] = status
    docs, page, total, per_page = _paginate(mongo.db.users, query, sort=("created_at", -1))
    return render_template("admin/users.html", users=docs, page=page, total=total, per_page=per_page, q=q, role=role, status=status)


@bp.route("/users/<user_id>")
@permission_required("manage_users")
def user_detail(user_id):
    user = mongo.db.users.find_one({"_id": to_object_id(user_id)})
    if not user:
        abort(404)
    posts = list(mongo.db.posts.find({"author_id": user["_id"]}).sort("created_at", -1).limit(20))
    submissions = list(mongo.db.submissions.find({"user_id": user["_id"]}).sort("created_at", -1).limit(20))
    return render_template("admin/user_detail.html", target=user, posts=posts, submissions=submissions)


@bp.route("/users/create-admin", methods=["POST"])
@super_admin_required
def create_admin():
    username = request.form.get("username", "").strip()
    real_name = request.form.get("real_name", "").strip() or "新管理员"
    if not username:
        flash("请填写管理员用户名。", "danger")
        return redirect(url_for("admin.users"))
    password = _random_password()
    try:
        mongo.db.users.insert_one(
            {
                "real_name": real_name,
                "username": username,
                "password_hash": hash_password(password),
                "contact": request.form.get("contact", "").strip(),
                "avatar_url": "",
                "bio": "",
                "role": "admin",
                "permissions": normalize_permissions(request.form.getlist("permissions")),
                "status": "active",
                "must_change_password": True,
                "created_at": now(),
                "updated_at": now(),
                "last_login_at": None,
            }
        )
        log_action("create_admin", "user", username, f"创建管理员，初始密码：{password}")
        flash(f"管理员已创建，初始密码：{password}", "success")
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
    elif action == "delete":
        update["status"] = "deleted"
    elif action == "role":
        role = request.form.get("role")
        if g.user.get("role") != "super_admin" or role not in {"user", "admin"}:
            abort(403)
        update["role"] = role
        update["permissions"] = PERMISSION_KEYS if role == "admin" else []
    else:
        abort(400)
    mongo.db.users.update_one({"_id": target["_id"]}, {"$set": update})
    log_action(f"user_{action}", "user", target["_id"], target.get("username", ""))
    flash("用户状态已更新。", "success")
    return redirect(request.referrer or url_for("admin.users"))


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
        {"$set": {"password_hash": hash_password(password), "must_change_password": True, "updated_at": now()}},
    )
    log_action("reset_password", "user", target["_id"], target.get("username", ""))
    flash(f"密码已重置，新密码：{password}", "success")
    return redirect(request.referrer or url_for("admin.users"))


@bp.route("/invites")
@permission_required("manage_invites")
def invites():
    q = request.args.get("q", "").strip()
    query = {}
    if q:
        rgx = _regex(q)
        query["$or"] = [{"code": rgx}, {"real_name": rgx}]
    docs, page, total, per_page = _paginate(mongo.db.invite_codes, query, sort=("created_at", -1))
    return render_template("admin/invites.html", invites=docs, page=page, total=total, per_page=per_page, q=q)


@bp.route("/invites/new", methods=["POST"])
@permission_required("manage_invites")
def invite_new():
    code = request.form.get("code", "").strip()
    real_name = request.form.get("real_name", "").strip()
    if not code or not real_name:
        flash("邀请码和真实姓名不能为空。", "danger")
        return redirect(url_for("admin.invites"))
    try:
        mongo.db.invite_codes.insert_one(
            {
                "code": code,
                "real_name": real_name,
                "used": False,
                "allow_reuse": request.form.get("allow_reuse") == "on",
                "used_by": None,
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


@bp.route("/invites/bulk-generate", methods=["POST"])
@permission_required("manage_invites")
def invites_bulk_generate():
    prefix = request.form.get("prefix", "MUXI").strip().upper() or "MUXI"
    prefix = re.sub(r"[^A-Z0-9_-]", "", prefix)[:12] or "MUXI"
    names = []
    for line in request.form.get("real_names", "").splitlines():
        name = line.strip()
        if name and name not in names:
            names.append(name)
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
                mongo.db.invite_codes.insert_one(
                    {
                        "code": code,
                        "real_name": real_name,
                        "used": False,
                        "allow_reuse": request.form.get("allow_reuse") == "on",
                        "used_by": None,
                        "created_at": now(),
                        "used_at": None,
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
    if not file or not file.filename:
        flash("请选择 CSV 文件。", "danger")
        return redirect(url_for("admin.invites"))
    success = 0
    failures = []
    text = io.TextIOWrapper(file.stream, encoding="utf-8-sig", newline="")
    reader = csv.DictReader(text)
    for index, row in enumerate(reader, start=2):
        code = (row.get("邀请码") or row.get("code") or "").strip()
        real_name = (row.get("真实姓名") or row.get("real_name") or "").strip()
        if not code or not real_name:
            failures.append(f"第 {index} 行缺少邀请码或姓名")
            continue
        used_value = (row.get("是否已使用") or "").strip().lower()
        used = used_value in {"是", "true", "1", "yes"}
        try:
            mongo.db.invite_codes.insert_one(
                {
                    "code": code,
                    "real_name": real_name,
                    "used": used,
                    "allow_reuse": False,
                    "used_by": None,
                    "created_at": now(),
                    "used_at": now() if used else None,
                }
            )
            success += 1
        except DuplicateKeyError:
            failures.append(f"第 {index} 行重复：{code} / {real_name}")
    log_action("import_invites", "invite_code", "", f"成功 {success}，失败 {len(failures)}")
    flash(f"导入完成：成功 {success} 条，失败 {len(failures)} 条。{'；'.join(failures[:5])}", "success" if success else "warning")
    return redirect(url_for("admin.invites"))


@bp.route("/invites/template")
@permission_required("manage_invites")
def invites_template():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["邀请码", "真实姓名", "是否已使用", "绑定用户ID", "创建时间", "使用时间"])
    writer.writerow(["MUXI2026A001", "张三", "否", "", "", ""])
    data = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    return send_file(data, as_attachment=True, download_name="muxi_invite_template.csv", mimetype="text/csv")


@bp.route("/invites/export")
@permission_required("manage_invites")
def invites_export():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["邀请码", "真实姓名", "是否已使用", "绑定用户ID", "创建时间", "使用时间"])
    for item in mongo.db.invite_codes.find({}).sort("created_at", -1):
        writer.writerow(
            [
                item.get("code", ""),
                item.get("real_name", ""),
                "是" if item.get("used") else "否",
                str(item.get("used_by") or ""),
                item.get("created_at", ""),
                item.get("used_at", ""),
            ]
        )
    data = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    return send_file(data, as_attachment=True, download_name="muxi_invites.csv", mimetype="text/csv")


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
    return redirect(request.referrer or url_for("admin.posts"))


@bp.route("/comments/<comment_id>/delete", methods=["POST"])
@permission_required("moderate_community")
def admin_delete_comment(comment_id):
    comment = mongo.db.comments.find_one({"_id": to_object_id(comment_id)})
    if not comment:
        abort(404)
    mongo.db.comments.update_one({"_id": comment["_id"]}, {"$set": {"status": "deleted", "updated_at": now()}})
    mongo.db.posts.update_one({"_id": comment["post_id"]}, {"$inc": {"comment_count": -1}})
    log_action("delete_comment", "comment", comment["_id"], "")
    flash("评论已删除。", "success")
    return redirect(request.referrer or url_for("admin.posts"))


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
                "admin_note": request.form.get("admin_note", "").strip(),
                "handled_by": g.user["_id"] if status != "pending" else None,
                "handled_at": now() if status != "pending" else None,
                "updated_at": now(),
            }
        },
    )
    log_action("handle_report", "report", report["_id"], status)
    flash("举报处理状态已更新。", "success")
    return redirect(request.referrer or url_for("admin.reports"))


def _activity_payload(existing=None):
    payload = {
        "title": request.form.get("title", "").strip(),
        "intro": request.form.get("intro", "").strip(),
        "requirements": request.form.get("requirements", "").strip(),
        "upload_instructions": request.form.get("upload_instructions", "").strip(),
        "start_time": request.form.get("start_time", "").strip(),
        "end_time": request.form.get("end_time", "").strip(),
        "status": request.form.get("status", "draft"),
        "allow_public_submissions_view": request.form.get("allow_public_submissions_view") == "on",
        "show_selected_works": request.form.get("show_selected_works") == "on",
        "updated_at": now(),
    }
    if payload["status"] not in {"draft", "active", "closed", "showcased"}:
        payload["status"] = "draft"
    cover = request.files.get("cover_image")
    if cover and cover.filename:
        payload["cover_image"] = save_upload(cover, "activities")
    samples = save_many(request.files.getlist("sample_images"), "activities")
    if samples:
        payload["sample_images"] = (existing or {}).get("sample_images", []) + samples
    return payload


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
            payload = _activity_payload()
        except UploadError as exc:
            flash(str(exc), "danger")
            return render_template("admin/activity_form.html", activity=None)
        if not payload["title"]:
            flash("活动标题不能为空。", "danger")
            return render_template("admin/activity_form.html", activity=None)
        payload.update({"cover_image": payload.get("cover_image", ""), "sample_images": payload.get("sample_images", [])})
        payload.update({"created_by": g.user["_id"], "created_at": now()})
        result = mongo.db.activities.insert_one(payload)
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
            payload = _activity_payload(activity)
        except UploadError as exc:
            flash(str(exc), "danger")
            return render_template("admin/activity_form.html", activity=activity)
        if not payload["title"]:
            flash("活动标题不能为空。", "danger")
            return render_template("admin/activity_form.html", activity=activity)
        mongo.db.activities.update_one({"_id": activity["_id"]}, {"$set": payload})
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
    mongo.db.activities.delete_one({"_id": activity["_id"]})
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
                "admin_note": request.form.get("admin_note", "").strip(),
                "reviewed_by": g.user["_id"],
                "reviewed_at": now(),
                "updated_at": now(),
            }
        },
    )
    log_action("review_submission", "submission", submission["_id"], status)
    flash("审核成功。", "success")
    return redirect(request.referrer or url_for("admin.submissions"))


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
                "admin_note": request.form.get("admin_note", "").strip(),
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
    query = {"activity_id": activity["_id"]}
    if mode == "selected":
        query["status"] = "selected"
    submissions = list(mongo.db.submissions.find(query))
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zip_file:
        for sub in submissions:
            for index, url in enumerate(sub.get("images", []), start=1):
                parts = url.strip("/").split("/")
                if len(parts) != 3 or parts[0] != "uploads":
                    continue
                path = safe_upload_path(parts[1], parts[2])
                if path:
                    zip_file.write(path, f"{sub['_id']}/{index}-{path.name}")
    buffer.seek(0)
    log_action("download_submissions", "activity", activity["_id"], mode)
    name = f"muxi_{activity['_id']}_{mode}.zip"
    return send_file(buffer, as_attachment=True, download_name=name, mimetype="application/zip")


@bp.route("/settings", methods=["GET", "POST"])
@permission_required("manage_settings")
def settings():
    settings_doc = mongo.db.site_settings.find_one({"key": "default"}) or {"key": "default"}
    if request.method == "POST":
        update = {
            "site_name": request.form.get("site_name", "").strip() or "泸州高中木樨映像",
            "club_intro": request.form.get("club_intro", "").strip(),
            "contact": request.form.get("contact", "").strip(),
            "footer": request.form.get("footer", "").strip(),
            "theme_color": request.form.get("theme_color", "").strip() or "#b8894f",
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
        try:
            for field, key in file_fields.items():
                file = request.files.get(field)
                if file and file.filename:
                    update[key] = save_upload(file, "site_assets")
        except UploadError as exc:
            flash(str(exc), "danger")
            return render_template("admin/settings.html", settings=settings_doc)
        mongo.db.site_settings.update_one({"key": "default"}, {"$set": update, "$setOnInsert": {"key": "default"}}, upsert=True)
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
