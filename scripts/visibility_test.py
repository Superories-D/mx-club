import os
import shutil
import sys
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from PIL import Image
from pymongo import MongoClient


ROOT = Path(__file__).resolve().parent.parent
DB_NAME = f"muxi_photo_visibility_{os.getpid()}"
UPLOAD_DIR = ROOT / "tmp" / "visibility_uploads"
MONGO_SERVER_URI = os.getenv("TEST_MONGO_URI", "mongodb://localhost:27017").rstrip("/")
sys.path.insert(0, str(ROOT))

os.environ["MONGO_URI"] = f"{MONGO_SERVER_URI}/{DB_NAME}?serverSelectionTimeoutMS=3000"
os.environ["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
os.environ["SITE_NAME"] = "木樨映像可见度测试"
os.environ["TESTING"] = "true"

from app import create_app  # noqa: E402
from app.extensions import mongo  # noqa: E402
from app.utils.security import hash_password, now  # noqa: E402


def image_file(name="test.png", color=(184, 137, 79), size=(160, 100)):
    buffer = BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer, name


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
        "cohort_tag": "可见度测试",
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


def main():
    cleanup_client = MongoClient(MONGO_SERVER_URI, serverSelectionTimeoutMS=3000)
    try:
        cleanup_client.admin.command("ping")
    except Exception as exc:
        raise SystemExit(f"MongoDB 不可用，无法运行 visibility test：{exc}") from exc

    app = create_app()
    anonymous = app.test_client()
    author_client = app.test_client()
    viewer_client = app.test_client()
    limited_client = app.test_client()
    admin_client = app.test_client()
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

    try:
        with app.app_context():
            admin = mongo.db.users.find_one({"role": "super_admin"})
            mongo.db.users.update_one({"_id": admin["_id"]}, {"$set": {"must_change_password": False}})
            admin = mongo.db.users.find_one({"_id": admin["_id"]})
            author_id = mongo.db.users.insert_one(user_doc("visibility_author")).inserted_id
            viewer_id = mongo.db.users.insert_one(user_doc("visibility_viewer")).inserted_id
            limited_id = mongo.db.users.insert_one(
                user_doc("visibility_limited_admin", "admin", ["manage_users"])
            ).inserted_id
            author = mongo.db.users.find_one({"_id": author_id})
            viewer = mongo.db.users.find_one({"_id": viewer_id})
            limited = mongo.db.users.find_one({"_id": limited_id})

        login_as(author_client, author)
        login_as(viewer_client, viewer)
        login_as(limited_client, limited)
        login_as(admin_client, admin)

        response = author_client.post(
            "/profile/settings",
            data={
                "_csrf_token": csrf(author_client),
                "action": "profile",
                "username": "visibility_author",
                "bio": "摄影社成员",
                "contact": "13812345678",
                "default_post_visibility": "followers",
                "default_follow_delay_days": "4",
                "avatar_crop_x": "15",
                "avatar_crop_y": "80",
                "avatar_crop_zoom": "180",
                "avatar": image_file("crop-avatar.png", size=(240, 120)),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        with app.app_context():
            author = mongo.db.users.find_one({"_id": author_id})
        cropped_url = author.get("avatar_url")
        cropped_path = UPLOAD_DIR / cropped_url.removeprefix("/uploads/")
        with Image.open(cropped_path) as cropped_avatar:
            cropped_ok = cropped_avatar.size == (512, 512) and cropped_avatar.format == "WEBP"
        check("uploaded avatar is cropped to 512px WEBP", response.status_code == 200 and cropped_ok)
        check(
            "profile stores default follower visibility",
            author.get("default_post_visibility") == "followers" and author.get("default_follow_delay_days") == 4,
        )

        preset_url = "/static/images/avatars/avatar-01.webp"
        response = author_client.post(
            "/profile/settings",
            data={
                "_csrf_token": csrf(author_client),
                "action": "profile",
                "username": "visibility_author",
                "bio": "摄影社成员",
                "contact": "13812345678",
                "default_post_visibility": "followers",
                "default_follow_delay_days": "4",
                "avatar_preset": preset_url,
            },
            follow_redirects=True,
        )
        with app.app_context():
            author = mongo.db.users.find_one({"_id": author_id})
        check(
            "preset avatar selection removes replaced upload",
            response.status_code == 200 and author.get("avatar_url") == preset_url and not cropped_path.exists(),
        )
        response = author_client.post(
            "/profile/settings",
            data={
                "_csrf_token": csrf(author_client),
                "action": "profile",
                "username": "visibility_author",
                "bio": "",
                "contact": "",
                "avatar_preset": "/static/images/avatars/not-allowed.webp",
            },
            follow_redirects=True,
        )
        with app.app_context():
            author = mongo.db.users.find_one({"_id": author_id})
        check("preset avatar whitelist rejects arbitrary URL", response.status_code == 200 and author.get("avatar_url") == preset_url)

        response = author_client.post(
            "/community/new",
            data={
                "_csrf_token": csrf(author_client),
                "title": "默认关注作品",
                "description": "等待期验证",
                "images": [image_file("followers.png", color=(70, 120, 80))],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        with app.app_context():
            follower_post = mongo.db.posts.find_one({"title": "默认关注作品"})
        follower_url = follower_post["images"][0]
        check(
            "new post inherits user visibility defaults",
            response.status_code == 200
            and follower_post.get("visibility") == "followers"
            and follower_post.get("follow_delay_days") == 4,
        )

        response = author_client.post(
            "/community/new",
            data={
                "_csrf_token": csrf(author_client),
                "title": "仅作者作品",
                "visibility": "private",
                "follow_delay_days": "7",
                "images": [image_file("private.png", color=(120, 70, 80))],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        with app.app_context():
            private_post = mongo.db.posts.find_one({"title": "仅作者作品"})
        private_url = private_post["images"][0]
        check("post form overrides user visibility defaults", response.status_code == 200 and private_post.get("visibility") == "private")

        list_response = anonymous.get("/community/")
        home_response = anonymous.get("/")
        detail_response = anonymous.get(f"/community/{follower_post['_id']}")
        check("locked follower post remains discoverable", "默认关注作品".encode() in list_response.data)
        check("locked cards never render protected image URL", follower_url.encode() not in list_response.data and follower_url.encode() not in home_response.data)
        check("locked detail renders prompt without protected image URL", detail_response.status_code == 200 and follower_url.encode() not in detail_response.data)
        check("anonymous cannot fetch locked follower image", anonymous.get(follower_url).status_code == 404)
        check("author can fetch own follower image", author_client.get(follower_url).status_code == 200)
        check("limited admin cannot bypass follower image lock", limited_client.get(follower_url).status_code == 404)
        check("moderator can fetch follower image", admin_client.get(follower_url).status_code == 200)
        check(
            "locked follower cannot interact with hidden image",
            viewer_client.post(
                f"/community/{follower_post['_id']}/like",
                data={"_csrf_token": csrf(viewer_client)},
            ).status_code
            == 404,
        )

        viewer_client.post(
            f"/users/{author_id}/follow",
            data={"_csrf_token": csrf(viewer_client)},
            follow_redirects=True,
        )
        check("new follower remains locked during waiting period", viewer_client.get(follower_url).status_code == 404)
        with app.app_context():
            mongo.db.follows.update_one(
                {"follower_id": viewer_id, "following_id": author_id},
                {"$set": {"created_at": now() - timedelta(days=5)}},
            )
        unlocked_response = viewer_client.get(follower_url)
        check(
            "mature follower unlocks protected image",
            unlocked_response.status_code == 200 and unlocked_response.headers.get("Cache-Control") == "private, no-store",
        )
        check("unlocked detail renders image URL", follower_url.encode() in viewer_client.get(f"/community/{follower_post['_id']}").data)

        list_response = anonymous.get("/community/")
        profile_response = anonymous.get(f"/users/{author_id}")
        check("private post is absent from public listings", "仅作者作品".encode() not in list_response.data and "仅作者作品".encode() not in profile_response.data)
        check("anonymous cannot open private post detail", anonymous.get(f"/community/{private_post['_id']}").status_code == 404)
        check("anonymous cannot fetch private post image", anonymous.get(private_url).status_code == 404)
        check("author can open own private post", author_client.get(f"/community/{private_post['_id']}").status_code == 200)
        check("author can fetch own private post image", author_client.get(private_url).status_code == 200)
        check("limited admin cannot bypass private detail", limited_client.get(f"/community/{private_post['_id']}").status_code == 404)
        check("limited admin cannot edit private post", limited_client.get(f"/community/{private_post['_id']}/edit").status_code == 403)
        check("moderator can open private post detail", admin_client.get(f"/community/{private_post['_id']}").status_code == 200)
        check("moderator can fetch private post image", admin_client.get(private_url).status_code == 200)

        with app.app_context():
            legacy_path = UPLOAD_DIR / "posts" / "legacy-public.png"
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (20, 20), color=(1, 2, 3)).save(legacy_path)
            legacy_url = "/uploads/posts/legacy-public.png"
            mongo.db.posts.insert_one(
                {
                    "author_id": author_id,
                    "title": "兼容旧公开作品",
                    "images": [legacy_url],
                    "status": "normal",
                    "created_at": now(),
                    "updated_at": now(),
                }
            )
        check("legacy post without visibility stays public", anonymous.get(legacy_url).status_code == 200)

        with app.app_context():
            invalid_path = UPLOAD_DIR / "posts" / "invalid-visibility.png"
            Image.new("RGB", (20, 20), color=(3, 2, 1)).save(invalid_path)
            invalid_url = "/uploads/posts/invalid-visibility.png"
            mongo.db.posts.insert_one(
                {
                    "author_id": author_id,
                    "title": "异常可见度作品",
                    "images": [invalid_url],
                    "visibility": "unexpected",
                    "status": "normal",
                    "created_at": now(),
                    "updated_at": now(),
                }
            )
        check("unknown stored visibility fails closed", anonymous.get(invalid_url).status_code == 404)

        ok = all(value for _, value in results)
        print("SUMMARY", "PASS" if ok else "FAIL", f"{sum(value for _, value in results)}/{len(results)}")
        raise SystemExit(0 if ok else 1)
    finally:
        cleanup_client.drop_database(DB_NAME)
        shutil.rmtree(UPLOAD_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
