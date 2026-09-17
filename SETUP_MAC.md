# Mac 边缘通道安装

当前正式链路是：企业微信桌面采集 -> 中台处理 -> 边缘执行发送。
统一控制面板负责启动、暂停、补录和查看状态；本机不需要配置 Pi 模型或 PostgreSQL。

## 环境准备

- macOS，已安装并登录企业微信。
- Python 3.10 或更新版本。
- Xcode Command Line Tools（`xcode-select --install`），用于编译 Swift。
- `screen` 命令，用于管理边缘进程。
- 管理员为本机分配的中台地址、设备 ID 和设备 Token。

旧 `一键安装.command` / `install.command` 仍是遗留本地 AI 安装流程，
不适用于当前边缘通道。请使用以下手动步骤。

## 安装与配置

在仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install ./wecom-gui
```

在 `wecom-gui/.env.local` 中配置以下字段；路径替换成本机绝对路径。
如果文件已经存在，仅修改所需字段，保留其他配置。

```bash
WECOM_GUI_PYTHON='/absolute/path/to/repository/.venv/bin/python'
WECOM_CHANNEL_BASE_URL='https://your-central-service.example.com'
WECOM_CHANNEL_DEVICE_ID='assigned-device-id'
WECOM_CHANNEL_DEVICE_TOKEN='assigned-device-token'
```

设备凭据单独分配，不复制其他机器的 Token，不提交 `.env.local`。

## 打开控制面板

双击 `启动客服.command`，或在仓库根目录执行：

```bash
./启动客服.command
```

它会构建并安装 `~/Applications/UDA WeCom Agent.app`，然后打开面板。
打开面板不自动启动收发；替换旧版应用时会停止边缘进程。

在系统设置的“隐私与安全性”中，为该应用授予“辅助功能”和“屏幕录制”权限。
更新后若权限失效，移除旧条目再添加新应用，并重新打开客户端。

## 日常操作

- 面板点击“启动接收”，确认中台连接后使用测试会话验证收发。
- AI 回复由中台接待配置控制；连接成功不代表 AI 已开启或消息已送达。
- “补录”是显式操作，历史消息不触发新 AI 回复。
- “补录明细”显示会话进度、消息摘要、上传状态和缺口，不是完整图片查看器。
- `查看日志.command` 查看当前边缘通道日志。
- `停止客服.command` 停止边缘通道、退出企业微信和控制面板。

只想暂停采集时，使用面板的“暂停接收”。恢复补录后的正常收发使用“恢复接收”。

当前恢复协议与运行边界见 [WECOM_GUI.md](wecom-gui/WECOM_GUI.md)。
