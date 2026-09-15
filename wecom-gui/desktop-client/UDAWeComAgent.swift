import AppKit
import ApplicationServices
import QuartzCore

private struct DesktopAccess {
    let accessibility: Bool
    let screenCapture: Bool

    static func current() -> DesktopAccess {
        DesktopAccess(accessibility: AXIsProcessTrusted(), screenCapture: CGPreflightScreenCaptureAccess())
    }

    var issue: String? {
        if !accessibility { return "需要重新授权无障碍" }
        if !screenCapture { return "需要屏幕录制权限" }
        return nil
    }
}

private enum JsonValue: Decodable, CustomStringConvertible {
    case string(String), number(Double), bool(Bool), null

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let item = try? value.decode(Bool.self) { self = .bool(item) }
        else if let item = try? value.decode(Double.self) { self = .number(item) }
        else { self = .string(try value.decode(String.self)) }
    }

    var description: String {
        switch self {
        case .string(let value): return value
        case .number(let value): return String(format: "%.0f", value)
        case .bool(let value): return value ? "true" : "false"
        case .null: return ""
        }
    }
}

private struct RuntimeProcess: Decodable {
    let process: String
    let status: String
    let phase: String
    let conversation_label: String
    let direction: String
    let rationale: String
    let metrics: [String: JsonValue]
    let error_code: String
    let updated_at: Double
}

private struct RuntimeEvent: Decodable {
    let process: String
    let status: String
    let phase: String
    let conversation_key: String
    let conversation_label: String
    let direction: String
    let rationale: String
    let metrics: [String: JsonValue]
    let error_code: String
    let occurred_at: Double
}

private enum ControlFailure: String {
    case restartWorker = "edge_worker_restart_required"
    case upgradeCentral = "central_history_recovery_upgrade_required"
    case unknown

    var message: String {
        switch self {
        case .restartWorker: return "请先停止并重新启动企微通道，再补齐聊天记录"
        case .upgradeCentral: return "请部署中台补录协议后再试"
        case .unknown: return "控制命令执行失败，请查看本机运行日志中的错误码后重试。"
        }
    }
}

private struct ControlReply: Decodable {
    let ok: Bool?
    let error: String?
    let error_code: String?

    var failure: ControlFailure {
        ControlFailure(rawValue: error_code ?? "") ?? ControlFailure(rawValue: error ?? "") ?? .unknown
    }
}

private struct RecoveryChatPreview: Decodable {
    let title: String
    let status: String
    let error_code: String
    let pages: Int
    var summary: String {
        let state = ["pending": "等待定位", "reading": "读取中", "completed": "核对完成", "gap": "未完成"][status] ?? "待确认"
        return "\(title) · \(state) · 已读取 \(pages) 页" + (error_code.isEmpty ? "" : " · \(recoveryErrorLabel(error_code))")
    }
}

private struct RecoveryMessagePreview: Decodable {
    let title: String
    let text: String
    let direction: String
    let observed_at: String
    let status: String
    let registered: Bool
    var summary: String {
        let state = status == "delivered" ? "已上传" : registered ? "已登记，附件待完成" : "待登记"
        let speaker = ["inbound": "客户", "outbound": "客服", "unknown": "方向待确认"][direction] ?? "方向待确认"
        return "[\(state)] \(title) · \(speaker)：\(text)"
    }
}

private func recoveryErrorLabel(_ code: String) -> String {
    return ["conversation_list_unavailable": "无法读取企微会话列表", "conversation_not_found": "未找到对应会话",
            "single_chat_not_selected": "未能确认企微单聊列表已选中", "single_chat_row_not_found": "未找到企微单聊入口",
            "conversation_open_unconfirmed": "会话未成功打开", "history_anchor_missing": "历史消息尚未对齐",
            "single_chat_prepare_unavailable": "无法切换到单聊列表", "inbox_page_unavailable": "会话列表读取失败",
            "accessibility_denied": "缺少无障碍权限", "window_not_on_screen": "企微窗口不在屏幕中"][code] ?? code
}

private struct RuntimeRecovery: Decodable {
    var id = ""
    var status = "idle"
    var phase = ""
    var conversation_label = ""
    var discovered = 0
    var completed = 0
    var registered = 0
    var pending_uploads = 0
    var pending_media = 0
    var pending_direction = 0
    var gaps = 0
    var error_code = ""
    var updated_at: Double = 0
    var desired_mode: String?
    var hold_normal_operation: Bool?
    var recent_chats: [RecoveryChatPreview] = []
    var recent_messages: [RecoveryMessagePreview] = []
    var discovery_error = ""

    private enum CodingKeys: String, CodingKey {
        case id, status, phase, conversation_label, discovered, completed, registered
        case recent_chats, recent_messages, discovery_error
        case pending_uploads, pending_media, pending_direction, gaps, error_code, updated_at, desired_mode, hold_normal_operation
    }

    init() {}

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        status = try values.decodeIfPresent(String.self, forKey: .status) ?? "idle"
        phase = try values.decodeIfPresent(String.self, forKey: .phase) ?? ""
        conversation_label = try values.decodeIfPresent(String.self, forKey: .conversation_label) ?? ""
        discovered = max(0, try values.decodeIfPresent(Int.self, forKey: .discovered) ?? 0)
        completed = max(0, try values.decodeIfPresent(Int.self, forKey: .completed) ?? 0)
        registered = max(0, try values.decodeIfPresent(Int.self, forKey: .registered) ?? 0)
        pending_uploads = max(0, try values.decodeIfPresent(Int.self, forKey: .pending_uploads) ?? 0)
        pending_media = max(0, try values.decodeIfPresent(Int.self, forKey: .pending_media) ?? 0)
        pending_direction = max(0, try values.decodeIfPresent(Int.self, forKey: .pending_direction) ?? 0)
        gaps = max(0, try values.decodeIfPresent(Int.self, forKey: .gaps) ?? 0)
        error_code = try values.decodeIfPresent(String.self, forKey: .error_code) ?? ""
        updated_at = try values.decodeIfPresent(Double.self, forKey: .updated_at) ?? 0
        desired_mode = try values.decodeIfPresent(String.self, forKey: .desired_mode)
        hold_normal_operation = try values.decodeIfPresent(Bool.self, forKey: .hold_normal_operation)
        recent_chats = Array((try values.decodeIfPresent([RecoveryChatPreview].self, forKey: .recent_chats) ?? []).prefix(20))
        recent_messages = Array((try values.decodeIfPresent([RecoveryMessagePreview].self, forKey: .recent_messages) ?? []).prefix(20))
        discovery_error = try values.decodeIfPresent(String.self, forKey: .discovery_error) ?? ""
    }

    var isActive: Bool { ["requested", "running"].contains(status) }
    var statusLabel: String {
        ["idle": "尚未补录", "requested": "补录已请求", "running": "正在补录", "paused": "补录已暂停",
         "completed": "补录已完成", "partial": "补录部分完成", "failed": "补录失败"][status] ?? "补录状态待确认"
    }

    var countsText: String {
        "发现会话 \(discovered) · 完成会话 \(completed) · 登记消息 \(registered)\n历史缺口 \(gaps) · 待上传 \(pending_uploads) · 图片待补 \(pending_media) · 方向待确认 \(pending_direction)"
    }
}

private func recoveryPhaseLabel(_ phase: String) -> String {
    let name = phase.hasPrefix("recovery_") ? String(phase.dropFirst("recovery_".count)) : phase
    return ["preflight": "补录准备", "discovering": "发现会话", "locating": "正在定位会话", "reading": "读取聊天记录", "capturing": "核对消息与图片", "gap": "历史存在缺口",
     "uploading": "上传补录记录", "network_retry": "等待网络重试", "paused": "已暂停", "completed": "补录完成",
     "partial": "部分完成", "resuming": "正在恢复正常收发", "resumed": "已恢复正常收发"][name] ?? phase
}

