import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/muxi_photo")
    DATABASE_NAME = os.getenv("DATABASE_NAME", "muxi_photo")
    SITE_NAME = os.getenv("SITE_NAME", "泸州高中木樨映像")
    ADMIN_INIT_SHOW_ON_PAGE = env_bool("ADMIN_INIT_SHOW_ON_PAGE", False)
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
    MAX_UPLOAD_SIZE_MB = env_int("MAX_UPLOAD_SIZE_MB", 10)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_SIZE_MB * 1024 * 1024 * 12
    DEBUG = os.getenv("FLASK_ENV", "development") == "development"
    TESTING = env_bool("TESTING", False)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME_DAYS = env_int("PERMANENT_SESSION_LIFETIME_DAYS", 14)
    PROXY_FIX = env_bool("PROXY_FIX", False)
    HEALTHCHECK_DATABASE_TIMEOUT_MS = env_int("HEALTHCHECK_DATABASE_TIMEOUT_MS", 1200)

    @classmethod
    def upload_root(cls) -> Path:
        root = Path(cls.UPLOAD_FOLDER)
        if not root.is_absolute():
            root = BASE_DIR / root
        return root
