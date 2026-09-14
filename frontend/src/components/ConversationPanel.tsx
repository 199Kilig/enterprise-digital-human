import { useEffect, useRef, useState } from 'react'

export interface ChatMessage {
  role: 'user' | 'digital' | 'system'
  text: string
  at: number
}

export default function ConversationPanel({
  messages,
  disabled,
  recording,
  liveText,
  micLevel,
  onSend,
  onMicToggle,
}: {
  messages: ChatMessage[]
  disabled: boolean
  recording: boolean
  liveText: string
  micLevel: number
  onSend: (text: string) => void
  onMicToggle: () => void
}) {
  const [draft, setDraft] = useState('')
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight })
  }, [messages.length])

  const submit = () => {
    const t = draft.trim()
    if (!t) return
    onSend(t)
    setDraft('')
  }

  return (
    <div className="conv">
      <div className="conv-list" ref={listRef}>
        {messages.length === 0 && (
          <div className="empty-state">
            输入一句话开始对话——后端会走真实链路：
            <br />
            状态机转「想」→ DeepSeek 流式产出 token → CosyVoice 流式合成语音并播放
            <br />
            （回答语音会实时播放，请确认系统音量）
          </div>
        )}
        {messages.map((m, i) => (
          <div className={`msg ${m.role}`} key={i}>
            <span className="who">
              {m.role === 'user' ? '用户' : m.role === 'digital' ? '数字人' : '链路'} ·{' '}
              <span className="num">{new Date(m.at).toLocaleTimeString('zh-CN')}</span>
            </span>
            <div className="bubble">{m.text}</div>
          </div>
        ))}
        {recording && (
          <div className="msg user">
            <span className="who">
              用户 · <span className="num">正在说…</span>
            </span>
            <div className="bubble" style={{ borderStyle: 'dashed' }}>
              {liveText || '（等待语音…）'}
            </div>
          </div>
        )}
      </div>
      <div className="conv-input">
        <button
          className={`btn mic${recording ? ' recording' : ''}`}
          onClick={onMicToggle}
          disabled={disabled}
          title={recording ? '点击结束并发送' : '点击开始语音输入（静音 1.2s 自动结束）'}
        >
          {recording ? '■ 结束' : '🎙 说话'}
        </button>
        <div className="mic-level" title="麦克风电平">
          <div className="mic-level-fill" style={{ width: `${Math.min(100, micLevel * 400)}%` }} />
        </div>
        <input
          value={draft}
          placeholder="或直接打字（如：你们家运费怎么算？）"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          disabled={disabled}
        />
        <button className="btn primary" onClick={submit} disabled={disabled}>
          发送
        </button>
      </div>
    </div>
  )
}
