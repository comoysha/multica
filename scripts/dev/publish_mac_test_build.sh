#!/usr/bin/env bash
# 给当前 worktree 发一个 Mac 桌面分支测试包：包连的是本机 worktree 的测试台，
# 从内部安装页直接下载 dmg 安装（电脑浏览器点一下），与正式版并存、数据目录分开。
#
# 用法（都在 worktree 内执行）：
#   scripts/dev/publish_mac_test_build.sh             发包（同一提交已发过则跳过，--force 强制重发）
#   scripts/dev/publish_mac_test_build.sh --needed    本分支改动是否涉及桌面端或其接口（退出码 0=是 1=否）
#   scripts/dev/publish_mac_test_build.sh --remove [<worktree 目录>]   下架（worktree 收尾时由底座的 remove.sh 自动调用）
#   scripts/dev/publish_mac_test_build.sh --install-url   只打印安装页地址（只读，取不到则失败）
#
# 打 dmg、发布到安装页、把测试台端口用 HTTPS 暴露到 Tailscale，都由共用底座的安装页工具
# ~/dev-platform/services/install-hub/hub.py 完成（不属于本仓库，可用 MULTICA_MAC_HUB 指到别处）。
# 本脚本只负责：找到本 worktree 测试台的端口、确认依赖已安装、
# 把本机测试台地址和专用凭据写入本次构建，互不影响其他分支包和正式版。
#
# 退出码：0 成功；3 本机没有发包工具（跳过，不算失败）；其余为失败。
set -euo pipefail

export TZ="Asia/Shanghai"
export CI=1

SLOT_PREFIX="multica-mac"
# 分支包换 appId / 显示名：和正式版并排装，数据目录（userData）也分开。
TEST_BUNDLE_ID="cn.comosha.multica.desktop.staging"
TEST_DISPLAY_NAME="Multica·分支"
# 改到这些路径才算「涉及桌面端或其接口」。桌面端与 views/ui/core 共享组件和 schema、
# 直连 Go 后端；按路径判断是轻量近似，可用 MULTICA_MAC_TEST_BUILD=1 强制发。
MAC_PATH_PATTERN='^(apps/desktop/|server/|packages/core/|packages/views/|packages/ui/|scripts/dev/(publish_mac_test_build\.sh|testbench_auth\.py|dp_env\.sh)$|scripts/dev-env\.sh$)'

# 安装页地址、某个包在不在，一律问 hub.py 的只读命令（base-url / has），不读它旁边的文件。
HUB="${MULTICA_MAC_HUB:-${HOME}/dev-platform/services/install-hub/hub.py}"

MODE="publish"
FORCE=0
TARGET_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --needed) MODE="needed"; shift ;;
    --remove) MODE="remove"; shift ;;
    --install-url) MODE="install-url"; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    -*) echo "mac-test-build: 未知参数 $1" >&2; exit 2 ;;
    *)
      [[ "${MODE}" == "remove" && -z "${TARGET_DIR}" ]] || { echo "mac-test-build: 多余的参数 $1" >&2; exit 2; }
      TARGET_DIR="$1"; shift ;;
  esac
done

