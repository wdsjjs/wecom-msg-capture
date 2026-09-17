# 服务端接入边界

本项目只提供桌面执行端，不包含现成服务端。协议客户端位于 `wecom-gui/cli_anything/wecom_gui/core/edge_channel.py`，请求和响应示例可参考同级测试目录的 `test_edge_channel.py` 与 `test_history_recovery.py`。

连接需要 HTTPS 地址、设备 ID 和 Token。请求使用 `Authorization: Bearer <token>` 与 `X-Wecom-Channel-Device-Id`；服务端应验证设备身份和数据范围。`WECOM_CHANNEL_LOCAL_TEST_MODE=true` 仅用于本地协议模拟，不应用于真实部署。

基本职责包括心跳与能力协商、消息注册和附件上传、发送任务租约、发送回执与失败核验。设备消息记录和服务端任务必须保持幂等，发送超时不能直接视为未发送并无条件重发。

## 历史恢复

心跳 `POST /api/wecom-channel/edge/heartbeat` 返回的能力中，需要包含 `capabilities.historyRecoveryV1=true` 才能运行补录。

恢复边界使用 `POST /api/wecom-channel/edge/recovery`：

```json
{
  "recovery_id": "00000000-0000-4000-8000-000000000001",
  "action": "begin"
}
```

响应为：

```json
{
  "accepted": true,
  "recovery_id": "00000000-0000-4000-8000-000000000001",
  "active": true
}
```

结束使用相同 ID 和 `action: "end"`，确认 `active: false`。服务端应持久化恢复栅栏，开始后停止新发送租约和 AI 发送，不依靠心跳 TTL 解锁；结束后不能自动重放已取消的旧任务。对已结束 ID 的延迟开始应返回非活动状态，客户端会拒绝复用。

历史消息通过 `source.recovery_id` 标识。服务端应将恢复期间的新历史观察与已登记 live 消息的重试区分开，不应因重试而永久改变已有 live 消息属性；同时应将恢复期间首次收录、缺少恢复字段的旧消息独立隔离。附件补全和方向修正不得绕过历史保护。

`occurred_at` 可能只是首次观测时间，不一定是原始发送时间；`sequence` 只表达单会话观察顺序。服务端需区分历史排序、最新 live 消息水位和 AI 输入选择。这些是接入要求，不代表本仓库已经提供或验证服务端实现。
