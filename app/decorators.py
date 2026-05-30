from functools import wraps

from flask import abort, flash, g, redirect, request, url_for


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            flash("请先登录。", "warning")
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def active_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            flash("请先登录。", "warning")
            return redirect(url_for("auth.login"))
        if g.user.get("status") != "active":
            flash("账号不可用，无法执行该操作。", "danger")
            return redirect(url_for("main.index"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            flash("请先登录后台。", "warning")
            return redirect(url_for("auth.login", next=request.full_path))
        if g.user.get("role") not in ("admin", "super_admin"):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def super_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            flash("请先登录。", "warning")
            return redirect(url_for("auth.login"))
        if g.user.get("role") != "super_admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped
