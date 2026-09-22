import { Suspense, lazy, useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { HealthResponse, MetricsResponse, MetricRow } from '../types'
import {
  IconGauge,
  IconPlay,
  IconRefresh,
  IconSparkle,
  IconTarget,
  IconTasks,
  IconTrend,
  IconWarn,
} from '../components/edu/Icons'

const RingProgress = lazy(() => import('../components/edu/RingProgress'))

/**
 * 首页 · 数字人运行总览（ADR-009）
 * ---------------------------------------------------------------------------
 * 数据口径：**全部来自后端真实只读接口** —— GET /api/v1/health、GET /api/v1/metrics
 * （后端读 backend/eval/reports/*.json）。页面上每个数字都能回溯到接口或报告文件。
 * 后端不可达时显示错误态并提供重试，**不做静态兜底**：展示查不到来源的数字正是本次
 * 改版要消除的问题（此前的学习域占位卡片已按 ADR-009 下线，learning.ts 仅供工具页使用）。
 * 交互真实性：「开始对话」进入 /console 的**真实** SSE 链路（流式 ASR → DeepSeek → CosyVoice + 打断）。
 */

const STATUS_META: Record<MetricRow['status'], { text: string; cls: string }> = {
  ok: { text: '达标', cls: 'ok' },
  warn: { text: '有争议', cls: 'warn' },
  fail: { text: '未达标', cls: 'fail' },
  pending: { text: '待插桩', cls: 'pending' },
}

/** 延迟预算条宽度：实测 / 目标（无实测返回 null，由调用方画灰条） */
function budgetPct(measured: number | null, target: number | null): number | null {
  if (measured == null || !target) return null
  return Math.min(100, Math.round((measured / target) * 100))
}

export default function LearningHomePage() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null)
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error'>('loading')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setPhase('loading')
    setError('')
    try {
      const [h, m] = await Promise.all([api.health(), api.metrics()])
      setHealth(h)
      setMetrics(m)
      setPhase('ready')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPhase('error')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const rows = metrics?.rows ?? []
  const measured = rows.filter((r) => r.status !== 'pending')
  const okCount = measured.filter((r) => r.status === 'ok').length
  const problems = rows.filter((r) => r.status === 'warn' || r.status === 'fail')
  const pending = rows.filter((r) => r.status === 'pending')
  const budget = metrics?.latency_budget ?? []
  const lip = health?.lip_service

  /** 诊断条目：逐条由真实状态推导（不是固定文案） */
  const findings: { level: 'ok' | 'warn' | 'info'; text: string }[] = []
  if (phase === 'ready' && health && metrics) {
    findings.push(
      lip?.reachable
        ? {
            level: 'ok',
            text: `口型服务在线（${lip.mode ?? '未知模式'}）—— 数字人口型由远端 GPU 实时驱动`,
          }
        : {
            level: 'warn',
            text: '口型服务未连接 —— 数字人不会动嘴。恢复：bash backend/deploy/restore-cloud-lip.sh',
          },
    )
    if (problems.length) {
      findings.push({
        level: 'warn',
        text: `${problems.length} 项指标未达标或有口径争议：${problems.map((p) => p.metric).join('、')}`,
      })
    }
    if (pending.length) {
      findings.push({
        level: 'info',
        text: `${pending.length} 项指标待插桩：${pending.map((p) => p.metric).join('、')}`,
      })
    }
    if (!problems.length && !pending.length) findings.push({ level: 'ok', text: '全部指标达标' })
    findings.push({
      level: 'info',
      text: `本页读数来自 ${metrics.reports_found.length} 份评测报告（${metrics.reports_dir}），原始记录可在「工程视图 → 评估台账」逐条核对`,
    })
  }

  if (phase === 'loading') {
    return (
      <div className="edu-page">
        <header className="edu-hero">
          <div>
            <h1>
              <span className="edu-hero-ico">
                <IconGauge size={16} />
              </span>
              运行总览
            </h1>
            <p>正在读取后端实时数据…</p>
          </div>
        </header>
        <div className="edu-dash">
          <section className="edu-card edu-card-skel" />
          <section className="edu-card edu-card-skel" />
          <section className="edu-card edu-card--tasks edu-card-skel" />
          <section className="edu-card edu-card-skel" />
          <section className="edu-card edu-card-skel" />
          <section className="edu-card edu-card--advice edu-card-skel" />
        </div>
      </div>
    )
  }

  if (phase === 'error') {
    return (
      <div className="edu-page">
        <header className="edu-hero">
          <div>
            <h1>
              <span className="edu-hero-ico">
                <IconWarn size={16} />
              </span>
              运行总览
            </h1>
            <p>数据来自后端实时接口，当前不可达</p>
          </div>
        </header>
        <div className="edu-dash">
          <section className="edu-card edu-card--advice">
            <div className="edu-card-head">
              <span className="edu-card-ico">
                <IconWarn size={13} />
              </span>
              <h2>读不到运行数据</h2>
              <span className="edu-card-src">/api/v1/health · /api/v1/metrics</span>
            </div>
            <p className="edu-finding edu-finding--warn">{error}</p>
            <p className="edu-card-note">
              本页不做静态兜底（展示查不到来源的数字正是本次改版要消除的问题）。请先双击{' '}
              <code>start.bat</code> 启动服务，后端就绪后点「重试」。
            </p>
            <button className="edu-start-btn" onClick={() => void load()}>
              <IconRefresh size={14} />
              重试
            </button>
          </section>
        </div>
      </div>
    )
  }

  return (
    <div className="edu-page">
      <header className="edu-hero">
        <div>
          <h1>
            <span className="edu-hero-ico">
              <IconGauge size={16} />
            </span>
            运行总览
          </h1>
          <p>数字人实时链路 · 运行状态与验收指标</p>
        </div>
        <div className="edu-hero-actions">
          <button className="edu-ghost" onClick={() => void load()}>
            <IconRefresh size={13} />
            刷新
          </button>
          <span className="edu-demo-badge">
            {metrics?.reports_found.length ?? 0} 份评测报告 · 后端实时接口
          </span>
        </div>
      </header>

      <div className="edu-dash">
        {/* ---- 运行状态（/api/v1/health）---- */}
        <section className="edu-card">
          <div className="edu-card-head">
            <span className="edu-card-ico">
              <IconGauge size={13} />
            </span>
            <h2>运行状态</h2>
            <span className="edu-card-src">/api/v1/health</span>
          </div>
          <ul className="edu-stat-list">
            <li>
              <span>后端 API</span>
              <b className={health?.api === 'up' ? 'is-ok' : 'is-bad'}>{health?.api ?? '—'}</b>
            </li>
            <li>
              <span>口型服务</span>
              <b className={lip?.reachable ? 'is-ok' : 'is-bad'}>
                {lip?.reachable ? '在线' : '未连接'}
              </b>
            </li>
            <li>
              <span>活跃会话</span>
              <b>{health?.sessions_active ?? 0}</b>
            </li>
            <li>
              <span>评测报告</span>
              <b>{health?.reports_present ?? 0} 份</b>
            </li>
          </ul>
          <Link className="edu-start-btn" to="/console">
            <IconPlay size={14} />
            开始对话
          </Link>
          <div className="edu-card-note">
            会话走真实链路：流式 ASR → DeepSeek → CosyVoice v2 + 打断；口型由云 GPU 驱动。
          </div>
        </section>

        {/* ---- 验收达标率（实测项中的达标占比）---- */}
        <section className="edu-card">
          <div className="edu-card-head">
            <span className="edu-card-ico">
              <IconTarget size={13} />
            </span>
            <h2>验收达标率</h2>
            <span className="edu-card-src">/api/v1/metrics</span>
          </div>
          <Suspense fallback={<div className="edu-ring edu-chart-skeleton" />}>
            <RingProgress done={okCount} total={measured.length} label="实测项中已达标" />
          </Suspense>
          <div className="edu-card-note">
            共 {rows.length} 项验收指标：{measured.length} 项有实测读数、{pending.length} 项待插桩（不计入分母）。
          </div>
        </section>

        {/* ---- 验收指标清单（跨两行）---- */}
        <section className="edu-card edu-card--tasks">
          <div className="edu-card-head">
            <span className="edu-card-ico">
              <IconTasks size={13} />
            </span>
            <h2>验收指标清单</h2>
            <span className="edu-card-src">{rows.length} 项</span>
          </div>
          <ul className="edu-metric-list">
            {rows.map((r) => (
              <li key={`${r.dimension}-${r.metric}`} className="edu-metric-row">
                <span className="edu-metric-dim">{r.dimension}</span>
                <div className="edu-metric-main">
                  <div className="edu-metric-name" title={r.scenario ?? undefined}>
                    {r.metric}
                  </div>
                  <div className="edu-metric-val">
                    {r.value ?? '—'}
                    {r.target ? <span className="muted"> / {r.target}</span> : null}
                  </div>
                </div>
                <span className={`edu-badge edu-badge--${STATUS_META[r.status].cls}`}>
                  {STATUS_META[r.status].text}
                </span>
              </li>
            ))}
            {!rows.length ? (
              <li className="edu-empty">后端未读到评测报告（backend/eval/reports/*.json）</li>
            ) : null}
          </ul>
          <div className="edu-card-note">
            逐条对应 eval/reports 原始报告，可在「工程视图 → 评估台账」核对每项的来源与测量环境。
          </div>
        </section>

        {/* ---- 未达标与待插桩 ---- */}
        <section className="edu-card">
          <div className="edu-card-head">
            <span className="edu-card-ico">
              <IconWarn size={13} />
            </span>
            <h2>未达标与待插桩</h2>
            <span className="edu-card-src">{problems.length + pending.length} 项</span>
          </div>
          {problems.length ? (
            <ul className="edu-problem-list">
              {problems.map((r) => (
                <li key={r.metric}>
                  <span className={`edu-badge edu-badge--${STATUS_META[r.status].cls}`}>
                    {STATUS_META[r.status].text}
                  </span>
                  <span className="edu-problem-name">{r.metric}</span>
                  <span className="muted">
                    {r.value ?? '—'} / {r.target ?? '—'}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="edu-empty">暂无未达标项</p>
          )}
          <div className="edu-card-note">
            待插桩 {pending.length} 项：{pending.map((p) => p.metric).join('、') || '无'}
          </div>
        </section>

        {/* ---- 延迟预算执行（latency_budget：DESIGN §5.1 目标 vs 实测）---- */}
        <section className="edu-card">
          <div className="edu-card-head">
            <span className="edu-card-ico">
              <IconTrend size={13} />
            </span>
            <h2>延迟预算执行</h2>
            <span className="edu-card-src">/api/v1/metrics</span>
          </div>
          <ul className="edu-budget-list">
            {budget.map((b) => {
              const pct = budgetPct(b.measured_ms, b.target_ms)
              return (
                <li key={b.key} className="edu-budget-row">
                  <div className="edu-budget-top">
                    <span>{b.label}</span>
                    <span className="edu-budget-num">
                      {b.measured_ms != null ? `${b.measured_ms} ms` : '待插桩'}
                      {b.target_ms != null ? <span className="muted"> / ≤{b.target_ms}</span> : null}
                    </span>
                  </div>
                  <div className="edu-budget-bar">
                    <div
                      className={`edu-budget-fill${pct == null ? ' is-empty' : ''}`}
                      style={{ width: `${pct ?? 100}%` }}
                    />
                  </div>
                </li>
              )
            })}
            {!budget.length ? <li className="edu-empty">延迟预算数据未随接口返回</li> : null}
          </ul>
        </section>

        {/* ---- 链路诊断（跨全宽，由真实状态推导）---- */}
        <section className="edu-card edu-card--advice">
          <div className="edu-card-head">
            <span className="edu-card-ico">
              <IconSparkle size={13} />
            </span>
            <h2>链路诊断</h2>
            <span className="edu-card-src">由 health + metrics 当前值推导</span>
          </div>
          <ul className="edu-finding-list">
            {findings.map((f, i) => (
              <li key={i} className={`edu-finding edu-finding--${f.level}`}>
                {f.text}
              </li>
            ))}
          </ul>
          <Link className="edu-start-btn" to="/metrics">
            <IconPlay size={14} />
            打开指标看板
          </Link>
        </section>
      </div>
    </div>
  )
}
