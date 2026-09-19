import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts'
import type { StagePoint } from '../../data/learning'

/**
 * 阶段目标折线（recharts LineChart）。
 * 目标线用 ReferenceLine 虚线单独画出：设计稿里「目标 90」是参考线而不是实测点，
 * 两者混成一条线会让人误以为目标已经达成。
 */
export default function StageGoalChart({
  points,
  target,
}: {
  points: StagePoint[]
  target: number
}) {
  return (
    <div className="edu-stage-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 20, right: 20, bottom: 0, left: -16 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="label"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 11, fill: 'var(--text-tertiary)' }}
          />
          <YAxis
            domain={[0, 100]}
            ticks={[0, 20, 40, 60, 80, 100]}
            tickLine={false}
            axisLine={false}
            width={38}
            tick={{ fontSize: 11, fill: 'var(--text-tertiary)' }}
          />
          <ReferenceLine y={target} stroke="var(--brand-300)" strokeDasharray="5 5" />
          <Line
            type="monotone"
            dataKey="value"
            stroke="var(--brand-500)"
            strokeWidth={2.4}
            dot={{ r: 4, fill: 'var(--bg-surface)', stroke: 'var(--brand-500)', strokeWidth: 2 }}
            activeDot={{ r: 5 }}
            label={{ position: 'top', fontSize: 11, fill: 'var(--text-secondary)' }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
