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
- 邀请码生成时可绑定用户标签/届别，注册后自动写入用户档案
- 管理员可按标签/届别筛选用户，并批量暂停或恢复普通用户的发帖、互动和投稿功能
- 管理员可标记“优质摄影”用户，优质用户的帖子和投稿默认不进入自动清理池
- 细粒度管理员权限，可按模块授予用户管理、注册表、社区审核、活动、投稿审核、网站设置、审计日志等权限
- CSV 注册表导入、导出、模板下载；支持“先指定数量生成空白注册表，再填姓名上传自动配对”的线下发码流程
- 素材征集活动创建、编辑、删除、状态管理
- 用户投稿、管理员单个/批量审核
- 用户可举报帖子或评论，管理员可在举报队列中处理
- 入选作品展示与投稿图片 zip 批量下载
- 存储治理：普通用户旧内容可在 30 天后标记为可删除，磁盘空间不足时按时间从早到晚清理本地图片
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

Docker 镜像内使用 Gunicorn 启动 Flask 应用，并提供 `/healthz` 与 `/readyz` 健康检查接口。`docker-compose.yml` 中 web 与 MongoDB 均配置了 healthcheck，MongoDB 就绪后 web 才会启动。

## Ubuntu 一键部署

项目提供 `setup.sh`，面向 Ubuntu 20.04 / 22.04 / 24.04 服务器。脚本会安装基础依赖、Docker Engine、Docker Compose 插件，拉取 `Superories-D/mx-club` 仓库，生成 `.env`，创建 `docker-compose.override.yml`，启动服务并等待 `/readyz` 就绪。

最简部署：

```bash
curl -fsSL https://raw.githubusercontent.com/Superories-D/mx-club/main/setup.sh | sudo bash
```

常用生产部署参数：

```bash
curl -fsSL https://raw.githubusercontent.com/Superories-D/mx-club/main/setup.sh | sudo bash -s -- \
  --install-dir /opt/muxi-photo \
  --port 5000 \
  --secure-cookie \
  --proxy-fix \
  --noninteractive
```

脚本支持通过环境变量覆盖配置，例如：

```bash
sudo SITE_NAME="泸州高中木樨映像" HTTP_PORT=8080 bash setup.sh
```

部署完成后查看初始管理员：

```bash
cd /opt/muxi-photo
docker compose -f docker-compose.yml -f docker-compose.override.yml logs web | grep 'super_admin'
```

公网部署建议不要暴露 MongoDB 端口。`setup.sh` 默认会在 override 文件中取消 MongoDB 宿主机端口映射；只有显式传入 `--expose-mongodb` 才会暴露 `27017`。

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
| `SESSION_COOKIE_SECURE` | HTTPS 部署时建议设为 `true` |
| `PROXY_FIX` | 反向代理后部署时建议设为 `true` |
| `GUNICORN_WORKERS` | Docker/Gunicorn worker 数量 |

## 生产部署检查清单

上线前请至少完成：

- 修改 `SECRET_KEY`，不要使用示例值。
- 若站点通过 HTTPS 访问，设置 `SESSION_COOKIE_SECURE=true`。
- 若前面有 Nginx、Caddy、Traefik 等反向代理，设置 `PROXY_FIX=true`，并让代理转发 `X-Forwarded-*` 请求头。
- 为 `uploads/` 和 MongoDB 数据卷准备持久化备份。
- 不要公开暴露 MongoDB 端口；公网部署时可移除 `mongodb` 的 `ports` 映射，仅保留 Docker 内部网络访问。
- 使用 `docker compose logs web` 保存首次生成的 super_admin 初始密码，首次登录后立即修改。

## 健康检查

- `GET /healthz`：应用进程存活检查，不访问数据库。
- `GET /readyz`：应用就绪检查，会 ping MongoDB。

示例：

```powershell
Invoke-WebRequest http://localhost:5000/readyz -UseBasicParsing
```

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

推荐使用后台“生成空白注册表”流程：

1. 管理员输入需要的邀请码数量，例如 30 个。
2. 可同时填写“用户标签/届别”，例如 `2026届`、`高2024级` 或 `春季摄影班`。
3. 系统生成 CSV，左侧是序号和“真实姓名（填写）”，右侧是“邀请码（剪下发放）”。
4. 负责老师按顺序填写真实姓名，右侧邀请码可剪切分发给对应同学。
5. 管理员上传填好姓名后的 CSV，系统自动按每行真实姓名和邀请码配对，并在用户注册时把标签写入用户档案。

空白表字段如下：

```csv
序号,真实姓名（填写）,用户标签/届别,邀请码（剪下发放）,备注
1,张三,2026届,MUXI2026A001,右侧邀请码可剪下分发；填好姓名后上传本表自动配对
```

也兼容旧式 CSV 直接导入，字段如下：

```csv
邀请码,真实姓名,用户标签/届别,是否已使用,绑定用户ID,创建时间,使用时间
MUXI2026A001,张三,2026届,否,,,
```

导入时会跳过重复的邀请码 + 真实姓名组合，并提示成功数量、失败数量和失败原因。

## 标签与毕业账号治理

注册表中的 `用户标签/届别` 会复制到新用户的 `cohort_tag` 字段。后台“用户管理”支持按标签筛选，并可对某个标签下的普通用户批量设置为 `restricted`。`restricted` 用户仍可登录和浏览公开页面，但不能发帖、评论、点赞、收藏、关注或提交活动投稿；恢复为 `active` 后功能恢复。

后台也可以把用户标记为“优质摄影”。该标记用于长期保留优质摄影用户的帖子和投稿图片，避免它们进入后续存储清理池。

## 存储治理

后台“存储治理”提供两步操作：

1. 标记可删除内容：默认把 30 天前、作者不是优质摄影用户的帖子和投稿标记为 `deletable`。
2. 磁盘不足时清理：当当前可用空间低于管理员填写的目标 MB 时，从 `deletable` 池中按内容创建时间从早到晚删除本地图片，并记录到文档的 `deleted_files`。

清理只删除 `/uploads/` 下经过安全路径校验的本地图片，不会删除数据库文档。优质摄影用户的内容不会被标记为可删除。

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

## 部署前 Smoke Test

本项目提供一个端到端 smoke test，会使用临时 MongoDB 数据库和临时上传目录，结束后自动清理。

```powershell
python scripts/smoke_test.py
```

覆盖内容包括：

- 首页、登录、注册、社区、活动、后台主要页面渲染
- 首次管理员生成
- 邀请码创建、批量生成和注册
- 邀请码标签传递、按届别批量暂停账号部分功能
- 登录、发帖、点赞、评论、举报
- 细粒度管理员权限拦截
- 创建活动、投稿、审核、入选 zip 下载
- 优质摄影用户存储豁免、普通旧内容标记和存储治理页面
- 健康检查接口

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
