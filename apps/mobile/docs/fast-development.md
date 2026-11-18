# 自用手机快速开发

自用 iPhone 上的 Multica Staging 通过 M4 的 Metro 加载 `dev/mobile`。用户先在 GitHub 合入 PR，M4 再同步已合入的版本，用户在 iPhone 上 Reload 验收。

## 固定运行环境

- 固定代码目录：`~/multica-mobile-dev`，分支 `dev/mobile`，不直接在此编辑。
- 用户级 LaunchAgent：`com.multica.mobile-metro`，登录后自动启动并在退出后重启。电脑必须开机、保持网络连接；睡眠或退出登录时不能提供服务。
- 本机私有设置：`~/.config/multica-mobile/config.json`，只保存代码目录、手机可达主机地址、端口和私有环境文件路径。环境内容不进入仓库。
- 若使用 Tailscale Serve，`public_url` 保存专用 HTTPS 入口，Metro 将它写入 manifest 和开发连接地址。代理由 Tailscale 持久管理，不依赖 agent 进程。首次配置前查看 `tailscale serve status`，选择没有被占用的专用端口，不改现有映射、ACL 或全局 DNS；使用 `tailscale serve --bg --https=<专用端口> http://127.0.0.1:8081`，停止此映射用 `tailscale serve --https=<专用端口> off`，不要使用 reset。手机需开启 Tailscale。
- Metro 日志位于固定目录的 `data/logs/com.multica.mobile-metro.{stdout,stderr}.log`。`~/.local/state/multica-mobile/history.jsonl` 记录交付前后的 SHA 和新交付对应的 PR 链接。

首次安装由 agent 执行：在已提交的任务目录运行 `python3 scripts/dev/mobile_fast.py init --env-file <现有私有环境文件绝对路径> --host <手机可达地址>`；在创建的固定目录执行 `npx -y pnpm@10.28.2 install --frozen-lockfile`，再执行 `./launchd_script/install_and_start_service.sh`。不会修改主工作区私有配置或安装页。

`--env-file` 必须是一份完整的自用手机私有配置，包含 `EXPO_PUBLIC_API_URL`、`EXPO_PUBLIC_WEB_URL` 和 `EXPO_PUBLIC_LOCAL_AUTH_TOKEN`；不能直接使用仓库自带的 `.env.staging`。该文件指向官方测试环境，而自用免登录凭据在另一份私有文件中；本服务显式关闭 Expo 自动加载环境文件，因此不会自动合并它们。实际专用文件为 `~/.config/multica-mobile/mobile.env`，权限 0600，由现有自用手机凭据及固定后端地址生成；原始文件保持原样。启动时检查配置完整性，并要求 API、Web 与 Metro 入口使用同一台个人服务器的主机名，可使用不同端口。凭据及完整代码包只在本机或用户自己的 tailnet 使用，不提交或附到任务评论。

## 日常任务

1. AI 从 `dev/mobile` 检出隔离任务目录，在 `feat/mu-n` 上提交、推送，并创建目标为 `dev/mobile` 的 PR。如果工作区开启了合入后改状态，在任务的 PR 区域选择“此任务合并后不改状态”。把 PR 链接和变更说明写回任务，将任务交回用户并保留待办。
2. 用户在 GitHub 审阅并合入 PR。请在合入后让本机智能体继续交付，或使用任务上的合并唤醒。交付前保持 Metro 当前版本。
3. M4 从带有新版交付脚本的隔离目录运行以下命令。首次切换时使用 MU-482 的任务目录，固定目录会在这次同步后获得新版脚本。

```bash
python3 scripts/dev/mobile_fast.py deliver <PR 编号>
```

脚本要求 M4 上的 `gh` 已登录且能读取 `comoysha/multica` 的 PR。它回读 PR 状态和合入提交，从 GitHub 获取 `dev/mobile`，再将干净的固定目录快进到该 PR 的合入提交。多项交付按 GitHub 合入顺序逐项执行，文件锁保证同一时间只有一次同步。Metro 启动日志确认提交且 `/status` 就绪后，脚本才记下交付。同步或启动失败时尝试恢复上一版并报告结果。AI 在任务中回写 PR、已交付的 SHA 和验收步骤。

用户在 iPhone 上 Reload，验收通过后手动标记完成。有问题在原任务继续处理，修复照常走 PR。任务状态保持待办、完成、取消。标记完成不会触发开 PR、合并或打包。日常界面和交互变更沿用已安装的开发客户端。

