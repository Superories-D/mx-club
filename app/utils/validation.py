import re
import unicodedata


USERNAME_RE = re.compile(r"^[\w.-]{2,40}$", re.UNICODE)
THEME_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ValidationError(ValueError):
    pass


def clean_text(value, label, max_length, required=False, min_length=0):
    text = str(value or "").strip()
    if required and not text:
        raise ValidationError(f"{label}不能为空。")
    if len(text) < min_length:
        raise ValidationError(f"{label}至少需要 {min_length} 个字符。")
    if len(text) > max_length:
        raise ValidationError(f"{label}不能超过 {max_length} 个字符。")
    if any(unicodedata.category(char).startswith("C") for char in text):
        raise ValidationError(f"{label}包含不支持的控制字符。")
    return text


def clean_username(value):
    username = clean_text(value, "用户名", 40, required=True, min_length=2)
    if not USERNAME_RE.fullmatch(username):
        raise ValidationError("用户名只能包含文字、数字、下划线、连字符和点号。")
    return username


def validate_password(password, label="密码"):
    value = str(password or "")
    if len(value) < 8:
        raise ValidationError(f"{label}至少需要 8 位。")
    if len(value) > 128:
        raise ValidationError(f"{label}不能超过 128 位。")
    return value


def clean_theme_color(value, fallback="#b8894f"):
    color = str(value or "").strip()
    return color if THEME_COLOR_RE.fullmatch(color) else fallback
