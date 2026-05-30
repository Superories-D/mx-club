PERMISSIONS = [
    ("manage_users", "用户管理", "查看用户列表、封禁/解封、注销账号、重置密码、创建管理员。"),
    ("manage_invites", "注册表管理", "创建、批量生成、导入、导出和删除邀请码。"),
    ("moderate_community", "社区内容审核", "隐藏/删除帖子、删除评论、处理举报。"),
    ("manage_activities", "活动管理", "创建、编辑、删除素材征集活动。"),
    ("review_submissions", "投稿审核", "审核投稿、批量审核、下载投稿图片。"),
    ("manage_storage", "存储治理", "标记过期普通内容，并在磁盘空间不足时按时间清理可删除图片。"),
    ("manage_settings", "网站设置", "修改站点基础信息、主题和默认视觉素材。"),
    ("view_audit_logs", "审计日志", "查看管理员关键操作记录。"),
]

PERMISSION_KEYS = [item[0] for item in PERMISSIONS]


def normalize_permissions(values):
    allowed = set(PERMISSION_KEYS)
    return [value for value in values if value in allowed]


def has_permission(user, permission):
    if not user:
        return False
    role = user.get("role")
    if role == "super_admin":
        return True
    if role != "admin":
        return False
    permissions = user.get("permissions")
    if permissions is None:
        return True
    return permission in permissions


def permission_label_map():
    return {key: label for key, label, _ in PERMISSIONS}
