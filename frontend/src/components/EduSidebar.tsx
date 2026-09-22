import { useCallback, useEffect, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import type { HealthResponse } from '../types'
import type { Theme } from '../hooks/useTheme'
import { learningTools } from '../data/learning'
import {
  IconBot,
  IconBook,
  IconCrown,
  IconGauge,
  IconHistory,
  IconLedger,
  IconMistake,
  IconRefresh,
  IconTarget,
  IconTool,
  IconTrend,
  IconVideo,
} from './edu/Icons'

/** 与 pages/ConsolePage.tsx 共用同一个落盘 key：这里读的是**真实**本地对话历史 */
const STORAGE_KEY = 'dh.console.session.v1'

const TOOL_ICONS: Record<string, typeof IconTool> = {
  mistakes: IconMistake,
  videos: IconVideo,
  knowledge: IconBook,
  stage: IconTrend,
}

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

      {/* 显式的主导航入口。end 必须有：否则 to="/" 在 /console 等路径上也会算 active */}
      <nav className="edu-side-nav">
        <NavLink
          to="/"
          end
          onClick={onClose}
          className={({ isActive }) => `navitem${isActive ? ' active' : ''}`}
        >
          <IconTarget size={15} />
          <span>
            <div>学习目标</div>
            <div className="muted" style={{ fontSize: 'var(--fs-micro)' }}>
              首页 · 学习仪表盘
            </div>
          </span>
        </NavLink>
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
            <li className="edu-empty">暂无本地对话记录，点「查看全部历史对话」开始提问</li>
          )}
        </ul>
        <Link className="edu-cta" to="/console" onClick={onClose} style={{ marginTop: 'var(--sp-2)' }}>
          查看全部历史对话 <span>›</span>
        </Link>
      </section>

      <section className="edu-side-block">
        <div className="edu-side-head">
          <IconTool size={14} />
          学习工具
        </div>
        <div className="edu-tools">
          {learningTools.map((t) => {
            const Icon = TOOL_ICONS[t.key] ?? IconTool
            return (
              <Link
                key={t.key}
                to={`/tools/${t.key}`}
                onClick={onClose}
                className={`edu-tool${t.status === 'planned' ? ' planned' : ''}`}
                title={t.desc}
              >
                <span className="edu-tool-ico">
                  <Icon size={16} />
                </span>
                {t.name}
              </Link>
            )
          })}
        </div>
      </section>

      <div className="edu-pro">
        <div className="edu-pro-head">
          <IconCrown size={15} />
          小奈 Pro
          <span className="edu-pro-tag">报告</span>
        </div>
        <div className="edu-pro-sub">解锁更强模型与高级能力</div>
        <button className="edu-pro-btn" disabled>
          敬请期待
        </button>
      </div>

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
        <span className="edu-demo-note">学习域数据为前端演示占位（后端未接学习域接口）</span>
        <div>
          工程数据来源：backend/eval/reports/*.json · docs/eval-history.md
          <br />
          契约：SPEC-接口与协议规范 v1.1
        </div>
      </footer>
    </aside>
  )
}
