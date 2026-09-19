/**
 * 分数徽标（纯 SVG，不是图表）：单元进度 1/2。
 * 环 + 分数式读数，对应设计稿「本周学习目标」卡左侧的圆形标记。
 */
export default function FractionBadge({ done, total }: { done: number; total: number }) {
  const r = 30
  const c = 2 * Math.PI * r
  const ratio = total > 0 ? Math.min(done / total, 1) : 0
  return (
    <svg
      className="edu-fraction"
      viewBox="0 0 78 78"
      role="img"
      aria-label={`单元进度 ${done} / ${total}`}
    >
      <circle cx="39" cy="39" r="36" fill="var(--brand-50)" />
      <circle cx="39" cy="39" r={r} fill="none" stroke="var(--brand-100)" strokeWidth="7" />
      <circle
        cx="39"
        cy="39"
        r={r}
        fill="none"
        stroke="var(--brand-300)"
        strokeWidth="7"
        strokeLinecap="round"
        strokeDasharray={`${ratio * c} ${c}`}
        transform="rotate(-90 39 39)"
      />
      <text
        x="39"
        y="35"
        textAnchor="middle"
        fontSize="15"
        fontWeight="700"
        fill="var(--brand-600)"
      >
        {done}
      </text>
      <line x1="29" y1="39" x2="49" y2="39" stroke="var(--brand-500)" strokeWidth="1.6" />
      <text
        x="39"
        y="55"
        textAnchor="middle"
        fontSize="15"
        fontWeight="700"
        fill="var(--brand-600)"
      >
        {total}
      </text>
    </svg>
  )
}
