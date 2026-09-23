import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  IconHistory,
  IconRefresh,
  IconSparkle,
  IconTarget,
  IconTasks,
  IconTrend,
  IconWarn,
} from '../components/edu/Icons'
import type { ChatMessage } from '../types'

/**
 * 学习洞察（`/insights`）
 * ---------------------------------------------------------------------------
 * 数据口径：**全部来自真实的本地对话记录**（localStorage `dh.console.session.v1`，
 * 与 `/console` 对话页、侧栏「最近对话」同源同结构）。本页不引入任何假数据，
 * 也不做静态兜底 —— 没有记录就明说没有，并把人引到 `/console` 去产生记录。
 *
 * ⚠️ 一条必须显式告知用户的边界：本地持久化只保留**最近 50 条消息**
 * （`useDuplexSession.ts` 的 PERSIST_LIMIT）。所以这里的"累计"是**窗口内累计**，
 * 不是历史全程累计。页面顶部把它写出来，避免数字误导。
 *
 * 后端缺口（错题集 / 知识点掌握度）不假装有：列在页尾，写明所需接口。
 * 这页取代了此前的 4 个 `/tools/:key` 占位页（错题集合 / 视频讲解 / 知识库 / 阶段目标）。
 */

const STORAGE_KEY = 'dh.console.session.v1'
/** 与 useDuplexSession.PERSIST_LIMIT 对齐，仅用于文案告知 */
const PERSIST_LIMIT = 50

interface PersistedSession {
  sessionId: string
  messages: ChatMessage[]
}

function readAll(): ChatMessage[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const p = JSON.parse(raw) as PersistedSession
    return p && Array.isArray(p.messages) ? p.messages : []
  } catch {
    return []
  }
}

function isSameDay(a: number, b: number): boolean {
  const da = new Date(a)
  const db = new Date(b)
  return (
    da.getFullYear() === db.getFullYear() &&
    da.getMonth() === db.getMonth() &&
    da.getDate() === db.getDate()
  )
}

function fmtTime(at: number): string {
  const d = new Date(at)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 秒 → 人类可读（用于累计对话时长；窗口内累计，文案里已说明） */
function fmtDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m} 分钟`
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`
}

