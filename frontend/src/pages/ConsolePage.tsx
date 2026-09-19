import { useEffect, useRef, useState } from 'react'
import { IconPlay, IconSparkle } from '../components/edu/Icons'
import { STAGE_CLIPS } from '../data/stageClips'
import { useDuplexSession } from '../hooks/useDuplexSession'
import type { ChatMessage, SessionState } from '../types'

/**
 * 学习者对话页（默认入口 `/console`）
 * ---------------------------------------------------------------------------
 * 只给"好观感"：数字人形象 + 对话气泡 + 一个话筒。**不展示**任何技术数据——
 * 无状态机原文、无 SSE 事件流、无延迟瀑布、无 token/TTFT/口型帧数、无 session_id。
 * 那些读数统一在链路工作台 `/studio`（侧栏「工程视图」分组内），见 StudioPage。
 *
 * 口径说明：不是"假装链路不存在"，而是把技术读数移出学习者视线——
 * 状态提示改为拟人化文案（我在听你说 / 小奈正在想 / 小奈正在讲解），
 * 能力信号仍在（可打断、可语音、有麦克风电平），只是不再以数字形式出现。
 */

/** 拟人化状态文案（不出现状态机枚举值） */
const STATE_TEXT: Record<SessionState, string> = {
  idle: '正在连接…',
  listening: '我在听你说',
  thinking: '小奈正在想…',
  speaking: '小奈正在讲解',
  interrupted: '已停下，你继续',
  error: '出了点小问题',
}

/** 技术读数行（`SSE done · tokens …`）不出现在学习者视图；链路提示（如"会话已重置"）保留。 */
function isTechnicalLine(m: ChatMessage): boolean {
  return m.role === 'system' && m.text.startsWith('SSE done')
}

/** system 气泡是链路提示：技术原因（后端重启/SSE 中断）留在工作台，学习者视图说人话。 */
function displayText(m: ChatMessage): string {
  if (m.role !== 'system') return m.text
  if (m.text.startsWith('服务端会话已重置')) {
    return '上面的对话是刚才的记录；我这边重新开始了一轮，我们从现在继续。'
  }
  if (m.text.startsWith('本轮无回复')) {
    return '我刚才好像没答上来，你把问题再说一遍好吗？'
  }
  return m.text
}

export default function ConsolePage() {
  const s = useDuplexSession()
  const [draft, setDraft] = useState('')
  const listRef = useRef<HTMLDivElement>(null)

  const visible = s.messages.filter((m) => !isTechnicalLine(m))
  const busy = s.state === 'thinking'
  const speaking = s.state === 'speaking'

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [visible.length, s.streamText])

  const submit = () => {
    const t = draft.trim()
    if (!t) return
    s.sendTurn(t)
    setDraft('')
  }

  const stageSrc = s.lipClip?.url ?? STAGE_CLIPS[0].src

  return (
    <div className="edu-chat">
      <header className="edu-chat-head">
        <div>
          <h1>
            <IconSparkle size={18} />
            和 小奈 一起学
          </h1>
          <p>点下面的话筒问我问题，也可以直接打字。我讲得不对、或者你想插话，随时打断我。</p>
        </div>
        <div className="edu-chat-head-actions">
          <span className={`edu-chat-state${speaking ? ' speaking' : ''}`}>
            <span className="edu-chat-dot" />
            {STATE_TEXT[s.state]}
          </span>
          <button className="edu-ghost" onClick={() => void s.resetSession({ fresh: true })}>
            重新开始
          </button>
        </div>
      </header>

      <div className="edu-chat-grid">
        {/* ---- 数字人形象（真实产物：MuseTalk 口型视频；无实时片段时回落到 V-01 产物） ---- */}
        <section className="edu-chat-stage">
          <div className="edu-chat-stage-media">
            <video
              key={stageSrc}
              src={stageSrc}
              autoPlay={!!s.lipClip}
              loop={!s.lipClip}
              muted={!!s.lipClip}
              playsInline
            />
            <div className={`edu-chat-halo${speaking ? ' on' : ''}`} />
          </div>
          <div className="edu-chat-stage-foot">
            <span className="edu-assistant-name">
              小奈 <span className="edu-tag-ai">AI</span>
            </span>
            <span className="edu-chat-mic" title="麦克风电平">
              <span className="edu-chat-mic-fill" style={{ width: `${Math.min(100, s.micLevel * 400)}%` }} />
            </span>
          </div>
        </section>

        {/* ---- 对话 ---- */}
        <section className="edu-chat-side">
          <div className="edu-chat-list" ref={listRef}>
            {visible.length === 0 && !s.recording && (
              <div className="edu-chat-intro">
                <div className="edu-chat-intro-title">今天想从哪里开始？</div>
                <div className="edu-chat-intro-sub">
                  比如问我：「通分是什么意思」「这道分数题我卡住了」「帮我出两道练习题」
                </div>
              </div>
            )}

            {visible.map((m, i) => (
              <div className={`edu-bubble-row ${m.role}`} key={`${m.at}-${i}`}>
                {m.role !== 'system' && (
                  <span className="edu-bubble-avatar">{m.role === 'user' ? '我' : '奈'}</span>
                )}
                <div className={`edu-bubble ${m.role}`}>{displayText(m)}</div>
              </div>
            ))}

            {s.recording && (
              <div className="edu-bubble-row user">
                <span className="edu-bubble-avatar">我</span>
                <div className="edu-bubble user listening">{s.liveText || '（我在听…）'}</div>
              </div>
            )}

            {s.streamText !== null && s.streamText !== '' && (
              <div className="edu-bubble-row digital">
                <span className="edu-bubble-avatar">奈</span>
                <div className="edu-bubble digital typing">
                  {s.streamText}
                  <span className="edu-caret" />
                </div>
              </div>
            )}
          </div>

          {s.error && (
            <div className="edu-chat-alert">
              链路出了点问题：{s.error}
              <span className="edu-chat-alert-hint">详细读数见侧栏「工程视图 → 链路工作台」</span>
            </div>
          )}

          <div className="edu-chat-input">
            <button
              className={`edu-chat-mic-btn${s.recording ? ' recording' : ''}`}
              onClick={s.toggleMic}
              disabled={!s.session || busy}
              title={s.recording ? '点击结束并发送' : '点击开始语音输入（停顿约 1.2 秒自动结束）'}
            >
              {s.recording ? '结束并发送' : '开始提问'}
            </button>

            {speaking && (
              <button className="edu-chat-stop" onClick={() => void s.interrupt()} title="让数字人停下">
                让她停下
              </button>
            )}

            <input
              value={draft}
              placeholder="或直接打字提问…"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submit()}
              disabled={!s.session || busy}
            />
            <button className="edu-chat-send" onClick={submit} disabled={!s.session || busy}>
              <IconPlay size={13} />
              发送
            </button>
          </div>
        </section>
      </div>
    </div>
  )
}