err() { echo "mac-test-build: $*" >&2; }
log() { printf '[%s] mac-test-build: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"; }

if [[ -n "${TARGET_DIR}" ]]; then
  WT_ROOT="$(cd "${TARGET_DIR}" && pwd -P)"
else
  WT_ROOT="${DP_REAL_ROOT:-}"
  if [[ -z "${WT_ROOT}" ]]; then
    WT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { err "不在 git 仓库中"; exit 1; }
  fi
  WT_ROOT="$(cd "${WT_ROOT}" && pwd -P)"
fi
DESKTOP_DIR="${WT_ROOT}/apps/desktop"
MANIFEST="${WT_ROOT}/.dev-platform-workspace.json"
STATE_FILE="${WT_ROOT}/data/mac-test-build.json"
# 分支包自己的 userData 目录（app 名 = productName = TEST_DISPLAY_NAME）。
VARIANT_CONFIG="${HOME}/Library/Application Support/${TEST_DISPLAY_NAME}/runtime-config.json"
VARIANT_UPDATER_PREFS="${HOME}/Library/Application Support/${TEST_DISPLAY_NAME}/updater-preferences.json"

# slug 只认身份证（建 worktree 时底座写进去的），不另算。
SLUG="${DP_SLUG:-}"
if [[ -z "${SLUG}" && -f "${MANIFEST}" ]]; then
  SLUG="$(jq -r '.slug // empty' "${MANIFEST}" 2>/dev/null || true)"
fi
if [[ -z "${SLUG}" ]]; then
  SLUG="$(git -C "${WT_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null | tr '[:upper:]_/' '[:lower:]---' || true)"
fi
SLOT="${SLOT_PREFIX}-${SLUG}"

hub_available() { [[ -x "${HUB}" ]]; }

hub_has_slot() {
  local rc=0
  "${HUB}" has "${SLOT}" >/dev/null || rc=$?
  return "${rc}"
}

hub_install_url() {
  local url
  url="$("${HUB}" base-url)" || { err "向 ${HUB} 查安装页地址失败。"; return 1; }
  url="${url%/}"
  [[ -n "${url}" ]] || { err "${HUB} 返回的安装页地址是空的。"; return 1; }
  printf '%s\n' "${url}"
}

# ---------------------------------------------------------------- --needed

mode_needed() {
  case "${MULTICA_MAC_TEST_BUILD:-auto}" in
    1) exit 0 ;;
    0) exit 1 ;;
  esac
  local base_ref="${MULTICA_LAND_BASE_REF:-origin/main}" base
  base="$(git -C "${WT_ROOT}" merge-base "${base_ref}" HEAD 2>/dev/null)" || exit 1
  if git -C "${WT_ROOT}" diff --no-renames --name-only "${base}" HEAD | grep -Eq "${MAC_PATH_PATTERN}"; then
    exit 0
  fi
  exit 1
}

# ---------------------------------------------------------------- --remove

mode_remove() {
  hub_available || exit 0
  # 没发过包就没有东西可下架；hub 对不存在的名字会报错，这里先问清楚。查询出错要说出来，不能当成「没发过」。
  local rc=0
  hub_has_slot || rc=$?
  case "${rc}" in
    0) ;;
    1) rm -f "${STATE_FILE}"; exit 0 ;;
    *) err "向安装页查询 ${SLOT} 是否已发布失败，没有下架。"; exit 1 ;;
  esac
  "${HUB}" remove "${SLOT}"
  rm -f "${STATE_FILE}"
  # 测试台随 worktree 一起收掉，分支包里指向它的配置一并删掉：
  # 留着的话已装的分支包会去连一个不在了的地址；删掉后它回落到 ~/.multica/desktop.json。
  rm -f "${VARIANT_CONFIG}"
  log "已下架 Mac 测试包 ${SLOT}"
}

# ---------------------------------------------------------------- 发包

# 本 worktree 测试台的 API 端口，与 iOS 脚本同一套发现逻辑：
# 底座登记的 DP_PORT 优先；否则按 DIR 匹配 ~/.multica/dev/envs/*/manifest.env 里的 BACKEND_PORT。
discover_api_port() {
  if [[ -n "${MULTICA_TEST_API_PORT:-}" ]]; then
    printf '%s\n' "${MULTICA_TEST_API_PORT}"
    return 0
  fi
  if [[ -n "${DP_PORT:-}" ]]; then
    printf '%s\n' "${DP_PORT}"
    return 0
  fi
  local envs_dir="${MULTICA_DEV_HOME:-${HOME}/.multica/dev}/envs" d dir
  for d in "${envs_dir}"/*/; do
    [[ -f "${d}manifest.env" ]] || continue
    dir="$(sed -n 's/^DIR=//p' "${d}manifest.env" | head -1)"
    [[ -n "${dir}" ]] || continue
    if [[ "$(cd "${dir}" 2>/dev/null && pwd -P)" == "${WT_ROOT}" ]]; then
      sed -n 's/^BACKEND_PORT=//p' "${d}manifest.env" | head -1
      return 0
    fi
  done
  return 1
}

# 桌面 App 与测试台运行在同一台 Mac 上，直连后端可避开本机代理的
# Tailscale DNS 劫持。安装页仍使用上面的公开端口供浏览器下载和自检。
discover_local_backend_port() {
  local envs_dir="${MULTICA_DEV_HOME:-${HOME}/.multica/dev}/envs" d dir
  for d in "${envs_dir}"/*/; do
    [[ -f "${d}manifest.env" ]] || continue
    dir="$(sed -n 's/^DIR=//p' "${d}manifest.env" | head -1)"
    [[ -n "${dir}" ]] || continue
    if [[ "$(cd "${dir}" 2>/dev/null && pwd -P)" == "${WT_ROOT}" ]]; then
      sed -n 's/^BACKEND_PORT=//p' "${d}manifest.env" | head -1
      return 0
    fi
  done
  return 1
}

