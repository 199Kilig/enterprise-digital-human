import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { LedgerResponse } from '../types'

export default function LedgerPage() {
  const [data, setData] = useState<LedgerResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')

  useEffect(() => {
    api
      .ledger()
      .then(setData)
      .catch((e: Error) => setError(e.message))
  }, [])

  const rows = useMemo(() => {
    const all = data?.rows ?? []
    if (!q.trim()) return all
    const k = q.trim().toLowerCase()
    return all.filter((r) =>
      [r.date, r.metric, r.value, r.scope, r.env, r.source].join(' ').toLowerCase().includes(k),
    )
  }, [data, q])

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>评估台账</h1>
          <div className="desc">
            <span className="num">docs/eval-history.md</span> 的原始记录（PRD §3「固定测试集 + 同口径回归台账」）。
            台账是量化指标的唯一入口，此处只读展示，不在后端重算。
          </div>
        </div>
        <div className="topbar-meta">
          <span className="kv">
            <span className="muted">记录</span>
            <span className="badge live">{rows.length}</span>
          </span>
        </div>
      </div>

      {error && (
        <div className="notice">
          <strong>无法读取台账</strong>
          <span>{error}</span>
        </div>
      )}

      <div className="panel">
        <div className="panel-head">
          <h2>指标登记</h2>
          <div className="grow" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="过滤：指标 / 环境 / 来源"
            style={{
              background: 'var(--bg-inset)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--r-md)',
              color: 'var(--text-primary)',
              padding: '5px 10px',
              fontFamily: 'inherit',
              fontSize: 'var(--fs-sm)',
              width: 260,
            }}
          />
        </div>
        <div className="ledger-scroll">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 92 }}>日期</th>
                <th style={{ width: 180 }}>指标</th>
                <th>数值</th>
                <th>口径 / 场景</th>
                <th style={{ width: 120 }}>环境</th>
                <th style={{ width: 240 }}>来源</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td className="mono muted">{r.date}</td>
                  <td>{r.metric}</td>
                  <td>{r.value}</td>
                  <td className="muted">{r.scope}</td>
                  <td className="muted">{r.env}</td>
                  <td className="mono" style={{ color: 'var(--text-secondary)' }}>
                    {r.source}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    无匹配记录
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
