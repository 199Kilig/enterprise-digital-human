/**
 * 教育版图标集（内联 SVG，零图标库依赖）
 * ---------------------------------------------------------------------------
 * 为什么不用图标库：package.json 的运行时依赖只有 react / react-dom / react-router-dom
 * 与 recharts，为十几个图标再引一个图标库不划算；统一 stroke=currentColor，跟随 token 换色。
 *
 * 注释只写「形态 + 语义」，不写具体页面/组件名：页面重构过一次，原先按使用位置写的注释
 * （如"错误集：错题集合（侧栏工具格）"）全部随之失效。功能下线的图标直接删，不留孤儿——
 * 导出符号不受 tsc `noUnusedLocals` 约束，删不干净只有人工比对才能发现。
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

/** 靶心：目标 */
export const IconTarget = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="12" r="5" />
    <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
  </Base>
)

/** 清单：条目/轮次 */
export const IconTasks = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 6h16" />
    <path d="M4 12h16" />
    <path d="M4 18h10" />
    <path d="M18 16l1.6 1.6L22 15" />
  </Base>
)

/** 警示三角：异常与待办 */
export const IconWarn = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 4 2.8 20h18.4L12 4Z" />
    <path d="M12 10v4" />
    <path d="M12 17.2h.01" />
  </Base>
)

/** 闪光：智能/灵感 */
export const IconSparkle = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3.5 13.7 9l5.5 1.7-5.5 1.7L12 18l-1.7-5.6L4.8 10.7 10.3 9 12 3.5Z" />
  </Base>
)

/** 播放：开始 */
export const IconPlay = (p: IconProps) => (
  <Base {...p}>
    <path d="M8 5.5v13l11-6.5-11-6.5Z" fill="currentColor" />
  </Base>
)

/** 历史：历史记录 */
export const IconHistory = (p: IconProps) => (
  <Base {...p}>
    <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1" />
    <path d="M3 4.5V9h4.5" />
    <path d="M12 8v4.5l3 1.8" />
  </Base>
)

/** 刷新：重新读取 */
export const IconRefresh = (p: IconProps) => (
  <Base {...p}>
    <path d="M20 11.5A8 8 0 0 0 6.3 6.3L4 8.5" />
    <path d="M4 4v4.5h4.5" />
    <path d="M4 12.5A8 8 0 0 0 17.7 17.7L20 15.5" />
    <path d="M20 20v-4.5h-4.5" />
  </Base>
)

/** 视频：视频产物 */
export const IconVideo = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="5" width="18" height="14" rx="2.5" />
    <path d="M10.5 9.5v5l4.5-2.5-4.5-2.5Z" fill="currentColor" stroke="none" />
  </Base>
)

/** 折线：趋势/洞察 */
export const IconTrend = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 18l5-5 4 3 7-8" />
    <path d="M20 8h-4.5M20 8v4.5" />
  </Base>
)

/** 机器人：数字人 */
export const IconBot = (p: IconProps) => (
  <Base {...p}>
    <rect x="4" y="7" width="16" height="12" rx="3.5" />
    <path d="M12 3.5V7" />
    <path d="M9.5 12.5h.01M14.5 12.5h.01" />
    <path d="M9.5 16h5" />
  </Base>
)

/** 仪表盘：指标 */
export const IconGauge = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 17a8 8 0 1 1 16 0" />
    <path d="M12 17l4-5" />
  </Base>
)

/** 台账：表格记录 */
export const IconLedger = (p: IconProps) => (
  <Base {...p}>
    <path d="M5 4h14v16H5z" />
    <path d="M9 4v16" />
    <path d="M13 9h3M13 13h3" />
  </Base>
)

/** 菜单：窄屏抽屉开关 */
export const IconMenu = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </Base>
)
