# macOS 桌面分发

构建机需要 Swift、uv 和独立 CPython 3.12。安装包包含运行时、依赖、预编译桌面辅助工具及原生面板。当前只验证 Apple Silicon，最低系统版本 macOS 14。

打包器只复制 Git 索引中的源码。首次构建前审查文件并执行 `git add`，确认未包含环境配置、消息数据库、图片或日志；无需提交或推送即可构建。

```bash
uv python install 3.12
python3 wecom-gui/scripts/build-desktop-distribution.py \
  --python /absolute/path/to/standalone-python/bin/python3.12 \
  --output dist/macos-arm64
python3 wecom-gui/scripts/verify-desktop-distribution.py \
  dist/macos-arm64/WeCom-Capture-Agent.pkg
```

输出目录必须为空。不要使用依赖本机动态库路径的系统或 Homebrew Python 代替独立运行时。安装目标为 `/Applications/WeCom Capture.app`，应用和边缘进程运行时升级会被拒绝。

默认构建使用临时应用签名，安装包未签名，可能被系统阻止。正式分发时增加 `--identity 'Developer ID Application: ...'` 和 `--installer-identity 'Developer ID Installer: ...'`，随后公证与装订：

```bash
xcrun notarytool submit dist/macos-arm64/WeCom-Capture-Agent.pkg \
  --keychain-profile YOUR_NOTARY_PROFILE --wait
xcrun stapler staple dist/macos-arm64/WeCom-Capture-Agent.pkg
spctl --assess --type install --verbose dist/macos-arm64/WeCom-Capture-Agent.pkg
```

安装后打开应用并配置自己的服务端，授予辅助功能和屏幕录制权限，再手动启动接收。设备配置保存在 `~/Library/Application Support/WeCom Capture/device.json`，权限为 `0600`；同目录的 `state`、`logs` 和 `images` 用于运行数据。安装包升级不会清除这些数据。

验证器会解包最终安装器，检查架构、最低系统版本、目标目录、升级保护、运行时依赖和包内容不可变性。仍需在目标机器上验证权限、设备鉴权与实际收发。
