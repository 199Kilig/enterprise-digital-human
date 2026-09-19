/**
 * 学习域数据（**前端集中占位**）
 * ---------------------------------------------------------------------------
 * 定位口径（本轮拍板）：教育场景演示皮肤。PRD/DESIGN 不动，后端一行不改。
 * 后端目前**没有学习域接口**（只有 /session、/chat/stream、/metrics、/eval/ledger、/health），
 * 所以本文件是唯一数据源，界面必须显式标注「演示数据」，不得让人误以为已接真实学习域。
 *
 * 接真实接口时替换本文件即可（页面只依赖这里的类型，不依赖取值方式）：
 *   GET /api/v1/learning/profile      → WeeklyGoal / StageGoal
 *   GET /api/v1/learning/tasks?date=  → TodayTask[]
 *   GET /api/v1/learning/weak-points  → WeakPoint[]
 *   GET /api/v1/learning/advice       → Advice
 */

export interface WeeklyGoal {
  /** 学科 · 单元（页面主标题） */
  subject: string
  unit: string
  /** 单元内已完成进度（分子/分母，页面显示成分数徽标 1/2） */
  unitProgress: { done: number; total: number }
  /** 掌握度百分比 */
  mastery: number
}

export interface WeeklyProgress {
  done: number
  total: number
}

export interface TodayTask {
  id: string
  text: string
  done: boolean
  /** 任务类型 → 图标（前端只做分类，不引入图标库） */
  kind: 'review' | 'mistake' | 'video' | 'check'
}

export interface WeakPoint {
  id: string
  name: string
  /** 知识点所属运算符号（列表左侧图标） */
  symbol: string
  tag: string
}

export interface StagePoint {
  label: string
  value: number
}

export interface StageGoal {
  /** 当前数学水平（折线最后一段实测） */
  current: number
  target: number
  points: StagePoint[]
}

export interface Advice {
  text: string
  /** 建议生成依据（界面 hover/副文案展示，避免"凭空建议"的观感） */
  basis: string
}

export interface LearnedToday {
  /** 累计学习时长（秒），进入页面后由前端计时器真实递增 */
  baseSeconds: number
  label: string
}

export const weeklyGoal: WeeklyGoal = {
  subject: '数学',
  unit: '分数单元',
  unitProgress: { done: 1, total: 2 },
  mastery: 82,
}

export const weeklyProgress: WeeklyProgress = { done: 12, total: 16 }

export const todayTasks: TodayTask[] = [
  { id: 't1', text: '10 分钟知识复习', done: true, kind: 'review' },
  { id: 't2', text: '3 道错题重做', done: true, kind: 'mistake' },
  { id: 't3', text: '观看 1 个 AI 教学视频', done: true, kind: 'video' },
  { id: 't4', text: '完成分数知识检测', done: false, kind: 'check' },
]

export const weakPoints: WeakPoint[] = [
  { id: 'w1', name: '异分母分数加减法', symbol: '+', tag: '需加强' },
  { id: 'w2', name: '分数大小比较', symbol: '>', tag: '需加强' },
  { id: 'w3', name: '小数乘法', symbol: '×', tag: '需加强' },
]

export const stageGoal: StageGoal = {
  current: 85,
  target: 90,
  points: [
    { label: '上周', value: 65 },
    { label: '本周', value: 75 },
    { label: '下周', value: 85 },
    { label: '目标', value: 90 },
  ],
}

export const advice: Advice = {
  text: '本周错题主要集中在通分与异分母分数加法，建议优先完成对应知识复习。',
  basis: '依据：近 7 天错题分布 + 待加强知识点清单',
}

export const learnedToday: LearnedToday = { baseSeconds: 7 * 3600 + 51 * 60 + 10, label: '自由专注' }

/** 学习工具入口（侧栏 4 格）。status=planned 的是占位能力，页面统一走 /tools/:key。 */
export interface LearningTool {
  key: string
  name: string
  status: 'live' | 'planned'
  desc: string
}

export const learningTools: LearningTool[] = [
  {
    key: 'mistakes',
    name: '错题集合',
    status: 'live',
    desc: '按知识点聚合的错题本（当前数据为演示占位，读 learning.ts 的 weakPoints）',
  },
  {
    key: 'videos',
    name: '视频讲解',
    status: 'planned',
    desc: 'AI 讲解视频（依赖口型驱动服务与本轮对话链路，规划中）',
  },
  {
    key: 'knowledge',
    name: '知识库',
    status: 'planned',
    desc: '教材知识库检索（PRD FR-10 知识库接入，P2 范围，规划中）',
  },
  {
    key: 'stage',
    name: '阶段目标',
    status: 'live',
    desc: '阶段目标与水平折线（演示数据，读 learning.ts 的 stageGoal）',
  },
]
