# 自用手机快速开发

MU-432 已获用户 A 方案授权：独立 `dev/mobile` 分支供手机刷新；主线合并仍由用户确认。首次手机连接、登录与演示标记已验证。Multica 使用本项目专用交付规则；公共底座规则继续服务其他已登记项目。

## 固定运行环境

- 固定代码目录：`~/multica-mobile-dev`，分支 `dev/mobile`，不直接在此编辑。
- 用户级 LaunchAgent：`com.multica.mobile-metro`，登录后自动启动并在退出后重启。电脑必须开机、保持网络连接；睡眠或退出登录时不能提供服务。
- 本机私有设置：`~/.config/multica-mobile/config.json`，只保存代码目录、手机可达主机地址、端口和私有环境文件路径。环境内容不进入仓库。
- 若使用 Tailscale Serve，`public_url` 保存专用 HTTPS 入口，Metro 将它写入 manifest 和开发连接地址。代理由 Tailscale 持久管理，不依赖 agent 进程。首次配置前查看 `tailscale serve status`，选择没有被占用的专用端口，不改现有映射、ACL 或全局 DNS；使用 `tailscale serve --bg --https=<专用端口> http://127.0.0.1:8081`，停止此映射用 `tailscale serve --https=<专用端口> off`，不要使用 reset。手机需开启 Tailscale。
- 日志：固定代码目录的 `data/logs/com.multica.mobile-metro.{stdout,stderr}.log`；整合历史：`~/.local/state/multica-mobile/history.jsonl`。

首次安装由 agent 执行：在已提交的任务目录运行 `python3 scripts/dev/mobile_fast.py init --env-file <现有私有环境文件绝对路径> --host <手机可达地址>`；在创建的固定目录执行 `npx -y pnpm@10.28.2 install --frozen-lockfile`，再执行 `./launchd_script/install_and_start_service.sh`。不会修改主工作区私有配置或安装页。

`--env-file` 必须是一份完整的自用手机私有配置，包含 `EXPO_PUBLIC_API_URL`、`EXPO_PUBLIC_WEB_URL` 和 `EXPO_PUBLIC_LOCAL_AUTH_TOKEN`；不能直接使用仓库自带的 `.env.staging`。该文件指向官方测试环境，而自用免登录凭据在另一份私有文件中；本服务显式关闭 Expo 自动加载环境文件，因此不会自动合并它们。实际专用文件为 `~/.config/multica-mobile/mobile.env`，权限 0600，由现有自用手机凭据及固定后端地址生成；原始文件保持原样。启动时检查配置完整性，并要求 API、Web 与 Metro 入口使用同一台个人服务器的主机名，可使用不同端口。凭据及完整代码包只在本机或用户自己的 tailnet 使用，不提交或附到任务评论。

## 日常任务

用户在 Multica 项目创建需求。项目仓库资源固定检出 `dev/mobile`。AI 直接在 Multica 提供的任务隔离 checkout（或该任务已有 worktree）实现，不调用旧底座的 `multica_start.py`、环境初始化或验收闸，不在主目录及持续运行目录直接编辑。使用 `feat/mu-n` 任务分支提交并推送；独立 clone 的提交不在持续目录的 Git 对象库里，因此先取得任务分支，再整合：

```bash
git -C ~/multica-mobile-dev fetch origin feat/mu-n
python3 scripts/dev/mobile_fast.py deliver <提交SHA> [后续提交SHA...]
```

按从旧到新的顺序传入普通提交；不传合并提交，也不自行合并 main。文件锁串行整合；共享目录有未提交修改时停止。整合时暂停 Metro，成功后恢复服务；冲突或失败自动中止整批操作，回到整合前提交，由 AI 在任务目录解决后重试。成功后 AI 推送 `dev/mobile`，在任务写明手机版本及改动，把负责人交回用户且不启动 Agent，保持待办；用户刷新检查后自行标记完成。当前简易工作流只有待办、完成、取消，不新增审核状态。日常不强制测试套件、AI 代码审或分发安装包。

手机必须安装包含 Expo 开发启动器的 Debug 客户端。显示名称为 Multica Staging 或 Safari 能唤起 App，都不能证明它支持 Metro：Release 包仍可能只执行内置代码。首次安装前，由 agent 将构建产物解压为 `.app`，执行 `python3 scripts/dev/mobile_fast.py verify-client <产物.app的绝对路径>`，核验原生 `EXDevLauncherController` 和 `exp+multica-mobile` URL scheme；缺少任何一项时停止安装，改为构建 Debug 开发客户端。此检查只证明产物具备连接入口，不能替代安装后的手机实测。