/** 提问归一化：去掉空白与标点后用于聚类（「通分是什么？」与「通分是什么」算同一问） */
function normalize(text: string): string {
  return text.replace(/[\s，。？！？!,.、；;：:'"“”‘’（）()【】\[\]]/g, '').toLowerCase()
}

interface TopQuestion {
  sample: string
  count: number
}

export default function InsightsPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const reload = useCallback(() => setMessages(readAll()), [])

  useEffect(() => {
    reload()
  }, [reload])

  const stats = useMemo(() => {
    const users = messages.filter((m) => m.role === 'user')
    const now = Date.now()

    // 累计对话时长：按消息时间戳排序后，用「相邻消息间隔 < 5 分钟」切会话段，
    // 每段时长 = 末条 - 首条（避免把中间隔了一天的空闲时间也算进去）
    const GAP_MS = 5 * 60 * 1000
    const sorted = [...messages].sort((a, b) => a.at - b.at).filter((m) => m.at > 0)
    let totalMs = 0
    let segStart: number | null = null
    let prev = 0
    for (const m of sorted) {
      if (segStart === null) {
        segStart = m.at
      } else if (m.at - prev > GAP_MS) {
        totalMs += prev - segStart
        segStart = m.at
      }
      prev = m.at
    }
    if (segStart !== null && prev > segStart) totalMs += prev - segStart

    // 活跃天数：按消息时间戳去重出天数
    const days = new Set(sorted.map((m) => new Date(m.at).toDateString()))

    // 高频提问 TOP 5（归一化后计数，展示时用最长的那条原文作样本）
    const bucket = new Map<string, { count: number; sample: string }>()
    for (const m of users) {
      const key = normalize(m.text)
      if (!key) continue
      const cur = bucket.get(key)
      if (cur) {
        cur.count += 1
        if (m.text.length > cur.sample.length) cur.sample = m.text
      } else {
        bucket.set(key, { count: 1, sample: m.text })
      }
    }
    const top: TopQuestion[] = [...bucket.values()]
      .sort((a, b) => b.count - a.count)
      .slice(0, 5)

    return {
      totalTurns: users.length,
      todayTurns: users.filter((m) => isSameDay(m.at, now)).length,
      durationS: totalMs / 1000,
      activeDays: days.size,
      top,
      recent: [...messages].filter((m) => m.at > 0).sort((a, b) => b.at - a.at).slice(0, 12),
      hasAny: messages.length > 0,
    }
  }, [messages])

  const maxTop = Math.max(1, ...stats.top.map((t) => t.count))

  return (
    <div className="edu-insights">
      <header className="edu-page-head">
        <div>
          <h1>
            <IconTrend size={18} />
            学习洞察
          </h1>
          <p>
            统计你自己产生的对话记录 —— 提问轮次、时长、常问的话题。数据来自本地记录，
            不上传、不伪造。
          </p>
        </div>
        <button className="edu-ghost" onClick={reload} title="重新读取本地对话记录">
          <IconRefresh size={13} /> 刷新
        </button>
      </header>

      {!stats.hasAny ? (
        /* 空态：明确说"还没有记录"，并给出产生记录的唯一入口 */
        <section className="edu-card edu-empty-state">
          <div className="edu-empty-title">还没有对话记录</div>
          <p className="edu-empty-sub">
            这页统计的是你真实问过的问题。去对话页问几个问题，回来就能看到数据。
          </p>
          <Link className="edu-cta edu-cta--inline" to="/console">
            去和小奈对话 <span>›</span>
          </Link>
        </section>
      ) : (
        <>
          {stats.totalTurns === 0 && (
            <div className="edu-insights-hint">
              <IconWarn size={14} />
              本地有对话消息但还没有你的提问（可能只有链路提示），再问一句就会有统计。
            </div>
          )}

          <section className="edu-insights-kpis">
            <div className="edu-card edu-kpi">
              <div className="edu-kpi-label">
                <IconTasks size={13} /> 提问轮次
              </div>
              <div className="edu-kpi-value">{stats.totalTurns}</div>
              <div className="edu-kpi-note">窗口内 · 你发出的问题条数</div>
            </div>
            <div className="edu-card edu-kpi">
              <div className="edu-kpi-label">
                <IconSparkle size={13} /> 今日提问
              </div>
              <div className="edu-kpi-value">{stats.todayTurns}</div>
              <div className="edu-kpi-note">按本地日期统计</div>
            </div>
            <div className="edu-card edu-kpi">
              <div className="edu-kpi-label">
                <IconHistory size={13} /> 对话时长
              </div>
              <div className="edu-kpi-value">{fmtDuration(stats.durationS)}</div>
              <div className="edu-kpi-note">相邻消息间隔 &lt; 5 分钟才连续计</div>
            </div>
            <div className="edu-card edu-kpi">
              <div className="edu-kpi-label">
                <IconTarget size={13} /> 活跃天数
              </div>
              <div className="edu-kpi-value">{stats.activeDays}</div>
              <div className="edu-kpi-note">出现过对话的自然日数</div>
            </div>
          </section>

          <div className="edu-insights-grid">
            <section className="edu-card">
              <div className="edu-card-head">
                <h2>常问的话题</h2>
                <span className="edu-card-sub">
                  按文本归一化聚类（忽略空白与标点），最多 5 条
                </span>
              </div>
              {stats.top.length === 0 ? (
                <p className="edu-muted-line">还没有可统计的提问。</p>
              ) : (
                <ul className="edu-toplist">
                  {stats.top.map((t) => (
                    <li key={t.sample}>
                      <div className="edu-topbar-label" title={t.sample}>
                        {t.sample}
                      </div>
                      <div className="edu-topbar-track">
                        <div
                          className="edu-topbar-fill"
                          style={{ width: `${Math.round((t.count / maxTop) * 100)}%` }}
                        />
                      </div>
                      <div className="edu-topbar-count">{t.count} 次</div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="edu-card">
              <div className="edu-card-head">
                <h2>最近对话记录</h2>
                <span className="edu-card-sub">最近 12 条 · 点击进入对话页继续</span>
              </div>
              <ul className="edu-recent">
                {stats.recent.map((m, i) => (
                  <li key={`${m.at}-${i}`} className={m.role}>
                    <span className={`edu-recent-role ${m.role}`}>
                      {m.role === 'user' ? '你' : m.role === 'digital' ? '奈' : '系'}
                    </span>
                    <span className="edu-recent-text" title={m.text}>
                      {m.text.length > 60 ? `${m.text.slice(0, 60)}…` : m.text}
                    </span>
                    <span className="edu-recent-time">{fmtTime(m.at)}</span>
                  </li>
                ))}
              </ul>
              <Link className="edu-cta" to="/console">
                去对话页继续 <span>›</span>
              </Link>
            </section>
          </div>

          <p className="edu-insights-scope">
            口径说明：本地记录最多保留最近 {PERSIST_LIMIT} 条消息，所以以上统计是
            <strong>窗口内</strong>数据，不是账号历史累计。清空对话或换浏览器会重新开始。
          </p>
        </>
      )}

      {/* ---- 后端缺口：显式列出，不假装有（守 ADR-009：不做查不到来源的数字） ---- */}
      <section className="edu-card edu-pending-card">
        <div className="edu-card-head">
          <h2>待接入能力</h2>
          <span className="edu-card-sub">需要后端新增接口，当前没有数据源，故不展示假数据</span>
        </div>
        <ul className="edu-pending">
          <li>
            <div className="edu-pending-name">错题集合</div>
            <div className="edu-pending-need">
              需要 <code>GET /api/v1/learning/mistakes</code>：按知识点聚合错题、错误次数排序
            </div>
            <div className="edu-pending-use">
              有了它之后：首页「待加强知识点」与这里的话题分析可以联动数字人逐题讲解
            </div>
          </li>
          <li>
            <div className="edu-pending-name">知识点掌握度</div>
            <div className="edu-pending-need">
              需要 <code>GET /api/v1/learning/mastery</code>：知识图谱节点 + 掌握水平
            </div>
            <div className="edu-pending-use">
              有了它之后：可画掌握度热力图与学习路径推荐（对标松鼠 Ai 的知识图谱定位）
            </div>
          </li>
        </ul>
      </section>
    </div>
  )
}