# 测试台必须已经起好、能正常响应。本脚本不代为启动：
# 人工核查用的测试台由合并闸的 gate.py prepare 按 testbench.up（make up）起好。
require_testbench() {
  local port="$1"
  if ! curl --noproxy '*' --fail --silent --max-time 5 "http://127.0.0.1:${port}/health" >/dev/null; then
    err "本 worktree 的测试台没有在正常响应（http://127.0.0.1:${port}/health 不通）。"
    err "先起测试台：make up，或让 AI 重新「准备验收」。"
    return 1
  fi
}

ensure_deps() {
  if [[ ! -d "${WT_ROOT}/node_modules" ]]; then
    log "首次发包，先安装依赖（pnpm install）…"
    (cd "${WT_ROOT}" && npx -y pnpm@10.28.2 install --frozen-lockfile)
  fi
}

# 同一个提交、同一个测试台地址已经发过，且安装页上还在 → 不重复打包。
already_published() {
  [[ "${FORCE}" != "1" && -f "${STATE_FILE}" ]] || return 1
  hub_has_slot || return 1
  [[ -z "$(git -C "${WT_ROOT}" status --porcelain=v1 -- . ':(exclude)data/mac-test-build.json' 2>/dev/null)" ]] || return 1
  [[ "$(jq -r '.sha // empty' "${STATE_FILE}")" == "$(git -C "${WT_ROOT}" rev-parse HEAD)" ]] || return 1
  [[ "$(jq -r '.server // empty' "${STATE_FILE}")" == "${SERVER_URL}" && "$(jq -r '.auth_fingerprint // empty' "${STATE_FILE}")" == "${AUTH_FINGERPRINT}" && "$(jq -r '.local_api // empty' "${STATE_FILE}")" == "${LOCAL_API_URL}" && "$(jq -r '.local_app // empty' "${STATE_FILE}")" == "${LOCAL_APP_URL}" ]]
}

# Keep automatic updates disabled for branch packages.
write_variant_config() {
  mkdir -p "$(dirname "${VARIANT_CONFIG}")"
  # A branch package must stay on the exact build under review. The shipped
  # updater UI remains available, but automatic GitHub updates stay off here.
  jq -n '{automaticUpdates:false}' >"${VARIANT_UPDATER_PREFS}"
}

print_summary() {
  cat <<EOF

  Mac 测试包已就绪
    安装页:   ${INSTALL_URL}/  （电脑浏览器打开，点一下直接下载 dmg）
    点这个包: 「分支测试包」里的「${PACKAGE_NAME}」
    装好后:   启动台里叫「${TEST_DISPLAY_NAME}」，与正式版并存、数据分开
    它连的是: ${LOCAL_API_URL}  （本机 worktree 的测试台）
EOF
}

