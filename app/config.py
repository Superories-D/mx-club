import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/muxi_photo")
    DATABASE_NAME = os.getenv("DATABASE_NAME", "muxi_photo")
    SITE_NAME = os.getenv("SITE_NAME", "泸州高中木樨映像")
    ADMIN_INIT_SHOW_ON_PAGE = os.getenv("ADMIN_INIT_SHOW_ON_PAGE", "false").lower() == "true"
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
    MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10"))
    MAX_CONTENT_LENGTH = MAX_UPLOAD_SIZE_MB * 1024 * 1024 * 12
    DEBUG = os.getenv("FLASK_ENV", "development") == "development"

    @classmethod
    def upload_root(cls) -> Path:
        root = Path(cls.UPLOAD_FOLDER)
        if not root.is_absolute():
            root = BASE_DIR / root
        return root
