#!/usr/bin/env bash
# 给当前 worktree 发一个 iOS 手机分支测试包：包连的是本 worktree 自己的测试台，
# 手机通过 Tailscale 从内部安装页安装，不插线、不走 TestFlight。
#
# 用法（都在 worktree 内执行）：
#   scripts/dev/publish_ios_test_build.sh             发包（同一提交已发过则跳过，--force 强制重发）
#   scripts/dev/publish_ios_test_build.sh --needed    本分支改动是否涉及手机端或其接口（退出码 0=是 1=否）
#   scripts/dev/publish_ios_test_build.sh --remove [<worktree 目录>]   下架（worktree 收尾时由底座的 remove.sh 自动调用）
#   scripts/dev/publish_ios_test_build.sh --install-url   只打印安装页地址（只读，取不到则失败）
#
# 打包签名、发布到安装页、把测试台端口用 HTTPS 暴露到 Tailscale，都由共用底座的安装页工具
# ~/dev-platform/services/install-hub/hub.py 完成（不属于本仓库，可用 MULTICA_IOS_HUB 指到别处）。
# 本脚本只负责：找到本 worktree 测试台的 API 端口、把 Expo 原生工程准备好、
# 把 EXPO_PUBLIC_API_URL（测试台的 tailnet 地址）烤进 JS bundle，其余交给 hub.py。
#
# 退出码：0 成功；3 本机没有发包工具（跳过，不算失败）；其余为失败。
set -euo pipefail

export TZ="Asia/Shanghai"
export CI=1
# CocoaPods 在非 UTF-8 环境下会崩。
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# 安装页与桌面上用来区分的固定值。测试包用专门的 staging bundle id，才能和正式版并排装。
SLOT_PREFIX="multica"
APP_NAME="Multica"
# 上游的 ai.multica.* 属于 Multica 官方的苹果团队，本机团队签不了，换成自己名下的 id。
TEST_BUNDLE_ID="cn.comosha.multica.staging"
TEST_DISPLAY_NAME="Multica·分支"
# 发测试包用的应用变体：staging 的 bundle id / 图标与正式版区分开，又不碰生产配置。
APP_VARIANT="staging"
# 改到这些路径才算「涉及手机端或其接口」。手机端直连 Go 后端与 core 的 schema，
# 这两处改了行为也会影响手机端；按路径判断是轻量近似，可用 MULTICA_IOS_TEST_BUILD=1 强制发。
IOS_PATH_PATTERN='^(apps/mobile/|server/|packages/core/|scripts/dev/publish_ios_test_build\.sh$)'

# 安装页地址、某个包在不在，一律问 hub.py 的只读命令（base-url / has），不读它旁边的文件：
# 配置和安装页数据不在程序旁边，那样读会得到空地址、下架会悄悄什么都不做。
HUB="${MULTICA_IOS_HUB:-${HOME}/dev-platform/services/install-hub/hub.py}"

MODE="publish"
FORCE=0
TARGET_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --needed) MODE="needed"; shift ;;
    --remove) MODE="remove"; shift ;;
    --install-url) MODE="install-url"; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    -*) echo "ios-test-build: 未知参数 $1" >&2; exit 2 ;;
    *)
      [[ "${MODE}" == "remove" && -z "${TARGET_DIR}" ]] || { echo "ios-test-build: 多余的参数 $1" >&2; exit 2; }
      TARGET_DIR="$1"; shift ;;
  esac
done

err() { echo "ios-test-build: $*" >&2; }
log() { printf '[%s] ios-test-build: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"; }

if [[ -n "${TARGET_DIR}" ]]; then
  WT_ROOT="$(cd "${TARGET_DIR}" && pwd -P)"
else
  WT_ROOT="${DP_REAL_ROOT:-}"
  if [[ -z "${WT_ROOT}" ]]; then
    WT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { err "不在 git 仓库中"; exit 1; }
  fi
  WT_ROOT="$(cd "${WT_ROOT}" && pwd -P)"
fi
MOBILE_DIR="${WT_ROOT}/apps/mobile"
MANIFEST="${WT_ROOT}/.dev-platform-workspace.json"
STATE_FILE="${WT_ROOT}/data/ios-test-build.json"

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
  case "${MULTICA_IOS_TEST_BUILD:-auto}" in
    1) exit 0 ;;
    0) exit 1 ;;
  esac
  local base_ref="${MULTICA_LAND_BASE_REF:-origin/main}" base
  base="$(git -C "${WT_ROOT}" merge-base "${base_ref}" HEAD 2>/dev/null)" || exit 1
  if git -C "${WT_ROOT}" diff --no-renames --name-only "${base}" HEAD | grep -Eq "${IOS_PATH_PATTERN}"; then
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
  log "已下架手机测试包 ${SLOT}"
}

