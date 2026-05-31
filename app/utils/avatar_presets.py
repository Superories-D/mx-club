AVATAR_PRESETS = tuple(f"/static/images/avatars/avatar-{index:02d}.webp" for index in range(1, 31))


def normalize_preset_avatar(value):
    avatar_url = str(value or "").strip()
    if not avatar_url:
        return ""
    if avatar_url not in AVATAR_PRESETS:
        raise ValueError("请选择站内提供的预设头像。")
    return avatar_url
