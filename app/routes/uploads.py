from flask import Blueprint, abort, send_file

from app.utils.files import safe_upload_path

bp = Blueprint("uploads", __name__, url_prefix="/uploads")


@bp.route("/<category>/<filename>")
def uploaded_file(category, filename):
    path = safe_upload_path(category, filename)
    if not path:
        abort(404)
    return send_file(path)
