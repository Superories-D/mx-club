from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from app.decorators import active_required
from app.extensions import mongo
from app.utils.files import UploadError, delete_upload_url, save_many
from app.utils.rate_limit import consume_rate_limit
from app.utils.security import now, parse_int, to_object_id
from app.utils.validation import ValidationError, clean_text

bp = Blueprint("activities", __name__, url_prefix="/activities")


@bp.route("/")
def list_activities():
    page = parse_int(request.args.get("page"), default=1)
    per_page = 9
    status = request.args.get("status", "").strip()
    query = {"status": {"$ne": "draft"}}
    if status in {"active", "closed", "showcased"}:
        query["status"] = status
    total = mongo.db.activities.count_documents(query)
    activities = list(
        mongo.db.activities.find(query).sort("created_at", -1).skip((page - 1) * per_page).limit(per_page)
    )
    return render_template(
        "activities/list.html", activities=activities, page=page, per_page=per_page, total=total, status=status
    )


@bp.route("/<activity_id>", methods=["GET"])
def detail(activity_id):
    activity = mongo.db.activities.find_one({"_id": to_object_id(activity_id), "status": {"$ne": "draft"}})
    if not activity:
        abort(404)
    selected = list(
        mongo.db.submissions.find({"activity_id": activity["_id"], "status": "selected"}).sort("created_at", -1).limit(60)
    )
    return render_template("activities/detail.html", activity=activity, selected=selected)


@bp.route("/<activity_id>/submit", methods=["POST"])
@active_required
def submit(activity_id):
    if not consume_rate_limit("activities.submit", 30, 3600, str(g.user["_id"])):
        abort(429)
    activity = mongo.db.activities.find_one({"_id": to_object_id(activity_id), "status": {"$in": ["active", "showcased"]}})
    if not activity:
        flash("活动已关闭或不可投稿。", "danger")
        return redirect(url_for("activities.list_activities"))
    try:
        images = save_many(request.files.getlist("images"), "submissions", required=True)
    except UploadError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("activities.detail", activity_id=activity_id))
    try:
        description = clean_text(request.form.get("description"), "投稿说明", 5000)
        location = clean_text(request.form.get("location"), "拍摄地点", 120)
        shoot_time = clean_text(request.form.get("shoot_time"), "拍摄时间", 40)
        contact = clean_text(request.form.get("contact"), "联系方式", 120) or g.user.get("contact", "")
    except ValidationError as exc:
        for url in images:
            delete_upload_url(url)
        flash(str(exc), "danger")
        return redirect(url_for("activities.detail", activity_id=activity_id))

    try:
        mongo.db.submissions.insert_one(
            {
                "activity_id": activity["_id"],
                "user_id": g.user["_id"],
                "images": images,
                "description": description,
                "location": location,
                "shoot_time": shoot_time,
                "contact": contact,
                "status": "pending",
                "storage_status": "active",
                "storage_marked_at": None,
                "storage_reason": "",
                "deleted_files": [],
                "admin_note": "",
                "reviewed_by": None,
                "reviewed_at": None,
                "created_at": now(),
                "updated_at": now(),
            }
        )
    except Exception:
        for url in images:
            delete_upload_url(url)
        raise
    flash("投稿成功，等待管理员审核。", "success")
    return redirect(url_for("activities.detail", activity_id=activity_id))


@bp.route("/<activity_id>/showcase")
def activity_showcase(activity_id):
    activity = mongo.db.activities.find_one({"_id": to_object_id(activity_id), "status": {"$ne": "draft"}})
    if not activity:
        abort(404)
    submissions = list(
        mongo.db.submissions.find({"activity_id": activity["_id"], "status": "selected"}).sort("created_at", -1).limit(120)
    )
    return render_template("activities/activity_showcase.html", activity=activity, submissions=submissions)
