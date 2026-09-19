import { PolarAngleAxis, RadialBar, RadialBarChart, ResponsiveContainer } from 'recharts'

/**
 * 本周进度环形（recharts RadialBarChart）。
 * 口径：分子分母都来自 data/learning.ts 的 weeklyProgress（演示数据）。
 * 中心读数用绝对定位覆盖在图表上——recharts 的 label 在高 DPI 下与环心对齐不稳。
 */
export default function RingProgress({
  done,
  total,
  label,
}: {
  done: number
  total: number
  label: string
}) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div className="edu-ring" role="img" aria-label={`本周进度 ${done} / ${total}，完成 ${pct}%`}>
      <ResponsiveContainer width="100%" height="100%">
        <RadialBarChart
          data={[{ name: 'progress', value: done, fill: 'var(--brand-500)' }]}
          innerRadius="76%"
          outerRadius="100%"
          startAngle={90}
          endAngle={-270}
          barSize={13}
        >
          <PolarAngleAxis type="number" domain={[0, Math.max(total, 1)]} tick={false} axisLine={false} />
          <RadialBar
            dataKey="value"
            background={{ fill: 'var(--bg-inset)' }}
            cornerRadius={8}
            isAnimationActive={false}
          />
        </RadialBarChart>
      </ResponsiveContainer>
      <div className="edu-ring-center">
        <span className="edu-ring-value">
          {done} / {total}
        </span>
        <span className="edu-ring-label">{label}</span>
      </div>
    </div>
  )
}
