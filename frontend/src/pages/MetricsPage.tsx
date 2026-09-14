import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { MetricRow, MetricsResponse } from '../types'

const DIM_ORDER = ['延迟', '音画同步', '质量', '稳定性', '并发', '资源']

export default function MetricsPage() {
  const [data, setData] = useState<MetricsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .metrics()
      .then(setData)
      .catch((e: Error) => setError(e.message))
  }, [])

  const rows = data?.rows ?? []
  const groups = DIM_ORDER.map((d) => ({ dim: d, items: rows.filter((r) => r.dimension === d) })).filter(
    (g) => g.items.length > 0,
  )
  const measured = rows.filter((r) => r.status !== 'pending').length

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>指标看板</h1>
          <div className="desc">
            PRD §3 验收标准的实测对照。数据源为 <span className="num">backend/eval/reports/*.json</span>
            （每个数字都有对应验证脚本），未执行项显示「未测」而不是占位值。
          </div>
        </div>
        <div className="topbar-meta">
          <span className="kv">
            <span className="muted">已实测</span>
            <span className="badge live">
              {measured}/{rows.length}
            </span>
          </span>
          <span className="kv">
            <span className="muted">报告目录</span>
            <span className="badge">{data ? data.reports_found.length : 0} 份</span>
          </span>
        </div>
      </div>

      {error && (
        <div className="notice">
          <strong>无法读取指标</strong>
          <span>{error}</span>
        </div>
      )}

      <div className="legend">
        <span className="item">
          <span className="swatch" style={{ background: 'var(--ok)' }} /> 达标
        </span>
        <span className="item">
          <span className="swatch" style={{ background: 'var(--warn)' }} /> 未达标（高于目标）
        </span>
        <span className="item">
          <span className="swatch" style={{ background: 'var(--fail)' }} /> 明显超出目标
        </span>
        <span className="item">
          <span className="swatch" style={{ background: 'var(--text-tertiary)' }} /> 未测 / 未执行
        </span>
      </div>

      {groups.map((g) => (
        <div key={g.dim}>
          <div className="page-head" style={{ marginBottom: 'var(--sp-3)' }}>
            <h1 style={{ fontSize: 'var(--fs-lg)' }}>{g.dim}</h1>
          </div>
          <div className="metric-grid">
            {g.items.map((r) => (
              <MetricCard key={r.metric} row={r} />
            ))}
          </div>
        </div>
      ))}

      <div className="panel">
        <div className="panel-head">
          <h2>数据来源明细</h2>
          <span className="hint">每行可回溯到脚本与报告文件（PRD FR-08）</span>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 76 }}>维度</th>
                <th style={{ width: 132 }}>指标</th>
                <th style={{ width: 116 }}>实测</th>
                <th style={{ width: 92 }}>目标</th>
                <th>口径 / 场景</th>
                <th style={{ width: 120 }}>环境</th>
                <th style={{ width: 240 }}>来源</th>
                <th style={{ width: 88 }}>日期</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.dimension}-${r.metric}`}>
                  <td className="muted">{r.dimension}</td>
                  <td>{r.metric}</td>
                  <td className="mono">{r.value ?? <span className="muted">未测</span>}</td>
                  <td className="mono muted">{r.target ?? '—'}</td>
                  <td className="muted">{r.scenario ?? '—'}</td>
                  <td className="muted">{r.env ?? '—'}</td>
                  <td className="mono" style={{ color: 'var(--text-secondary)' }}>
                    {r.source ?? '—'}
                  </td>
                  <td className="mono muted">{r.measuredAt ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function MetricCard({ row }: { row: MetricRow }) {
  return (
    <div className="metric-card">
      <div className="dim">{row.dimension}</div>
      <div className="name">{row.metric}</div>
      <div className={`reading ${row.status}`}>{row.value ?? '未测'}</div>
      <div className="meta">
        <span>目标 {row.target ?? '未定'}</span>
        <span>{row.scenario}</span>
        {row.source && <span className="src">{row.source}</span>}
      </div>
    </div>
  )
}
