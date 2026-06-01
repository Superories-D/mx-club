import os
import shutil
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image
from pymongo import MongoClient


ROOT = Path(__file__).resolve().parent.parent
DB_NAME = f"muxi_photo_security_{os.getpid()}"
UPLOAD_DIR = ROOT / "tmp" / "security_uploads"
MONGO_SERVER_URI = os.getenv("TEST_MONGO_URI", "mongodb://localhost:27017").rstrip("/")
sys.path.insert(0, str(ROOT))

os.environ["MONGO_URI"] = f"{MONGO_SERVER_URI}/{DB_NAME}?serverSelectionTimeoutMS=3000"
os.environ["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
os.environ["SITE_NAME"] = "木樨映像安全测试"
os.environ["TESTING"] = "true"

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.db_indexes import create_indexes  # noqa: E402
from app.extensions import mongo  # noqa: E402
from app.utils.permissions import PERMISSION_KEYS  # noqa: E402
from app.utils.security import hash_password, now  # noqa: E402


def image_file(name="test.png", image_format="PNG", size=(96, 72)):
    buffer = BytesIO()
    Image.new("RGB", size, color=(184, 137, 79)).save(buffer, format=image_format)
    buffer.seek(0)
    return buffer, name


def main():
    cleanup_client = MongoClient(MONGO_SERVER_URI, serverSelectionTimeoutMS=3000)
    try:
        cleanup_client.admin.command("ping")
    except Exception as exc:
        raise SystemExit(f"MongoDB 不可用，无法运行 security test：{exc}") from exc

    app = create_app()
    admin_client = app.test_client()
    user_client = app.test_client()
    anonymous_client = app.test_client()
    peer_super_client = app.test_client()
    results = []

    def check(name, condition):
        results.append((name, bool(condition)))
        print(("PASS" if condition else "FAIL"), name)

    def csrf(client):
        client.get("/")
        with client.session_transaction() as session:
            return session.get("_csrf_token")

    def login_as(client, user):
        with client.session_transaction() as session:
            session["user_id"] = str(user["_id"])
            session["session_version"] = user.get("session_version", 0)

    def user_doc(username, role="user", permissions=None):
        return {
            "real_name": username,
            "username": username,
            "password_hash": hash_password("secret123"),
            "contact": "13812345678",
            "avatar_url": "",
            "bio": "",
            "default_post_visibility": "public",
            "default_follow_delay_days": 0,
            "role": role,
            "permissions": permissions or [],
            "status": "active",
            "cohort_tag": "安全测试",
            "quality_photographer": False,
            "restricted_reason": "",
            "allow_peer_super_admin_management": False,
            "awarded_title_ids": [],
            "equipped_title_id": None,
            "session_version": 0,
            "must_change_password": False,
            "created_at": now(),
            "updated_at": now(),
            "last_login_at": None,
        }

    try:
        with app.app_context():
            admin = mongo.db.users.find_one({"role": "super_admin"})
            mongo.db.users.update_one({"_id": admin["_id"]}, {"$set": {"must_change_password": False}})
            admin = mongo.db.users.find_one({"_id": admin["_id"]})
            user_id = mongo.db.users.insert_one(user_doc("security_user")).inserted_id
            user = mongo.db.users.find_one({"_id": user_id})
            limited_id = mongo.db.users.insert_one(user_doc("limited_manager", "admin", ["manage_users"])).inserted_id
            limited = mongo.db.users.find_one({"_id": limited_id})
            peer_id = mongo.db.users.insert_one(user_doc("peer_manager", "admin", ["manage_settings"])).inserted_id
            peer = mongo.db.users.find_one({"_id": peer_id})
            peer_super_id = mongo.db.users.insert_one(
                {**user_doc("peer_super_admin", "super_admin", PERMISSION_KEYS), "allow_peer_super_admin_management": False}
            ).inserted_id
            peer_super_admin = mongo.db.users.find_one({"_id": peer_super_id})

        login_as(admin_client, admin)
        login_as(user_client, user)
        login_as(peer_super_client, peer_super_admin)

        response = anonymous_client.get("/")
        check("security headers include CSP", "default-src 'self'" in response.headers.get("Content-Security-Policy", ""))
        check("security headers deny sniffing", response.headers.get("X-Content-Type-Options") == "nosniff")
        response = anonymous_client.get("/", base_url="https://localhost")
        check("HTTPS response includes HSTS", response.headers.get("Strict-Transport-Security", "").startswith("max-age="))

        class BadProductionConfig(Config):
            SECRET_KEY = "change-me-in-production"
            DEBUG = False
            TESTING = False

        try:
            create_app(BadProductionConfig)
            rejected_default_secret = False
        except RuntimeError:
            rejected_default_secret = True
        check("production rejects default SECRET_KEY", rejected_default_secret)

        class WeakProductionConfig(Config):
            SECRET_KEY = "too-short"
            DEBUG = False
            TESTING = False

        try:
            create_app(WeakProductionConfig)
            rejected_weak_secret = False
        except RuntimeError:
            rejected_weak_secret = True
        check("production rejects short SECRET_KEY", rejected_weak_secret)

        duplicate_db_name = f"{DB_NAME}_duplicate_invites"
        cleanup_client[duplicate_db_name].invite_codes.insert_many(
            [
                {"code": "DUPLICATE001", "real_name": "甲"},
                {"code": "DUPLICATE001", "real_name": "乙"},
            ]
        )

        try:
            create_indexes(cleanup_client[duplicate_db_name])
            rejected_duplicate_invites = False
        except RuntimeError:
            rejected_duplicate_invites = True
        finally:
            cleanup_client.drop_database(duplicate_db_name)
        check("startup rejects duplicate invite codes", rejected_duplicate_invites)

        response = anonymous_client.post("/register", data={})
        check("POST without CSRF is rejected", response.status_code == 400)
        check("invalid ObjectId returns 404", anonymous_client.get("/community/not-an-object-id").status_code == 404)
        check("logout cannot be triggered by GET", user_client.get("/logout").status_code == 405)
        check("path traversal upload URL is rejected", anonymous_client.get("/uploads/posts/%2e%2e%2fsecret.txt").status_code == 404)
        check("unknown upload category is rejected", anonymous_client.get("/uploads/private/file.png").status_code == 404)

        login_client = app.test_client()
        token = csrf(login_client)
        response = login_client.post(
            "/login?next=//evil.example/steal",
            data={"_csrf_token": token, "username": "security_user", "password": "secret123"},
            follow_redirects=False,
        )
        check("scheme-relative login redirect is blocked", response.status_code == 302 and "evil.example" not in response.headers["Location"])

        encoded_redirect_client = app.test_client()
        response = encoded_redirect_client.post(
            "/login?next=/%252F%252Fevil.example/steal",
            data={"_csrf_token": csrf(encoded_redirect_client), "username": "security_user", "password": "secret123"},
            follow_redirects=False,
        )
        check("encoded login redirect bypass is blocked", response.status_code == 302 and "evil.example" not in response.headers["Location"])

        with app.app_context():
            post_path = UPLOAD_DIR / "posts" / "private-post.png"
            post_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (20, 20), color=(1, 2, 3)).save(post_path)
            post_url = "/uploads/posts/private-post.png"
            post_id = mongo.db.posts.insert_one(
                {
                    "author_id": user["_id"],
                    "title": "隐藏帖",
                    "description": "",
                    "images": [post_url],
                    "status": "hidden",
                    "created_at": now(),
                    "updated_at": now(),
                }
            ).inserted_id
            submission_path = UPLOAD_DIR / "submissions" / "private-submission.png"
            submission_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (20, 20), color=(4, 5, 6)).save(submission_path)
            submission_url = "/uploads/submissions/private-submission.png"
            submission_id = mongo.db.submissions.insert_one(
                {
                    "activity_id": None,
                    "user_id": user["_id"],
                    "images": [submission_url],
                    "status": "pending",
                    "created_at": now(),
                    "updated_at": now(),
                }
            ).inserted_id
        check("anonymous cannot fetch hidden post image", anonymous_client.get(post_url).status_code == 404)
        check("post owner can fetch hidden post image", user_client.get(post_url).status_code == 200)
        check("anonymous cannot fetch pending submission", anonymous_client.get(submission_url).status_code == 404)
        check("submission owner can fetch pending submission", user_client.get(submission_url).status_code == 200)
        check("reviewer can fetch pending submission", admin_client.get(submission_url).status_code == 200)
        with app.app_context():
            mongo.db.posts.update_one({"_id": post_id}, {"$set": {"status": "normal"}})
            mongo.db.submissions.update_one({"_id": submission_id}, {"$set": {"status": "selected"}})
        check("public can fetch normal post image", anonymous_client.get(post_url).status_code == 200)
        check("public can fetch selected submission", anonymous_client.get(submission_url).status_code == 200)

        csrf_token = csrf(user_client)
        before_files = len(list((UPLOAD_DIR / "posts").glob("*")))
        response = user_client.post(
            "/community/new",
            data={
                "_csrf_token": csrf_token,
                "title": "伪装图片",
                "images": [(BytesIO(b"not an image"), "fake.png")],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        after_files = len(list((UPLOAD_DIR / "posts").glob("*")))
        check("fake image upload is rejected and cleaned", response.status_code == 200 and before_files == after_files)

        response = user_client.post(
            "/community/new",
            data={
                "_csrf_token": csrf(user_client),
                "title": "扩展名伪装",
                "images": [image_file("wrong.jpg", image_format="PNG")],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        with app.app_context():
            mismatch_post = mongo.db.posts.find_one({"title": "扩展名伪装"})
        check("image extension mismatch is rejected", response.status_code == 200 and mismatch_post is None)

        response = user_client.post(
            "/community/new",
            data={
                "_csrf_token": csrf(user_client),
                "title": "过多图片",
                "images": [image_file(f"{index}.png") for index in range(13)],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        with app.app_context():
            too_many_post = mongo.db.posts.find_one({"title": "过多图片"})
        check("too many images are rejected", response.status_code == 200 and too_many_post is None)

        original_pixels = app.config["MAX_IMAGE_PIXELS"]
        app.config["MAX_IMAGE_PIXELS"] = 10
        response = user_client.post(
            "/community/new",
            data={
                "_csrf_token": csrf(user_client),
                "title": "像素过高",
                "images": [image_file("pixels.png")],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        app.config["MAX_IMAGE_PIXELS"] = original_pixels
        with app.app_context():
            pixel_post = mongo.db.posts.find_one({"title": "像素过高"})
        check("high pixel image is rejected", response.status_code == 200 and pixel_post is None)

        before_activity_files = len(list((UPLOAD_DIR / "activities").glob("*")))
        response = admin_client.post(
            "/admin/activities/new",
            data={
                "_csrf_token": csrf(admin_client),
                "title": "活动素材回滚",
                "cover_image": image_file("cover.png"),
                "sample_images": [(BytesIO(b"not an image"), "fake.png")],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        after_activity_files = len(list((UPLOAD_DIR / "activities").glob("*")))
        check("failed activity multi-upload cleans staged files", response.status_code == 200 and before_activity_files == after_activity_files)

        before_site_files = len(list((UPLOAD_DIR / "site_assets").glob("*")))
        response = admin_client.post(
            "/admin/settings",
            data={
                "_csrf_token": csrf(admin_client),
                "site_name": "木樨映像",
                "logo": image_file("logo.png"),
                "home_banner": (BytesIO(b"not an image"), "fake.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        after_site_files = len(list((UPLOAD_DIR / "site_assets").glob("*")))
        check("failed site asset multi-upload cleans staged files", response.status_code == 200 and before_site_files == after_site_files)

        response = user_client.post(
            "/profile/settings",
            data={
                "_csrf_token": csrf(user_client),
                "action": "profile",
                "username": "security_user",
                "bio": "",
                "contact": "",
                "avatar": image_file("first-avatar.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        with app.app_context():
            first_avatar = mongo.db.users.find_one({"_id": user["_id"]}).get("avatar_url")
        response = user_client.post(
            "/profile/settings",
            data={
                "_csrf_token": csrf(user_client),
                "action": "profile",
                "username": "security_user",
                "bio": "",
                "contact": "",
                "avatar": image_file("second-avatar.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        with app.app_context():
            second_avatar = mongo.db.users.find_one({"_id": user["_id"]}).get("avatar_url")
        first_avatar_path = UPLOAD_DIR / first_avatar.removeprefix("/uploads/")
        second_avatar_path = UPLOAD_DIR / second_avatar.removeprefix("/uploads/")
        check(
            "replacing avatar deletes old file",
            response.status_code == 200
            and first_avatar != second_avatar
            and not first_avatar_path.exists()
            and second_avatar_path.exists(),
        )

        post_rate_statuses = []
        for _ in range(21):
            post_rate_statuses.append(
                user_client.post(
                    "/community/new",
                    data={
                        "_csrf_token": csrf(user_client),
                        "title": "限流测试",
                        "images": [(BytesIO(b"not an image"), "fake.png")],
                    },
                    content_type="multipart/form-data",
                    environ_base={"REMOTE_ADDR": "203.0.113.51"},
                ).status_code
            )
        check("post creation abuse is rate limited", post_rate_statuses[0] == 200 and post_rate_statuses[-1] == 429)

        with app.app_context():
            mongo.db.invite_codes.insert_one(
                {
                    "code": "RELEASE001",
                    "real_name": "重复用户",
                    "used": False,
                    "allow_reuse": False,
                    "used_by": None,
                    "created_at": now(),
                }
            )
        register_client = app.test_client()
        response = register_client.post(
            "/register",
            data={
                "_csrf_token": csrf(register_client),
                "invite_code": "RELEASE001",
                "real_name": "重复用户",
                "username": "security_user",
                "contact": "13812345678",
                "password": "secret123",
            },
            follow_redirects=True,
        )
        with app.app_context():
            released_invite = mongo.db.invite_codes.find_one({"code": "RELEASE001"})
        check("failed registration releases single-use invite claim", response.status_code == 200 and released_invite.get("used") is False)

        login_as(user_client, user)
        response = admin_client.post(
            f"/admin/users/{user['_id']}/reset-password",
            data={"_csrf_token": csrf(admin_client)},
            follow_redirects=True,
        )
        check("admin password reset succeeds", response.status_code == 200)
        response = user_client.get("/profile/settings", follow_redirects=False)
        check("password reset invalidates existing sessions", response.status_code == 302 and "/login" in response.headers["Location"])

        limited_client = app.test_client()
        login_as(limited_client, limited)
        response = limited_client.post(
            f"/admin/users/{peer['_id']}/reset-password",
            data={"_csrf_token": csrf(limited_client)},
            follow_redirects=False,
        )
        check("ordinary admin cannot reset peer admin password", response.status_code == 403)

        response = admin_client.post(
            f"/admin/users/{peer_super_admin['_id']}/reset-password",
            data={"_csrf_token": csrf(admin_client)},
            follow_redirects=False,
        )
        check("super_admin cannot reset peer super_admin before opt-in", response.status_code == 403)

        response = admin_client.post(
            f"/admin/users/{peer_super_admin['_id']}/action",
            data={"_csrf_token": csrf(admin_client), "action": "restrict"},
            follow_redirects=False,
        )
        check("super_admin cannot restrict peer super_admin before opt-in", response.status_code == 403)

        response = peer_super_client.post(
            "/profile/settings",
            data={
                "_csrf_token": csrf(peer_super_client),
                "action": "profile",
                "username": "peer_super_admin",
                "bio": "",
                "contact": "13812345678",
                "default_post_visibility": "public",
                "default_follow_delay_days": "0",
                "allow_peer_super_admin_management": "on",
            },
            follow_redirects=True,
        )
        with app.app_context():
            peer_super_admin = mongo.db.users.find_one({"_id": peer_super_admin["_id"]})
        check(
            "peer super_admin can opt in to same-level management",
            response.status_code == 200 and peer_super_admin.get("allow_peer_super_admin_management") is True,
        )

        response = admin_client.post(
            f"/admin/users/{peer_super_admin['_id']}/action",
            data={"_csrf_token": csrf(admin_client), "action": "restrict", "restricted_reason": "peer-test"},
            follow_redirects=True,
        )
        with app.app_context():
            peer_super_admin = mongo.db.users.find_one({"_id": peer_super_admin["_id"]})
        check(
            "super_admin can manage opted-in peer super_admin",
            response.status_code == 200 and peer_super_admin.get("status") == "restricted",
        )

        response = limited_client.post(
            f"/admin/users/{peer_super_admin['_id']}/reset-password",
            data={"_csrf_token": csrf(limited_client)},
            follow_redirects=False,
        )
        check("ordinary admin still cannot reset opted-in super_admin password", response.status_code == 403)

        with app.app_context():
            title_id = mongo.db.member_titles.insert_one(
                {
                    "name": "Unauthorized Title",
                    "description": "",
                    "is_active": True,
                    "sort_order": 1,
                    "created_by": admin["_id"],
                    "created_at": now(),
                    "updated_at": now(),
                }
            ).inserted_id
            mongo.db.users.update_one({"_id": user["_id"]}, {"$set": {"must_change_password": False}})
            user = mongo.db.users.find_one({"_id": user["_id"]})
        login_as(user_client, user)
        response = user_client.post(
            "/profile/settings",
            data={
                "_csrf_token": csrf(user_client),
                "action": "profile",
                "username": "security_user",
                "bio": "",
                "contact": "13812345678",
                "default_post_visibility": "public",
                "default_follow_delay_days": "0",
                "equipped_title_id": str(title_id),
            },
            follow_redirects=True,
        )
        with app.app_context():
            user = mongo.db.users.find_one({"_id": user["_id"]})
        check(
            "user cannot equip unawarded title",
            response.status_code == 200 and user.get("equipped_title_id") is None,
        )

        with app.app_context():
            mongo.db.activities.insert_one(
                {
                    "title": "转义测试",
                    "intro": "",
                    "requirements": "<script>alert(1)</script>",
                    "upload_instructions": "<img src=x onerror=alert(1)>",
                    "status": "active",
                    "created_at": now(),
                }
            )
            activity = mongo.db.activities.find_one({"title": "转义测试"})
        response = anonymous_client.get(f"/activities/{activity['_id']}")
        check("activity HTML is escaped", b"<script>alert(1)</script>" not in response.data and b"&lt;script&gt;" in response.data)

        with app.app_context():
            mongo.db.submissions.insert_one(
                {
                    "activity_id": activity["_id"],
                    "user_id": user["_id"],
                    "images": [submission_url],
                    "status": "selected",
                    "created_at": now(),
                    "updated_at": now(),
                }
            )
        original_zip_limit = app.config["MAX_ZIP_DOWNLOAD_MB"]
        app.config["MAX_ZIP_DOWNLOAD_MB"] = 0
        response = admin_client.get(f"/admin/activities/{activity['_id']}/download?mode=selected")
        app.config["MAX_ZIP_DOWNLOAD_MB"] = original_zip_limit
        check("oversized zip download is rejected", response.status_code == 413)

        response = admin_client.post(
            f"/admin/activities/{activity['_id']}/delete",
            data={"_csrf_token": csrf(admin_client)},
            follow_redirects=True,
        )
        with app.app_context():
            retained_activity = mongo.db.activities.find_one({"_id": activity["_id"]})
        check("activity with submissions cannot be deleted", response.status_code == 200 and retained_activity is not None)

        response = admin_client.post(
            "/admin/settings",
            data={"_csrf_token": csrf(admin_client), "theme_color": "red; background:url(https://evil.example/x)"},
            follow_redirects=True,
        )
        with app.app_context():
            settings = mongo.db.site_settings.find_one({"key": "default"})
        check("theme color CSS injection is rejected", response.status_code == 200 and settings.get("theme_color") == "#b8894f")

        with app.app_context():
            mongo.db.invite_codes.insert_one(
                {
                    "code": "FORMULA001",
                    "real_name": " =HYPERLINK(\"https://evil.example\")",
                    "cohort_tag": "@SUM(1+1)",
                    "used": False,
                    "created_at": now(),
                }
            )
        response = admin_client.get("/admin/invites/export")
        csv_text = response.data.decode("utf-8-sig")
        check("CSV export neutralizes spreadsheet formulas", "' =HYPERLINK" in csv_text and "'@SUM" in csv_text)

        response = admin_client.post(
            "/admin/invites/import",
            data={
                "_csrf_token": csrf(admin_client),
                "csv_file": (BytesIO("邀请码,真实姓名\nBAD\u0000CODE,测试用户\n".encode("utf-8")), "control.csv"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        with app.app_context():
            control_invite = mongo.db.invite_codes.find_one({"code": "BAD\u0000CODE"})
        check("CSV import rejects control characters", response.status_code == 200 and control_invite is None)

        response = admin_client.post(
            "/admin/invites/import",
            data={
                "_csrf_token": csrf(admin_client),
                "csv_file": (BytesIO(b"x" * (2 * 1024 * 1024 + 1)), "too-large.csv"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        check("oversized CSV import is rejected", response.status_code == 200 and "CSV 文件不能超过 2MB".encode("utf-8") in response.data)

        rate_client = app.test_client()
        statuses = []
        for _ in range(21):
            statuses.append(
                rate_client.post(
                    "/login",
                    data={"_csrf_token": csrf(rate_client), "username": "nobody", "password": "bad-password"},
                    environ_base={"REMOTE_ADDR": "203.0.113.50"},
                ).status_code
            )
        check("login brute force is rate limited", statuses[-1] == 429)

        with app.app_context():
            spoof_target_id = mongo.db.users.insert_one(user_doc("spoof_target")).inserted_id
        admin_client.post(
            f"/admin/users/{spoof_target_id}/action",
            data={"_csrf_token": csrf(admin_client), "action": "restrict"},
            headers={"X-Forwarded-For": "198.51.100.77"},
        )
        with app.app_context():
            audit = mongo.db.audit_logs.find_one({"target_id": str(spoof_target_id)}, sort=[("created_at", -1)])
        check("audit log ignores untrusted forwarded IP header", audit and audit.get("ip") != "198.51.100.77")

        ok = all(value for _, value in results)
        print("SUMMARY", "PASS" if ok else "FAIL", f"{sum(value for _, value in results)}/{len(results)}")
        raise SystemExit(0 if ok else 1)
    finally:
        cleanup_client.drop_database(DB_NAME)
        shutil.rmtree(UPLOAD_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
