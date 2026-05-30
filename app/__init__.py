from pathlib import Path

from bson import ObjectId
from flask import Flask, g, redirect, render_template, request, session, url_for
from pymongo.errors import PyMongoError

from app.config import Config
from app.db_indexes import create_indexes
from app.extensions import mongo
from app.utils.files import ensure_upload_dirs
from app.utils.init_admin import initialize_admin
from app.utils.security import csrf_field, get_csrf_token, get_site_settings, mask_contact, validate_csrf


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.config["UPLOAD_ROOT"] = config_class.upload_root()
    Path(app.config["UPLOAD_ROOT"]).mkdir(parents=True, exist_ok=True)

    mongo.init_app(app)

    with app.app_context():
        ensure_upload_dirs()
        try:
            create_indexes(mongo.db, app.logger)
            _ensure_site_settings()
            initialize_admin(app)
        except PyMongoError as exc:
            app.logger.warning("MongoDB 初始化暂不可用：%s", exc)

    register_blueprints(app)
    register_hooks(app)
    register_template_helpers(app)
    register_error_handlers(app)
    return app


def _ensure_site_settings():
    if mongo.db.site_settings.find_one({"key": "default"}):
        return
    mongo.db.site_settings.insert_one(
        {
            "key": "default",
            "site_name": Config.SITE_NAME,
            "logo": "",
            "home_banner": "/static/images/generated/home-banner.png",
            "auth_background": "/static/images/generated/auth-background.png",
            "community_cover": "/static/images/generated/community-cover.png",
            "activity_cover": "/static/images/generated/activity-cover.png",
            "default_avatar": "/static/images/generated/default-avatar.png",
            "empty_illustration": "/static/images/generated/empty-state.png",
            "club_intro": "木樨映像是泸州高中摄影社团，用影像记录校园里的光影、桂香和少年心事。",
            "contact": "请联系社团管理员。",
            "footer": "© 泸州高中木樨映像 Muxi Photo",
            "theme_color": "#b8894f",
        }
    )


def register_blueprints(app):
    from app.routes.activities import bp as activities_bp
    from app.routes.admin import bp as admin_bp
    from app.routes.auth import bp as auth_bp
    from app.routes.community import bp as community_bp
    from app.routes.main import bp as main_bp
    from app.routes.profile import bp as profile_bp
    from app.routes.uploads import bp as uploads_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(community_bp)
    app.register_blueprint(activities_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(uploads_bp)


def register_hooks(app):
    @app.before_request
    def load_user_and_guard():
        if request.endpoint and request.endpoint.startswith("static"):
            return
        validate_csrf()
        g.user = None
        user_id = session.get("user_id")
        if user_id:
            try:
                g.user = mongo.db.users.find_one({"_id": ObjectId(user_id)})
            except Exception:
                session.clear()
                return
        if g.user and g.user.get("status") in ("banned", "deleted") and request.endpoint != "auth.logout":
            session.clear()
            return redirect(url_for("auth.login"))
        if (
            g.user
            and g.user.get("must_change_password")
            and request.endpoint not in {"admin.force_profile", "auth.logout", "uploads.uploaded_file"}
            and not (request.endpoint or "").startswith("static")
        ):
            return redirect(url_for("admin.force_profile"))


def register_template_helpers(app):
    @app.context_processor
    def inject_common():
        return {
            "current_user": getattr(g, "user", None),
            "site_settings": get_site_settings(),
            "csrf_field": csrf_field,
            "csrf_token": get_csrf_token,
        }

    @app.template_filter("datetime")
    def format_datetime(value):
        if not value:
            return ""
        return value.strftime("%Y-%m-%d %H:%M")

    @app.template_filter("date")
    def format_date(value):
        if not value:
            return ""
        return value.strftime("%Y-%m-%d")

    @app.template_filter("mask_contact")
    def filter_mask_contact(value):
        return mask_contact(value)


def register_error_handlers(app):
    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", code=400, message=getattr(error, "description", "请求无效。")), 400

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("error.html", code=403, message="你没有权限访问这个页面。"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message="页面或内容不存在。"), 404

    @app.errorhandler(413)
    def too_large(error):
        return render_template("error.html", code=413, message="上传文件过大。"), 413

    @app.errorhandler(500)
    def internal_error(error):
        return render_template("error.html", code=500, message="服务暂时开小差了，请稍后再试。"), 500
