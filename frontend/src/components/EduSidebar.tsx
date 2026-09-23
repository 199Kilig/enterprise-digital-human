import { useCallback, useEffect, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import type { HealthResponse } from '../types'
import type { Theme } from '../hooks/useTheme'
import {
  IconBot,
  IconGauge,
  IconHistory,
  IconLedger,
  IconRefresh,
  IconSparkle,
  IconTarget,
  IconTrend,
} from './edu/Icons'

/**
 * 侧栏（信息架构 2026-09-23 重构）
 * ---------------------------------------------------------------------------
 * 产品视角（主导航，4 项）：学习目标 / 对话辅导 / 学习洞察 / 数字人资产
 *
 * 重构前主导航只有「学习目标」一项 —— `/console` 对话页**不在主导航里**，
 * 用户只能点侧栏助手卡或「查看全部历史对话」进入，这是"界面像半成品"的直接原因之一。
 *
 * 已移除：
 *   · 「学习工具」4 格（错题集合/视频讲解/知识库/阶段目标）—— 全部指向 `/tools/:key`
 *     占位页，无后端数据源；其能力由 `/insights`（真数据）+ 页尾「待接入能力」承接。
 *   · 「小奈 Pro」卡片 —— 只有一个 `disabled` 按钮写着"敬请期待"，是纯装饰。
 *     （依据：飞书卡片规范「盲目使用卡片设计，会使得阅读低效和屏幕空间浪费」）
 */

/** 与 pages/ConsolePage.tsx 共用同一个落盘 key：这里读的是**真实**本地对话历史 */
const STORAGE_KEY = 'dh.console.session.v1'

/** 主导航：产品视角的四个入口 */
const MAIN_NAV = [
  { to: '/', label: '学习目标', hint: '首页 · 运行总览', Icon: IconTarget, end: true },
  { to: '/console', label: '对话辅导', hint: '和数字人实时对话', Icon: IconSparkle, end: false },
  { to: '/insights', label: '学习洞察', hint: '提问统计与记录', Icon: IconTrend, end: false },
  { to: '/library', label: '数字人资产', hint: '形象 · 产物 · 服务', Icon: IconBot, end: false },
]

const ENG_VIEWS = [
  { to: '/studio', label: '链路工作台', hint: '技术详情', Icon: IconBot },
  { to: '/metrics', label: '指标看板', hint: '五维验收指标 vs 目标', Icon: IconGauge },
  { to: '/ledger', label: '评估台账', hint: 'eval-history 原始记录', Icon: IconLedger },
]

interface RecentItem {
  text: string
  at: number
}

/** 读 ConsolePage 落盘的本地会话，取最近 3 条用户提问。解析失败一律当空（不抛）。 */
function readRecent(): RecentItem[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    const msgs = (parsed as { messages?: unknown } | null)?.messages
    if (!Array.isArray(msgs)) return []
    return msgs
      .filter(
        (m): m is { role: string; text: string; at?: unknown } =>
          typeof m === 'object' &&
          m !== null &&
          (m as { role?: unknown }).role === 'user' &&
          typeof (m as { text?: unknown }).text === 'string',
      )
      .slice(-3)
      .reverse()
      .map((m) => ({ text: m.text, at: typeof m.at === 'number' ? m.at : 0 }))
  } catch {
    return []
  }
}

