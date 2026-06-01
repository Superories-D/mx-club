import os
import csv
import shutil
import sys
from datetime import timedelta
from io import BytesIO, StringIO
from pathlib import Path

from PIL import Image
from pymongo import MongoClient


ROOT = Path(__file__).resolve().parent.parent
DB_NAME = f"muxi_photo_smoke_{os.getpid()}"
UPLOAD_DIR = ROOT / "tmp" / "smoke_uploads"
MONGO_SERVER_URI = os.getenv("TEST_MONGO_URI", "mongodb://localhost:27017").rstrip("/")
sys.path.insert(0, str(ROOT))

os.environ["MONGO_URI"] = f"{MONGO_SERVER_URI}/{DB_NAME}?serverSelectionTimeoutMS=3000"
os.environ["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
os.environ["SITE_NAME"] = "泸州高中木樨映像"
os.environ["TESTING"] = "true"

from app import create_app  # noqa: E402
from app.extensions import mongo  # noqa: E402
from app.utils.security import hash_password, now  # noqa: E402


def image_file(name="test.png"):
    buffer = BytesIO()
    Image.new("RGB", (96, 72), color=(184, 137, 79)).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer, name


def main():
    client_for_cleanup = MongoClient(MONGO_SERVER_URI, serverSelectionTimeoutMS=3000)
    try:
        client_for_cleanup.admin.command("ping")
    except Exception as exc:
        raise SystemExit(f"MongoDB 不可用，无法运行 smoke test：{exc}") from exc

    app = create_app()
    client = app.test_client()
    results = []

    def csrf_token():
        with client.session_transaction() as sess:
            return sess.get("_csrf_token")

    def logout():
        client.get("/")
        return client.post("/logout", data={"_csrf_token": csrf_token()}, follow_redirects=True)

    def login_as_session(user):
        with client.session_transaction() as sess:
            sess["user_id"] = str(user["_id"])
            sess["session_version"] = user.get("session_version", 0)

    def check(name, condition):
        results.append((name, bool(condition)))
        print(("PASS" if condition else "FAIL"), name)

    try:
        for path in ["/", "/login", "/register", "/community/", "/activities/", "/showcase", "/healthz", "/readyz"]:
            response = client.get(path)
            check(f"GET {path}", response.status_code == 200)

        with app.app_context():
            admin = mongo.db.users.find_one({"role": "super_admin"})
            check("auto super_admin exists", admin is not None)
            check("super_admin must change password", admin and admin.get("must_change_password") is True)
            mongo.db.users.update_one({"_id": admin["_id"]}, {"$set": {"must_change_password": False}})

        login_as_session(admin)
        for path in [
            "/admin/",
            "/admin/users",
            "/admin/member-titles",
            "/admin/invites",
            "/admin/posts",
            "/admin/comments",
            "/admin/reports",
            "/admin/activities",
            "/admin/submissions",
            "/admin/storage",
            "/admin/settings",
            "/admin/audit-logs",
        ]:
            response = client.get(path)
            check(f"ADMIN {path}", response.status_code == 200)

        response = client.post(
            "/admin/users/create-admin",
            data={
                "_csrf_token": csrf_token(),
                "username": "ops_admin",
                "real_name": "运营管理员",
                "contact": "ops@example.com",
                "role": "admin",
                "permissions": ["manage_users", "review_submissions"],
            },
            follow_redirects=True,
        )
        with app.app_context():
            ops_admin = mongo.db.users.find_one({"username": "ops_admin"})
        check("create ordinary admin", response.status_code == 200 and ops_admin and ops_admin.get("role") == "admin")

        response = client.post(
            "/admin/users/create-admin",
            data={
                "_csrf_token": csrf_token(),
                "username": "peer_root",
                "real_name": "同级管理员",
                "contact": "peer@example.com",
                "role": "super_admin",
            },
            follow_redirects=True,
        )
        with app.app_context():
            peer_root = mongo.db.users.find_one({"username": "peer_root"})
            mongo.db.users.update_one({"_id": peer_root["_id"]}, {"$set": {"must_change_password": False}})
            peer_root = mongo.db.users.find_one({"_id": peer_root["_id"]})
        check(
            "create peer super admin",
            response.status_code == 200
            and peer_root
            and peer_root.get("role") == "super_admin"
            and peer_root.get("allow_peer_super_admin_management") is False,
        )

        response = client.post(
            "/admin/member-titles",
            data={"_csrf_token": csrf_token(), "name": "校园光影记录者", "description": "冒烟测试头衔", "sort_order": "10"},
            follow_redirects=True,
        )
        with app.app_context():
            smoke_title = mongo.db.member_titles.find_one({"name": "校园光影记录者"})
        check("create member title", response.status_code == 200 and smoke_title is not None)

        client.get("/admin/invites")
        response = client.post(
            "/admin/invites/new",
            data={"_csrf_token": csrf_token(), "code": "SMOKE001", "real_name": "调试同学", "cohort_tag": "2026届"},
            follow_redirects=True,
        )
        check("create invite", response.status_code == 200)
        response = client.post(
            "/admin/invites/generate-sheet",
            data={"_csrf_token": csrf_token(), "prefix": "SHEET", "count": "2", "cohort_tag": "2026届"},
            follow_redirects=True,
        )
        sheet_text = response.data.decode("utf-8-sig")
        sheet_rows = list(csv.DictReader(StringIO(sheet_text)))
        with app.app_context():
            pending_sheet_count = mongo.db.invite_codes.count_documents({"code": {"$regex": "^SHEET"}, "real_name": ""})
        check(
            "generate blank invite sheet",
            response.status_code == 200
            and len(sheet_rows) == 2
            and pending_sheet_count == 2
            and sheet_rows[0].get("用户标签/届别") == "2026届",
        )
        filled_sheet = StringIO()
        writer = csv.DictWriter(filled_sheet, fieldnames=sheet_rows[0].keys())
        writer.writeheader()
        for idx, row in enumerate(sheet_rows, start=1):
            row["真实姓名（填写）"] = f"填表同学{idx}"
            writer.writerow(row)
        response = client.post(
            "/admin/invites/bind-sheet",
            data={"_csrf_token": csrf_token(), "sheet_file": (BytesIO(filled_sheet.getvalue().encode("utf-8-sig")), "sheet.csv")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        with app.app_context():
            bound_sheet_count = mongo.db.invite_codes.count_documents({"code": {"$regex": "^SHEET"}, "sheet_status": "bound"})
        check("bind filled invite sheet", response.status_code == 200 and bound_sheet_count == 2)
        response = client.post(
            "/admin/invites/bulk-generate",
            data={"_csrf_token": csrf_token(), "prefix": "BULK", "cohort_tag": "2026届", "real_names": "批量同学一\n批量同学二"},
            follow_redirects=True,
        )
        with app.app_context():
            bulk_count = mongo.db.invite_codes.count_documents({"code": {"$regex": "^BULK"}})
        check("direct bulk generate invites", response.status_code == 200 and bulk_count == 2)

        logout()
        client.get("/register")
        response = client.post(
            "/register",
            data={
                "_csrf_token": csrf_token(),
                "invite_code": "SMOKE001",
                "real_name": "调试同学",
                "username": "smoke_user",
                "contact": "13812345678",
                "password": "secret123",
            },
            follow_redirects=True,
        )
        with app.app_context():
            smoke_user = mongo.db.users.find_one({"username": "smoke_user"})
        check("register invited user", response.status_code == 200 and smoke_user is not None)
        check("invite cohort copied to user", smoke_user and smoke_user.get("cohort_tag") == "2026届")
        with app.app_context():
            cohort_peer_id = mongo.db.users.insert_one(
                {
                    "real_name": "批量头衔成员",
                    "username": "title_batch_user",
                    "password_hash": hash_password("secret123"),
                    "contact": "",
                    "avatar_url": "",
                    "bio": "",
                    "default_post_visibility": "public",
                    "default_follow_delay_days": 0,
                    "role": "user",
                    "permissions": [],
                    "status": "active",
                    "cohort_tag": "2026届",
                    "quality_photographer": False,
                    "restricted_reason": "",
                    "allow_peer_super_admin_management": False,
                    "awarded_title_ids": [],
                    "equipped_title_id": None,
                    "must_change_password": False,
                    "session_version": 0,
                    "created_at": now(),
                    "updated_at": now(),
                    "last_login_at": None,
                }
            ).inserted_id
            cohort_peer = mongo.db.users.find_one({"_id": cohort_peer_id})

        client.get("/login")
        response = client.post(
            "/login",
            data={"_csrf_token": csrf_token(), "username": "smoke_user", "password": "secret123"},
            follow_redirects=True,
        )
        check("login user", response.status_code == 200)

        client.get("/community/new")
        response = client.post(
            "/community/new",
            data={
                "_csrf_token": csrf_token(),
                "title": "调试光影",
                "description": "完整链路测试",
                "tags": "测试,光影",
                "images": [image_file("post.png")],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        check("create post", response.status_code == 200)
        with app.app_context():
            post = mongo.db.posts.find_one({"title": "调试光影"})
        check("post persisted", post is not None)
        client.get(f"/community/{post['_id']}")
        response = client.post(f"/community/{post['_id']}/like", data={"_csrf_token": csrf_token()}, follow_redirects=True)
        check("toggle like", response.status_code == 200)
        response = client.post(
            f"/community/{post['_id']}/comments",
            data={"_csrf_token": csrf_token(), "content": "很好看"},
            follow_redirects=True,
        )
        check("comment post", response.status_code == 200)
        with app.app_context():
            comment = mongo.db.comments.find_one({"post_id": post["_id"]})
        response = client.post(
            f"/community/{post['_id']}/report",
            data={"_csrf_token": csrf_token(), "reason": "测试举报", "detail": "帖子举报测试"},
            follow_redirects=True,
        )
        check("report post", response.status_code == 200)
        response = client.post(
            f"/community/comments/{comment['_id']}/report",
            data={"_csrf_token": csrf_token(), "reason": "测试举报", "detail": "评论举报测试"},
            follow_redirects=True,
        )
        with app.app_context():
            report_count = mongo.db.reports.count_documents({"status": "pending"})
        check("report comment", response.status_code == 200 and report_count == 2)

        with client.session_transaction() as sess:
            sess["user_id"] = str(admin["_id"])
        client.get("/admin/activities/new")
        response = client.post(
            "/admin/activities/new",
            data={
                "_csrf_token": csrf_token(),
                "title": "调试征集",
                "intro": "调试活动",
                "requirements": "上传照片",
                "upload_instructions": "多图上传",
                "status": "active",
                "cover_image": image_file("cover.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        check("create activity", response.status_code == 200)
        with app.app_context():
            activity = mongo.db.activities.find_one({"title": "调试征集"})
        check("activity persisted", activity is not None)

        logout()
        client.get("/login")
        client.post(
            "/login",
            data={"_csrf_token": csrf_token(), "username": "smoke_user", "password": "secret123"},
            follow_redirects=True,
        )
        client.get(f"/activities/{activity['_id']}")
        response = client.post(
            f"/activities/{activity['_id']}/submit",
            data={
                "_csrf_token": csrf_token(),
                "description": "投稿测试",
                "contact": "13812345678",
                "images": [image_file("submit.png")],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        check("submit activity work", response.status_code == 200)
        with app.app_context():
            submission = mongo.db.submissions.find_one({"activity_id": activity["_id"]})
        check("submission persisted", submission is not None)

        with client.session_transaction() as sess:
            sess["user_id"] = str(admin["_id"])
        client.get("/admin/submissions")
        response = client.post(
            f"/admin/submissions/{submission['_id']}/review",
            data={"_csrf_token": csrf_token(), "status": "selected", "admin_note": "通过"},
            follow_redirects=True,
        )
        check("review submission", response.status_code == 200)
        response = client.get(f"/admin/activities/{activity['_id']}/download?mode=selected")
        check("download selected zip", response.status_code == 200 and response.mimetype == "application/zip")
        response = client.get(f"/activities/{activity['_id']}")
        check(
            "activity detail shows admin download shortcuts",
            response.status_code == 200
            and f"/admin/activities/{activity['_id']}/download?mode=all".encode("utf-8") in response.data
            and f"/admin/activities/{activity['_id']}/download?mode=selected".encode("utf-8") in response.data,
        )
        logout()
        anonymous_activity = client.get(f"/activities/{activity['_id']}")
        check(
            "activity detail hides admin download shortcuts from anonymous",
            anonymous_activity.status_code == 200
            and f"/admin/activities/{activity['_id']}/download?mode=all".encode("utf-8") not in anonymous_activity.data,
        )
        login_as_session(admin)

        response = client.post(
            f"/admin/users/{smoke_user['_id']}/award-title",
            data={"_csrf_token": csrf_token(), "title_id": str(smoke_title["_id"])},
            follow_redirects=True,
        )
        with app.app_context():
            smoke_user = mongo.db.users.find_one({"_id": smoke_user["_id"]})
        check(
            "award title to single user",
            response.status_code == 200 and smoke_title["_id"] in smoke_user.get("awarded_title_ids", []),
        )

        response = client.post(
            "/admin/users/batch-award-title",
            data={"_csrf_token": csrf_token(), "cohort_tag": "2026届", "title_id": str(smoke_title["_id"])},
            follow_redirects=True,
        )
        with app.app_context():
            cohort_peer = mongo.db.users.find_one({"_id": cohort_peer["_id"]})
        check(
            "batch award title by cohort",
            response.status_code == 200 and smoke_title["_id"] in cohort_peer.get("awarded_title_ids", []),
        )

        response = client.post(
            f"/admin/users/{ops_admin['_id']}/action",
            data={"_csrf_token": csrf_token(), "action": "role", "role": "super_admin"},
            follow_redirects=True,
        )
        with app.app_context():
            ops_admin = mongo.db.users.find_one({"_id": ops_admin["_id"]})
        check("promote admin to super_admin", response.status_code == 200 and ops_admin.get("role") == "super_admin")

        response = client.post(
            f"/admin/users/{peer_root['_id']}/action",
            data={"_csrf_token": csrf_token(), "action": "restrict"},
            follow_redirects=False,
        )
        check("peer super_admin is protected by default", response.status_code == 403)

        login_as_session(peer_root)
        client.get("/profile/settings")
        response = client.post(
            "/profile/settings",
            data={
                "_csrf_token": csrf_token(),
                "action": "profile",
                "username": "peer_root",
                "bio": "",
                "contact": "peer@example.com",
                "default_post_visibility": "public",
                "default_follow_delay_days": "0",
                "allow_peer_super_admin_management": "on",
            },
            follow_redirects=True,
        )
        with app.app_context():
            peer_root = mongo.db.users.find_one({"_id": peer_root["_id"]})
        check(
            "peer super_admin can enable same-level management",
            response.status_code == 200 and peer_root.get("allow_peer_super_admin_management") is True,
        )

        login_as_session(admin)
        response = client.post(
            f"/admin/users/{peer_root['_id']}/action",
            data={"_csrf_token": csrf_token(), "action": "restrict", "restricted_reason": "peer-test"},
            follow_redirects=True,
        )
        with app.app_context():
            peer_root = mongo.db.users.find_one({"_id": peer_root["_id"]})
        check("peer super_admin becomes manageable after opt-in", response.status_code == 200 and peer_root.get("status") == "restricted")

        logout()
        client.get("/login")
        client.post(
            "/login",
            data={"_csrf_token": csrf_token(), "username": "smoke_user", "password": "secret123"},
            follow_redirects=True,
        )
        response = client.post(
            "/profile/settings",
            data={
                "_csrf_token": csrf_token(),
                "action": "profile",
                "username": "smoke_user",
                "bio": "",
                "contact": "13812345678",
                "default_post_visibility": "public",
                "default_follow_delay_days": "0",
                "equipped_title_id": str(smoke_title["_id"]),
            },
            follow_redirects=True,
        )
        profile_response = client.get(f"/users/{smoke_user['_id']}")
        detail_response = client.get(f"/community/{post['_id']}")
        with app.app_context():
            smoke_user = mongo.db.users.find_one({"_id": smoke_user["_id"]})
        check(
            "member can equip awarded title",
            response.status_code == 200 and smoke_user.get("equipped_title_id") == smoke_title["_id"],
        )
        check(
            "equipped title renders on profile and post detail",
            "校园光影记录者".encode("utf-8") in profile_response.data
            and "校园光影记录者".encode("utf-8") in detail_response.data,
        )

        login_as_session(admin)

        response = client.post(
            f"/admin/users/{smoke_user['_id']}/action",
            data={"_csrf_token": csrf_token(), "action": "mark_quality"},
            follow_redirects=True,
        )
        with app.app_context():
            smoke_user = mongo.db.users.find_one({"_id": smoke_user["_id"]})
        check("mark quality photographer", response.status_code == 200 and smoke_user.get("quality_photographer") is True)

        with app.app_context():
            old_time = now() - timedelta(days=40)
            ordinary_id = mongo.db.users.insert_one(
                {
                    "real_name": "普通旧用户",
                    "username": "ordinary_old",
                    "password_hash": hash_password("secret123"),
                    "contact": "",
                    "avatar_url": "",
                    "bio": "",
                    "role": "user",
                    "permissions": [],
                    "status": "active",
                    "cohort_tag": "2025届",
                    "quality_photographer": False,
                    "restricted_reason": "",
                    "allow_peer_super_admin_management": False,
                    "awarded_title_ids": [],
                    "equipped_title_id": None,
                    "must_change_password": False,
                    "session_version": 0,
                    "created_at": old_time,
                    "updated_at": old_time,
                    "last_login_at": None,
                }
            ).inserted_id
            mongo.db.posts.update_one(
                {"_id": post["_id"]},
                {"$set": {"created_at": old_time, "storage_status": "active", "storage_marked_at": None}},
            )
            mongo.db.submissions.update_one(
                {"_id": submission["_id"]},
                {"$set": {"created_at": old_time, "storage_status": "active", "storage_marked_at": None}},
            )
            ordinary_post_id = mongo.db.posts.insert_one(
                {
                    "author_id": ordinary_id,
                    "title": "普通旧帖",
                    "description": "",
                    "images": post.get("images", []),
                    "status": "normal",
                    "storage_status": "active",
                    "created_at": old_time,
                    "updated_at": old_time,
                }
            ).inserted_id
            deleted_post_id = mongo.db.posts.insert_one(
                {
                    "author_id": ordinary_id,
                    "title": "已删除普通旧帖",
                    "description": "",
                    "images": post.get("images", []),
                    "status": "deleted",
                    "storage_status": "active",
                    "created_at": old_time,
                    "updated_at": old_time,
                }
            ).inserted_id
            ordinary_submission_id = mongo.db.submissions.insert_one(
                {
                    "activity_id": activity["_id"],
                    "user_id": ordinary_id,
                    "images": submission.get("images", []),
                    "description": "普通旧投稿",
                    "contact": "",
                    "status": "pending",
                    "storage_status": "active",
                    "created_at": old_time,
                    "updated_at": old_time,
                }
            ).inserted_id
        client.get("/admin/storage")
        response = client.post(
            "/admin/storage",
            data={"_csrf_token": csrf_token(), "action": "mark", "older_than_days": "30"},
            follow_redirects=True,
        )
        with app.app_context():
            ordinary_post = mongo.db.posts.find_one({"_id": ordinary_post_id})
            deleted_post = mongo.db.posts.find_one({"_id": deleted_post_id})
            ordinary_submission = mongo.db.submissions.find_one({"_id": ordinary_submission_id})
            quality_post = mongo.db.posts.find_one({"_id": post["_id"]})
            quality_submission = mongo.db.submissions.find_one({"_id": submission["_id"]})
        check(
            "storage marks ordinary old content only",
            response.status_code == 200
            and ordinary_post.get("storage_status") == "deletable"
            and deleted_post.get("storage_status") == "deletable"
            and ordinary_submission.get("storage_status") == "deletable"
            and quality_post.get("storage_status") != "deletable"
            and quality_submission.get("storage_status") != "deletable",
        )
        response = client.post(
            "/admin/storage",
            data={"_csrf_token": csrf_token(), "action": "cleanup", "target_free_mb": "1"},
            follow_redirects=True,
        )
        check("storage cleanup route", response.status_code == 200)

        response = client.post(
            "/admin/users/batch-status",
            data={"_csrf_token": csrf_token(), "cohort_tag": "2026届", "action": "restrict", "restricted_reason": "毕业测试"},
            follow_redirects=True,
        )
        with app.app_context():
            smoke_user = mongo.db.users.find_one({"_id": smoke_user["_id"]})
        check("batch restrict by cohort", response.status_code == 200 and smoke_user.get("status") == "restricted")
        logout()
        client.get("/login")
        client.post(
            "/login",
            data={"_csrf_token": csrf_token(), "username": "smoke_user", "password": "secret123"},
            follow_redirects=True,
        )
        response = client.get("/community/new", follow_redirects=False)
        check("restricted user cannot create post", response.status_code == 302)

        with app.app_context():
            limited_password = "secret123"
            limited = mongo.db.users.insert_one(
                {
                    "real_name": "受限管理员",
                    "username": "limited_admin",
                    "password_hash": hash_password(limited_password),
                    "contact": "",
                    "avatar_url": "",
                    "bio": "",
                    "role": "admin",
                    "permissions": ["manage_invites"],
                    "status": "active",
                    "cohort_tag": "",
                    "quality_photographer": False,
                    "restricted_reason": "",
                    "allow_peer_super_admin_management": False,
                    "awarded_title_ids": [],
                    "equipped_title_id": None,
                    "must_change_password": False,
                    "session_version": 0,
                    "created_at": now(),
                    "updated_at": now(),
                    "last_login_at": None,
                }
            ).inserted_id
        logout()
        client.get("/login")
        client.post(
            "/login",
            data={"_csrf_token": csrf_token(), "username": "limited_admin", "password": limited_password},
            follow_redirects=True,
        )
        check("permission allows granted module", client.get("/admin/invites").status_code == 200)
        check("permission blocks missing module", client.get("/admin/users").status_code == 403)

        ok = all(value for _, value in results)
        print("SUMMARY", "PASS" if ok else "FAIL", f"{sum(value for _, value in results)}/{len(results)}")
        raise SystemExit(0 if ok else 1)
    finally:
        client_for_cleanup.drop_database(DB_NAME)
        shutil.rmtree(UPLOAD_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
