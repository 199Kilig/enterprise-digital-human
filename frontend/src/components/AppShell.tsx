import { NavLink, Outlet } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useTheme } from '../hooks/useTheme'
import type { HealthResponse } from '../types'

const NAV = [
  { to: '/console', idx: '01', label: '会话工作台', hint: '听→想→说→演 实时链路' },
  { to: '/metrics', idx: '02', label: '指标看板', hint: '五维验收指标 vs 目标' },
  { to: '/ledger', idx: '03', label: '评估台账', hint: 'eval-history 原始记录' },
]

export default function AppShell() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [theme, setTheme] = useTheme()

  useEffect(() => {
    let alive = true
    const tick = () =>
      api
        .health()
        .then((h) => {
          if (!alive) return
          setHealth(h)
          setHealthError(null)
        })
        .catch((e: Error) => alive && setHealthError(e.message))
    tick()
    const id = window.setInterval(tick, 10_000)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [])

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          实时交互数字人
          <span className="sub">链路工作台 · P1 骨架</span>
        </div>
        <div className="grow" />
        <div className="topbar-meta">
          <span className="kv">
            <span className="muted">backend</span>
            {healthError ? (
              <span className="badge fail">
                <span className="dot" />
                不可达
              </span>
            ) : (
              <span className="badge ok">
                <span className="dot pulse" />
                {health ? '8010 在线' : '探测中'}
              </span>
            )}
          </span>
          <span className="kv">
            <span className="muted">lip 服务</span>
            {health ? (
              <span className={`badge ${health.lip_service.reachable ? 'ok' : 'warn'}`}>
                {health.lip_service.url.replace('http://', '')} ·{' '}
                {health.lip_service.reachable ? health.lip_service.mode ?? '在线' : '未启动'}
              </span>
            ) : (
              <span className="badge pending">—</span>
            )}
          </span>
          <span className="kv">
            <span className="muted">实测报告</span>
            <span className="badge">{health ? `${health.reports_present} 份` : '—'}</span>
          </span>
          <div className="theme-toggle" role="group" aria-label="主题切换">
            <button
              className={theme === 'light' ? 'on' : ''}
              onClick={() => setTheme('light')}
              aria-pressed={theme === 'light'}
            >
              浅色
            </button>
            <button
              className={theme === 'dark' ? 'on' : ''}
              onClick={() => setTheme('dark')}
              aria-pressed={theme === 'dark'}
            >
              深色
            </button>
          </div>
        </div>
      </header>

      <div className="body">
        <nav className="sidenav">
          <div className="group-label">视图</div>
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) => `navitem${isActive ? ' active' : ''}`}
            >
              <span className="idx">{n.idx}</span>
              <span>
                <div>{n.label}</div>
                <div className="muted" style={{ fontSize: 'var(--fs-micro)' }}>
                  {n.hint}
                </div>
              </span>
            </NavLink>
          ))}
          <div className="footer">
            数据来源：backend/eval/reports/*.json
            <br />
            docs/eval-history.md
            <br />
            契约：SPEC-接口与协议规范 v1.1
          </div>
        </nav>

        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