手机必须安装包含 Expo 开发启动器的 Debug 客户端。显示名称为 Multica Staging 或 Safari 能唤起 App，都不能证明它支持 Metro：Release 包仍可能只执行内置代码。首次安装前，由 agent 将构建产物解压为 `.app`，执行 `python3 scripts/dev/mobile_fast.py verify-client <产物.app的绝对路径>`，核验原生 `EXDevLauncherController` 和 `exp+multica-mobile` URL scheme；缺少任何一项时停止安装，改为构建 Debug 开发客户端。此检查只证明产物具备连接入口，不能替代安装后的手机实测。

通过核验后，使用 `status` 打印的开发链接或在开发启动页输入 Metro 地址。手机与电脑需有相互可达的网络（如同一局域网或 Tailscale）；启用手机本地网络权限。进入 App 后摇手机打开开发菜单并 Reload，或从开发启动页重新打开服务。首次验证须看到专用演示文字并有手机实际连接记录，电脑 HTTP 成功不能替代。

直接点击旧 Staging 图标可能继续打开之前的内置代码或旧开发地址。确认客户端支持 Metro 后，首次切换应通过当前 Metro 网页的“Open”入口或 `status` 打印的开发链接打开，之后才能用刷新加载新代码。如果显示“无法进入，请检查本地服务或更新客户端”，表示当前执行的页面未能完成个人账号初始化；先核验安装产物是否包含开发启动器、实际代码来源，再核验私有 API 地址及凭据的 `/api/me` 响应。不要仅凭 Safari 跳转或 Metro `/status` 成功判断 App 已加载开发代码；未证实客户端不兼容前，不直接重装或清空手机数据。

## 应用设置与数据

右上角的开发工具按钮打开 Expo 菜单，选择 `Reload` 刷新代码。关闭开发菜单后，在底部打开“更多”，点击弹出菜单顶部的头像或用户名进入设置。验证 MU-482 时，合入前设置页最上方显示 `MU-432 · 01`，交付后 Reload 应显示 `MU-482 · 01`。

个人开发客户端连接日常使用的本地 Multica 后端，与电脑共用真实任务数据；Staging 名称不表示隔离测试库。开发流程不自动复制、清空或迁移数据库。验证行为避免创建或删除真实业务记录作为测试夹具。

## 状态、恢复和回退

在任务目录或固定目录执行：

```bash
python3 scripts/dev/mobile_fast.py status
python3 scripts/dev/mobile_fast.py restart
```

回退已交付的改动时，从最新 `dev/mobile` 新建任务分支，提交 revert 并开 PR。用户合入后走同一条交付命令，再让手机 Reload。恢复某次失败的本地同步由交付脚本处理，GitHub 上的合入历史保留。

停止并移除自动启动：固定目录执行 `./launchd_script/uninstall_and_stop_service.sh`；重新启用执行安装脚本。只管理本服务，保留源码与日志。若也使用 Tailscale Serve，按上面的专用端口命令单独停止代理；启用代理也需单独执行对应命令。仅停 Metro 时代理返回服务不可用，不会自动改代理目标。

无法连接时先检查电脑是否在线、手机 VPN/本地网络权限、`status` 和服务日志。地址改变时在私有 config.json 更新 host 后重新安装并启动服务。若客户端缺少原生模块、Expo/RN 版本不符、签名过期，或交付涉及依赖、插件或原生配置，安排在 M2 重新构建、核验并安装开发客户端，再同步已合入的代码。日常 Metro 交付命令会拦截涉及原生或依赖输入的变动。

若回环地址可用但局域网请求超时，检查 macOS 防火墙是否允许**服务实际使用的 Node 可执行文件**接收传入连接。不同路径的 Node 可能有不同规则；不能用不符合 React Native/Metro 引擎要求的旧版本替代。添加规则若需要管理员密码，由用户在系统设置完成或本机执行管理员命令，agent 不索取密码，不关闭防火墙。

## 项目范围与全局指导

Multica 项目从 `~/.config/dev-platform/projects.json` 定向退出；bridge 与 `multica_start.py` 仅匹配登记项目，因此不再为此项目启动旧验收、打包或自动合并流程。其他项目登记、既有安装页及 MU-396、MU-430、MU-431 的状态保持原样。

全局工作区指导仍由公共底座每日同步，不手动修改共享上下文；其适用范围是接入项目，冲突时项目规则优先。本项目的专用例外保存在项目描述和 `dev/mobile` 的 `AGENTS.md`，项目仓库资源固定到 `dev/mobile`，确保后续任务读取同一份规则，避免被全局同步重新拉回旧流程。
