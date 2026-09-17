import { Fragment, useEffect, useRef, type ReactNode } from 'react';
import { ArrowLeft, ArrowUpRight, Bot, Check, CheckCheck, ChevronRight, CircleCheck, History, Image as ImageIcon, Inbox, MessageSquarePlus, MoreHorizontal, Search, Send, ShieldCheck, Sparkles, UserRound, X } from 'lucide-react';
import type { Conversation, DemoState, Message } from './model';
import { latest } from './model';

export function IconButton({ label, children, onClick, disabled = false, className = '' }: { label: string; children: ReactNode; onClick: () => void; disabled?: boolean; className?: string }) {
  return <button type="button" className={`icon-button ${className}`} aria-label={label} title={label} onClick={onClick} disabled={disabled}>{children}</button>;
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: () => void; label: string }) {
  return <button className={`toggle ${checked ? 'on' : ''}`} role="switch" aria-checked={checked} aria-label={label} onClick={onChange}><span /></button>;
}

export function Avatar({ conversation, small = false }: { conversation: Conversation; small?: boolean }) {
  return <span className={`avatar ${conversation.color} ${small ? 'small' : ''}`}>{conversation.avatar}</span>;
}

export function ConversationList({ conversations, selected, onSelect, query, setQuery, filter, setFilter }: {
  conversations: Conversation[]; selected: string; onSelect: (id: string) => void;
  query: string; setQuery: (q: string) => void; filter: string; setFilter: (f: string) => void;
}) {
  const visible = conversations.filter(c => (!query || `${c.name} ${c.topic} ${c.messages.map(m => m.text).join(' ')}`.toLowerCase().includes(query.toLowerCase())) && (filter === 'all' || filter === 'unread' && c.unread > 0 || filter === 'manual' && !c.ai));
  return <aside className="conversation-list" aria-label="会话列表">
    <div className="list-heading"><h2>会话</h2><span className="count">{conversations.length}</span><span className="list-heading-note">全部联系人</span></div>
    <label className="search"><Search size={16} /><input aria-label="搜索会话" placeholder="搜索联系人或消息" value={query} onChange={e => setQuery(e.target.value)} />{query && <button aria-label="清空搜索" onClick={() => setQuery('')}><X size={14} /></button>}</label>
    <div className="list-tabs" role="tablist" aria-label="会话筛选">{[['all', '全部'], ['unread', '未读'], ['manual', '人工']].map(([key, label]) => <button key={key} role="tab" aria-selected={filter === key} onClick={() => setFilter(key)}>{label}{key === 'unread' && <span>{conversations.filter(c => c.unread > 0).length}</span>}</button>)}</div>
    <div className="conversation-scroll">
      {visible.map(c => <button className={`conversation-row ${selected === c.id ? 'selected' : ''}`} key={c.id} onClick={() => onSelect(c.id)} aria-current={selected === c.id ? 'true' : undefined}>
        <div className="avatar-wrap"><Avatar conversation={c} />{c.unread > 0 && <span className="unread-dot" />}</div>
        <div className="conversation-copy"><div className="row-title"><strong>{c.name}</strong><time>{latest(c)?.time}</time></div><p>{latest(c)?.kind === 'image' ? '[图片]' : latest(c)?.text}</p><div className="row-meta"><span>{c.topic}</span><span className={c.resolved ? 'resolved-text' : c.ai ? 'ai-text' : 'manual-text'}>{c.resolved ? '已完成' : c.ai ? 'AI 托管' : '人工接待'}</span></div></div>
      </button>)}
      {!visible.length && <div className="empty"><Search /><strong>没有匹配的会话</strong><span>试试其他关键词或筛选条件</span></div>}
    </div>
    <div className="list-footer"><ShieldCheck size={14} /><span>演示会话 · 全部为虚构数据</span></div>
  </aside>;
}

export function MessageBubble({ message, conversation, onImage }: { message: Message; conversation: Conversation; onImage: () => void }) {
  if (message.direction === 'system') return <div className="system-message"><span />{message.text}<span /></div>;
  const outgoing = message.direction === 'outbound';
  return <div className={`message-row ${outgoing ? 'outgoing' : 'incoming'}`} data-history={message.history || undefined}>
    {!outgoing && <Avatar conversation={conversation} small />}
    <div className="message-content"><div className="message-meta"><span>{outgoing ? message.author === 'ai' ? 'AI 助手' : '演示坐席' : conversation.name}</span><time>{message.time}</time>{message.history && <span className="history-label"><History size={11} />历史补录</span>}</div>
      {message.kind === 'image' ? <button className="image-message" onClick={onImage} aria-label="查看图片：设备状态示例"><img src={`${import.meta.env.BASE_URL}device-sample.png`} alt="虚构设备的连接状态示例" /><span><ImageIcon size={13} />设备状态示例<ArrowUpRight size={13} /></span></button> : <div className="bubble">{message.text}</div>}
      {outgoing && <div className="delivery"><CheckCheck size={12} />{message.history ? '历史记录' : message.author === 'ai' ? '演示 AI 回复' : '演示发送成功'}</div>}
    </div>
    {outgoing && <span className={`agent-avatar ${message.author === 'ai' ? 'bot' : ''}`}>{message.author === 'ai' ? <Bot size={17} /> : <UserRound size={17} />}</span>}
  </div>;
}

