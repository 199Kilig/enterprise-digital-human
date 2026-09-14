import type { SessionState } from '../types'

/** 四段流水线（DESIGN §3.1 / SPEC §4.1：听 → 想 → 说 → 演） */
const STEPS = [
  { key: 'listening', name: '听', desc: 'ASR 流式识别', module: 'asr/' },
  { key: 'thinking', name: '想', desc: 'LLM 流式生成', module: 'brain/' },
  { key: 'speaking', name: '说', desc: 'TTS 首包即播', module: 'tts/' },
  { key: 'performing', name: '演', desc: '口型驱动 + 播放', module: 'lip/ render/' },
] as const

export default function StateStepper({
  state,
  speakingActive,
}: {
  state: SessionState
  speakingActive: boolean
}) {
  // 状态机 → 步骤态：interrupted 停在被打断的那一步
  const activeIndex =
    state === 'thinking' ? 1 : state === 'speaking' ? 2 : state === 'idle' ? -1 : 0

  return (
    <div className="stepper" role="list" aria-label="会话状态机">
      {STEPS.map((s, i) => {
        const isPerform = s.key === 'performing'
        const active =
          (i === activeIndex && state !== 'interrupted') || (isPerform && speakingActive && state === 'speaking')
        const done = state !== 'idle' && i < activeIndex
        const interrupted = state === 'interrupted' && i === activeIndex
        const dataState = interrupted ? 'interrupted' : active ? 'active' : done ? 'done' : 'idle'
        return (
          <div className="step" key={s.key} data-state={dataState} role="listitem">
            <div className="name">
              <span>{s.name}</span>
              <span className="muted" style={{ fontWeight: 400 }}>
                {s.desc}
              </span>
            </div>
            <div className="bar" />
            <div className="desc">
              {s.module}
              {interrupted ? ' · 已打断' : ''}
            </div>
          </div>
        )
      })}
    </div>
  )
}
