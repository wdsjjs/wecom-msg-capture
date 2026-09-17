export type Message = {
  id: string; direction: 'inbound' | 'outbound' | 'system'; text: string;
  time: string; kind?: 'image'; history?: boolean; author?: 'ai' | 'human';
};
export type Conversation = {
  id: string; name: string; avatar: string; color: string; topic: string;
  unread: number; ai: boolean; resolved: boolean; messages: Message[];
};
export type Activity = { id: string; time: string; title: string; detail: string; kind: 'message' | 'device' | 'recovery' | 'setting' };
export type DemoState = { conversations: Conversation[]; online: boolean; globalAi: boolean; recovery: boolean; events: Activity[] };
export type Action =
  | { type: 'read'; id: string }
  | { type: 'send' | 'receive'; id: string; text: string; image?: boolean }
  | { type: 'toggleAi' | 'resolve'; id: string }
  | { type: 'toggleGlobalAi' | 'toggleOnline' | 'beginRecovery' | 'endRecovery' | 'reset' };

let sequence = 0;
const uid = () => `demo-${Date.now()}-${sequence++}`;
const clock = () => new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
const message = (id: string, direction: Message['direction'], text: string, time: string, extra: Partial<Message> = {}): Message => ({ id, direction, text, time, ...extra });

export function initialState(): DemoState {
  return {
    online: true, globalAi: true, recovery: false,
    conversations: [
      { id: 'demo-visitor-01', name: '访客 01', avatar: '01', color: 'mint', topic: '使用咨询', unread: 0, ai: true, resolved: false, messages: [
        message('m1', 'system', '会话已建立', '10:32'),
        message('m2', 'inbound', '你好，想了解一下这个工具。', '10:32'),
        message('m3', 'outbound', '你好！可以先从消息接收和会话管理开始体验，有问题随时告诉我。', '10:33', { author: 'ai' }),
        message('m4', 'inbound', '这是我当前看到的设备状态。', '10:35'),
        message('m5', 'inbound', '设备状态示例', '10:35', { kind: 'image' }),
        message('m6', 'inbound', '如果设备暂时离线，消息会丢失吗？', '10:36'),
      ] },
      { id: 'demo-visitor-02', name: '访客 02', avatar: '02', color: 'violet', topic: '图片消息', unread: 2, ai: true, resolved: false, messages: [
        message('b1', 'inbound', '图片消息也可以在这里查看吗？', '10:28'),
        message('b2', 'inbound', '想确认一下图片的采集状态。', '10:29'),
      ] },
      { id: 'demo-visitor-03', name: '访客 03', avatar: '03', color: 'peach', topic: '人工接待', unread: 1, ai: false, resolved: false, messages: [
        message('c1', 'inbound', '你好，我希望由人工协助。', '10:20'),
        message('c2', 'system', '已切换为人工接待', '10:21'),
        message('c3', 'outbound', '你好，我已经接手这个会话，请问需要什么帮助？', '10:22', { author: 'human' }),
        message('c4', 'inbound', '可以帮我确认一下操作步骤吗？', '10:24'),
      ] },
      { id: 'demo-visitor-04', name: '访客 04', avatar: '04', color: 'blue', topic: '历史记录', unread: 0, ai: true, resolved: false, messages: [
        message('d1', 'inbound', '之前的聊天记录可以补录吗？', '10:12'),
        message('d2', 'outbound', '可以通过历史补录整理可读取的记录，补录内容不会触发新的自动回复。', '10:13', { author: 'ai' }),
      ] },
      { id: 'demo-visitor-05', name: '访客 05', avatar: '05', color: 'rose', topic: '连接配置', unread: 0, ai: false, resolved: true, messages: [
        message('e1', 'inbound', '已经连接成功了，谢谢。', '09:58'),
        message('e2', 'outbound', '不客气，祝你使用顺利。', '09:59', { author: 'human' }),
      ] },
      { id: 'demo-visitor-06', name: '访客 06', avatar: '06', color: 'sand', topic: '首次使用', unread: 0, ai: true, resolved: true, messages: [
        message('f1', 'inbound', '好的，已经了解了。', '09:46'),
        message('f2', 'outbound', '后续有问题可以继续留言。', '09:47', { author: 'ai' }),
      ] },
    ],
    events: [
      { id: 'event-1', time: '10:36', title: '收到新消息', detail: '访客 01 · 文本消息已收录', kind: 'message' },
      { id: 'event-2', time: '10:35', title: '图片已就绪', detail: '访客 01 · 示例附件可查看', kind: 'message' },
      { id: 'event-3', time: '10:21', title: '人工接待', detail: '访客 03 · 自动回复已暂停', kind: 'setting' },
      { id: 'event-4', time: '09:30', title: '设备已连接', detail: '演示 Mac · 接收就绪', kind: 'device' },
    ],
  };
}

