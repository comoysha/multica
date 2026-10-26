#!/usr/bin/env bash
# 共用底座（~/dev-platform）调用的工作目录环境脚本：每个工作目录一套独立的测试台。
#
# 用法（底座按 dev-platform.json 调用）：
#   scripts/dev/dp_env.sh setup      新工作目录：刷新正式库快照、软链共享文件、写 .env.worktree、装依赖
#   scripts/dev/dp_env.sh up         起本工作目录的测试台（副本库第一次从快照克隆，之后保留）
#   scripts/dev/dp_env.sh down       停本工作目录的测试台进程（数据保留）
#   scripts/dev/dp_env.sh restore    把副本库重置成快照的样子，再起测试台
#   scripts/dev/dp_env.sh teardown [<工作目录>]   拆工作目录前：停进程、删环境登记、删副本库容器和数据卷、下架分支测试包
#   scripts/dev/dp_env.sh --check [<工作目录>]    只自检，不改动
#
# 测试数据 = 正式库的副本：「正式库」是本机在用的 Multica 库（容器 multica-postgres-1 里的 multica）。
# setup 用 pg_dump 只读导出一份快照到主库的 data/golden/multica.dump（导不出来就沿用旧快照），
# 每个工作目录软链这份快照，在自己的容器 / 端口里克隆出副本库。正式库只读，从不写。
#
# 软链：data/golden 和主库里的本地配置（apps/mobile/.env.*.local）从主库软链过来，改一处全部生效。
# 不软链：node_modules（构建工具不认跨根软链）、.env.worktree（本工作目录的副本库配置，软链会让两个
# 工作目录写同一个库）。根目录的 .env 也不软链：Makefile 见到 .env 就不读 .env.worktree，隔离会失效。
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

LIVE_CONTAINER="multica-postgres-1"
LIVE_DB="multica"

CMD="${1:-}"; shift || true
ROOT="${1:-${DP_REAL_ROOT:-}}"
[[ -n "${ROOT}" ]] || ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { err "不在 git 仓库中"; exit 1; }
ROOT="$(cd "${ROOT}" && pwd -P)"
cd "${ROOT}"
ENV_FILE="${ROOT}/.env.worktree"
MAIN_ROOT="$(dirname "$(cd "${ROOT}" && cd "$(git rev-parse --git-common-dir)" && pwd -P)")"
GOLDEN_DIR="${MAIN_ROOT}/data/golden"
GOLDEN="${GOLDEN_DIR}/multica.dump"
SHARED_LOCAL_FILES="apps/mobile/.env.development.local apps/mobile/.env.staging.local apps/mobile/.env.production.local"

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
  if [[ -e "${ROOT}/.env" ]]; then
    err "工作目录里有 .env，Makefile 会用它而不是 .env.worktree，隔离会失效，已停下。"
    exit 1
  fi
  if [[ -z "${project}" || "${project}" == "${LIVE_PROJECT}" || -z "${port}" || "${port}" == "${LIVE_PG_PORT}" ]]; then
    err "本工作目录的 .env.worktree 没有隔离（docker 项目名=${project:-空}，数据库端口=${port:-空}），"
    err "会碰到正在用的 Multica，已停下。先运行 scripts/dev/dp_env.sh setup。"
    exit 1
  fi
  export COMPOSE_PROJECT_NAME="${project}" POSTGRES_PORT="${port}"
}

# 正式库只读导出：pg_dump 不写库。先导到临时文件，成功才替换，导一半不会坏掉旧快照。
refresh_golden() {
  mkdir -p "${GOLDEN_DIR}"
  local tmp="${GOLDEN}.tmp.$$"
  if docker exec "${LIVE_CONTAINER}" pg_dump -U multica -Fc --no-owner "${LIVE_DB}" >"${tmp}" 2>/dev/null && [[ -s "${tmp}" ]]; then
    mv "${tmp}" "${GOLDEN}"
    log "已刷新正式库快照 ${GOLDEN}"
  else
    rm -f "${tmp}"
    [[ -s "${GOLDEN}" ]] || { err "导不出正式库快照，也没有旧快照。"; exit 1; }
    log "导不出正式库快照，沿用旧快照 ${GOLDEN}"
  fi
}

link_exact() {
  local target="$1" link="$2"
  [[ -e "${target}" ]] || return 0
  if [[ -L "${link}" ]]; then
    [[ "$(readlink "${link}")" == "${target}" ]] && return 0
    rm -f "${link}"
  elif [[ -e "${link}" ]]; then
    err "${link} 已存在且不是软链，不覆盖"; exit 1
  fi
  mkdir -p "$(dirname "${link}")"
  ln -s "${target}" "${link}"
  log "软链 ${link} → ${target}"
}

