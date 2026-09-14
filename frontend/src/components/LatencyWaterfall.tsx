/**
 * 延迟瀑布图（PRD FR-07 延迟打点 / DESIGN §5.1 延迟预算）
 * 形态锚定 Datadog APM trace span 瀑布：左侧标签 / 中部 span / 右侧读数。
 * 实测值来自后端 /api/v1/metrics（读 backend/eval/reports/*.json），
 * 未测段显示为虚线占位，不编造数字。
 */
export interface WfRow {
  key: string
  label: string
  ms: number | null
  targetMs: number | null
  note: string
}

function fmt(ms: number) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`
}

export default function LatencyWaterfall({ rows }: { rows: WfRow[] }) {
  const measured = rows.filter((r) => r.ms != null) as (WfRow & { ms: number })[]
  const targets = rows.filter((r) => r.targetMs != null).map((r) => r.targetMs as number)
  const maxScale = Math.max(1000, ...measured.map((r) => r.ms), ...targets) * 1.15

  return (
    <div>
      <div className="waterfall">
        {rows.map((r) => {
          const over = r.ms != null && r.targetMs != null && r.ms > r.targetMs
          const near = r.ms != null && r.targetMs != null && !over && r.ms > r.targetMs * 0.85
          const cls = r.ms == null ? 'pending' : over ? 'over' : near ? 'warn' : ''
          const left = r.ms != null ? Math.min(100, ((r.targetMs ?? 0) / maxScale) * 100) : 0
          const width = r.ms != null ? Math.max(1, (r.ms / maxScale) * 100) : 0
          return (
            <div className="wf-row" key={r.key}>
              <div className="wf-label" title={r.note}>
                {r.label}
              </div>
              <div className="wf-track">
                {r.ms != null ? (
                  <div className={`wf-span ${cls}`} style={{ left: `${left}%`, width: `${width}%` }} />
                ) : (
                  <div className="wf-span pending" style={{ left: 0, width: '18%' }} />
                )}
              </div>
              <div className="wf-value">
                {r.ms != null ? (
                  <>
                    {fmt(r.ms)}
                    {r.targetMs != null && (
                      <div className="target" style={{ fontSize: 'var(--fs-micro)' }}>
                        目标 {fmt(r.targetMs)}
                      </div>
                    )}
                  </>
                ) : (
                  <span className="muted">未测</span>
                )}
              </div>
            </div>
          )
        })}
      </div>
      <div className="wf-axis">
        <span>段</span>
        <div className="ticks">
          <span>0</span>
          <span className="num">{fmt(maxScale / 2)}</span>
          <span className="num">{fmt(maxScale)}</span>
        </div>
        <span style={{ textAlign: 'right' }}>实测 / 目标</span>
      </div>
    </div>
  )
}