private struct RuntimeSnapshot: Decodable {
    let states: [RuntimeProcess]
    let events: [RuntimeEvent]
    var recovery: RuntimeRecovery? = nil

    var desiredMode: String? { recovery?.desired_mode }
    var holdsNormalOperation: Bool {
        if let hold = recovery?.hold_normal_operation { return hold }
        if desiredMode == "normal", ["resuming", "uploading", "network_retry"].contains(recovery?.phase ?? "") { return true }
        if recovery?.isActive == true { return true }
        if let recovery, !["idle", "paused", "completed", "partial", "failed"].contains(recovery.status) { return true }
        if let desiredMode { return desiredMode != "normal" }
        return recovery.map { $0.status != "idle" } ?? false
    }
    var isResuming: Bool { desiredMode == "normal" && holdsNormalOperation }
    var canRecover: Bool { !(recovery?.isActive == true && holdsNormalOperation) && !isResuming }
    var canPauseRecovery: Bool {
        holdsNormalOperation && (recovery?.isActive == true || isResuming
            || (desiredMode == "recovery" && (recovery == nil || recovery?.status == "idle")))
    }
    var recoverySummary: String {
        if isResuming { return "恢复正常中 · 待确认" }
        if recovery?.isActive == true { return recovery!.statusLabel }
        if recovery?.status == "failed" { return "补录失败 · 待恢复" }
        if recovery?.status == "partial" { return "部分完成 · 待恢复" }
        if recovery?.status == "completed" { return "补录结束 · 待恢复" }
        if recovery?.status == "paused" || desiredMode == "paused" { return "补录已暂停 · 待恢复" }
        return "补录模式 · 待确认"
    }
    var overall: (text: String, status: String) {
        if holdsNormalOperation {
            return (recoverySummary, recovery?.status == "failed" ? "failed" : "waiting")
        }
        let edge = states.first { $0.process == "edge_channel" }
        let status = edge?.status == "waiting" && edge?.phase == "waiting_for_command" ? "running" : edge?.status ?? "stopped"
        let text = ["failed": "需要处理", "waiting": "等待确认", "retrying": "等待确认",
                    "running": "稳定运行", "idle": "稳定运行"][status] ?? "服务未启动"
        return (text, status)
    }

    var recoveryDetails: String {
        guard let recovery else { return "聊天记录补录: 尚未补录" }
        var detail = "聊天记录补录: \(recovery.statusLabel)\n阶段: \(recoveryPhaseLabel(recovery.phase)) (\(recovery.phase))\n会话: \(recovery.conversation_label)\n"
            + recovery.countsText + "\n错误码: \(recovery.error_code.isEmpty ? "无" : recovery.error_code)"
            + "\n更新时间: \(recovery.updated_at)\n运行模式: \(desiredMode ?? "未提供")\n正常自动收发: \(holdsNormalOperation ? "未恢复" : "已放行")"
            + (recoveryIssue.map { "\n处理提示: \($0)" } ?? "")
        detail += "\n\n最近会话（最多 20 个）\n"
        detail += recovery.recent_chats.isEmpty ? "尚未读取会话" : recovery.recent_chats.map(\.summary).joined(separator: "\n")
        detail += "\n\n最近核对消息（最多 20 条；时间为首次观测时间）\n"
        let messages = recovery.recent_messages.map { $0.summary + "\n首次观测：" + $0.observed_at }
        detail += messages.isEmpty ? "本次尚无补录消息" : messages.joined(separator: "\n\n")
        if !recovery.discovery_error.isEmpty { detail += "\n列表发现失败：" + recoveryErrorLabel(recovery.discovery_error) }
        return detail
    }

    var recoveryIssue: String? {
        if let failure = ControlFailure(rawValue: recovery?.error_code ?? "") { return failure.message }
        return states.lazy.compactMap { ControlFailure(rawValue: $0.error_code)?.message }.first
    }
}

private final class FloatingStatusPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

private final class DashboardLabel: NSTextField {
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
}

