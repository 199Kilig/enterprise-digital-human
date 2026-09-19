import { Link, useParams } from 'react-router-dom'
import StageGoalChart from '../components/edu/StageGoalChart'
import { IconBook, IconMistake, IconTool, IconTrend, IconVideo } from '../components/edu/Icons'
import { learningTools, stageGoal, weakPoints } from '../data/learning'

const TOOL_ICONS: Record<string, typeof IconTool> = {
  mistakes: IconMistake,
  videos: IconVideo,
  knowledge: IconBook,
  stage: IconTrend,
}

/** 占位规划要点：写清楚「这个能力要依赖什么」，而不是空喊"敬请期待" */
const PLANS: Record<string, { title: string; points: string[] }> = {
  mistakes: {
    title: '错题集合实现要点',
    points: [
      '数据来源：需要学习域接口（GET /api/v1/learning/mistakes），后端当前没有该域',
      '聚合口径：按知识点分组 + 错误次数排序，驱动首页「待加强知识点」与「智能建议」',
      '与数字人联动：点开错题 → 直接把题目喂给 /chat/stream，让数字人逐步讲解（现有链路已具备）',
    ],
  },
  videos: {
    title: '视频讲解实现要点',
    points: [
      'PRD FR-04 口型驱动：文本/音频 → 口型帧，后端已有 /api/v1/lip/infer（ADR-003 选型 MuseTalk）',
      'ADR-005/006：整句 H.264 分片 + 音频时钟排队播放，前端 useLipVideo 已按 start_ms 排期',
      '缺口：讲稿生成与分镜（需要教学脚本域），本轮不做',
    ],
  },
  knowledge: {
    title: '知识库实现要点',
    points: [
      'PRD FR-10 明确标注为 P2：MVP 阶段对话大脑先直连大模型，不接检索',
      '演进路径：教材切片 → 向量化 → 检索增强（对话大脑编排层增加 retrieve 节点）',
      '与打断的关系：检索是「想」段耗时，需要纳入延迟预算重新分配（DESIGN §5.1）',
    ],
  },
  stage: {
    title: '阶段目标实现要点',
    points: [
      '数据来源：需要学习域接口（GET /api/v1/learning/stage），当前为 learning.ts 占位',
      '图上「目标线」是参考线而非实测点：目标未达成时不能画成折线端点（会误导）',
      '目标达成后触发动作：重排今日任务优先级 + 建议数字人复述薄弱点',
    ],
  },
}

/**
 * 学习工具占位页（/tools/:key）
 * ---------------------------------------------------------------------------
 * 设计稿里的四个工具入口只有「错题集合 / 阶段目标」有演示数据，
 * 「视频讲解 / 知识库」依赖尚未实现的能力（口型分镜、P2 检索）。
 * 这里不放假界面，而是把实现要点与依赖写清楚 —— 占位要占得有价值。
 */
export default function ToolPlaceholderPage() {
  const { key } = useParams<{ key: string }>()
  const tool = learningTools.find((t) => t.key === key)

  if (!tool) {
    return (
      <div className="edu-ph">
        <h1>未找到该学习工具</h1>
        <p className="edu-ph-sub">
          可用入口：错题集合 / 视频讲解 / 知识库 / 阶段目标。
          <Link to="/"> 返回学习目标</Link>
        </p>
      </div>
    )
  }

  const Icon = TOOL_ICONS[tool.key] ?? IconTool
  const plan = PLANS[tool.key]

  return (
    <div className="edu-ph">
      <h1>
        <Icon size={18} />
        {tool.name}
      </h1>
      <p className="edu-ph-sub">{tool.desc}</p>

      {tool.status === 'planned' && (
        <div className="edu-ph-note" style={{ marginBottom: 'var(--sp-4)' }}>
          占位页：该能力尚未实现，本页只登记依赖与实现要点，不提供假界面。
        </div>
      )}

      {tool.key === 'mistakes' && (
        <div className="edu-ph-card">
          <h2>当前演示数据集（来自 learning.ts）</h2>
          <ul className="edu-ph-list">
            {weakPoints.map((w) => (
              <li key={w.id}>
                <span className="edu-weak-sym">{w.symbol}</span>
                {w.name}
                <span className="edu-ph-tag">{w.tag} · 演示</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {tool.key === 'stage' && (
        <div className="edu-ph-card">
          <h2>
            阶段目标折线（当前 {stageGoal.current} / 目标 {stageGoal.target}）
          </h2>
          <StageGoalChart points={stageGoal.points} target={stageGoal.target} />
        </div>
      )}

      {plan && (
        <div className="edu-ph-card">
          <h2>{plan.title}</h2>
          <ul className="edu-ph-list">
            {plan.points.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
