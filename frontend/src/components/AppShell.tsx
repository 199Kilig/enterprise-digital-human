import { Outlet } from 'react-router-dom'
import { Suspense, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useTheme } from '../hooks/useTheme'
import EduSidebar from './EduSidebar'
import type { HealthResponse } from '../types'

/**
 * 应用骨架（教育版）
 * ---------------------------------------------------------------------------
 * 骨架结构对齐本轮设计稿：左侧教育版侧栏（助手卡 / 最近对话 / 学习工具 / Pro 卡），
 * 右侧内容区由各页面自己渲染页头。
 * 侧栏的「已连接 / 尚未连接」绑定真实 /api/v1/health 探测（10s 轮询），不是装饰文案。
 */
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
    <div className="edu-shell">
      <EduSidebar health={health} healthError={healthError} theme={theme} setTheme={setTheme} />
      <main className="edu-main">
        <div className="edu-main-inner">
          {/* 页面按需加载：只让内容区出占位，侧栏与页头不参与重渲染 */}
          <Suspense fallback={<div className="edu-route-loading">加载中…</div>}>
            <Outlet />
          </Suspense>
        </div>
      </main>
    </div>
  )
}
