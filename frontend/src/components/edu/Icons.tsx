/**
 * 教育版图标集（内联 SVG，零图标库依赖）
 * ---------------------------------------------------------------------------
 * 为什么不用图标库：现有 package.json 只有 react/react-dom/react-router (+本轮 recharts)，
 * 为 12 个图标引一个 300KB 的库不划算；统一 stroke=currentColor，跟随 token 换色。
 */
import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement> & { size?: number }

function Base({ size = 16, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  )
}

/** 靶心：学习目标（页面主图标） */
export const IconTarget = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="12" r="5" />
    <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
  </Base>
)

/** 奖杯/单元：本周学习目标 */
export const IconUnit = (p: IconProps) => (
  <Base {...p}>
    <path d="M8 21h8" />
    <path d="M12 17v4" />
    <path d="M7 4h10v5a5 5 0 0 1-10 0V4Z" />
    <path d="M7 5H4v2a3 3 0 0 0 3 3" />
    <path d="M17 5h3v2a3 3 0 0 1-3 3" />
  </Base>
)

/** 环形进度：本周进度 */
export const IconRing = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3a9 9 0 1 0 9 9" />
    <path d="M12 7v5l3 2" />
  </Base>
)

/** 清单：今日学习任务 */
export const IconTasks = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 6h16" />
    <path d="M4 12h16" />
    <path d="M4 18h10" />
    <path d="M18 16l1.6 1.6L22 15" />
  </Base>
)

/** 警示三角：待加强知识点 */
export const IconWarn = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 4 2.8 20h18.4L12 4Z" />
    <path d="M12 10v4" />
    <path d="M12 17.2h.01" />
  </Base>
)

/** 旗帜：阶段目标 */
export const IconFlag = (p: IconProps) => (
  <Base {...p}>
    <path d="M6 21V4" />
    <path d="M6 5h11l-2 3.5L17 12H6" />
  </Base>
)

/** 闪光：智能建议 */
export const IconSparkle = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3.5 13.7 9l5.5 1.7-5.5 1.7L12 18l-1.7-5.6L4.8 10.7 10.3 9 12 3.5Z" />
  </Base>
)

/** 播放：开始学习 */
export const IconPlay = (p: IconProps) => (
  <Base {...p}>
    <path d="M8 5.5v13l11-6.5-11-6.5Z" fill="currentColor" />
  </Base>
)

/** 历史：最近对话 */
export const IconHistory = (p: IconProps) => (
  <Base {...p}>
    <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1" />
    <path d="M3 4.5V9h4.5" />
    <path d="M12 8v4.5l3 1.8" />
  </Base>
)

/** 刷新：重新读取本地对话记录 */
export const IconRefresh = (p: IconProps) => (
  <Base {...p}>
    <path d="M20 11.5A8 8 0 0 0 6.3 6.3L4 8.5" />
    <path d="M4 4v4.5h4.5" />
    <path d="M4 12.5A8 8 0 0 0 17.7 17.7L20 15.5" />
    <path d="M20 20v-4.5h-4.5" />
  </Base>
)

/** 皇冠：小奈 Pro 报告 */
export const IconCrown = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 17h16" />
    <path d="M4 7l4 4 4-6 4 6 4-4v9H4V7Z" />
  </Base>
)

/** 错误集：错题集合（侧栏工具格） */
export const IconMistake = (p: IconProps) => (
  <Base {...p}>
    <path d="M6 3h9l4 4v14H6V3Z" />
    <path d="M9.5 11l5 6" />
    <path d="M14.5 11l-5 6" />
  </Base>
)

/** 视频：视频讲解 */
export const IconVideo = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="5" width="18" height="14" rx="2.5" />
    <path d="M10.5 9.5v5l4.5-2.5-4.5-2.5Z" fill="currentColor" stroke="none" />
  </Base>
)

/** 书本：知识库 */
export const IconBook = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 5.5A2 2 0 0 1 6 3.5h13V20H6a2 2 0 0 0-2 2V5.5Z" />
    <path d="M8 8h7M8 12h5" />
  </Base>
)

/** 折线：阶段目标（侧栏工具格） */
export const IconTrend = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 18l5-5 4 3 7-8" />
    <path d="M20 8h-4.5M20 8v4.5" />
  </Base>
)

/** 机器人/数字人对话：AI 答疑入口 */
export const IconBot = (p: IconProps) => (
  <Base {...p}>
    <rect x="4" y="7" width="16" height="12" rx="3.5" />
    <path d="M12 3.5V7" />
    <path d="M9.5 12.5h.01M14.5 12.5h.01" />
    <path d="M9.5 16h5" />
  </Base>
)

/** 指标看板 */
export const IconGauge = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 17a8 8 0 1 1 16 0" />
    <path d="M12 17l4-5" />
  </Base>
)

/** 台账 */
export const IconLedger = (p: IconProps) => (
  <Base {...p}>
    <path d="M5 4h14v16H5z" />
    <path d="M9 4v16" />
    <path d="M13 9h3M13 13h3" />
  </Base>
)

/** 占位页通用：工具/施工中 */
/** 菜单：窄屏侧栏抽屉的开关 */
export const IconMenu = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </Base>
)

/** 关闭：抽屉/浮层 */
export const IconClose = (p: IconProps) => (
  <Base {...p}>
    <path d="M6 6l12 12M18 6L6 18" />
  </Base>
)

export const IconTool = (p: IconProps) => (
  <Base {...p}>
    <path d="M14.5 5.5a3.5 3.5 0 0 0 4.8 4.8l-8 8-2.6-2.6 8-8Z" />
    <path d="M6.5 17.5 4 20" />
  </Base>
)