通过核验后，使用 `status` 打印的开发链接或在开发启动页输入 Metro 地址。手机与电脑需有相互可达的网络（如同一局域网或 Tailscale）；启用手机本地网络权限。进入 App 后摇手机打开开发菜单并 Reload，或从开发启动页重新打开服务。首次验证须看到专用演示文字并有手机实际连接记录，电脑 HTTP 成功不能替代。

直接点击旧 Staging 图标可能继续打开之前的内置代码或旧开发地址。确认客户端支持 Metro 后，首次切换应通过当前 Metro 网页的“Open”入口或 `status` 打印的开发链接打开，之后才能用刷新加载新代码。如果显示“无法进入，请检查本地服务或更新客户端”，表示当前执行的页面未能完成个人账号初始化；先核验安装产物是否包含开发启动器、实际代码来源，再核验私有 API 地址及凭据的 `/api/me` 响应。不要仅凭 Safari 跳转或 Metro `/status` 成功判断 App 已加载开发代码；未证实客户端不兼容前，不直接重装或清空手机数据。

## 应用设置与数据

右上角的开发工具按钮打开 Expo 菜单，`Reload` 用于刷新代码。应用设置的入口是：关闭开发菜单 → 底部“更多（…）”→ 弹出菜单顶部的头像／用户名。演示标记 `MU-432 · 01` 位于设置页最上方。

个人开发客户端连接日常使用的本地 Multica 后端，与电脑共用真实任务数据；Staging 名称不表示隔离测试库。开发流程不自动复制、清空或迁移数据库。验证行为避免创建或删除真实业务记录作为测试夹具。

## 状态、恢复和回退

在任务目录或固定目录执行：

```bash
python3 scripts/dev/mobile_fast.py status
python3 scripts/dev/mobile_fast.py restart
python3 scripts/dev/mobile_fast.py rollback <共享目录里要撤回的提交SHA>
```

回退创建新的 revert 提交，不重置历史；多条回退按从新到旧顺序给出。回退后同样推送 `dev/mobile` 并让手机刷新。再次交付已回退功能时应提交新修改或撤回 revert 提交。不要强推，不回滚用户私有文件。日志记录整合前后 SHA，可用 `git log dev/mobile` 查版本。

停止并移除自动启动：固定目录执行 `./launchd_script/uninstall_and_stop_service.sh`；重新启用执行安装脚本。只管理本服务，保留源码与日志。若也使用 Tailscale Serve，按上面的专用端口命令单独停止代理；启用代理也需单独执行对应命令。仅停 Metro 时代理返回服务不可用，不会自动改代理目标。

无法连接时先检查电脑是否在线、手机 VPN/本地网络权限、`status` 和服务日志。地址改变时在私有 config.json 更新 host 后重新安装/启动服务；不随意更改 tailnet ACL。客户端若缺少原生模块、Expo/RN 版本不符、签名过期，或任务改到依赖、插件、原生配置，需要重新构建安装开发客户端。工具保守拦截这些提交，由 AI 单独安排构建及兼容性核验；不要通过强行整合绕过拦截。普通页面和交互改动使用现有客户端。

若回环地址可用但局域网请求超时，检查 macOS 防火墙是否允许**服务实际使用的 Node 可执行文件**接收传入连接。不同路径的 Node 可能有不同规则；不能用不符合 React Native/Metro 引擎要求的旧版本替代。添加规则若需要管理员密码，由用户在系统设置完成或本机执行管理员命令，agent 不索取密码，不关闭防火墙。

## 项目范围与全局指导

Multica 项目从 `~/.config/dev-platform/projects.json` 定向退出；bridge 与 `multica_start.py` 仅匹配登记项目，因此不再为此项目启动旧验收、打包或自动合并流程。其他项目登记、既有安装页及 MU-396、MU-430、MU-431 的状态保持原样。

全局工作区指导仍由公共底座每日同步，不手动修改共享上下文；其适用范围是接入项目，冲突时项目规则优先。本项目的专用例外保存在项目描述和 `dev/mobile` 的 `AGENTS.md`，项目仓库资源固定到 `dev/mobile`，确保后续任务读取同一份规则，避免被全局同步重新拉回旧流程。