export function Chat({ conversation, state, active, draft, setDraft, onSend, onToggleAi, onResolve, onIncoming, onDraft, onImage, onAttach, onBack, onDetails }: {
  conversation: Conversation; state: DemoState; active: boolean; draft: string; setDraft: (s: string) => void;
  onSend: () => void; onToggleAi: () => void; onResolve: () => void; onIncoming: () => void;
  onDraft: () => void; onImage: () => void; onAttach: () => void; onBack: () => void; onDetails: () => void;
}) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest' }); }, [conversation.id, conversation.messages.length, active]);
  const paused = !state.online || state.recovery;
  return <section className="chat" aria-label={`${conversation.name}的会话`}>
    <header className="chat-heading">
      <IconButton label="返回会话列表" className="mobile-back" onClick={onBack}><ArrowLeft size={18} /></IconButton>
      <Avatar conversation={conversation} small /><div className="chat-title"><h2>{conversation.name}<span className="source-badge">微信联系人</span></h2><span>演示账号 A<span className="separator">/</span>{conversation.topic}</span></div>
      <div className="chat-actions"><label>AI 托管<Toggle label="当前会话 AI 托管" checked={conversation.ai} onChange={onToggleAi} /></label><IconButton label="会话信息" onClick={onDetails}><MoreHorizontal size={19} /></IconButton></div>
    </header>
    <div className={`conversation-status ${conversation.ai && state.globalAi ? '' : 'manual'}`}><span><span className="status-dot" />{state.recovery ? '历史恢复中 · 自动回复暂停' : !state.online ? '设备离线 · 发送暂停' : conversation.resolved ? '会话已完成' : conversation.ai && state.globalAi ? 'AI 接待中' : '人工接待中'}</span><button onClick={onResolve}><CircleCheck size={14} />{conversation.resolved ? '重新打开' : '标记完成'}</button></div>
    <div className="message-scroll" aria-label="消息记录">{conversation.messages.map((m, index) => <Fragment key={m.id}>{(index === 0 || !!conversation.messages[index - 1].history !== !!m.history) && <div className="date-divider">{m.history ? '历史记录 · 示例时间' : '今天'}</div>}<MessageBubble message={m} conversation={conversation} onImage={onImage} /></Fragment>)}<div ref={bottom} /></div>
    <div className="composer">
      <div className="composer-toolbar"><strong>人工回复</strong><div className="toolbar-tools"><IconButton label="添加示例图片" disabled={paused} onClick={onAttach}><ImageIcon size={17} /></IconButton><IconButton label="模拟收到新消息" disabled={!state.online} onClick={onIncoming}><MessageSquarePlus size={17} /></IconButton><button className="draft-button" onClick={onDraft} disabled={paused}><Sparkles size={14} />演示草稿</button></div></div>
      <textarea aria-label="回复内容" maxLength={2000} value={draft} onChange={e => setDraft(e.target.value)} placeholder={paused ? state.recovery ? '结束历史恢复后可发送回复' : '设备离线，暂时无法发送' : '输入回复内容…'} disabled={paused} />
      <div className="composer-bottom"><span>{draft.length}/2000<span className="composer-divider">·</span>仅发送到演示会话</span><button className="primary" onClick={onSend} disabled={paused || !draft.trim()}><Send size={15} />发送回复</button></div>
    </div>
  </section>;
}

export function Details({ conversation, state }: { conversation: Conversation; state: DemoState }) {
  return <div className="details-content"><div className="details-profile"><Avatar conversation={conversation} /><h3>{conversation.name}</h3><span>虚构联系人</span></div>
    <section><h3>会话信息</h3><dl><dt>所属账号</dt><dd>演示账号 A</dd><dt>会话类型</dt><dd>外部联系人 · 单聊</dd><dt>接待方式</dt><dd>{conversation.ai ? 'AI 托管' : '人工接待'}</dd><dt>会话状态</dt><dd>{conversation.resolved ? '已完成' : '进行中'}</dd><dt>消息数量</dt><dd>{conversation.messages.filter(m => m.direction !== 'system').length} 条</dd></dl></section>
    <section><h3>会话标签</h3><div className="tags"><span>{conversation.topic}</span><span>演示访客</span></div></section>
    <section><h3>近期动态</h3><ol className="mini-timeline">{state.events.slice(0, 4).map(event => <li key={event.id}><span className="timeline-point" /><time>{event.time}</time><strong>{event.title}</strong><p>{event.detail}</p></li>)}</ol></section>
    <div className="details-foot"><ShieldCheck size={16} /><span>本地演示环境</span><Check size={13} /></div>
  </div>;
}

export function Modal({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return <dialog ref={ref} className={`modal ${wide ? 'wide' : ''}`} aria-label={title} onCancel={e => { e.preventDefault(); onClose(); }} onClick={e => { if (e.target === ref.current) onClose(); }}><div className="modal-inner"><header><h2>{title}</h2><IconButton label="关闭弹窗" onClick={onClose}><X size={19} /></IconButton></header>{children}</div></dialog>;
}

export function EmptyPanel() { return <div className="empty"><Inbox size={28} /><strong>选择一个会话</strong><span>会话消息会显示在这里</span><ChevronRight size={16} /></div>; }