# ---------------------------------------------------------------- 发包

# 本 worktree 测试台的 API 端口。multica 的环境（make up）把自己的端口记在
# ~/.multica/dev/envs/<名字>/manifest.env 里，按 DIR 匹配本 worktree；
# 底座登记的 DP_PORT（如有）优先，可用 MULTICA_TEST_API_PORT 覆盖。
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

# 测试台必须已经起好、能正常响应。本脚本不代为启动：
# 人工核查用的测试台由合并闸的 gate.py prepare 按 testbench.up（make up）起好，
# 另起一个只会让包连到别的东西上。
require_testbench() {
  local port="$1"
  if ! curl --noproxy '*' --fail --silent --max-time 5 "http://127.0.0.1:${port}/health" >/dev/null; then
    err "本 worktree 的测试台没有在正常响应（http://127.0.0.1:${port}/health 不通）。"
    err "先起测试台：make up，或让 AI 重新「准备验收」。"
    return 1
  fi
}

# 原生工程（ios/）是 Expo prebuild 生成的、不进 git。第一次发包前先把工程
# 生成出来并装好 CocoaPods（这两件都很慢）；hub 侧的 --prebuild 只做增量的
# config plugins 重刷（秒级），保证 app.config.ts 的改动每次都生效。
ensure_native_project() {
  if [[ ! -d "${WT_ROOT}/node_modules" ]]; then
    log "首次发包，先安装依赖（pnpm install）…"
    npx -y pnpm@10.28.2 install --frozen-lockfile
  fi
  if [[ ! -d "${MOBILE_DIR}/ios" || ! -d "${MOBILE_DIR}/ios/Pods" ]]; then
    log "首次发包，生成 iOS 原生工程并装 CocoaPods（要几分钟）…"
    (cd "${MOBILE_DIR}" && APP_ENV="${APP_VARIANT}" EXPO_BUNDLE_IDENTIFIER_STAGING="${TEST_BUNDLE_ID}" npx -y pnpm@10.28.2 exec expo prebuild -p ios)
  fi
}

# 同一个提交、同一个测试台地址已经发过，且安装页上还在 → 不重复打包（打一次要几十分钟）。
already_published() {
  [[ "${FORCE}" != "1" && -f "${STATE_FILE}" ]] || return 1
  hub_has_slot || return 1
  [[ -z "$(git -C "${WT_ROOT}" status --porcelain=v1 -- . ':(exclude)apps/mobile/ios' ':(exclude)data/ios-test-build.json' 2>/dev/null)" ]] || return 1
  [[ "$(jq -r '.sha // empty' "${STATE_FILE}")" == "$(git -C "${WT_ROOT}" rev-parse HEAD)" ]] || return 1
  [[ "$(jq -r '.server // empty' "${STATE_FILE}")" == "${SERVER_URL}" ]]
}

print_summary() {
  cat <<EOF

  手机测试包已就绪
    安装页:   ${INSTALL_URL}/  （手机开着 Tailscale，用 Safari 打开）
    点这个包: 「分支测试包」里的「${PACKAGE_NAME}」
    装好后:   桌面图标叫「${TEST_DISPLAY_NAME}」，与正式版并存
    它连的是: ${SERVER_URL}  （本 worktree 的测试台）
EOF
}

