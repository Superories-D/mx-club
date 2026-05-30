# 泸州高中木樨映像

泸州高中木樨映像是一个完整可运行的摄影社团官网，包含摄影作品社区、校园摄影素材征集、邀请码注册、后台审核和站点设置等功能。

## 技术栈

- 后端：Python Flask
- 数据库：MongoDB
- MongoDB 驱动：PyMongo / Flask-PyMongo
- 前端：Flask Jinja2 模板 + 原生 CSS + 少量原生 JS
- 鉴权：Flask Session
- 文件存储：本地 `uploads/`
- 部署：Dockerfile + docker-compose

## 功能列表

- 首次启动自动生成 `super_admin`
- 管理员强制首次修改资料和密码
- 邀请码 + 真实姓名注册
- 登录、退出、资料修改、密码修改、账号注销
- 摄影作品发布、多图上传、编辑、删除
- 点赞、收藏、评论、关注
- 用户主页与联系方式打码
- 管理员用户管理、封禁、解封、注销、重置密码
- CSV 注册表导入、导出、模板下载
- 素材征集活动创建、编辑、删除、状态管理
- 用户投稿、管理员单个/批量审核
- 入选作品展示与投稿图片 zip 批量下载
- 网站基础信息和默认视觉素材设置
- 管理员关键操作审计日志

## 本地运行

1. 安装并启动 MongoDB。
2. 创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

3. 复制环境变量：

```powershell
Copy-Item .env.example .env
```

本地直连 MongoDB 时，建议把 `.env` 中的 `MONGO_URI` 改为：

```env
MONGO_URI=mongodb://localhost:27017/muxi_photo
```

4. 启动服务：

```powershell
python run.py
```

访问：`http://localhost:5000`

## Docker 运行

```powershell
docker compose up -d --build
```

访问：`http://localhost:5000`

查看初始管理员账号密码：

```powershell
docker compose logs web
```

首次空数据库启动时，日志中会出现：

```text
木樨映像初始 super_admin 已生成，用户名：...，密码：...
```

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `FLASK_ENV` | `development` 或生产环境值 |
| `SECRET_KEY` | Flask Session 密钥，生产环境必须修改 |
| `MONGO_URI` | MongoDB 连接地址 |
| `DATABASE_NAME` | 数据库名称 |
| `UPLOAD_FOLDER` | 上传目录，默认 `uploads` |
| `MAX_UPLOAD_SIZE_MB` | 单文件上传大小限制，默认 10MB |
| `SITE_NAME` | 默认网站名称 |
| `ADMIN_INIT_SHOW_ON_PAGE` | 保留配置，当前默认通过日志查看初始管理员 |

## MongoDB 配置

应用启动时会自动创建集合索引，包括用户唯一用户名、邀请码复合唯一索引、点赞/收藏/关注唯一索引，以及帖子、评论、活动、投稿、审计日志常用查询索引。

主要集合：

- `users`
- `invite_codes`
- `posts`
- `comments`
- `likes`
- `favorites`
- `follows`
- `activities`
- `submissions`
- `site_settings`
- `audit_logs`

## 首次管理员逻辑

系统启动时检查 `users` 集合中是否存在 `admin` 或 `super_admin`。如果不存在，会自动生成一个 `super_admin`，并把用户名和密码写入服务端日志。该账号 `must_change_password=true`，首次登录后必须修改真实姓名、用户名和密码。

后台入口：

```text
http://localhost:5000/admin
```

## 注册表 CSV 格式

优先支持 CSV 导入，字段如下：

```csv
邀请码,真实姓名,是否已使用,绑定用户ID,创建时间,使用时间
MUXI2026A001,张三,否,,,
```

导入时会跳过重复的邀请码 + 真实姓名组合，并提示成功数量、失败数量和失败原因。

## 文件上传目录

```text
uploads/
├─ avatars/
├─ posts/
├─ activities/
├─ submissions/
└─ site_assets/
```

上传限制：

- 仅允许 `jpg`、`jpeg`、`png`、`webp`
- 单文件默认最大 10MB
- 使用 UUID 重命名
- 使用 Pillow 校验真实图片
- 通过 `/uploads/<category>/<filename>` 安全访问

## 默认视觉素材

默认图片位于：

```text
app/static/images/generated/
```

包含首页 Banner、登录背景、社区封面、活动封面、默认头像、空状态插画。详细提示词见 `docs/image_prompts.md`。管理员可在后台“网站设置”中上传新图片替换。

## 常见问题

- 看不到初始管理员：确认数据库为空，并查看 `docker compose logs web` 或本地终端日志。
- 注册失败：确认邀请码和真实姓名与后台注册表完全一致，且邀请码未使用。
- 上传失败：确认文件是真实图片，格式为 jpg/jpeg/png/webp，且没有超过大小限制。
- 后台无权限：只有 `admin` 和 `super_admin` 可以访问 `/admin`。

## 后续可扩展方向

- Excel 注册表导入
- 图片压缩、缩略图与 EXIF 信息提取
- 更细粒度的审核状态流转
- 活动投稿通知
- 搜索高亮和更完整的全文搜索
- 对象存储或 CDN 接入
