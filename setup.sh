#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="muxi-photo"
REPO_URL="${REPO_URL:-https://github.com/Superories-D/mx-club.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/muxi-photo}"
HTTP_PORT="${HTTP_PORT:-80}"
SITE_NAME="${SITE_NAME:-泸州高中木樨映像}"
MAX_UPLOAD_SIZE_MB="${MAX_UPLOAD_SIZE_MB:-10}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-2}"
SESSION_COOKIE_SECURE="${SESSION_COOKIE_SECURE:-false}"
PROXY_FIX="${PROXY_FIX:-false}"
EXPOSE_MONGODB="${EXPOSE_MONGODB:-false}"
RUN_SMOKE_TEST="${RUN_SMOKE_TEST:-false}"
NONINTERACTIVE="${NONINTERACTIVE:-false}"

log() {
  printf '\033[1;32m[%s]\033[0m %s\n' "$APP_NAME" "$*"
}

warn() {
  printf '\033[1;33m[%s]\033[0m %s\n' "$APP_NAME" "$*" >&2
}

die() {
  printf '\033[1;31m[%s]\033[0m %s\n' "$APP_NAME" "$*" >&2
  exit 1
}

usage() {
  printf '%s\n' 'Ubuntu 一键部署脚本：泸州高中木樨映像 / Muxi Photo

用法：
  bash setup.sh [选项]

常用选项：
  --install-dir PATH       部署目录，默认 /opt/muxi-photo
  --repo-url URL           Git 仓库地址，默认 https://github.com/Superories-D/mx-club.git
  --branch NAME            分支名，默认 main
  --port PORT              Web 端口，默认 80
  --site-name NAME         网站名称
  --secure-cookie          启用 SESSION_COOKIE_SECURE=true，HTTPS 部署建议开启
  --proxy-fix              启用 Flask ProxyFix，反向代理后部署建议开启
  --expose-mongodb         将 MongoDB 27017 暴露到宿主机，不建议公网部署开启
  --run-smoke-test         部署后运行 scripts/smoke_test.py，需要宿主机 Python 依赖
  --noninteractive         不询问确认，适合 CI 或云服务器初始化脚本
  -h, --help               查看帮助

也可以通过环境变量覆盖：
  INSTALL_DIR, REPO_URL, BRANCH, HTTP_PORT, SITE_NAME, SECRET_KEY,
  SESSION_COOKIE_SECURE, PROXY_FIX, EXPOSE_MONGODB, GUNICORN_WORKERS

示例：
  curl -fsSL https://raw.githubusercontent.com/Superories-D/mx-club/main/setup.sh | sudo bash
  sudo bash setup.sh --secure-cookie --proxy-fix
'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      INSTALL_DIR="${2:?缺少 --install-dir 参数}"
      shift 2
      ;;
    --repo-url)
      REPO_URL="${2:?缺少 --repo-url 参数}"
      shift 2
      ;;
    --branch)
      BRANCH="${2:?缺少 --branch 参数}"
      shift 2
      ;;
    --port)
      HTTP_PORT="${2:?缺少 --port 参数}"
      shift 2
      ;;
    --site-name)
      SITE_NAME="${2:?缺少 --site-name 参数}"
      shift 2
      ;;
    --secure-cookie)
      SESSION_COOKIE_SECURE="true"
      shift
      ;;
    --proxy-fix)
      PROXY_FIX="true"
      shift
      ;;
    --expose-mongodb)
      EXPOSE_MONGODB="true"
      shift
      ;;
    --run-smoke-test)
      RUN_SMOKE_TEST="true"
      shift
      ;;
    --noninteractive)
      NONINTERACTIVE="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "未知参数：$1"
      ;;
  esac
done

validate_options() {
  [[ "$HTTP_PORT" =~ ^[0-9]+$ ]] || die "--port 必须是 1 到 65535 之间的整数。"
  (( HTTP_PORT >= 1 && HTTP_PORT <= 65535 )) || die "--port 必须是 1 到 65535 之间的整数。"
}

require_ubuntu() {
  [[ -r /etc/os-release ]] || die "无法识别系统：缺少 /etc/os-release。"
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || die "该脚本仅面向 Ubuntu。当前系统：${PRETTY_NAME:-unknown}"
}

require_root() {
  [[ "$(id -u)" -eq 0 ]] || die "请使用 root 或 sudo 运行：sudo bash setup.sh"
}

confirm_deploy() {
  if [[ "$NONINTERACTIVE" == "true" ]]; then
    return
  fi
  cat <<EOF
即将部署：
  仓库：$REPO_URL
  分支：$BRANCH
  目录：$INSTALL_DIR
  Web 端口：$HTTP_PORT
  网站名称：$SITE_NAME
  HTTPS Cookie：$SESSION_COOKIE_SECURE
  反向代理 ProxyFix：$PROXY_FIX
EOF
  read -r -p "确认继续？[y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]] || die "已取消。"
}

install_base_packages() {
  export DEBIAN_FRONTEND=noninteractive
  log "安装基础依赖。"
  apt-get update
  apt-get install -y ca-certificates curl gnupg git openssl python3 python3-pip python3-venv
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker 与 Docker Compose 已安装。"
    return
  fi

  log "安装 Docker Engine 与 Compose 插件。"
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
  fi

  local codename
  codename="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME}")"
  local arch
  arch="$(dpkg --print-architecture)"
  cat >/etc/apt/sources.list.d/docker.list <<EOF
deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${codename} stable
EOF

  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

