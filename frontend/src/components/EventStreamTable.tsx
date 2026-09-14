import type { StreamEvent } from '../types'

function summarize(type: string, payload: Record<string, unknown>): string {
  switch (type) {
    case 'thinking':
      return `state=${payload.state ?? '?'}`
    case 'tts_audio':
      return `seq=${payload.seq} ${payload.start_ms}–${payload.end_ms}ms 音频${String(
        (payload.audio_b64 as string)?.length ?? 0,
      )}B`
    case 'lip_frame':
      return `seq=${payload.seq} pts=${payload.pts_ms}ms frame#${payload.frame_idx}`
    case 'brain_token':
      return String(payload.token ?? '')
    case 'done':
      return `audio=${payload.total_seq_audio} lip=${payload.total_seq_lip} ${payload.duration_ms}ms`
    case 'error':
      return `${payload.code}: ${payload.message}`
    case 'interrupted':
      return `state=${payload.state ?? 'interrupted'}`
    default:
      return JSON.stringify(payload).slice(0, 120)
  }
}

export default function EventStreamTable({ events }: { events: StreamEvent[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th style={{ width: 48 }}>#</th>
            <th style={{ width: 110 }}>event</th>
            <th style={{ width: 72 }}>t+</th>
            <th>payload 摘要</th>
          </tr>
        </thead>
        <tbody>
          {events.length === 0 && (
            <tr>
              <td colSpan={4} className="muted">
                尚无事件。发送一次对话后，此处按 SPEC §3.3 显示原始 SSE 事件。
              </td>
            </tr>
          )}
          {events.map((e) => (
            <tr key={`${e.seq}-${e.type}`}>
              <td className="mono">{e.seq}</td>
              <td>
                <span className="event-type" data-t={e.type}>
                  {e.type}
                </span>
                {e.seqGap && (
                  <span className="badge fail" style={{ marginLeft: 6 }}>
                    seq 跳变
                  </span>
                )}
              </td>
              <td className="mono">{e.atMs}ms</td>
              <td className="mono" style={{ color: 'var(--text-secondary)' }}>
                {summarize(e.type, e.payload)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
