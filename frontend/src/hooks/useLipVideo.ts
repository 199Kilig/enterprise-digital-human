/**
 * 口型视频片段排队播放（SPEC §3.5 第 2b 条 / ADR-005）。
 *
 * 契约：`lip_video` 事件带 start_ms（该句在整段回答中的起点）与 video_b64。
 * 调度基准 = **音频时钟**（与 tts_audio 同源）：ConsolePage 里
 *   audioClockMs = (audioCtx.currentTime - sink.base) * 1000
 * 片段应在 audioClockMs 到达其 start_ms 时开始播放。
 *
 * 迟到策略（SPEC §3.5 2b 明确要求"由前端策略决定，实测偏差需记录进评估台账"）：
 * 服务端口型生成通常慢于语音播报，片段到达时往往已过 start_ms。
 * 本实现选择**立即从头播放**（不丢帧，接受音画偏差），并把每次迟到量记进 stats，
 * 供工作台展示与台账回填——**不假装同步**。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export interface LipSegment {
  seq: number
  startMs: number
  durationMs: number
  nFrames: number
  fps: number
  url: string
  bytes: number
  /** 到达时已落后音频时钟多少 ms（正数=迟到） */
  lateMs: number | null
}

export interface LipVideoStats {
  segments: number
  bytes: number
  lateMeanMs: number | null
  lateMaxMs: number | null
  /** 当前在播片段的实测偏差（start_ms 与实际开播时刻之差） */
  lastLateMs: number | null
}

export function useLipVideo(getAudioClockMs: () => number | null) {
  const [current, setCurrent] = useState<LipSegment | null>(null)
  const [stats, setStats] = useState<LipVideoStats>({
    segments: 0,
    bytes: 0,
    lateMeanMs: null,
    lateMaxMs: null,
    lastLateMs: null,
  })

  const queueRef = useRef<LipSegment[]>([])
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const currentUrlRef = useRef<string | null>(null)
  const latesRef = useRef<number[]>([])

  const clearTimer = () => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }

  const playNow = useCallback((seg: LipSegment) => {
    const clock = getAudioClockMs()
    const late = clock === null ? null : Math.max(0, Math.round(clock - seg.startMs))
    if (late !== null) {
      latesRef.current.push(late)
    }
    const shown: LipSegment = { ...seg, lateMs: late }

    if (currentUrlRef.current && currentUrlRef.current !== seg.url) {
      URL.revokeObjectURL(currentUrlRef.current) // 换片段即回收，避免 blob 泄漏
    }
    currentUrlRef.current = seg.url
    setCurrent(shown)

    const arr = latesRef.current
    const mean = arr.length ? Math.round(arr.reduce((a, b) => a + b, 0) / arr.length) : null
    const max = arr.length ? Math.max(...arr) : null
    setStats((s) => ({
      segments: s.segments + 1,
      bytes: s.bytes + seg.bytes,
      lateMeanMs: mean,
      lateMaxMs: max,
      lastLateMs: late,
    }))
  }, [getAudioClockMs])

  const scheduleNext = useCallback(() => {
    if (timerRef.current !== null) return
    const seg = queueRef.current[0]
    if (!seg) return

    const clock = getAudioClockMs()
    if (clock === null || clock >= seg.startMs) {
      queueRef.current.shift()
      playNow(seg)
      scheduleNext() // 队列里可能还有更晚的片段
      return
    }
    const waitMs = seg.startMs - clock
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      const head = queueRef.current.shift()
      if (head) playNow(head)
      scheduleNext()
    }, waitMs)
  }, [getAudioClockMs, playNow])

  const enqueue = useCallback(
    (seg: LipSegment) => {
      queueRef.current.push(seg)
      queueRef.current.sort((a, b) => a.startMs - b.startMs)
      scheduleNext()
    },
    [scheduleNext],
  )

  const reset = useCallback(() => {
    clearTimer()
    queueRef.current.forEach((s) => URL.revokeObjectURL(s.url))
    queueRef.current = []
    if (currentUrlRef.current) {
      URL.revokeObjectURL(currentUrlRef.current)
      currentUrlRef.current = null
    }
    latesRef.current = []
    setCurrent(null)
    setStats({ segments: 0, bytes: 0, lateMeanMs: null, lateMaxMs: null, lastLateMs: null })
  }, [])

  useEffect(() => () => {
    clearTimer()
    if (currentUrlRef.current) URL.revokeObjectURL(currentUrlRef.current)
  }, [])

  return { current, stats, enqueue, reset }
}