mode_publish() {
  if [[ ! -d "${MOBILE_DIR}" ]]; then
    err "找不到 ${MOBILE_DIR}，这不是 multica 仓库。"
    exit 1
  fi
  hub_available || { err "本机没有发包工具 ${HUB}，跳过手机测试包。"; exit 3; }
  if [[ ! "${SLOT}" =~ ^[a-z0-9][a-z0-9-]{0,60}$ ]]; then
    err "测试包名字 ${SLOT} 不合安装页的规则（小写字母、数字、连字符，最长 61 个字符）。"
    exit 1
  fi

  local api_port
  api_port="$(discover_api_port)" || { err "找不到本 worktree 的测试台端口（先 make up / make worktree-env）。"; err "也可以显式给 MULTICA_TEST_API_PORT=<端口>。"; exit 1; }
  require_testbench "${api_port}" || exit 1
  ensure_native_project

  # Expo 工程的 scheme / workspace 名是 prebuild 按 app.config.ts 生成的，不硬编码。
  local workspace scheme
  workspace="$(ls -d "${MOBILE_DIR}"/ios/*.xcworkspace 2>/dev/null | head -1 || true)"
  [[ -n "${workspace}" ]] || { err "prebuild 后没有找到 ios/*.xcworkspace。"; exit 1; }
  scheme="$(basename "${workspace}" .xcworkspace)"

  INSTALL_URL="$(hub_install_url)" || exit 1
  SERVER_URL="$("${HUB}" url "${api_port}")"
  PACKAGE_NAME="${APP_NAME} · ${SLUG}"
  # 分支包不讲版本号：它是临时的，要认的是「哪个分支、哪个 PR」。PR 号问不到就只写分支名。
  local pr_number branch_label
  pr_number="$(cd "${WT_ROOT}" && gh pr view --json number --jq .number 2>/dev/null || true)"
  branch_label="${SLUG}"
  [[ "${pr_number}" =~ ^[0-9]+$ ]] && branch_label="${SLUG} · PR #${pr_number}"

  # EXPO_PUBLIC_* 在打 JS bundle 时（xcodebuild 的 Bundle 阶段起 Metro）内联进包，
  # 从本脚本的环境继承；显式置空 WEB_URL，免得打包时加载的 .env.production 把
  # 菜单里的「复制链接」指向线上。
  export EXPO_PUBLIC_API_URL="${SERVER_URL}"
  export EXPO_PUBLIC_WEB_URL=""

  if already_published; then
    log "当前提交的测试包已在安装页上，不重复打包（要重打加 --force）。"
  else
    log "打包并发布 ${SLOT}（连 ${SERVER_URL}），首次编译 React Native 要几十分钟…"
    "${HUB}" publish \
      --slot "${SLOT}" \
      --name "${PACKAGE_NAME}" \
      --label "${branch_label}" \
      --project "${workspace}" \
      --scheme "${scheme}" \
      --port "${api_port}" \
      --bundle-id "${TEST_BUNDLE_ID}" \
      --display-name "${TEST_DISPLAY_NAME}" \
      --prebuild "APP_ENV=${APP_VARIANT} EXPO_BUNDLE_IDENTIFIER_STAGING=${TEST_BUNDLE_ID} npx -y pnpm@10.28.2 exec expo prebuild -p ios --no-install" \
      --setting "CURRENT_PROJECT_VERSION=$(git -C "${WT_ROOT}" rev-list --count HEAD)"
    mkdir -p "$(dirname "${STATE_FILE}")"
    jq -n \
      --arg slot "${SLOT}" --arg name "${PACKAGE_NAME}" --arg server "${SERVER_URL}" \
      --arg install "${INSTALL_URL}" --arg sha "$(git -C "${WT_ROOT}" rev-parse HEAD)" \
      --arg at "$(date '+%Y-%m-%d %H:%M:%S %Z')" \
      '{slot:$slot, name:$name, server:$server, install_url:$install, sha:$sha, published_at:$at}' \
      >"${STATE_FILE}"
  fi

  # 包发出去了不等于手机连得上：从对外地址真实请求一次测试台。
  # 本机若开着代理的「假地址」DNS，tailnet 域名在本机会被解析成 198.18.x.x，直接请求必然失败；
  # 这时改用本机真实的 Tailscale 地址再试一次（手机不经过本机代理，不受影响）。
  local reach_args=() host ts_ip
  host="$(printf '%s' "${SERVER_URL}" | sed -E 's#^https?://([^:/]+).*#\1#')"
  if [[ "${host}" == *.ts.net ]] && ts_ip="$(tailscale ip -4 2>/dev/null | head -1)" && [[ -n "${ts_ip}" ]]; then
    reach_args=(--resolve "${host}:$(printf '%s' "${SERVER_URL}" | sed -E 's#.*:([0-9]+)$#\1#'):${ts_ip}")
  fi
  if ! curl --noproxy '*' --fail --silent --max-time 10 -o /dev/null "${SERVER_URL}/health" \
     && ! curl --noproxy '*' --fail --silent --max-time 10 -o /dev/null ${reach_args[@]+"${reach_args[@]}"} "${SERVER_URL}/health"; then
    err "包已发布，但对外地址 ${SERVER_URL} 访问不通，手机上会连不上测试台。"
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
