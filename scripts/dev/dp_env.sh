#!/usr/bin/env bash
# 共用底座（~/dev-platform）调用的工作目录环境脚本：每个工作目录一套独立的测试台。
#
# 用法（底座按 dev-platform.json 调用）：
#   scripts/dev/dp_env.sh setup      新工作目录：写 .env.worktree（独立数据库端口与 docker 项目名）、装依赖
#   scripts/dev/dp_env.sh up         起本工作目录的测试台（独立 Postgres 容器 + make up）
#   scripts/dev/dp_env.sh down       停本工作目录的测试台进程（数据保留）
#   scripts/dev/dp_env.sh restore    数据保留、重新起测试台
#   scripts/dev/dp_env.sh teardown [<工作目录>]   拆工作目录前：停进程、删环境登记、删数据库容器和数据卷
#
# 为什么要隔离：本机还跑着一套正在用的 Multica（docker 项目名 multica，容器 multica-postgres-1 /
# multica-backend-1 / multica-frontend-1，Postgres 占 5432）。仓库自带的 docker-compose.yml 项目名也是
# multica、端口也是 5432，不隔离的话测试台会直接操作那套正在用的数据库。
# 这里给每个工作目录一个自己的 docker 项目名（multica-dp-<slug>）和自己的 Postgres 端口（底座分的 DP_PORT），
# 写进 .env.worktree；Makefile 会把它们导出给 docker compose，所以 make up / make down 只碰本工作目录那套。
set -euo pipefail

# 正在用的那套 Multica，任何情况下都不碰。
LIVE_PROJECT="multica"
LIVE_PG_PORT=5432

err() { echo "dp_env: $*" >&2; }
log() { echo "dp_env: $*"; }

CMD="${1:-}"; shift || true
ROOT="${1:-${DP_REAL_ROOT:-}}"
[[ -n "${ROOT}" ]] || ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { err "不在 git 仓库中"; exit 1; }
ROOT="$(cd "${ROOT}" && pwd -P)"
cd "${ROOT}"
ENV_FILE="${ROOT}/.env.worktree"

env_get() { [[ -f "${ENV_FILE}" ]] && sed -n "s/^$1=//p" "${ENV_FILE}" | tail -1; }

env_set() {
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  if grep -q "^${key}=" "${ENV_FILE}"; then
    sed "s|^${key}=.*|${key}=${val}|" "${ENV_FILE}" >"${tmp}"
  else
    cat "${ENV_FILE}" >"${tmp}"; printf '%s=%s\n' "${key}" "${val}" >>"${tmp}"
  fi
  mv "${tmp}" "${ENV_FILE}"
}

slug() {
  local s="${DP_SLUG:-}"
  [[ -n "${s}" ]] || s="$(jq -r '.slug // empty' "${ROOT}/.dev-platform-workspace.json" 2>/dev/null || true)"
  [[ -n "${s}" ]] || s="$(git rev-parse --abbrev-ref HEAD | sed 's#.*/##')"
  printf '%s' "${s}" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g; s/--*/-/g; s/^-//; s/-$//'
}

# 本工作目录的 docker 项目名和 Postgres 端口都必须和正在用的那套不同，否则拒绝往下走。
guard() {
  local project port
  project="$(env_get COMPOSE_PROJECT_NAME)"
  port="$(env_get POSTGRES_PORT)"
  if [[ -z "${project}" || "${project}" == "${LIVE_PROJECT}" || -z "${port}" || "${port}" == "${LIVE_PG_PORT}" ]]; then
    err "本工作目录的 .env.worktree 没有隔离（docker 项目名=${project:-空}，数据库端口=${port:-空}），"
    err "会碰到正在用的 Multica，已停下。先运行 scripts/dev/dp_env.sh setup。"
    exit 1
  fi
  export COMPOSE_PROJECT_NAME="${project}" POSTGRES_PORT="${port}"
}

cmd_setup() {
  local port="${DP_PORT:-}" project db
  [[ -n "${port}" ]] || { err "底座没有给端口（DP_PORT），无法隔离数据库。"; exit 1; }
  [[ "${port}" != "${LIVE_PG_PORT}" ]] || { err "分到的端口和正在用的 Multica 撞了：${port}"; exit 1; }
  project="multica-dp-$(slug)"
  [[ -f "${ENV_FILE}" ]] || bash scripts/init-worktree-env.sh "${ENV_FILE}" >/dev/null
  db="$(env_get POSTGRES_DB)"
  env_set COMPOSE_PROJECT_NAME "${project}"
  env_set POSTGRES_PORT "${port}"
  env_set DATABASE_URL "postgres://multica:multica@localhost:${port}/${db}?sslmode=disable"
  log "已隔离：docker 项目 ${project}，数据库端口 ${port}，库名 ${db}"
  if [[ ! -d "${ROOT}/node_modules" ]]; then
    log "安装依赖…"
    npx -y pnpm@10.28.2 install --frozen-lockfile
  fi
}

cmd_up() {
  guard
  # 先把本工作目录自己的 Postgres 起好、能连上，再交给 make up；
  # make up 只会在端口上没人应答时才去起容器，这样它连的一定是这个。
  docker compose up -d postgres
  local i
  for i in $(seq 1 60); do
    docker compose exec -T postgres pg_isready -U multica -d postgres >/dev/null 2>&1 && break
    sleep 1
  done
  make up
}

cmd_down() {
  [[ -f "${ENV_FILE}" ]] || exit 0
  guard
  make down || true
}

cmd_teardown() {
  [[ -f "${ENV_FILE}" ]] || { scripts/dev/publish_ios_test_build.sh --remove "${ROOT}" || true; exit 0; }
  guard
  make down || true
  bash scripts/dev-env.sh destroy --yes || true
  docker compose down -v --remove-orphans || true
  scripts/dev/publish_ios_test_build.sh --remove "${ROOT}" || true
  log "已拆掉测试台：docker 项目 ${COMPOSE_PROJECT_NAME}（含数据卷）"
}

case "${CMD}" in
  setup) cmd_setup ;;
  up|restore) cmd_up ;;
  down) cmd_down ;;
  teardown) cmd_teardown ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
