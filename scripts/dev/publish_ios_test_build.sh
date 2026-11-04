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
# M4 核验当前测试台并准备限定构建输入，M2 按已提交配方生成原生工程、编译和签名。
# 安装页只核验回传 IPA/远端证明后发布，不在本机预构建或编译。
# API 和副本专用凭据按同一输入写入 JS；不传数据库、工作目录身份证或整个 .env。
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
IOS_PATH_PATTERN='^(apps/mobile/|server/|packages/core/|scripts/dev/(publish_ios_test_build\.sh|client_adapter\.py|ios_metro\.py|ios_device_build\.py|ios_remote_env\.py|testbench_auth\.py|dp_env\.sh)$|scripts/dev-env\.sh$)'

# 安装页地址、某个包在不在，一律问 hub.py 的只读命令（base-url / has），不读它旁边的文件：
# 配置和安装页数据不在程序旁边，那样读会得到空地址、下架会悄悄什么都不做。
PLATFORM="${DEV_PLATFORM_ROOT:-${HOME}/dev-platform}"
HUB="${MULTICA_IOS_HUB:-${PLATFORM}/services/install-hub/hub.py}"

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
prepare_client_contract() {
  CLIENT_TOOL="${MULTICA_CLIENT_TOOL:-${PLATFORM}/tools/client/contract.py}"
  CLIENT_CONTRACT_FILE="${WT_ROOT}/data/client-contract-ios.json"
  mkdir -p "${WT_ROOT}/data"
  python3 "${WT_ROOT}/scripts/dev/client_adapter.py" verify >"${CLIENT_CONTRACT_FILE}.tmp" || return 1
  mv "${CLIENT_CONTRACT_FILE}.tmp" "${CLIENT_CONTRACT_FILE}"
}

# 同一个提交、同一个测试台地址已经发过，且安装页上还在 → 不重复打包（打一次要几十分钟）。
already_published() {
  [[ "${FORCE}" != "1" && -f "${STATE_FILE}" ]] || return 1
  hub_has_slot || return 1
  [[ -z "$(git -C "${WT_ROOT}" status --porcelain=v1 -- . ':(exclude)apps/mobile/ios' ':(exclude)data/ios-test-build.json' 2>/dev/null)" ]] || return 1
  [[ "$(jq -r '.sha // empty' "${STATE_FILE}")" == "$(git -C "${WT_ROOT}" rev-parse HEAD)" ]] || return 1
  [[ "$(jq -r '.server // empty' "${STATE_FILE}")" == "${SERVER_URL}" && "$(jq -r '.auth_fingerprint // empty' "${STATE_FILE}")" == "${AUTH_FINGERPRINT}" ]]
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

  local identity_slug
  identity_slug="$(jq -r '.slug // empty' "${MANIFEST}")"
  [[ -n "${identity_slug}" && "${SLUG}" == "${identity_slug}" ]] || { err "DP_SLUG 与当前工作目录身份证不一致。"; exit 1; }
  local api_port backend_port
  prepare_client_contract || exit 1
  api_port="$(jq -r '.worktree_port' "${CLIENT_CONTRACT_FILE}")"
  backend_port="$(jq -r '.services.backend.port' "${CLIENT_CONTRACT_FILE}")"
  if [[ -n "${MULTICA_TEST_API_PORT:-}" && "${MULTICA_TEST_API_PORT}" != "${api_port}" ]]; then
    err "MULTICA_TEST_API_PORT differs from the verified worktree; refusing to publish."
    exit 1
  fi
  export EXPO_PUBLIC_LOCAL_AUTH_TOKEN
  EXPO_PUBLIC_LOCAL_AUTH_TOKEN="$(python3 "${WT_ROOT}/scripts/dev/testbench_auth.py" "${backend_port}" "${MOBILE_DIR}/.env.local" EXPO_PUBLIC_LOCAL_AUTH_TOKEN "${WT_ROOT}/data/ios-test-auth.json")" || exit 1
  AUTH_FINGERPRINT="$(printf '%s' "${EXPO_PUBLIC_LOCAL_AUTH_TOKEN}" | shasum -a 256 | cut -d ' ' -f 1)"
  INSTALL_URL="$(hub_install_url)" || exit 1
  SERVER_URL="$(jq -r '.public_url' "${CLIENT_CONTRACT_FILE}")"
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
    log "派发 M2 打包 ${SLOT}（连 ${SERVER_URL}），本机不执行原生编译…"
    local remote_result artifact remote_receipt
    remote_result="$(python3 "${WT_ROOT}/scripts/dev/ios_device_build.py" --recipe ios-branch-ipa --json)"
    artifact="$(jq -r '.artifacts.ipa' <<<"${remote_result}")"
    remote_receipt="$(jq -r '.result_file' <<<"${remote_result}")"
    [[ "$(jq -r '.api_url' <<<"${remote_result}")" == "${SERVER_URL}" && "$(jq -r '.auth_fingerprint' <<<"${remote_result}")" == "${AUTH_FINGERPRINT}" ]] || {
      err "远端构建结果与本次测试台或副本凭据不一致。"; exit 1;
    }
    "${HUB}" publish \
      --slot "${SLOT}" \
      --name "${PACKAGE_NAME}" \
      --label "${branch_label}" \
      --project "${WT_ROOT}" \
      --scheme MulticaStaging \
      --port "${api_port}" \
      --bundle-id "${TEST_BUNDLE_ID}" \
      --display-name "${TEST_DISPLAY_NAME}" \
      --artifact "${artifact}" \
      --remote-receipt "${remote_receipt}"
    mkdir -p "$(dirname "${STATE_FILE}")"
    jq -n \
      --arg slot "${SLOT}" --arg name "${PACKAGE_NAME}" --arg server "${SERVER_URL}" \
      --arg auth "${AUTH_FINGERPRINT}" --arg install "${INSTALL_URL}" --arg sha "$(git -C "${WT_ROOT}" rev-parse HEAD)" \
      --arg at "$(date '+%Y-%m-%d %H:%M:%S %Z')" \
      '{slot:$slot, name:$name, server:$server, install_url:$install, sha:$sha, published_at:$at, auth_fingerprint:$auth}' \
      >"${STATE_FILE}"
  fi

  python3 "${CLIENT_TOOL}" verify-artifact --project "${WT_ROOT}" \
    --platform ios --contract "${CLIENT_CONTRACT_FILE}" \
    --evidence "${WT_ROOT}/data/client-artifact-ios.json" || exit 1
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