link_shared() {
  local f
  link_exact "${GOLDEN_DIR}" "${ROOT}/data/golden"
  for f in ${SHARED_LOCAL_FILES}; do link_exact "${MAIN_ROOT}/${f}" "${ROOT}/${f}"; done
}

cmd_setup() {
  local port="${DP_PORT:-}" project db
  [[ -n "${port}" ]] || { err "底座没有给端口（DP_PORT），无法隔离数据库。"; exit 1; }
  [[ "${port}" != "${LIVE_PG_PORT}" ]] || { err "分到的端口和正在用的 Multica 撞了：${port}"; exit 1; }
  project="multica-dp-$(slug)"
  [[ "${MAIN_ROOT}" != "${ROOT}" ]] || { err "这是主库，不是工作目录。"; exit 1; }
  refresh_golden
  link_shared
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
  clone_from_golden "${1:-keep}"
  make up
}

pg() { docker compose exec -T postgres "$@"; }

# 副本库：库不存在（或要求重置）时从快照整库克隆；已有就保留，测试中改的数据不丢。
clone_from_golden() {
  local db exists
  db="$(env_get POSTGRES_DB)"
  [[ -n "${db}" && "${db}" != "${LIVE_DB}" ]] || { err "副本库名不对：${db:-空}"; exit 1; }
  [[ -s "${ROOT}/data/golden/multica.dump" ]] || { err "没有快照 data/golden/multica.dump，先跑 setup。"; exit 1; }
  exists="$(pg psql -U multica -d postgres -Atc "select 1 from pg_database where datname='${db}'" || true)"
  if [[ "${exists}" == "1" && "$1" != "reset" ]]; then
    return 0
  fi
  if [[ "${exists}" == "1" ]]; then
    make down >/dev/null 2>&1 || true
    pg psql -U multica -d postgres -qc "DROP DATABASE \"${db}\" WITH (FORCE)"
  fi
  pg psql -U multica -d postgres -qc "CREATE DATABASE \"${db}\""
  pg pg_restore -U multica -d "${db}" --no-owner --no-acl <"${ROOT}/data/golden/multica.dump" \
    || { err "从快照克隆副本库失败"; exit 1; }
  log "已从正式库快照克隆副本库 ${db}"
}

cmd_check() {
  local fail=0 f project port
  ck() { if eval "$2"; then echo "  ok:   $1"; else echo "  fail: $1" >&2; fail=$((fail + 1)); fi; }
  project="$(env_get COMPOSE_PROJECT_NAME)"; port="$(env_get POSTGRES_PORT)"
  ck "不是主库" '[[ "${MAIN_ROOT}" != "${ROOT}" ]]'
  ck "主库有正式库快照" '[[ -s "${GOLDEN}" ]]'
  ck "data/golden 软链到主库" '[[ -L "${ROOT}/data/golden" && "$(readlink "${ROOT}/data/golden")" == "${GOLDEN_DIR}" ]]'
  for f in ${SHARED_LOCAL_FILES}; do
    [[ -e "${MAIN_ROOT}/${f}" ]] && ck "${f} 软链到主库" "[[ -L '${ROOT}/${f}' ]]"
  done
  ck "node_modules 是本目录自己的真实目录" '[[ -d "${ROOT}/node_modules" && ! -L "${ROOT}/node_modules" ]]'
  ck ".env.worktree 是本目录自己的真实文件" '[[ -f "${ENV_FILE}" && ! -L "${ENV_FILE}" ]]'
  ck "没有根目录 .env（否则不读 .env.worktree）" '[[ ! -e "${ROOT}/.env" ]]'
  ck "docker 项目名独立（${project:-空}）" '[[ -n "${project}" && "${project}" != "${LIVE_PROJECT}" ]]'
  ck "数据库端口独立（${port:-空}）" '[[ -n "${port}" && "${port}" != "${LIVE_PG_PORT}" ]]'
  ck "副本库名不是正式库（$(env_get POSTGRES_DB)）" '[[ "$(env_get POSTGRES_DB)" != "${LIVE_DB}" ]]'
  ck "DATABASE_URL 指向本目录的端口" '[[ "$(env_get DATABASE_URL)" == *"localhost:${port}/"* ]]'
  if [[ ${fail} -ne 0 ]]; then echo "dp_env: 自检没通过（${fail} 项）" >&2; return 1; fi
  echo "dp_env: 自检通过"
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
  up) cmd_up ;;
  restore) cmd_up reset ;;
  --check) cmd_check ;;
  down) cmd_down ;;
  teardown) cmd_teardown ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