mode_publish() {
  if [[ ! -d "${DESKTOP_DIR}" ]]; then
    err "找不到 ${DESKTOP_DIR}，这不是 multica 仓库。"
    exit 1
  fi
  hub_available || { err "本机没有发包工具 ${HUB}，跳过 Mac 测试包。"; exit 3; }
  if [[ ! "${SLOT}" =~ ^[a-z0-9][a-z0-9-]{0,60}$ ]]; then
    err "测试包名字 ${SLOT} 不合安装页的规则（小写字母、数字、连字符，最长 61 个字符）。"
    exit 1
  fi

  local api_port backend_port
  api_port="$(discover_api_port)" || { err "找不到本 worktree 的测试台端口（先 make up / make worktree-env）。"; err "也可以显式给 MULTICA_TEST_API_PORT=<端口>。"; exit 1; }
  require_testbench "${api_port}" || exit 1
  backend_port="$(discover_local_backend_port)" || { err "找不到本 worktree 的后端端口。"; exit 1; }
  require_testbench "${backend_port}" || exit 1
  export VITE_LOCAL_AUTH_TOKEN
  VITE_LOCAL_AUTH_TOKEN="$(python3 "${WT_ROOT}/scripts/dev/testbench_auth.py" "${backend_port}" "${DESKTOP_DIR}/.env.local" VITE_LOCAL_AUTH_TOKEN "${WT_ROOT}/data/mac-test-auth.json")" || exit 1
  AUTH_FINGERPRINT="$(printf '%s' "${VITE_LOCAL_AUTH_TOKEN}" | shasum -a 256 | cut -d ' ' -f 1)"
  ensure_deps

  INSTALL_URL="$(hub_install_url)" || exit 1
  SERVER_URL="$("${HUB}" url "${api_port}")"
  LOCAL_API_URL="http://127.0.0.1:${backend_port}"
  LOCAL_APP_URL="http://127.0.0.1:${api_port}"
  export VITE_BRANCH_API_URL="${LOCAL_API_URL}" VITE_BRANCH_APP_URL="${LOCAL_APP_URL}"
  PACKAGE_NAME="Multica · ${SLUG} · Mac"
  # 分支包不讲版本号：它是临时的，要认的是「哪个分支、哪个 PR」。PR 号问不到就只写分支名。
  local pr_number branch_label
  pr_number="$(cd "${WT_ROOT}" && gh pr view --json number --jq .number 2>/dev/null || true)"
  branch_label="${SLUG}"
  [[ "${pr_number}" =~ ^[0-9]+$ ]] && branch_label="${SLUG} · PR #${pr_number}"

  if already_published; then
    log "当前提交的测试包已在安装页上，不重复打包（要重打加 --force）。"
  else
    log "打包并发布 ${SLOT}（连 ${SERVER_URL}），Electron 打包要几分钟…"
    "${HUB}" publish \
      --platform mac \
      --slot "${SLOT}" \
      --project "${DESKTOP_DIR}" \
      --port "${api_port}" \
      --bundle-id "${TEST_BUNDLE_ID}" \
      --display-name "${TEST_DISPLAY_NAME}" \
      --label "${branch_label}"
    mkdir -p "$(dirname "${STATE_FILE}")"
    jq -n \
      --arg slot "${SLOT}" --arg name "${PACKAGE_NAME}" --arg server "${SERVER_URL}" \
      --arg api "${LOCAL_API_URL}" --arg app "${LOCAL_APP_URL}" --arg auth "${AUTH_FINGERPRINT}" --arg install "${INSTALL_URL}" --arg sha "$(git -C "${WT_ROOT}" rev-parse HEAD)" \
      --arg at "$(date '+%Y-%m-%d %H:%M:%S %Z')" \
      '{slot:$slot, name:$name, server:$server, install_url:$install, sha:$sha, published_at:$at, auth_fingerprint:$auth, local_api:$api, local_app:$app}' \
      >"${STATE_FILE}"
  fi

  write_variant_config

  # 包发出去了不等于连得上：从对外地址真实请求一次测试台。
  # 本机若开着代理的「假地址」DNS，tailnet 域名在本机会被解析成 198.18.x.x，直接请求必然失败；
  # 这时改用本机真实的 Tailscale 地址再试一次。
  local reach_args=() host ts_ip
  host="$(printf '%s' "${SERVER_URL}" | sed -E 's#^https?://([^:/]+).*#\1#')"
  if [[ "${host}" == *.ts.net ]] && ts_ip="$(tailscale ip -4 2>/dev/null | head -1)" && [[ -n "${ts_ip}" ]]; then
    reach_args=(--resolve "${host}:$(printf '%s' "${SERVER_URL}" | sed -E 's#.*:([0-9]+)$#\1#'):${ts_ip}")
  fi
  if ! curl --noproxy '*' --fail --silent --max-time 10 -o /dev/null "${SERVER_URL}/health" \
     && ! curl --noproxy '*' --fail --silent --max-time 10 -o /dev/null ${reach_args[@]+"${reach_args[@]}"} "${SERVER_URL}/health"; then
    err "包已发布，但对外地址 ${SERVER_URL} 访问不通，装好的分支包会连不上测试台。"
    err "检查本机 Tailscale 是否在线、tailscale serve status 里有没有这个端口。"
    exit 1
  fi
  print_summary
}

mode_install_url() {
  hub_available || { err "本机没有发包工具 ${HUB}。"; exit 3; }
  hub_install_url || exit 1
}

case "${MODE}" in
  needed) mode_needed ;;
  install-url) mode_install_url ;;
  remove) mode_remove ;;
  publish) mode_publish ;;
esac