prepare_app_dir() {
  mkdir -p "$(dirname "$INSTALL_DIR")"

  if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "更新已有仓库：$INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
    return
  fi

  if [[ -e "$INSTALL_DIR" ]]; then
    if [[ -z "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
      rmdir "$INSTALL_DIR"
    else
      die "$INSTALL_DIR 已存在但不是 Git 仓库。为避免误删数据，请手动处理或换一个 --install-dir。"
    fi
  fi

  if [[ -f "docker-compose.yml" && -d "app" && "$(pwd -P)" == "$(dirname "$INSTALL_DIR")"* ]]; then
    warn "检测到当前目录像项目目录，但不等于 INSTALL_DIR；仍将 clone 到 $INSTALL_DIR。"
  fi

  log "克隆仓库到：$INSTALL_DIR"
  git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
}

generate_secret_key() {
  if [[ -n "${SECRET_KEY:-}" ]]; then
    printf '%s' "$SECRET_KEY"
  else
    openssl rand -hex 32
  fi
}

write_env_file() {
  local secret_key
  secret_key="$(generate_secret_key)"

  log "写入生产 .env。"
  cat >"$INSTALL_DIR/.env" <<EOF
FLASK_ENV=production
SECRET_KEY=${secret_key}
HTTP_PORT=${HTTP_PORT}
MONGO_URI=mongodb://mongodb:27017/muxi_photo?serverSelectionTimeoutMS=5000
DATABASE_NAME=muxi_photo
UPLOAD_FOLDER=uploads
MAX_UPLOAD_SIZE_MB=${MAX_UPLOAD_SIZE_MB}
SITE_NAME=${SITE_NAME}
ADMIN_INIT_SHOW_ON_PAGE=false
SESSION_COOKIE_SECURE=${SESSION_COOKIE_SECURE}
PROXY_FIX=${PROXY_FIX}
GUNICORN_WORKERS=${GUNICORN_WORKERS}
EOF
  chmod 600 "$INSTALL_DIR/.env"
}

write_compose_override() {
  log "写入 docker-compose.override.yml。"
  if [[ "$EXPOSE_MONGODB" == "true" ]]; then
    cat >"$INSTALL_DIR/docker-compose.override.yml" <<EOF
services:
  web:
    env_file:
      - .env
  mongodb:
    ports:
      - "27017:27017"
EOF
  else
    cat >"$INSTALL_DIR/docker-compose.override.yml" <<EOF
services:
  web:
    env_file:
      - .env
EOF
  fi
}

ensure_upload_dirs() {
  mkdir -p \
    "$INSTALL_DIR/uploads/avatars" \
    "$INSTALL_DIR/uploads/posts" \
    "$INSTALL_DIR/uploads/activities" \
    "$INSTALL_DIR/uploads/submissions" \
    "$INSTALL_DIR/uploads/site_assets"
}

compose() {
  docker compose -f "$INSTALL_DIR/docker-compose.yml" -f "$INSTALL_DIR/docker-compose.override.yml" --project-directory "$INSTALL_DIR" "$@"
}

deploy_stack() {
  log "构建并启动 Docker 服务。"
  compose pull mongodb
  compose up -d --build
}

wait_for_ready() {
  local url="http://127.0.0.1:${HTTP_PORT}/readyz"
  log "等待服务就绪：$url"
  for _ in $(seq 1 60); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "服务已就绪。"
      return
    fi
    sleep 2
  done
  compose ps || true
  compose logs --tail=120 web || true
  die "服务未在预期时间内就绪，请查看上方日志。"
}

maybe_run_smoke_test() {
  if [[ "$RUN_SMOKE_TEST" != "true" ]]; then
    return
  fi
  log "运行宿主机 smoke test。"
  python3 -m venv "$INSTALL_DIR/.smoke-venv"
  "$INSTALL_DIR/.smoke-venv/bin/python" -m pip install --upgrade pip
  "$INSTALL_DIR/.smoke-venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"
  (cd "$INSTALL_DIR" && "$INSTALL_DIR/.smoke-venv/bin/python" scripts/smoke_test.py)
}

print_summary() {
  local port_suffix=""
  if [[ "$HTTP_PORT" != "80" ]]; then
    port_suffix=":${HTTP_PORT}"
  fi
  cat <<EOF

部署完成。

访问地址：
  http://SERVER_IP${port_suffix}
  http://127.0.0.1${port_suffix}

后台入口：
  http://SERVER_IP${port_suffix}/admin

查看初始 super_admin：
  cd ${INSTALL_DIR}
  docker compose -f docker-compose.yml -f docker-compose.override.yml logs web | grep 'super_admin'

常用命令：
  cd ${INSTALL_DIR}
  docker compose -f docker-compose.yml -f docker-compose.override.yml ps
  docker compose -f docker-compose.yml -f docker-compose.override.yml logs -f web
  docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --build

重要提醒：
  1. 首次登录后立即修改 super_admin 初始密码。
  2. 生产公网部署请配置 HTTPS，并重新运行脚本时加 --secure-cookie --proxy-fix。
  3. 请定期备份 Docker volume 中的 MongoDB 数据和 ${INSTALL_DIR}/uploads。
EOF
}

main() {
  validate_options
  require_ubuntu
  require_root
  confirm_deploy
  install_base_packages
  install_docker
  prepare_app_dir
  ensure_upload_dirs
  write_env_file
  write_compose_override
  deploy_stack
  wait_for_ready
  maybe_run_smoke_test
  print_summary
}

if [[ "${MUXI_SETUP_SOURCE_ONLY:-false}" != "true" ]]; then
  main "$@"
fi
