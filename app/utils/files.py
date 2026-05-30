import mimetypes
import uuid
from pathlib import Path

from flask import current_app
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename


ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_MIME_PREFIXES = {"image/jpeg", "image/png", "image/webp"}
UPLOAD_CATEGORIES = {"avatars", "posts", "activities", "submissions", "site_assets"}


class UploadError(ValueError):
    pass


def ensure_upload_dirs():
    root = current_app.config["UPLOAD_ROOT"]
    for category in UPLOAD_CATEGORIES:
        (root / category).mkdir(parents=True, exist_ok=True)


def allowed_extension(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _validate_image(path, mimetype):
    if mimetype not in ALLOWED_MIME_PREFIXES:
        guessed, _ = mimetypes.guess_type(path.name)
        if guessed not in ALLOWED_MIME_PREFIXES:
            raise UploadError("文件 MIME 类型不支持。")
    try:
        with Image.open(path) as img:
            if (img.format or "").upper() not in {"JPEG", "PNG", "WEBP"}:
                raise UploadError("图片格式不支持。")
            img.verify()
    except (UnidentifiedImageError, OSError):
        raise UploadError("上传文件不是有效图片。")


def save_upload(file_storage, category):
    if category not in UPLOAD_CATEGORIES:
        raise UploadError("上传目录不合法。")
    if not file_storage or not file_storage.filename:
        raise UploadError("请选择上传文件。")
    if not allowed_extension(file_storage.filename):
        raise UploadError("文件格式不支持，仅允许 jpg、jpeg、png、webp。")

    original = secure_filename(file_storage.filename)
    ext = original.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    target_dir = current_app.config["UPLOAD_ROOT"] / category
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename

    max_bytes = current_app.config["MAX_UPLOAD_SIZE_MB"] * 1024 * 1024
    stream = file_storage.stream
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    if size > max_bytes:
        raise UploadError(f"文件过大，单文件不能超过 {current_app.config['MAX_UPLOAD_SIZE_MB']}MB。")

    file_storage.save(path)
    try:
        _validate_image(path, file_storage.mimetype)
    except UploadError:
        path.unlink(missing_ok=True)
        raise
    return f"/uploads/{category}/{filename}"


def save_many(files, category, required=False):
    saved = []
    for file_storage in files:
        if file_storage and file_storage.filename:
            saved.append(save_upload(file_storage, category))
    if required and not saved:
        raise UploadError("请至少上传一张图片。")
    return saved


def safe_upload_path(category, filename):
    if category not in UPLOAD_CATEGORIES:
        return None
    safe_name = secure_filename(filename)
    if safe_name != filename:
        return None
    root = current_app.config["UPLOAD_ROOT"].resolve()
    path = (root / category / safe_name).resolve()
    try:
        path.relative_to(root / category)
    except ValueError:
        return None
    if not path.exists() or not path.is_file():
        return None
    return path