function shortTime(at: number): string {
  if (!at) return ''
  const d = new Date(at)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export default function EduSidebar({
  health,
  healthError,
  theme,
  setTheme,
  open,
  onClose,
}: {
  health: HealthResponse | null
  healthError: string | null
  theme: Theme
  setTheme: (t: Theme) => void
  /** 窄屏抽屉是否展开（≥960px 侧栏常驻，该值无意义） */
  open: boolean
  /** 关闭抽屉：点遮罩 / 点导航项 / 按 Esc 都会调它 */
  onClose: () => void
}) {
  const [recent, setRecent] = useState<RecentItem[]>([])
  const reload = useCallback(() => setRecent(readRecent()), [])

  // 每次路由变化重读一次：在 /console 聊完回到首页，侧栏立刻反映新记录
  useEffect(() => {
    reload()
  }, [reload])

  const online = !healthError && health !== null

  return (
    <aside id="edu-side" className={`edu-side${open ? ' open' : ''}`}>
      {/* 点标识回首页：此前侧栏没有任何指向 / 的入口，从首页点出去就回不来（只能靠浏览器后退） */}
      <Link to="/" className="edu-assistant" onClick={onClose} title="回到学习目标首页">
        <img className="edu-avatar" src="/media/avatar_face.png" alt="AI 学习助手形象" />
        <div>
          <div className="edu-assistant-name">
            小奈 <span className="edu-tag-ai">AI</span>
          </div>
          <div className="edu-assistant-sub">你的专属 AI 学习助手</div>
        </div>
      </Link>

      {/* 连接状态绑定真实 /api/v1/health 探测（10s 轮询，见 AppShell） */}
      <span
        className={`edu-conn${online ? ' on' : ''}`}
        title={
          online
            ? `backend 8010 在线 · lip 服务 ${
                health?.lip_service.reachable ? '在线' : '未启动'
              } · 活跃会话 ${health?.sessions_active ?? 0}`
            : healthError ?? '正在探测 backend...'
        }
      >
        <span className="edu-conn-dot" />
        {online ? '已连接 · 数字人链路在线' : health ? '探测中' : '尚未连接'}
      </span>

      {/* 主导航（产品视角）。end 必须有：否则 to="/" 在 /console 等路径上也会算 active */}
      <nav className="edu-side-nav">
        {MAIN_NAV.map(({ to, label, hint, Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onClose}
            className={({ isActive }) => `navitem${isActive ? ' active' : ''}`}
          >
            <Icon size={15} />
            <span>
              <div>{label}</div>
              <div className="muted" style={{ fontSize: 'var(--fs-micro)' }}>
                {hint}
              </div>
            </span>
          </NavLink>
        ))}
      </nav>

      <section className="edu-side-block">
        <div className="edu-side-head">
          <IconHistory size={14} />
          最近对话
          <span className="edu-spacer" />
          <button className="edu-icon-btn" onClick={reload} title="重新读取本地对话记录">
            <IconRefresh size={13} />
          </button>
        </div>
        <ul className="edu-history">
          {recent.length > 0 ? (
            recent.map((r, i) => (
              <li key={`${r.at}-${i}`}>
                <Link to="/console" onClick={onClose} title={`${shortTime(r.at)} · ${r.text}`}>
                  {shortTime(r.at)} {r.text}
                </Link>
              </li>
            ))
          ) : (
            <li className="edu-empty">暂无本地对话记录，去「对话辅导」问一句就有了</li>
          )}
        </ul>
        <Link className="edu-cta" to="/insights" onClick={onClose} style={{ marginTop: 'var(--sp-2)' }}>
          查看学习洞察 <span>›</span>
        </Link>
      </section>

      <section className="edu-side-block">
        <div className="edu-side-head">
          <IconGauge size={14} />
          工程视图
        </div>
        <nav style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
          {ENG_VIEWS.map(({ to, label, hint, Icon }) => (
            <NavLink
              key={to}
              to={to}
              onClick={onClose}
              className={({ isActive }) => `navitem${isActive ? ' active' : ''}`}
            >
              <Icon size={15} />
              <span>
                <div>{label}</div>
                <div className="muted" style={{ fontSize: 'var(--fs-micro)' }}>
                  {hint}
                </div>
              </span>
            </NavLink>
          ))}
        </nav>
      </section>

      <footer className="edu-side-foot">
        <div className="edu-theme-row">
          <span>主题</span>
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
        <span className="edu-demo-note">
          学习洞察的统计来自本机对话记录（浏览器本地，最多保留最近 50 条），不上传
        </span>
        <div>
          工程数据来源：backend/eval/reports/*.json · docs/eval-history.md
          <br />
          契约：SPEC-接口与协议规范 v1.1
        </div>
      </footer>
    </aside>
  )
}
