import os
import csv
import shutil
import sys
from io import BytesIO, StringIO
from pathlib import Path

from PIL import Image
from pymongo import MongoClient


ROOT = Path(__file__).resolve().parent.parent
DB_NAME = f"muxi_photo_smoke_{os.getpid()}"
UPLOAD_DIR = ROOT / "tmp" / "smoke_uploads"
sys.path.insert(0, str(ROOT))

os.environ["MONGO_URI"] = f"mongodb://localhost:27017/{DB_NAME}?serverSelectionTimeoutMS=3000"
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
    client_for_cleanup = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=3000)
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

        with client.session_transaction() as sess:
            sess["user_id"] = str(admin["_id"])
        for path in [
            "/admin/",
            "/admin/users",
            "/admin/invites",
            "/admin/posts",
            "/admin/comments",
            "/admin/reports",
            "/admin/activities",
            "/admin/submissions",
            "/admin/settings",
            "/admin/audit-logs",
        ]:
            response = client.get(path)
            check(f"ADMIN {path}", response.status_code == 200)

        client.get("/admin/invites")
        response = client.post(
            "/admin/invites/new",
            data={"_csrf_token": csrf_token(), "code": "SMOKE001", "real_name": "调试同学"},
            follow_redirects=True,
        )
        check("create invite", response.status_code == 200)
        response = client.post(
            "/admin/invites/generate-sheet",
            data={"_csrf_token": csrf_token(), "prefix": "SHEET", "count": "2"},
            follow_redirects=True,
        )
        sheet_text = response.data.decode("utf-8-sig")
        sheet_rows = list(csv.DictReader(StringIO(sheet_text)))
        with app.app_context():
            pending_sheet_count = mongo.db.invite_codes.count_documents({"code": {"$regex": "^SHEET"}, "real_name": ""})
        check("generate blank invite sheet", response.status_code == 200 and len(sheet_rows) == 2 and pending_sheet_count == 2)
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
            data={"_csrf_token": csrf_token(), "prefix": "BULK", "real_names": "批量同学一\n批量同学二"},
            follow_redirects=True,
        )
        with app.app_context():
            bulk_count = mongo.db.invite_codes.count_documents({"code": {"$regex": "^BULK"}})
        check("direct bulk generate invites", response.status_code == 200 and bulk_count == 2)

        client.get("/logout")
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

        client.get("/logout")
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
                    "must_change_password": False,
                    "created_at": now(),
                    "updated_at": now(),
                    "last_login_at": None,
                }
            ).inserted_id
        client.get("/logout")
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
