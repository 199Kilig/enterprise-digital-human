import { useState } from 'react'
import type { SessionState } from '../types'
import type { LipSegment, LipVideoStats } from '../hooks/useLipVideo'

const STATE_LABEL: Record<SessionState, string> = {
  idle: '空闲',
  listening: '聆听中',
  thinking: '思考中',
  speaking: '说话中',
  interrupted: '已打断',
  error: '异常',
}

const STATE_CLASS: Record<SessionState, string> = {
  idle: 'pending',
  listening: 'live',
  thinking: 'warn',
  speaking: 'ok',
  interrupted: 'fail',
  error: 'fail',
}

export interface StageMeta {
  label: string
  value: string
}

export interface StageClip {
  label: string
  src: string
  note: string
}

/**
 * 数字人舞台：左侧预览（收小尺寸），右侧会话/产物详情。
 * clips 为云 GPU MuseTalk 真实推理产物，不做占位动画冒充；
 * 视频缺失时显示空态而不是假画面。
 */
export default function AvatarStage({
  state,
  sessionId,
  clips,
  liveClip,
  lipStats,
  meta,
  canInterrupt,
  onInterrupt,
  onReconnect,
}: {
  state: SessionState
  sessionId: string | null
  clips: StageClip[]
  /** 本轮实时口型片段（ADR-005 H.264）；无则回落到 clips 里的离线产物 */
  liveClip: LipSegment | null
  lipStats: LipVideoStats
  meta: StageMeta[]
  canInterrupt: boolean
  onInterrupt: () => void
  onReconnect: () => void
}) {
  const [clipIdx, setClipIdx] = useState(0)
  const [failedSrc, setFailedSrc] = useState<string | null>(null)
  const clip = clips[clipIdx]
  const src = liveClip?.url ?? clip.src
  const mediaOk = failedSrc !== src

  return (
    <div className="stage-layout">
      <div className="stage">
        {mediaOk ? (
          <video
            key={src}
            src={src}
            autoPlay={!!liveClip}
            controls={!liveClip}
            loop={!liveClip}
            muted={!!liveClip}
            playsInline
            onError={() => setFailedSrc(src)}
          />
        ) : (
          <div className="empty">
            未找到口型产物
            <br />
            <span className="num" style={{ fontSize: 'var(--fs-micro)' }}>
              {clip.src}
            </span>
            <br />
            放入云 GPU V-01 生成视频后自动播放
          </div>
        )}
        <div className="overlay">
          <span className={`badge ${STATE_CLASS[state]}`}>
            <span className={`dot${state !== 'idle' ? ' pulse' : ''}`} />
            {STATE_LABEL[state]}
          </span>
          {liveClip && <span className="badge ok">口型片段 #{liveClip.seq}</span>}
        </div>
      </div>

      <div className="stage-side">
        <div className="stage-side-head">
          <h3>会话详情</h3>
          <span className="badge">{mediaOk ? '产物在线' : '无产物'}</span>
        </div>

        <div className="stage-side-control">
          <label htmlFor="clip-select">产物</label>
          <select id="clip-select" value={clipIdx} onChange={(e) => setClipIdx(Number(e.target.value))}>
            {clips.map((c, i) => (
              <option value={i} key={c.src}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
        <div className="muted" style={{ fontSize: 'var(--fs-micro)', lineHeight: 1.6 }}>
          {clip.note}
        </div>

        <div className="kv-row">
          <span className="k">session_id</span>
          <span className="v">{sessionId ? sessionId.slice(0, 12) : '—'}</span>
        </div>
        <div className="kv-row">
          <span className="k">状态机</span>
          <span className="v">{state}</span>
        </div>
        <div className="kv-row">
          <span className="k">口型链路</span>
          <span className="v">
            {lipStats.segments > 0
              ? `H.264 片段 ×${lipStats.segments} · ${(lipStats.bytes / 1024).toFixed(0)} KB`
              : '—'}
          </span>
        </div>
        <div className="kv-row">
          <span className="k">音画偏差</span>
          <span className="v">
            {lipStats.lateMeanMs === null
              ? '—'
              : `均 +${lipStats.lateMeanMs}ms / 峰 +${lipStats.lateMaxMs ?? 0}ms`}
          </span>
        </div>
        {meta.map((m) => (
          <div className="kv-row" key={m.label}>
            <span className="k">{m.label}</span>
            <span className="v">{m.value}</span>
          </div>
        ))}

        <div className="stage-actions">
          <button className="btn danger" disabled={!canInterrupt} onClick={onInterrupt}>
            打断
          </button>
          <button className="btn" onClick={onReconnect}>
            重建会话
          </button>
        </div>
        <div className="muted" style={{ fontSize: 'var(--fs-micro)', lineHeight: 1.6 }}>
          打断（SPEC §5.2）仅在 speaking 态生效，其余状态幂等忽略。
        </div>
      </div>
    </div>
  )
}
