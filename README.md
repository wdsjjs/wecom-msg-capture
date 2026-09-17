# WeCom Capture

**macOS 上的企业微信桌面消息采集与发送工具。**

通过辅助功能（Accessibility）和屏幕识别读取会话、捕获图片，使用本地 SQLite 维护消息状态。提供原生 Dock 应用和 Python CLI，可连接自建服务端同步消息、执行发送指令和显式补录历史。

这是通用桌面自动化工具，不是企业微信官方 SDK，不包含业务知识库、行业推荐流程或内置 AI 服务。无需配置服务端即可使用本地 CLI 检查和读取桌面；持续同步、远程发送任务及历史补录需要实现兼容的[通道协议](docs/channel-protocol.md)。

## 功能

| 能力 | 说明 |
| --- | --- |
| 原生面板 | Dock 入口、设备配置、权限检查、接收控制、运行状态 |
| 本地读取 | 查看当前会话文本、识别消息方向、捕获可访问的图片 |
| 发送执行 | 手动 CLI 发送，或执行服务端下发的发送任务并回传结果 |
| 持久消息队列 | SQLite 消息记录、注册去重、上传重试和发送核验 |
| 历史恢复 | 显式开始、暂停和结束，展示会话进度、消息摘要与缺口 |

消息上传、服务端处理和最终发送是独立阶段。工具不会自行生成 AI 回复；如需 AI，可在自己的服务端实现。

## 环境要求

- macOS 14+，当前桌面打包流程在 Apple Silicon 上验证，Intel 尚未验证。
- 已安装并登录企业微信，保持可交互的桌面会话。
- 辅助功能和屏幕录制权限。
- 源码开发：Python 3.10+、Xcode Command Line Tools、`screen`。

窗口遮挡、锁屏和企业微信版本变化可能影响读取和定位。本工具仅操作当前桌面可访问的内容，不承诺完整导出全部聊天记录。不应同时启动多个工具争用同一个企业微信窗口。

## 从源码开始

在项目根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ./wecom-gui
.venv/bin/python -m cli_anything.wecom_gui --help
```

检查环境、读取当前会话：

```bash
.venv/bin/python -m cli_anything.wecom_gui doctor
.venv/bin/python -m cli_anything.wecom_gui --json chat read --last 10
```

首次运行按系统提示为实际执行程序授权。更多 CLI 命令可通过 `--help` 查看。`reply send` 会操作当前会话；验证命令格式时先使用 `--dry-run`。

### 连接服务端与 Dock 面板

将 `wecom-gui/.env.example` 复制为同目录的 `.env.local`，配置自己的 Python 绝对路径、HTTPS 服务地址、设备 ID 和 Token。示例凭据无法连接真实服务。

```bash
./启动客服.command
```

源码入口会构建并打开 `~/Applications/WeCom Capture.app`。打开应用不自动开始收发，配置与授权完成后再点击“启动接收”。Dock 图标可通过右键菜单保留。

- `查看日志.command`：查看本地运行日志。
- `停止客服.command`：停止本项目进程、退出面板与企业微信。
- 面板“暂停接收”：仅暂停接收，不退出企业微信。

历史恢复必须由服务端声明支持，结束恢复需要明确确认；不能使用心跳超时自动恢复发送。补录期间观察到的时间不等于消息原始发送时间。

## 独立安装包

打包版本包含 Python 和辅助工具，使用者无需安装开发环境。应用名为 **WeCom Capture**，使用独立 Bundle ID `org.wecomcapture.desktop`。

构建及验证步骤见[桌面分发说明](docs/desktop-distribution.md)。当前目录不附带已发布或已公证的安装包；临时签名构建可能被 macOS 阻止。App Store 不是分发前提，正式对外分发建议使用 Developer ID 签名和 Apple 公证。

## 配置与隐私

独立应用将配置、日志、图片及状态保存在：

```text
~/Library/Application Support/WeCom Capture/
```

源码 CLI 默认状态目录为 `~/.wecom-capture/`，可通过 `WECOM_GUI_STATE_DIR` 覆盖。设备配置文件权限为 `0600`。示例配置没有默认服务地址，应用不会内置连接任何部署环境。

使用前应确保有权处理相应会话。不要提交设备凭据、消息数据库、日志或聊天图片；提交 Issue 时使用虚构数据和脱敏截图。

## 开发与测试

| 路径 | 用途 |
| --- | --- |
| `wecom-gui/desktop-client/` | Swift 原生面板和安装器 |
| `wecom-gui/cli_anything/wecom_gui/core/` | 会话读取、消息状态、通道与恢复逻辑 |
| `wecom-gui/cli_anything/wecom_gui/utils/` | macOS 桌面接口 |
| `wecom-gui/cli_anything/wecom_gui/tests/` | 离线回归及原生渲染测试 |
| `wecom-gui/scripts/` | 启动、诊断、打包与校验入口 |

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest wecom-gui/cli_anything/wecom_gui/tests -q
```

测试隔离本地状态并阻止未模拟的 HTTP 请求。原生渲染测试需要 macOS 和 Swift；测试通过不代表已经验证真实账号的端到端收发。

## 参与贡献

欢迎提交可复现的问题和范围明确的 Pull Request。请附系统与企业微信版本、操作步骤、预期行为和脱敏日志。新增示例仅使用虚构名称及保留示例域名，避免加入任何组织品牌或业务资料。

## 许可证

许可证尚未选定，当前未授予开源许可。对外发布前需由维护者确认代码发布权利并添加 `LICENSE`；移除名称与配置不改变代码权属。