private final class FloatingDashboardView: NSView {
    static let panelSize = NSSize(width: 420, height: 560)
    private var snapshot = RuntimeSnapshot(states: [], events: [])
    private var labels: [DashboardLabel] = []
    private var labelCount = 0
    private var controls: [String: NSButton] = [:]
    private let pulseHalo = CALayer()
    private var expanded = false
    private var operations: [() -> Void] = []
    var wecomRunning = false { didSet { if oldValue != wecomRunning { rebuildContent() } } }
    var controlNotice = "" { didSet { if oldValue != controlNotice { rebuildContent() } } }
    var actionInFlight = false { didSet { if oldValue != actionInFlight { rebuildContent() } } }
    var onDetails: (() -> Void)?
    var onLogs: (() -> Void)?
    var onWorkbench: (() -> Void)?
    var onStartWeCom: (() -> Void)?
    var onStopWeCom: (() -> Void)?
    var onStartEdge: (() -> Void)?
    var onStopEdge: (() -> Void)?
    var onRecoverHistory: (() -> Void)?
    var onPauseRecovery: (() -> Void)?

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.addSublayer(pulseHalo)
        pulseHalo.cornerRadius = 4
        let animation = CABasicAnimation(keyPath: "opacity")
        animation.fromValue = 0.5
        animation.toValue = 1
        animation.duration = 1.8
        animation.autoreverses = true
        animation.repeatCount = .infinity
        pulseHalo.add(animation, forKey: "breathing")
        rebuildContent()
    }
    required init?(coder: NSCoder) { nil }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        rebuildContent()
    }
    func update(_ value: RuntimeSnapshot) {
        if value.recovery?.isActive == true && snapshot.recovery?.isActive != true { expanded = true }
        snapshot = value
        let height: CGFloat = expanded ? 740 : 560
        if frame.height != height {
            if let window {
                var rect = window.frame
                let outerHeight = window.frameRect(forContentRect: NSRect(x: 0, y: 0, width: 420, height: height)).height
                rect.origin.y += rect.height - outerHeight
                rect.size.height = outerHeight
                window.setFrame(rect, display: true)
            }
            setFrameSize(NSSize(width: 420, height: height))
        }
        rebuildContent()
    }
    private var edge: RuntimeProcess? { snapshot.states.first { $0.process == "edge_channel" } }
    private var edgeRunning: Bool { ["running", "waiting", "retrying", "idle"].contains(edge?.status ?? "") }
    private func text(_ value: String, _ x: CGFloat, _ y: CGFloat, _ width: CGFloat,
                      size: CGFloat = 13, weight: NSFont.Weight = .regular, color: NSColor = .labelColor) {
        if labelCount == labels.count {
            let label = DashboardLabel(labelWithString: "")
            label.maximumNumberOfLines = 1
            label.lineBreakMode = .byTruncatingTail
            addSubview(label)
            labels.append(label)
        }
        let label = labels[labelCount]
        labelCount += 1
        label.stringValue = value
        label.font = .systemFont(ofSize: size, weight: weight)
        label.textColor = color
        label.frame = NSRect(x: x, y: y, width: width, height: 24)
        label.toolTip = value
        label.isHidden = value.isEmpty
    }
    private func button(_ id: String, _ title: String, _ icon: String, _ frame: NSRect,
                        enabled: Bool = true, primary: Bool = false) {
        let button: NSButton
        if let existing = controls[id] { button = existing }
        else {
            button = NSButton(title: title, target: self, action: #selector(activate(_:)))
            button.identifier = NSUserInterfaceItemIdentifier(id)
            button.bezelStyle = .rounded
            button.imagePosition = .imageLeading
            button.font = .systemFont(ofSize: 13, weight: .medium)
            addSubview(button)
            controls[id] = button
        }
        button.title = title
        button.image = NSImage(systemSymbolName: icon, accessibilityDescription: title)
        button.toolTip = title
        button.frame = frame
        button.isEnabled = enabled
        button.isHidden = false
        button.bezelColor = primary ? .controlAccentColor : nil
        button.contentTintColor = primary ? .white : .labelColor
    }
    private func divider(_ y: CGFloat) {
        let rect = NSRect(x: 24, y: y, width: bounds.width - 48, height: 1)
        operations.append { NSColor.separatorColor.setFill(); rect.fill() }
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.windowBackgroundColor.setFill()
        bounds.fill()
        for operation in operations { operation() }
    }
    private func rebuildContent() {
        labelCount = 0
        operations.removeAll(keepingCapacity: true)
        for control in controls.values { control.isHidden = true }
        defer {
            for label in labels.dropFirst(labelCount) { label.isHidden = true }
            needsDisplay = true
        }
        let top = bounds.height
        let overall = snapshot.overall
        let needsAttention = controlNotice.contains("需要") || controlNotice.contains("失败")
        let tint: NSColor = needsAttention || overall.status == "failed" ? .systemRed
            : ["waiting", "retrying"].contains(overall.status) ? .systemOrange
            : edgeRunning ? .systemGreen : .secondaryLabelColor
        text("企微客服助手", 24, top - 54, 240, size: 22, weight: .semibold)
        text("UDA", 340, top - 50, 56, size: 12, weight: .semibold, color: .tertiaryLabelColor)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        pulseHalo.frame = NSRect(x: 27, y: top - 79, width: 8, height: 8)
        pulseHalo.backgroundColor = tint.cgColor
        CATransaction.commit()
        text(needsAttention ? "需要处理" : overall.text, 44, top - 87, 320, size: 13, weight: .medium, color: tint)
        divider(top - 104)
        text("企业微信", 24, top - 143, 110, color: .secondaryLabelColor)
        text(wecomRunning ? "已打开" : "未打开", 150, top - 143, 246)
        text("消息接收", 24, top - 177, 110, color: .secondaryLabelColor)
        let phase = snapshot.holdsNormalOperation ? "补录期间暂停" : !edgeRunning ? "已停止"
            : edge?.status == "retrying" ? "连接重试中"
            : edge?.phase == "waiting_for_command" ? "等待新消息" : "正在同步"
        text(phase, 150, top - 177, 246)
        text("中台连接", 24, top - 211, 110, color: .secondaryLabelColor)
        let connected = edge?.phase == "waiting_for_command" && edgeRunning && edge?.status != "retrying"
        text(connected ? "已连接" : edgeRunning ? "等待连接确认" : "未连接", 150, top - 211, 246,
             color: connected ? .systemGreen : .secondaryLabelColor)
        button("wecom", wecomRunning ? "打开企微" : "启动企微", "bubble.left.and.bubble.right",
               NSRect(x: 24, y: top - 263, width: 176, height: 34), enabled: !actionInFlight)
        button("edge", snapshot.holdsNormalOperation ? "恢复接收" : edgeRunning ? "暂停接收" : "启动接收",
               snapshot.holdsNormalOperation || !edgeRunning ? "play.fill" : "pause.fill",
               NSRect(x: 216, y: top - 263, width: 180, height: 34), enabled: !actionInFlight,
               primary: snapshot.holdsNormalOperation || !edgeRunning)
        let gap = edge?.metrics["pending_alignment"]?.description ?? "0"
        let hasGap = gap != "0" || edge?.error_code == "message_alignment_pending"
        let notice = !controlNotice.isEmpty && controlNotice != "状态已更新" ? controlNotice
            : hasGap ? (gap == "0" ? "有历史记录待对齐" : "\(gap) 处历史记录待对齐") : ""
        text(notice, 24, top - 303, 250, size: 12, color: .systemOrange)
        if !notice.isEmpty {
            button("details", "查看详情", "info.circle", NSRect(x: 280, y: top - 302, width: 116, height: 28))
        }
        divider(top - 320)
        text("聊天记录补录", 24, top - 357, 180, size: 14, weight: .semibold)
        button("expand", "", expanded ? "chevron.up" : "chevron.down",
               NSRect(x: 354, y: top - 356, width: 42, height: 28))
        controls["expand"]?.toolTip = expanded ? "收起补录详情" : "展开补录详情"
        let recovery = snapshot.recovery ?? RuntimeRecovery()
        text(recovery.statusLabel, 24, top - 389, 190, size: 12, color: .secondaryLabelColor)
        button("recover", "补录", "clock.arrow.circlepath", NSRect(x: 230, y: top - 390, width: 78, height: 30),
               enabled: !actionInFlight && snapshot.canRecover)
        button("pause", "暂停", "pause.fill", NSRect(x: 318, y: top - 390, width: 78, height: 30),
               enabled: !actionInFlight && snapshot.canPauseRecovery)
        if expanded {
            text(recoveryPhaseLabel(recovery.phase.isEmpty ? "preflight" : recovery.phase), 24, top - 426, 372,
                 size: 12, color: .secondaryLabelColor)
            text(recovery.conversation_label.isEmpty ? "暂无正在补录的会话" : recovery.conversation_label,
                 24, top - 454, 372, size: 12)
            text("会话 \(recovery.completed) / \(recovery.discovered)", 24, top - 484, 180, size: 12)
            text("已登记 \(recovery.registered) 条", 216, top - 484, 180, size: 12)
            let pending = [("缺口", recovery.gaps), ("待上传", recovery.pending_uploads),
                           ("待补图", recovery.pending_media), ("待确认", recovery.pending_direction)]
                .filter { $0.1 > 0 }.map { "\($0.0) \($0.1)" }.joined(separator: " · ")
            text(pending.isEmpty ? "暂无待处理项" : pending, 24, top - 514, 372, size: 12, color: .secondaryLabelColor)
            let latestChat = recovery.conversation_label.isEmpty ? recovery.recent_chats.first?.summary : nil
            text(latestChat ?? "最近核对消息", 24, top - 544, 372, size: 12, color: .secondaryLabelColor)
            for index in 0..<2 {
                let preview = recovery.recent_messages.indices.contains(index) ? recovery.recent_messages[index].summary
                    : index == 0 ? "本次尚无补录消息" : ""
                text(preview, 24, top - 572 - CGFloat(index) * 26, 372, size: 12)
            }
            button("details", "补录明细", "list.bullet", NSRect(x: 280, y: 105, width: 116, height: 28))
        }
        divider(100)
        button("workbench", "中台", "arrow.up.right.square", NSRect(x: 24, y: 48, width: 112, height: 32))
        button("logs", "日志", "doc.text", NSRect(x: 154, y: 48, width: 112, height: 32))
        button("more", "更多", "ellipsis", NSRect(x: 284, y: 48, width: 112, height: 32))
        text("本机控制台", 24, 14, 372, size: 11, color: .tertiaryLabelColor)
    }
    @objc private func activate(_ sender: NSButton) {
        switch sender.identifier?.rawValue {
        case "details": onDetails?()
        case "logs": onLogs?()
        case "workbench": onWorkbench?()
        case "expand":
            expanded.toggle()
            let height: CGFloat = expanded ? 740 : 560
            if let window {
                var frame = window.frame
                let outerHeight = window.frameRect(forContentRect: NSRect(x: 0, y: 0, width: 420, height: height)).height
                frame.origin.y += frame.height - outerHeight
                frame.size.height = outerHeight
                window.setFrame(frame, display: true)
            }
            setFrameSize(NSSize(width: 420, height: height))
            rebuildContent()
        case "more":
            let menu = NSMenu()
            for (title, action) in [("运行详情", #selector(showDetails)), ("停止企业微信", #selector(stopWeCom))] {
                let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
                item.target = self
                item.isEnabled = !actionInFlight || action == #selector(showDetails)
                menu.addItem(item)
            }
            menu.popUp(positioning: nil, at: NSPoint(x: 0, y: sender.bounds.maxY), in: sender)
        default:
            guard !actionInFlight else { return }
            switch sender.identifier?.rawValue {
            case "recover": if snapshot.canRecover { onRecoverHistory?() }
            case "pause": if snapshot.canPauseRecovery { onPauseRecovery?() }
            case "wecom": onStartWeCom?()
            case "edge": if snapshot.holdsNormalOperation || !edgeRunning { onStartEdge?() } else { onStopEdge?() }
            default: break
            }
        }
    }
    @objc private func showDetails() { onDetails?() }
    @objc private func stopWeCom() { if !actionInFlight { onStopWeCom?() } }
#if DESKTOP_RENDER_TEST
    func clickControl(_ id: String) {
        guard let button = controls[id], !button.isHidden else { return }
        precondition(hitTest(NSPoint(x: button.frame.midX, y: button.frame.midY)) === button,
                     "Native button cannot receive clicks")
        button.performClick(nil)
    }
#endif
}
private final class AgentApp: NSObject, NSApplicationDelegate {
    private let root: String
    private let item: NSStatusItem? = {
#if DESKTOP_RENDER_TEST
        return nil
#else
        return NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
#endif
    }()
    private let menu = NSMenu()
    private let summary = NSMenuItem(title: "本机状态: 正在读取...", action: nil, keyEquivalent: "")
    private let edge = NSMenuItem(title: "边缘通道: 已停止", action: nil, keyEquivalent: "")
    private let centralCommands = NSMenuItem(title: "中台指令: 等待边缘通道", action: nil, keyEquivalent: "")
    private let recoverySummary = NSMenuItem(title: "聊天记录补录: 尚未补录", action: nil, keyEquivalent: "")
    private var commandItems: [String: NSMenuItem] = [:]
    private var snapshot = RuntimeSnapshot(states: [], events: [])
    private let control: String
    private var detailWindow: NSWindow?
    private var detailTextView: NSTextView?
    private var floatingPanel: FloatingStatusPanel?
    private var dashboard: FloatingDashboardView?
    private var timer: Timer?
    private var refreshing = false
    private var actionInFlight = false
    private var pendingMode: String?
    private var pendingRecoveryBaseline: (id: String, updatedAt: Double)?
    private var controlRevision = 0
    private var actionNotice = ""
    private var refreshError = ""
#if DESKTOP_RENDER_TEST
    private var testInvoke: ((String, [String], @escaping (String, Bool) -> Void) -> Void)?
    private var testAccess = DesktopAccess(accessibility: true, screenCapture: true)
    private var testErrorShown = false
    private var testErrorMessage = ""
#endif

    override init() {
        let app = URL(fileURLWithPath: Bundle.main.bundlePath)
        let resourceRoot = Bundle.main.url(forResource: "wecom-gui-root", withExtension: "txt")
            .flatMap { try? String(contentsOf: $0, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines) }
        root = ProcessInfo.processInfo.environment["WECOM_GUI_ROOT"] ?? resourceRoot ?? app.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().path
        control = ProcessInfo.processInfo.environment["WECOM_CONTROL_PATH"] ?? "\(root)/scripts/wecom-control"
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        item?.button?.image = indicator(.systemGray)
        makeFloatingDashboard()
        menu.autoenablesItems = false
        menu.addItem(summary)
        menu.addItem(edge)
        menu.addItem(centralCommands)
        menu.addItem(recoverySummary)
        menu.addItem(.separator())
        addCommand("补齐聊天记录", #selector(recoverHistory), "recover-history")
        addCommand("暂停补录", #selector(pauseRecovery), "pause-recovery")
        addCommand("启动边缘通道", #selector(startEdge), "start-edge")
        addCommand("停止边缘通道", #selector(stopEdge), "stop-edge")
        addCommand("重启边缘通道", #selector(restartEdge), "restart-edge")
        addCommand("启动企微", #selector(startWeCom), "start-wecom")
        addCommand("停止企微", #selector(stopWeCom), "stop-wecom")
        add("检查系统权限", #selector(checkPermissions))
        menu.addItem(.separator())
        add("显示/隐藏状态浮窗", #selector(toggleDashboard)); add("查看本机流转详情", #selector(showDetails)); add("打开运行日志", #selector(openLogs)); add("打开中台工作台", #selector(openWorkbench))
        menu.addItem(.separator()); add("退出 UDA WeCom Agent", #selector(quit))
        item?.menu = menu
        render()
        refresh()
#if !DESKTOP_RENDER_TEST
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.refresh() }
#endif
    }

    private func makeFloatingDashboard() {
        let size = FloatingDashboardView.panelSize
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let origin = NSPoint(x: max(screen.minX + 8, screen.maxX - size.width - 24),
                             y: max(screen.minY + 8, screen.maxY - size.height - 56))
        let panel = FloatingStatusPanel(
            contentRect: NSRect(origin: origin, size: size),
            styleMask: [.titled, .closable, .nonactivatingPanel], backing: .buffered, defer: false
        )
        panel.isOpaque = false
        panel.title = "企微客服助手"
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.isMovableByWindowBackground = true
        panel.hidesOnDeactivate = false
        let view = FloatingDashboardView(frame: NSRect(origin: .zero, size: size))
        view.onDetails = { [weak self] in self?.showDetails() }
        view.onLogs = { [weak self] in self?.openLogs() }
        view.onWorkbench = { [weak self] in self?.openWorkbench() }
        view.onStartWeCom = { [weak self] in self?.controlAction("start-wecom") }
        view.onStopWeCom = { [weak self] in self?.controlAction("stop-wecom") }
        view.onStartEdge = { [weak self] in self?.controlAction("start-edge") }
        view.onStopEdge = { [weak self] in self?.controlAction("stop-edge") }
        view.onRecoverHistory = { [weak self] in self?.recoverHistory() }
        view.onPauseRecovery = { [weak self] in self?.pauseRecovery() }
        panel.contentView = view
        floatingPanel = panel
        dashboard = view
#if !DESKTOP_RENDER_TEST
        panel.orderFrontRegardless()
#endif
    }

    private func add(_ title: String, _ action: Selector) {
        let entry = NSMenuItem(title: title, action: action, keyEquivalent: "")
        entry.target = self
        menu.addItem(entry)
    }
    private func addCommand(_ title: String, _ action: Selector, _ command: String) {
        add(title, action)
        commandItems[command] = menu.items.last
    }
    private func indicator(_ color: NSColor) -> NSImage? {
        NSImage(systemSymbolName: "circle.fill", accessibilityDescription: "UDA WeCom Agent")?.withSymbolConfiguration(.init(paletteColors: [color]))
    }

    private func invoke(_ executable: String, _ args: [String], completion: @escaping (String, Bool) -> Void) {
#if DESKTOP_RENDER_TEST
        guard let testInvoke else { preconditionFailure("Native tests must inject a command runner") }
        testInvoke(executable, args, completion)
#else
        let directory = root
        DispatchQueue.global(qos: .utility).async {
            let task = Process(); task.currentDirectoryURL = URL(fileURLWithPath: directory)
            task.executableURL = URL(fileURLWithPath: executable)
            task.arguments = args
            let pipe = Pipe(); task.standardOutput = pipe; task.standardError = pipe
            do {
                try task.run()
                // Drain while the child runs; waiting for exit first can fill the pipe.
                let result = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                task.waitUntilExit()
                let success = task.terminationStatus == 0
                DispatchQueue.main.async { completion(result, success) }
            } catch {
                DispatchQueue.main.async { completion("\(error)", false) }
            }
        }
#endif
    }

    private func refresh() {
        guard !refreshing, !actionInFlight else { return }
        refreshing = true
        let revision = controlRevision
        invoke(control, ["runtime-status"]) { [weak self] text, success in
            guard let self else { return }
            precondition(Thread.isMainThread)
            self.refreshing = false
            // A read started before a control command cannot confirm its new mode.
            guard revision == self.controlRevision else { self.refresh(); return }
            if success, let data = text.data(using: .utf8), let value = try? JSONDecoder().decode(RuntimeSnapshot.self, from: data) {
                self.snapshot = value
                if let mode = self.pendingMode {
                    let finishedNewRecovery = mode == "recovery" && value.recovery.map { recovery in
                        ["paused", "completed", "partial", "failed"].contains(recovery.status)
                            && (recovery.id != self.pendingRecoveryBaseline?.id || recovery.updated_at > (self.pendingRecoveryBaseline?.updatedAt ?? 0))
                    } == true
                    let confirmed = (value.desiredMode == mode && (mode != "normal" || !value.holdsNormalOperation))
                        || finishedNewRecovery
                        || (value.desiredMode == nil && ((mode == "recovery" && value.recovery?.isActive == true)
                            || (mode == "paused" && value.recovery?.status == "paused")
                            || (mode == "normal" && !value.holdsNormalOperation)))
                    if confirmed {
                        self.pendingMode = nil
                        self.pendingRecoveryBaseline = nil
                        self.actionNotice = "状态已更新"
                    }
                }
                self.refreshError = ""
            } else {
                self.refreshError = "状态读取失败，请查看运行日志"
            }
            self.render()
        }
    }

    private var displaySnapshot: RuntimeSnapshot {
        guard let pendingMode else { return snapshot }
        if pendingMode == "normal", snapshot.isResuming { return snapshot }
        var value = snapshot
        var recovery = value.recovery ?? RuntimeRecovery()
        recovery.desired_mode = pendingMode
        recovery.hold_normal_operation = true
        recovery.phase = ["recovery": "补录请求待确认", "paused": "暂停请求待确认", "normal": "resuming"][pendingMode] ?? ""
        if pendingMode == "recovery" { recovery.status = "requested" }
        value.recovery = recovery
        return value
    }

    private var desktopAccess: DesktopAccess {
#if DESKTOP_RENDER_TEST
        return testAccess
#else
        return DesktopAccess.current()
#endif
    }

    private func render() {
        precondition(Thread.isMainThread)
        let access = desktopAccess
        let snapshot = displaySnapshot
        let edgeState = snapshot.states.first { $0.process == "edge_channel" }
        edge.title = snapshot.holdsNormalOperation ? "边缘通道: \(snapshot.recoverySummary)" : line("边缘通道", edgeState)
        centralCommands.title = snapshot.holdsNormalOperation ? "中台指令: 正常自动收发未恢复" : commandLine(edgeState)
        recoverySummary.title = "聊天记录补录: \(snapshot.recovery?.statusLabel ?? "尚未补录")"
        let overall = snapshot.overall
        let color: NSColor = overall.status == "failed" ? .systemRed : ["waiting", "retrying"].contains(overall.status) ? .systemYellow : ["running", "idle"].contains(overall.status) ? .systemGreen : .systemGray
        item?.button?.image = indicator(color)
        summary.title = "本机状态: \(overall.text)"
        for (command, entry) in commandItems {
            entry.isEnabled = !actionInFlight && (command != "recover-history" || snapshot.canRecover)
                && (command != "pause-recovery" || snapshot.canPauseRecovery)
        }
        commandItems["start-edge"]?.title = snapshot.holdsNormalOperation ? "启动边缘通道（恢复正常运行）" : "启动边缘通道"
        dashboard?.update(snapshot)
        dashboard?.wecomRunning = NSRunningApplication.runningApplications(withBundleIdentifier: "com.tencent.WeWorkMac").contains { !$0.isTerminated }
        dashboard?.actionInFlight = actionInFlight
        let issue = access.issue ?? (refreshError.isEmpty ? nil : refreshError) ?? snapshot.recoveryIssue
        dashboard?.controlNotice = issue ?? actionNotice
        if detailWindow?.isVisible == true, detailTextView?.string != detailsText {
            detailTextView?.string = detailsText
        }
        if let issue {
            item?.button?.image = indicator(.systemRed)
            summary.title = "本机状态: \(issue)"
        }
        writeDesktopHealth(access)
    }

    private func writeDesktopHealth(_ access: DesktopAccess) {
#if !DESKTOP_RENDER_TEST
        let health: [String: Any] = ["pid": ProcessInfo.processInfo.processIdentifier,
            "accessibility": access.accessibility, "screen_capture": access.screenCapture,
            "state_read_ok": refreshError.isEmpty, "action_in_flight": actionInFlight,
            "updated_at": Date().timeIntervalSince1970]
        let directory = URL(fileURLWithPath: "\(root)/.codex-run")
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: health, options: [.sortedKeys])
            try data.write(to: directory.appendingPathComponent("desktop-health.json"), options: .atomic)
        } catch { NSLog("Unable to write desktop health: %@", error.localizedDescription) }
#endif
    }

    private func line(_ title: String, _ value: RuntimeProcess?) -> String {
        guard let value else { return "\(title): 已停止" }
        let detail = value.conversation_label.isEmpty ? "" : " · \(value.conversation_label)"
        let error = value.error_code.isEmpty ? "" : " · \(value.error_code)"
        return "\(title): \(label(value.status)) · \(value.phase)\(detail)\(error)"
    }

    private func label(_ status: String) -> String {
        ["idle": "正常", "running": "运行中", "waiting": "等待确认", "retrying": "重试中", "failed": "失败", "stopped": "已停止"][status] ?? status
    }

    private func commandLine(_ edgeState: RuntimeProcess?) -> String {
        guard let edgeState else { return "中台指令: 等待边缘通道" }
        if edgeState.status == "failed" { return "中台指令: 连接异常" }
        if edgeState.phase == "waiting_for_command" { return "中台指令: 长轮询等待中" }
        return "中台指令: 经边缘通道流转"
    }

    private func supervise(_ action: String, _ service: String, completion: (() -> Void)? = nil) {
        controlAction("\(action)-\(service)", completion: completion)
    }
    private func controlAction(_ action: String, completion: (() -> Void)? = nil) {
        precondition(Thread.isMainThread)
        guard !actionInFlight else { return }
        if action == "recover-history", !displaySnapshot.canRecover { return }
        if action == "pause-recovery", !displaySnapshot.canPauseRecovery { return }
        if ["start-edge", "restart-edge", "recover-history"].contains(action), !ensureEdgePermissions() { return }
        controlRevision += 1
        let previousMode = pendingMode
        let previousBaseline = pendingRecoveryBaseline
        let mode = ["recover-history": "recovery", "pause-recovery": "paused", "start-edge": "normal", "restart-edge": "normal"][action]
        if let mode {
            pendingMode = mode
            pendingRecoveryBaseline = (snapshot.recovery?.id ?? "", snapshot.recovery?.updated_at ?? 0)
        }
        actionInFlight = true
        actionNotice = "正在执行操作..."
        render()
        invoke(control, [action]) { [weak self] output, success in
            guard let self else { return }
            precondition(Thread.isMainThread)
            let reply = try? JSONDecoder().decode(ControlReply.self, from: Data(output.utf8))
            let succeeded = success && reply?.ok != false
            let failure = reply?.failure ?? .unknown
            self.controlRevision += 1
            self.actionInFlight = false
            if !succeeded {
                self.pendingMode = previousMode
                self.pendingRecoveryBaseline = previousBaseline
            }
            self.actionNotice = succeeded ? "操作已提交，正在确认状态" : failure.message
            self.render()
            self.refresh()
            if !succeeded { self.showCommandError(failure) }
            completion?()
        }
    }
    private func ensureEdgePermissions() -> Bool {
        let access = desktopAccess
        guard let issue = access.issue else { return true }
        render()
#if DESKTOP_RENDER_TEST
        return false
#else
        let alert = NSAlert()
        alert.messageText = issue
        alert.informativeText = !access.accessibility
            ? "请在系统设置的隐私与安全性 > 辅助功能中授权 UDA WeCom Agent。更新后如果已经开启，请移除旧条目，再添加 ~/Applications/UDA WeCom Agent.app。授权完成后重新点击启动边缘。"
            : "请在系统设置的隐私与安全性 > 屏幕与系统音频录制中允许 UDA WeCom Agent，用于读取消息气泡方向和图片。授权后重新打开客户端，再点击启动边缘。"
        alert.addButton(withTitle: "打开系统设置")
        alert.addButton(withTitle: "稍后")
        if alert.runModal() == .alertFirstButtonReturn {
            if !access.accessibility {
                let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
                _ = AXIsProcessTrustedWithOptions(options)
                NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
            } else {
                _ = CGRequestScreenCaptureAccess()
                NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!)
            }
        }
        return false
#endif
    }
    @objc private func checkPermissions() { _ = ensureEdgePermissions() }
    private func showCommandError(_ failure: ControlFailure) {
#if DESKTOP_RENDER_TEST
        testErrorShown = true
        testErrorMessage = failure.message
#else
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "操作未完成"
        alert.informativeText = failure.message
        alert.addButton(withTitle: "知道了")
        alert.runModal()
#endif
    }
    @objc private func recoverHistory() { controlAction("recover-history") }
    @objc private func pauseRecovery() { controlAction("pause-recovery") }
    @objc private func startEdge() { supervise("start", "edge") }
    @objc private func stopEdge() { supervise("stop", "edge") }
    @objc private func restartEdge() { supervise("restart", "edge") }
    @objc private func startWeCom() { controlAction("start-wecom") }
    @objc private func stopWeCom() { controlAction("stop-wecom") }
    @objc private func toggleDashboard() {
        guard let floatingPanel else { return }
        if floatingPanel.isVisible { floatingPanel.orderOut(nil) }
        else { floatingPanel.orderFrontRegardless() }
    }
    @objc private func openLogs() { NSWorkspace.shared.open(URL(fileURLWithPath: "\(root)/.codex-run")) }
    @objc private func openWorkbench() { NSWorkspace.shared.open(URL(string: ProcessInfo.processInfo.environment["WECOM_WORKBENCH_URL"] ?? "https://knowledge-cs.uda.cn/operations/wecom-message-workbench")!) }
    @objc private func quit() { NSApp.terminate(nil) }

    private var detailsText: String {
        let formatter = ISO8601DateFormatter()
        return displaySnapshot.recoveryDetails + "\n\n" + snapshot.events.prefix(20).map { item in
            return "\(formatter.string(from: Date(timeIntervalSince1970: item.occurred_at)))  \(item.process)  \(label(item.status))  \(item.phase)\n会话: \(item.conversation_label)  方向: \(item.direction.isEmpty ? "-" : item.direction)\(item.error_code.isEmpty ? "" : "  错误码: \(item.error_code)")\n"
        }.joined(separator: "\n")
    }

    @objc private func showDetails() {
        if let detailWindow, detailWindow.isVisible {
            detailTextView?.string = detailsText
            detailWindow.makeKeyAndOrderFront(nil)
            return
        }
        let text = NSTextView(frame: NSRect(x: 0, y: 0, width: 760, height: 520)); text.isEditable = false
        detailTextView = text
        text.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        text.string = detailsText
        let scroll = NSScrollView(frame: text.bounds); scroll.documentView = text; scroll.hasVerticalScroller = true; scroll.autoresizingMask = [.width, .height]
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 520), styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.title = "企微客服助手 - 补录明细与运行记录"; window.contentView = scroll; detailWindow = window; window.makeKeyAndOrderFront(nil)
    }
}

#if DESKTOP_RENDER_TEST
private func testSnapshotJSON(status: String? = nil, mode: String? = nil, phase: String = "discovering",
                              name: String = "测试会话", updatedAt: Double = 100, hold: Bool? = nil,
                              gaps: Int = 1, pendingMedia: Int = 3, pendingUploads: Int = 2, pendingDirection: Int = 4,
                              errorCode: String? = nil) -> String {
    let process: [String: Any] = ["process": "edge_channel", "status": "running", "phase": "waiting_for_command",
        "conversation_label": name, "direction": "unknown", "rationale": "PRIVATE_CUSTOMER_BODY",
        "metrics": ["api_key": "PRIVATE_TEST_KEY"], "error_code": "", "updated_at": updatedAt]
    var event = process
    event["conversation_key"] = "PRIVATE_CONVERSATION_KEY"
    event["occurred_at"] = updatedAt
    var fixture: [String: Any] = ["states": [process], "events": [event]]
    if let status {
        var recovery: [String: Any] = ["id": "recovery-test", "status": status, "phase": phase,
            "conversation_label": name, "discovered": 21, "completed": 14, "registered": 120,
            "pending_uploads": pendingUploads, "pending_media": pendingMedia, "gaps": gaps, "pending_direction": pendingDirection,
            "error_code": errorCode ?? (status == "failed" ? "media_pending" : ""), "updated_at": updatedAt,
            "body": "PRIVATE_CUSTOMER_BODY", "token": "PRIVATE_TEST_KEY"]
        recovery["desired_mode"] = mode
        recovery["hold_normal_operation"] = hold
        recovery["recent_chats"] = [["title": name, "status": "reading", "pages": 3, "error_code": ""]]
        recovery["recent_messages"] = [
            ["title": name, "text": "这是用于验收的合成消息", "direction": "inbound", "observed_at": "测试观测时间", "status": "delivered", "registered": true],
            ["title": name, "text": "[图片]", "direction": "unknown", "observed_at": "测试观测时间", "status": "waiting_media", "registered": true],
        ]
        fixture["recovery"] = recovery
    }
    return String(data: try! JSONSerialization.data(withJSONObject: fixture), encoding: .utf8)!
}

private func decodeTestSnapshot(_ text: String) -> RuntimeSnapshot {
    try! JSONDecoder().decode(RuntimeSnapshot.self, from: Data(text.utf8))
}

private func testClick(_ view: FloatingDashboardView, _ x: CGFloat, _ y: CGFloat) {
    view.clickControl(y == 104 ? "edge" : x < 180 ? "recover" : "pause")
}

private func testRecoveryDecodingAndModes() {
    let legacy = decodeTestSnapshot(testSnapshotJSON())
    precondition(legacy.recovery == nil && !legacy.holdsNormalOperation && !legacy.canPauseRecovery)
    let sparse = decodeTestSnapshot(#"{"states":[],"events":[],"recovery":{"status":"paused","pending_media":null}}"#)
    precondition(sparse.recovery?.pending_media == 0 && sparse.holdsNormalOperation)
    let nullRecovery = decodeTestSnapshot(#"{"states":[],"events":[],"recovery":null}"#)
    precondition(nullRecovery.recovery == nil)
    for status in ["idle", "requested", "running", "paused", "completed", "partial", "failed"] {
        for mode: String? in [nil, "recovery", "paused", "normal"] {
            let snapshot = decodeTestSnapshot(testSnapshotJSON(status: status, mode: mode))
            let active = ["requested", "running"].contains(status)
            let held = active || (mode == nil ? status != "idle" : mode != "normal")
            precondition(snapshot.holdsNormalOperation == held, "Incorrect normal-operation gate for \(status)/\(mode ?? "nil")")
            precondition((snapshot.overall.text == "稳定运行") == !held)
            precondition(snapshot.canRecover == !active)
            precondition(snapshot.canPauseRecovery == (active || (mode == "recovery" && status == "idle")))
            precondition(snapshot.recovery?.discovered == 21 && snapshot.recovery?.completed == 14 && snapshot.recovery?.registered == 120)
            precondition(snapshot.recovery?.gaps == 1 && snapshot.recovery?.pending_uploads == 2 && snapshot.recovery?.pending_media == 3)
            precondition(snapshot.recovery?.pending_direction == 4)
            precondition(!snapshot.recoveryDetails.contains("PRIVATE_"))
            for hold in [true, false] {
                let explicit = decodeTestSnapshot(testSnapshotJSON(status: status, mode: mode, hold: hold))
                precondition(explicit.holdsNormalOperation == hold, "Server hold flag did not take precedence")
                precondition((explicit.overall.text == "稳定运行") == !hold)
            }
        }
    }
    precondition(decodeTestSnapshot(testSnapshotJSON(status: "future_status", mode: "normal")).holdsNormalOperation)
    for phase in ["resuming", "uploading", "network_retry"] {
        let waiting = decodeTestSnapshot(testSnapshotJSON(status: "completed", mode: "normal", phase: phase, hold: true))
        precondition(waiting.isResuming && waiting.overall.text.contains("待确认") && !waiting.canRecover)
        precondition(decodeTestSnapshot(testSnapshotJSON(status: "completed", mode: "normal", phase: phase)).holdsNormalOperation)
    }
    let directionOnly = decodeTestSnapshot(testSnapshotJSON(status: "partial", mode: "paused", hold: true,
        gaps: 0, pendingMedia: 0, pendingUploads: 0, pendingDirection: 7))
    precondition(directionOnly.recovery?.gaps == 0 && directionOnly.recovery?.pending_direction == 7)
    precondition(directionOnly.recoverySummary.contains("部分完成") && !directionOnly.recoverySummary.contains("缺口"))
    let mediaOnly = decodeTestSnapshot(testSnapshotJSON(status: "partial", mode: "paused", hold: true,
        gaps: 0, pendingMedia: 3, pendingUploads: 0, pendingDirection: 0))
    precondition(mediaOnly.recovery?.gaps == 0 && !mediaOnly.recovery!.statusLabel.contains("缺口"))
}

private extension AgentApp {
    static func testRecoveryControls() {
        let agent = AgentApp()
        var commands: [String] = []
        var pending: [(command: String, completion: (String, Bool) -> Void)] = []
        let executable = agent.control
        agent.testInvoke = { path, arguments, completion in
            precondition(Thread.isMainThread && path == executable && arguments.count == 1)
            commands.append(arguments[0])
            pending.append((arguments[0], completion))
        }
        func finish(_ command: String, _ response: String = "", success: Bool = true) {
            guard let index = pending.firstIndex(where: { $0.command == command }) else { fatalError("No pending \(command)") }
            pending.remove(at: index).completion(response, success)
        }
        func select(_ command: String) {
            let entry = agent.commandItems[command]!
            precondition(entry.target === agent)
            NSApp.sendAction(entry.action!, to: entry.target, from: entry)
        }
        agent.applicationDidFinishLaunching(Notification(name: NSApplication.didFinishLaunchingNotification))
        precondition(commands == ["runtime-status"], "Opening the app started a service")
        precondition(agent.floatingPanel?.isVisible == false && agent.timer == nil, "Test opened live UI or polling")
        let view = agent.dashboard!
        precondition(agent.commandItems["pause-recovery"]?.isEnabled == false)

        // Leave the launch read pending to exercise a stale response crossing a command.
        testClick(view, 95, 144)
        precondition(commands == ["runtime-status", "recover-history"] && agent.actionInFlight && view.actionInFlight)
        precondition(agent.commandItems.values.allSatisfy { !$0.isEnabled })
        testClick(view, 95, 144)
        testClick(view, 255, 144)
        testClick(view, 277, 104)
        select("recover-history")
        select("start-edge")
        precondition(commands.count == 2, "Duplicate UI actions escaped the command lock")
        precondition(agent.displaySnapshot.holdsNormalOperation && !agent.summary.title.contains("稳定运行"))
        finish("recover-history")
        precondition(!agent.actionInFlight && !view.actionInFlight && agent.pendingMode == "recovery")
        finish("runtime-status", testSnapshotJSON())
        precondition(agent.snapshot.states.isEmpty && agent.pendingMode == "recovery", "Stale status replaced the recovery request")
        finish("runtime-status", testSnapshotJSON(status: "running", mode: "recovery"))
        precondition(agent.pendingMode == nil && agent.commandItems["recover-history"]?.isEnabled == false)
        precondition(agent.commandItems["pause-recovery"]?.isEnabled == true && agent.centralCommands.title.contains("未恢复"))
        precondition(!agent.detailsText.contains("PRIVATE_"), "Details exposed arbitrary runtime payloads")

        testClick(view, 255, 144)
        precondition(commands.last == "pause-recovery" && agent.actionInFlight)
        finish("pause-recovery", "PRIVATE_CUSTOMER_BODY PRIVATE_TEST_KEY", success: false)
        precondition(agent.testErrorShown && !agent.actionNotice.contains("PRIVATE_") && !agent.actionInFlight)
        finish("runtime-status", testSnapshotJSON(status: "running", mode: "recovery"))
        select("pause-recovery")
        precondition(commands.last == "pause-recovery")
        finish("pause-recovery")
        finish("runtime-status", testSnapshotJSON(status: "paused", mode: "paused"))
        precondition(agent.pendingMode == nil && agent.summary.title.contains("待恢复"))
        precondition(agent.commandItems["recover-history"]?.isEnabled == true && agent.commandItems["pause-recovery"]?.isEnabled == false)

        testClick(view, 277, 104)
        precondition(commands.last == "start-edge" && agent.displaySnapshot.holdsNormalOperation)
        finish("start-edge")
        for phase in ["resuming", "uploading", "network_retry"] {
            finish("runtime-status", testSnapshotJSON(status: "completed", mode: "normal", phase: phase, hold: true))
            precondition(agent.pendingMode == "normal" && agent.displaySnapshot.holdsNormalOperation)
            precondition(agent.displaySnapshot.recovery?.phase == phase && agent.centralCommands.title.contains("未恢复"))
            precondition(!agent.summary.title.contains("稳定运行") && agent.commandItems["recover-history"]?.isEnabled == false)
            agent.refresh()
        }
        finish("runtime-status", testSnapshotJSON(status: "running", mode: "normal", phase: "resumed", hold: false))
        precondition(agent.pendingMode == nil && agent.summary.title.contains("稳定运行"))
        precondition(!agent.displaySnapshot.holdsNormalOperation && agent.commandItems["recover-history"]?.isEnabled == true)

        // A short recovery can finish before the first fresh status arrives.
        select("recover-history")
        finish("recover-history")
        finish("runtime-status", testSnapshotJSON(status: "partial", mode: "paused", updatedAt: 200))
        precondition(agent.pendingMode == nil && agent.summary.title.contains("部分完成"))
        select("recover-history")
        finish("recover-history")
        finish("runtime-status", "invalid JSON", success: false)
        precondition(agent.pendingMode == "recovery" && agent.displaySnapshot.holdsNormalOperation && !agent.refreshError.isEmpty)
        agent.refresh()
        finish("runtime-status", testSnapshotJSON(status: "running", mode: "recovery", updatedAt: 300))
        precondition(agent.pendingMode == nil && agent.refreshError.isEmpty)

        agent.testAccess = DesktopAccess(accessibility: false, screenCapture: false)
        let beforeDeniedStart = commands.count
        select("start-edge")
        precondition(commands.count == beforeDeniedStart && !agent.actionInFlight)
        select("pause-recovery")
        precondition(commands.last == "pause-recovery", "Permission loss blocked pausing")
        finish("pause-recovery")
        finish("runtime-status", testSnapshotJSON(status: "paused", mode: "paused"))
        let beforeDeniedRecovery = commands.count
        select("recover-history")
        precondition(commands.count == beforeDeniedRecovery && !agent.actionInFlight)

        agent.testAccess = DesktopAccess(accessibility: true, screenCapture: true)
        select("recover-history")
        finish("recover-history", #"{"ok":false,"error":"edge_worker_restart_required","code":"RuntimeError","body":"PRIVATE_CUSTOMER_BODY"}"#, success: false)
        precondition(agent.pendingMode == nil && !agent.actionInFlight)
        precondition(agent.actionNotice == "请先停止并重新启动企微通道，再补齐聊天记录")
        precondition(agent.testErrorMessage == agent.actionNotice && agent.displaySnapshot.recovery?.status == "paused")
        finish("runtime-status", testSnapshotJSON(status: "paused", mode: "paused", hold: true))
        precondition(view.controlNotice == agent.actionNotice && !agent.summary.title.contains("正在补录"))
        select("recover-history")
        finish("recover-history", #"{"ok":false,"error":"PRIVATE_CUSTOMER_BODY PRIVATE_TEST_KEY"}"#)
        precondition(agent.pendingMode == nil && agent.testErrorMessage == ControlFailure.unknown.message)
        finish("runtime-status", testSnapshotJSON(status: "failed", mode: "paused", phase: "recovery_paused",
            hold: true, errorCode: "central_history_recovery_upgrade_required"))
        precondition(view.controlNotice == "请部署中台补录协议后再试")
        precondition(agent.detailsText.contains("请部署中台补录协议后再试") && !agent.detailsText.contains("PRIVATE_"))
        precondition(recoveryPhaseLabel("recovery_paused") == "已暂停")
        precondition(pending.isEmpty)
        agent.floatingPanel?.orderOut(nil)
    }
}

private func runDesktopRenderTest() {
    let app = NSApplication.shared
    app.setActivationPolicy(.prohibited)
    testRecoveryDecodingAndModes()
    AgentApp.testRecoveryControls()
    let view = FloatingDashboardView(frame: NSRect(origin: .zero, size: FloatingDashboardView.panelSize))
    let window = NSWindow(contentRect: view.bounds, styleMask: [.borderless], backing: .buffered, defer: false)
    window.contentView = view
    let statuses = ["stopped", "running", "waiting", "retrying", "failed", "idle"]
    let recoveryStatuses = ["idle", "requested", "running", "paused", "completed", "partial", "failed"]
    let phases = ["preflight", "discovering", "reading", "capturing", "uploading", "network_retry", "paused", "completed", "partial", "resuming", "resumed"]
    let labels = ["", "客户测试", "示例客户 @微信", "图片📷 / e\u{301}", String(repeating: "长名称", count: 50)]
    let iterations = Int(CommandLine.arguments.dropFirst().first ?? "4000") ?? 4000
    var retainedLabels: [ObjectIdentifier] = []
    var maxLabels = 0
    var colorfulPixels = 0
    for index in 0..<iterations {
        autoreleasepool {
            if index % 24 == 0 {
                let current = index / 24
                let status = statuses[current % statuses.count]
                let name = labels[current % labels.count]
                let process = RuntimeProcess(process: "edge_channel", status: status, phase: "waiting_for_command",
                    conversation_label: name, direction: "unknown", rationale: "", metrics: [:], error_code: "", updated_at: 0)
                let events = (0..<(current % 4)).map { eventIndex in
                    RuntimeEvent(process: "edge_channel", status: status, phase: "media_capture",
                        conversation_key: "test-conversation", conversation_label: name, direction: "unknown",
                        rationale: "test", metrics: [:], error_code: "", occurred_at: Double(eventIndex))
                }
                var recovery = decodeTestSnapshot(testSnapshotJSON(status: recoveryStatuses[current % recoveryStatuses.count],
                    mode: current % 4 == 0 ? "normal" : "recovery", phase: phases[current % phases.count], name: name,
                    hold: current % 4 != 0)).recovery
                if current % 5 == 0 { recovery?.pending_direction = Int.max }
                view.update(RuntimeSnapshot(states: [process], events: events, recovery: recovery))
                view.controlNotice = current % 2 == 0 ? "状态更新" : ""
                view.actionInFlight = current % 3 == 0
                view.appearance = NSAppearance(named: current % 2 == 0 ? .darkAqua : .aqua)
            }
            let fields = view.subviews.compactMap { $0 as? NSTextField }
            maxLabels = max(maxLabels, fields.count)
            precondition(fields.count < 40, "Text controls grew across refreshes")
            if retainedLabels.isEmpty { retainedLabels = fields.map(ObjectIdentifier.init) }
            precondition(Array(fields.prefix(retainedLabels.count)).map(ObjectIdentifier.init) == retainedLabels)
            for field in fields where !field.isHidden {
                precondition(view.bounds.contains(field.frame), "Text escaped the panel")
                precondition(!field.stringValue.contains("PRIVATE_") && !(field.toolTip?.contains("PRIVATE_") ?? false))
            }
            let visible = fields.filter { !$0.isHidden }
            for (index, field) in visible.enumerated() {
                for other in visible.dropFirst(index + 1) {
                    precondition(!field.frame.intersects(other.frame), "Overlapping labels: \(field.stringValue) / \(other.stringValue)")
                }
            }
            guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { fatalError("No bitmap") }
            view.cacheDisplay(in: view.bounds, to: bitmap)
            if index == iterations - 1 {
                for x in stride(from: 0, to: bitmap.pixelsWide, by: 10) {
                    for y in stride(from: 0, to: bitmap.pixelsHigh, by: 10) {
                        if let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB), color.redComponent > 0.4 {
                            colorfulPixels += 1
                        }
                    }
                }
            }
        }
    }
    if CommandLine.arguments.count > 2 {
        view.update(decodeTestSnapshot(testSnapshotJSON(status: "running", mode: "recovery", phase: "reading",
            name: "离屏验收会话", hold: true)))
        view.actionInFlight = false
        view.controlNotice = ""
        let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds)!
        view.cacheDisplay(in: view.bounds, to: bitmap)
        try! bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[2]))
        view.update(decodeTestSnapshot(testSnapshotJSON()))
        view.wecomRunning = true
        view.clickControl("expand")
        precondition(view.bounds.height == 560, "Collapsed layout has the wrong height")
        for (name, appearance) in [("light", NSAppearance.Name.aqua), ("dark", NSAppearance.Name.darkAqua)] {
            view.appearance = NSAppearance(named: appearance)
            view.viewDidChangeEffectiveAppearance()
            let image = view.bitmapImageRepForCachingDisplay(in: view.bounds)!
            view.cacheDisplay(in: view.bounds, to: image)
            try! image.representation(using: .png, properties: [:])!.write(to:
                URL(fileURLWithPath: CommandLine.arguments[2] + "." + name + ".png"))
        }
        var pauses = 0
        view.onStopEdge = { pauses += 1 }
        view.clickControl("edge")
        precondition(pauses == 1, "Running edge button must pause reception")
    }
    precondition(colorfulPixels > 20, "Panel rendering is blank")
    precondition(view.layer?.sublayers?.contains { $0.animation(forKey: "breathing") != nil } == true,
                 "Breathing animation missing")
    var starts = 0
    view.update(RuntimeSnapshot(states: [], events: []))
    view.actionInFlight = false
    view.onStartEdge = { starts += 1 }
    view.clickControl("edge")
    precondition(starts == 1, "Start control did not dispatch its callback")
    print("{\"frames\":\(iterations),\"max_text_controls\":\(maxLabels),\"nonblank\":true,\"control_click\":true,\"breathing\":true,\"recovery_modes\":84,\"command_routing\":true,\"duplicate_guard\":true,\"stale_status\":true,\"no_autostart\":true,\"privacy\":true,\"nonoverlap\":true,\"release_barrier\":true,\"partial_counts\":true,\"controlled_errors\":true}")
}
runDesktopRenderTest()
#else
private let app = NSApplication.shared
private let delegate = AgentApp()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
#endif
