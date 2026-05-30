import secrets
import string

from pymongo.errors import DuplicateKeyError

from app.extensions import mongo
from app.utils.permissions import PERMISSION_KEYS
from app.utils.security import hash_password, now


def _random_password(length=14):
    alphabet = string.ascii_letters + string.digits + "!@#$%&"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def initialize_admin(app):
    admin_exists = mongo.db.users.find_one({"role": {"$in": ["admin", "super_admin"]}})
    if admin_exists:
        return
    try:
        mongo.db.system_locks.insert_one({"key": "initial_admin", "created_at": now()})
    except DuplicateKeyError:
        app.logger.warning("检测到初始管理员生成锁，但当前没有管理员账号；如曾异常中断，请人工检查数据库。")
        return

    admin_exists = mongo.db.users.find_one({"role": {"$in": ["admin", "super_admin"]}})
    if admin_exists:
        return

    username = f"muxi_root_{secrets.token_hex(3)}"
    password = _random_password()
    mongo.db.users.insert_one(
        {
            "real_name": "初始管理员",
            "username": username,
            "password_hash": hash_password(password),
            "contact": "",
            "avatar_url": "",
            "bio": "",
            "role": "super_admin",
            "permissions": PERMISSION_KEYS,
            "status": "active",
            "cohort_tag": "",
            "quality_photographer": False,
            "session_version": 0,
            "must_change_password": True,
            "created_at": now(),
            "updated_at": now(),
            "last_login_at": None,
        }
    )
    app.logger.warning("木樨映像初始 super_admin 已生成，用户名：%s，密码：%s", username, password)