export function draftReply(text: string): string {
  if (/离线|连接/.test(text)) return '设备离线时，工作台会暂停创建发送任务。恢复连接后，请先核对消息同步与发送状态，避免重复回复。';
  if (/图片/.test(text)) return '可以在会话中查看已采集的图片。若图片尚未就绪，可以查看设备权限和采集记录。';
  if (/历史|补录/.test(text)) return '可以通过历史补录查看可读取的会话记录。补录消息仅进入历史，不会触发新的自动回复。';
  return '收到你的消息了。请告诉我具体遇到的情况，我会协助你确认接下来的操作。';
}

function record(state: DemoState, title: string, detail: string, kind: Activity['kind']): DemoState {
  return { ...state, events: [{ id: uid(), time: clock(), title, detail, kind }, ...state.events].slice(0, 80) };
}

export function reducer(state: DemoState, action: Action): DemoState {
  if (action.type === 'reset') return initialState();
  if (action.type === 'toggleOnline') return record({ ...state, online: !state.online }, state.online ? '设备已离线' : '设备已连接', '演示 Mac · 连接状态已更新', 'device');
  if (action.type === 'toggleGlobalAi') return record({ ...state, globalAi: !state.globalAi }, state.globalAi ? '全局 AI 接待已暂停' : '全局 AI 接待已开启', '仅影响演示自动回复', 'setting');
  if (action.type === 'beginRecovery') {
    if (state.recovery || !state.online) return state;
    const conversations = state.conversations.map((c, index) => index > 2 || c.messages.some(m => m.id === `history-${c.id}-1`) ? c : ({ ...c, messages: [
      message(`history-${c.id}-1`, 'inbound', '历史示例：你好，想了解使用方式。', '昨天 15:10', { history: true }),
      message(`history-${c.id}-2`, 'outbound', '历史示例：你好，可以从设备连接开始。', '昨天 15:12', { history: true, author: 'human' }),
      ...c.messages,
    ] }));
    return record({ ...state, recovery: true, conversations }, '历史记录已收录', '3 个会话 · 6 条历史消息 · 未触发 AI', 'recovery');
  }
  if (action.type === 'endRecovery') {
    if (!state.recovery) return state;
    return record({ ...state, recovery: false }, '已结束恢复暂停', '仅处理后续新消息，不重发历史任务', 'recovery');
  }
  if (!('id' in action)) return state;
  const current = state.conversations.find(c => c.id === action.id);
  if (!current) return state;
  const update = (next: Conversation) => ({ ...state, conversations: state.conversations.map(c => c.id === next.id ? next : c) });
  if (action.type === 'read') return update({ ...current, unread: 0 });
  if (action.type === 'toggleAi') return record(update({ ...current, ai: !current.ai }), current.ai ? '已切换人工接待' : '已开启 AI 托管', current.name, 'setting');
  if (action.type === 'resolve') return record(update({ ...current, resolved: !current.resolved }), current.resolved ? '会话已重新打开' : '会话已标记完成', current.name, 'setting');
  if (action.type === 'send' || action.type === 'receive') {
    const text = action.text.trim().slice(0, 2000);
    if (!text || !state.online || (action.type === 'send' && state.recovery)) return state;
    const incoming = action.type === 'receive';
    const item = message(uid(), incoming ? 'inbound' : 'outbound', text, clock(), {
      author: incoming ? undefined : 'human', kind: action.image ? 'image' : undefined,
      history: incoming && state.recovery,
    });
    const messages = [...current.messages, item];
    if (incoming && state.globalAi && current.ai && !state.recovery) {
      messages.push(message(uid(), 'outbound', draftReply(text), clock(), { author: 'ai' }));
    }
    const next = update({ ...current, messages, resolved: incoming && !state.recovery ? false : current.resolved });
    return record(next, state.recovery ? '恢复期间收录' : incoming ? '模拟入站已处理' : '演示回复已发送', `${current.name} · ${state.recovery ? '仅历史，不触发 AI' : incoming && messages.length > current.messages.length + 1 ? '已生成演示 AI 回复' : incoming ? '等待人工回复' : '本地会话已更新'}`, 'message');
  }
  return state;
}

export const latest = (c: Conversation) => [...c.messages].reverse().find(m => !m.history && m.direction !== 'system');
