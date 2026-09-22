import { Suspense, lazy, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import FractionBadge from '../components/edu/FractionBadge'
/** 图表按需加载：recharts（含 d3 依赖）是本项目最大的单个依赖，首屏不必等它。
 *  fallback 用与真实容器同高的骨架（.edu-ring 176px / .edu-stage-chart 196px），
 *  图表到位时不产生位移跳动。 */
const RingProgress = lazy(() => import('../components/edu/RingProgress'))
const StageGoalChart = lazy(() => import('../components/edu/StageGoalChart'))
import { FocusTimer, PomodoroTimer } from '../components/edu/Timers'
import {
  IconFlag,
  IconPlay,
  IconRing,
  IconSparkle,
  IconTarget,
  IconTasks,
  IconUnit,
  IconWarn,
} from '../components/edu/Icons'
import {
  advice,
  learnedToday,
  stageGoal,
  todayTasks,
  weakPoints,
  weeklyGoal,
  weeklyProgress,
} from '../data/learning'

/**
 * 教育门面首页 · 学习目标仪表盘
 * ---------------------------------------------------------------------------
 * 数据口径：全部来自 src/data/learning.ts（前端占位，后端无学习域接口）。
 * 每张卡片右上角带「演示数据」标注，页头另有一条全局说明 —— 不让人误判为真实业务数据。
 * 交互真实性：任务勾选是本地状态；「开始学习」「查看全部历史对话」跳到 /console，
 * 走的是**真实的** SSE 链路（DeepSeek + CosyVoice 流式 + 打断）。
 */
export default function LearningHomePage() {
  const navigate = useNavigate()
  const [tasks, setTasks] = useState(todayTasks)
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const remaining = tasks.filter((t) => !t.done).length

  const toggle = (id: string) =>
    setTasks((list) => list.map((t) => (t.id === id ? { ...t, done: !t.done } : t)))

  return (
    <div className="edu-page">
      <header className="edu-hero">
        <div>
          <h1>
            <span className="edu-hero-ico">
              <IconTarget size={16} />
            </span>
            学习目标
          </h1>
          <p>从学习记录到目标规划，让每一步都清晰可见</p>
        </div>
        <div className="edu-hero-actions">
          <FocusTimer baseSeconds={learnedToday.baseSeconds} label={learnedToday.label} />
          <PomodoroTimer seconds={14 * 60 + 9} />
          <button className="edu-ghost" onClick={() => setFeedbackOpen((v) => !v)}>
            反馈
          </button>
          <span className="edu-demo-badge">演示数据 · 学习域后端未接入</span>
        </div>
      </header>

      {feedbackOpen && (
        <div className="edu-ph-note" style={{ marginBottom: 'var(--sp-4)' }}>
          反馈通道尚未接入（本轮范围为前端教育场景皮肤）：正式版将把反馈写入学习档案，
          并驱动「待加强知识点」的重排。当前不作假提交。
        </div>
      )}

      <div className="edu-dash">
        {/* ---- 本周学习目标 ---- */}
        <section className="edu-card">
          <div className="edu-card-head">
            <span className="edu-card-ico">
              <IconUnit size={13} />
            </span>
            <h2>本周学习目标</h2>
            <span className="edu-card-src">演示数据</span>
          </div>
          <div className="edu-weekly">
            <FractionBadge
              done={weeklyGoal.unitProgress.done}
              total={weeklyGoal.unitProgress.total}
            />
            <div>
              <div className="edu-weekly-unit">
                {weeklyGoal.subject} · {weeklyGoal.unit}
              </div>
              <div className="edu-mastery-row">
                掌握度：<b>{weeklyGoal.mastery}%</b>
              </div>
            </div>
          </div>
          <div className="edu-weekly-foot">
            单元进度 {weeklyGoal.unitProgress.done}/{weeklyGoal.unitProgress.total} ·{' '}
            掌握度口径：错题订正率与单元检测加权（占位口径，接学习域后替换）
          </div>
        </section>

        {/* ---- 本周进度 ---- */}
        <section className="edu-card">
          <div className="edu-card-head">
            <span className="edu-card-ico">
              <IconRing size={13} />
            </span>
            <h2>本周进度</h2>
            <span className="edu-card-src">演示数据</span>
          </div>
          <Suspense fallback={<div className="edu-ring edu-chart-skeleton" />}>
            <RingProgress
              done={weeklyProgress.done}
              total={weeklyProgress.total}
              label="项已完成"
            />
          </Suspense>
        </section>

        {/* ---- 今日学习任务（跨两行） ---- */}
        <section className="edu-card edu-card--tasks">
          <div className="edu-card-head">
            <span className="edu-card-ico">
              <IconTasks size={13} />
            </span>
            <h2>今日学习任务</h2>
            <span className="edu-card-src">
              剩余 {remaining} 项
            </span>
          </div>
          <ul className="edu-tasks">
            {tasks.map((t, i) => (
              <li key={t.id}>
                <button
                  className={`edu-task${t.done ? ' done' : ''}`}
                  onClick={() => toggle(t.id)}
                  aria-pressed={t.done}
                  title={t.done ? '点击取消完成' : '点击标记完成'}
                >
                  <span className="edu-task-idx">{i + 1}</span>
                  <span className="edu-task-text">{t.text}</span>
                  <span className="edu-task-check">✓</span>
                </button>
              </li>
            ))}
          </ul>
          <button className="edu-start-btn" onClick={() => navigate('/console')}>
            <IconPlay size={14} />
            开始学习
          </button>
          <div className="edu-task-foot">
            「开始学习」进入数字人对话：真实 SSE 链路（流式 ASR → DeepSeek → CosyVoice + 打断）。
            任务勾选为本地状态，不落库。
          </div>
        </section>

        {/* ---- 待加强知识点 ---- */}
        <section className="edu-card">
          <div className="edu-card-head">
            <span className="edu-card-ico">
              <IconWarn size={13} />
            </span>
            <h2>待加强知识点</h2>
            <span className="edu-card-src">演示数据</span>
          </div>
          <ul className="edu-weak">
            {weakPoints.map((w) => (
              <li className="edu-weak-item" key={w.id}>
                <span className="edu-weak-sym">{w.symbol}</span>
                <span className="edu-weak-name">{w.name}</span>
                <span className="edu-weak-tag">{w.tag}</span>
              </li>
            ))}
          </ul>
          <div className="edu-weekly-foot">
            来源：近 7 天错题聚合（占位口径）· <Link to="/tools/mistakes">查看错题集合</Link>
          </div>
        </section>

        {/* ---- 阶段目标 ---- */}
        <section className="edu-card">
          <div className="edu-card-head">
            <span className="edu-card-ico">
              <IconFlag size={13} />
            </span>
            <h2>阶段目标</h2>
            <span className="edu-card-src">演示数据</span>
          </div>
          <div className="edu-stage-head">
            当前数学水平：<b>{stageGoal.current}</b>
            <span className="edu-spacer" />
            <span className="edu-stage-target">
              目标：<b>{stageGoal.target}</b>
            </span>
          </div>
          <Suspense fallback={<div className="edu-stage-chart edu-chart-skeleton" />}>
            <StageGoalChart points={stageGoal.points} target={stageGoal.target} />
          </Suspense>
        </section>

        {/* ---- 智能建议（跨全宽） ---- */}
        <section className="edu-card edu-card--advice">
          <div className="edu-advice">
            <div className="edu-advice-figure">
              <img src="/media/avatar_portrait_raw.png" alt="AI 学习助手形象" />
            </div>
            <div style={{ minWidth: 0 }}>
              <div className="edu-advice-title">
                <IconSparkle size={15} />
                智能建议
              </div>
              <div className="edu-advice-text">{advice.text}</div>
              <div className="edu-advice-basis">{advice.basis} · 演示数据（建议未由真实诊断生成）</div>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
