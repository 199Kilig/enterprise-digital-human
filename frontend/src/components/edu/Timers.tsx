import { useEffect, useState } from 'react'
import { IconRing, IconTarget } from './Icons'

function fmt(totalSeconds: number): string {
  const s = Math.max(totalSeconds, 0)
  const hh = String(Math.floor(s / 3600)).padStart(2, '0')
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

/**
 * 累计学习时长。起始值来自 data/learning.ts（演示数据），
 * 进入页面后按真实时间每秒递增 —— 只有"在走"的表盘才配放在页头。
 */
export function FocusTimer({ baseSeconds, label }: { baseSeconds: number; label: string }) {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => setElapsed((e) => e + 1), 1000)
    return () => window.clearInterval(id)
  }, [])
  return (
    <span className="edu-timer" title="起始时长来自前端演示数据（learning.ts），进入页面后按真实时间递增">
      <IconTarget size={14} />
      学习
      <span className="edu-timer-value">{fmt(baseSeconds + elapsed)}</span>
      {label}
    </span>
  )
}

/** 番茄钟倒计时（演示：从 learning.ts 的起始读数递减，归零即停） */
export function PomodoroTimer({ seconds }: { seconds: number }) {
  const [left, setLeft] = useState(seconds)
  useEffect(() => {
    const id = window.setInterval(() => setLeft((v) => (v > 0 ? v - 1 : 0)), 1000)
    return () => window.clearInterval(id)
  }, [])
  return (
    <span
      className="edu-pomo"
      title={left === 0 ? '专注周期已结束' : '专注计时（演示读数，倒计时真实运行）'}
    >
      <IconRing size={14} />
      {fmt(left)}
    </span>
  )
}
