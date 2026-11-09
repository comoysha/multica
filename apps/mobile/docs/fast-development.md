# 自用手机快速开发

MU-432 已获用户 A 方案授权：独立 `dev/mobile` 分支供手机刷新；主线合并仍由用户确认。本流程首次手机验证完成前，不移除公共底座登记，不宣称迁移已完成。

## 固定运行环境

- 固定代码目录：`~/multica-mobile-dev`，分支 `dev/mobile`，不直接在此编辑。
- 用户级 LaunchAgent：`com.multica.mobile-metro`，登录后自动启动并在退出后重启。电脑必须开机、保持网络连接；睡眠或退出登录时不能提供服务。
- 本机私有设置：`~/.config/multica-mobile/config.json`，只保存代码目录、手机可达主机地址、端口和私有环境文件路径。环境内容不进入仓库。
- 日志：固定代码目录的 `data/logs/com.multica.mobile-metro.{stdout,stderr}.log`；整合历史：`~/.local/state/multica-mobile/history.jsonl`。

首次安装由 agent 执行：在已提交的任务目录运行 `python3 scripts/dev/mobile_fast.py init --env-file <现有私有环境文件绝对路径> --host <手机可达地址>`；在创建的固定目录执行 `pnpm install --frozen-lockfile`，再执行 `./launchd_script/install_and_start_service.sh`。不会修改主工作区私有配置或安装页。

## 日常任务

用户在 Multica 项目创建需求。AI 在任务目录实现并提交，推送任务分支，再执行：

```bash
python3 scripts/dev/mobile_fast.py deliver <提交SHA> [后续提交SHA...]
```

按从旧到新的顺序传入普通提交；不传合并提交，也不自行合并 main。文件锁串行整合；共享目录有未提交修改时停止。整合时暂停 Metro，成功后恢复服务；冲突或失败自动中止整批操作，回到整合前提交，由 AI 在任务目录解决后重试。成功后 AI 推送 `dev/mobile`，在任务写明手机版本及改动，把任务交给用户 In Review；用户刷新检查后自行标记 Done。日常不强制测试套件、AI 代码审或分发安装包。

手机打开 Multica Staging 开发客户端，使用 `status` 打印的开发链接或在开发启动页输入 Metro 地址。手机与电脑需有相互可达的网络（如同一局域网或 Tailscale）；启用手机本地网络权限。进入 App 后摇手机打开开发菜单并 Reload，或从开发启动页重新打开服务。首次验证须看到专用演示文字并有手机实际连接记录，电脑 HTTP 成功不能替代。

## 状态、恢复和回退

在任务目录或固定目录执行：

```bash
python3 scripts/dev/mobile_fast.py status
python3 scripts/dev/mobile_fast.py restart
python3 scripts/dev/mobile_fast.py rollback <共享目录里要撤回的提交SHA>
```

回退创建新的 revert 提交，不重置历史；多条回退按从新到旧顺序给出。回退后同样推送 `dev/mobile` 并让手机刷新。再次交付已回退功能时应提交新修改或撤回 revert 提交。不要强推，不回滚用户私有文件。日志记录整合前后 SHA，可用 `git log dev/mobile` 查版本。

停止并移除自动启动：固定目录执行 `./launchd_script/uninstall_and_stop_service.sh`；重新启用执行安装脚本。只管理本服务，保留源码与日志。

无法连接时先检查电脑是否在线、手机 VPN/本地网络权限、`status` 和服务日志。地址改变时在私有 config.json 更新 host 后重新安装/启动服务；不随意更改 tailnet ACL。客户端若缺少原生模块、Expo/RN 版本不符、签名过期，或任务改到依赖、插件、原生配置，需要重新构建安装开发客户端。工具保守拦截这些提交，由 AI 单独安排构建及兼容性核验；不要通过强行整合绕过拦截。普通页面和交互改动使用现有客户端。

## 迁移完成条件

完成一次手机实测后，才定向移除 `~/.config/dev-platform/projects.json` 中 Multica 的登记，核查 bridge、自动启动规则、项目资源和仓库指导。仅 Multica 使用上述专用流程；其他项目规则和安装页保持原样。当前全局工作区指导仍由公共底座管理，必须补上 Multica 的例外并防止后续同步重新覆盖。MU-396、MU-430、MU-431 不因此自动完成或取消。
