import { useCallback, useEffect, useRef, useState } from 'react'
import { api, openStream } from '../api/client'
import { useMicCapture, type MicCallbacks } from './useMicCapture'
import { useLipVideo, type LipSegment, type LipVideoStats } from './useLipVideo'
import type {
  ChatMessage,
  MetricsResponse,
  SessionInfo,
  SessionState,
  SseEventType,
  StreamEvent,
} from '../types'

/**
 * 双向会话 hook：会话生命周期 + SSE 事件 + PCM 播放 + 打断 + 麦克风。
 *
 * 为什么抽出来：同一套链路要被**两个界面**消费——
 *   `/console` 学习者对话页（只要对话与声音，不要技术数字）
 *   `/studio`  链路工作台（状态机 / 事件流 / 延迟瀑布 / 口型统计）
 * 逻辑只留一份，避免两个界面各长出一套"看起来差不多"的链路代码后逐渐漂移。
 *
 * ⚠️ 搬运纪律：所有注释标注的修复点（打断静默期 mutedRef、flushPartial 半句落地、
 * barge 监听的 epoch 核对、开麦前先 stopPlayback）都是实测缺陷的产物，
 * 搬动时只准搬位置、不准删改行为（依据 PROGRESS-2026-09-17 与 RUNBOOK §3.17）。
 */

/** 带 seq 的流式事件类型（SPEC §3.3：媒体事件才有 seq） */
const SEQ_TYPES: SseEventType[] = ['brain_token', 'tts_audio', 'lip_frame', 'lip_video']

/** PCM 播放（SPEC §3.5 第 2 条：以音频时钟为基准调度）。 */
interface AudioSink {
  ctx: AudioContext
  base: number
  gain: GainNode // 打断时用它做 20ms 淡出（硬切会产生"咔嗒"爆音）
  sources: AudioBufferSourceNode[]
}

/** base64 → 字节（口型片段解码用；片段约数百 KB，逐字符转换可接受） */
function b64ToBytes(b64: string): Uint8Array<ArrayBuffer> {
  const raw = atob(b64)
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

/** 本地会话持久化（**轻档**：只存前端）。见 PROGRESS-2026-09-17 §一 */
const STORAGE_KEY = 'dh.console.session.v1'
const PERSIST_LIMIT = 50

interface PersistedSession {
  sessionId: string
  messages: ChatMessage[]
}

function loadPersisted(): PersistedSession | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const p = JSON.parse(raw) as PersistedSession
    return p && typeof p.sessionId === 'string' && Array.isArray(p.messages) ? p : null
  } catch {
    return null
  }
}

function savePersisted(p: PersistedSession): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(p))
  } catch {
    /* 隐私模式 / 配额满：持久化失败不影响正常使用 */
  }
}

function clearPersisted(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* 同上 */
  }
}

export interface DuplexSession {
  session: SessionInfo | null
  state: SessionState
  /** 原始 SSE 事件（/studio 事件流表格用；/console 不展示） */
  events: StreamEvent[]
  messages: ChatMessage[]
  /** 正在流式产出的回答（未落地为消息前） */
  streamText: string | null
  lastTurn: { ttft: number | null; total: number; tokens: number } | null
  metrics: MetricsResponse | null
  error: string | null
  audioStats: { chunks: number; ms: number; words: number } | null
  liveText: string
  micLevel: number
  recording: boolean
  monitoring: boolean
  lipClip: LipSegment | null
  lipStats: LipVideoStats
  /** 会话建立中（首屏空态用） */
  connecting: boolean
  sendTurn: (text: string) => void
  interrupt: () => Promise<void>
  resetSession: (opts?: { fresh?: boolean }) => Promise<void>
  startMic: () => Promise<void>
  stopMic: () => void
  stopMonitor: () => void
  /** 对话页的麦克风按钮：开麦前先让数字人闭嘴（见 onMicToggle 注释） */
  toggleMic: () => void
}

export function useDuplexSession(): DuplexSession {
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [state, setState] = useState<SessionState>('idle')
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streamText, setStreamText] = useState<string | null>(null)
  const [lastTurn, setLastTurn] = useState<{ ttft: number | null; total: number; tokens: number } | null>(
    null,
  )
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [audioStats, setAudioStats] = useState<{ chunks: number; ms: number; words: number } | null>(null)
  const [liveText, setLiveText] = useState('')
  const [micLevel, setMicLevel] = useState(0)
  const [connecting, setConnecting] = useState(true)

  const closeRef = useRef<null | (() => void)>(null)
  const t0Ref = useRef(0)
  const seqRef = useRef<Record<string, number>>({})
  const bufRef = useRef('')
  const audioRef = useRef<AudioSink | null>(null)
  const micCbRef = useRef<MicCallbacks | null>(null)
  const bargeAtRef = useRef(0)
  // 打断静默期：/interrupt 发出后到新一轮开始前，丢弃**在途到达**的音频/口型分片。
  // 为什么需要：关流是异步的，打断瞬间可能已有 1~2 个 tts_audio 事件在队列里，
  // 不丢弃会在 stopPlayback() 之后重建 AudioSink 继续出声（症状："停完又蹦半句"）。
  const mutedRef = useRef(false)

  const { recording, monitoring, start: startMicRaw, stop: stopMic, stopMonitor } = useMicCapture(
    session?.session_id ?? null,
    micCbRef,
  )

  /** 音频时钟（ms，相对本轮回答起点）：口型片段的排期基准（SPEC §3.5 2b） */
  const audioClockMs = useCallback(() => {
    const sink = audioRef.current
    if (!sink) return null
    return (sink.ctx.currentTime - sink.base) * 1000
  }, [])
  const { current: lipClip, stats: lipStats, enqueue: enqueueLip, reset: resetLip } = useLipVideo(audioClockMs)

  /** 把一片 base64 PCM 排进播放时间轴（首片建立时间基准） */
  const playPcmChunk = useCallback((b64: string, startMs: number) => {
    const raw = atob(b64)
    const bytes = new Uint8Array(raw.length)
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)
    const i16 = new Int16Array(bytes.buffer, 0, bytes.length >> 1)

    let sink = audioRef.current
    if (!sink) {
      const ctx = new AudioContext()
      const gain = ctx.createGain()
      gain.connect(ctx.destination)
      sink = { ctx, base: ctx.currentTime + 0.15, gain, sources: [] } // 150ms 起播余量
      audioRef.current = sink
    }
    const buffer = sink.ctx.createBuffer(1, i16.length, 16000)
    const ch = buffer.getChannelData(0)
    for (let i = 0; i < i16.length; i++) ch[i] = i16[i] / 32768
    const src = sink.ctx.createBufferSource()
    src.buffer = buffer
    src.connect(sink.gain)
    src.start(sink.base + startMs / 1000)
    sink.sources.push(src)
    src.onended = () => {
      const k = sink.sources.indexOf(src)
      if (k >= 0) sink.sources.splice(k, 1)
    }
  }, [])

  /**
   * 打断时立刻停止播放 —— DESIGN-打断 §3.3 规则3 的前端清理协议。
   * 为什么必须显式 stop 已排期的 source：`src.start(base + startMs/1000)` 是**排到时间轴上**的，
   * 不 stop 就会继续播完 —— 这正是"插话了但数字人还在说"的直接原因。
   */
  const stopPlayback = useCallback(() => {
    const sink = audioRef.current
    if (!sink) return
    audioRef.current = null
    try {
      const now = sink.ctx.currentTime
      sink.gain.gain.cancelScheduledValues(now)
      sink.gain.gain.setValueAtTime(sink.gain.gain.value, now)
      sink.gain.gain.linearRampToValueAtTime(0, now + 0.02)
      for (const s of sink.sources) {
        try {
          s.stop(now + 0.02)
        } catch {
          /* 已自然结束的 source 调 stop 会抛，忽略 */
        }
      }
    } catch {
      /* 音频上下文异常不应阻塞打断流程 */
    }
    window.setTimeout(() => void sink.ctx.close().catch(() => undefined), 80)
  }, [])

  // 指标（延迟预算 / 产物信息）：全部来自 eval/reports 真实报告
  useEffect(() => {
    api.metrics().then(setMetrics).catch((e: Error) => setError(e.message))
  }, [])

  /** 建立会话。默认先尝试恢复 localStorage 里的会话；`fresh: true` 强制清空重开。 */
  const resetSession = useCallback(
    async (opts?: { fresh?: boolean }) => {
      closeRef.current?.()
      setEvents([])
      setStreamText(null)
      setLastTurn(null)
      setError(null)
      setConnecting(true)
      seqRef.current = {}
      bufRef.current = ''
      mutedRef.current = false
      resetLip()

      const saved = opts?.fresh ? null : loadPersisted()
      if (opts?.fresh) {
        setMessages([])
        clearPersisted()
      }

      if (saved) {
        setMessages(saved.messages) // 先恢复历史：用户立刻看到上一轮的对话
        try {
          const probe = await api.getSession(saved.sessionId)
          setSession({ session_id: probe.session_id, created_at: probe.created_at })
          t0Ref.current = Date.now()
          setState('listening')
          setConnecting(false)
          return // 复用成功：后端上下文（ctx.history）也在，多轮连续
        } catch {
          // 后端重启过 → 旧会话失效，但本地历史保留
          setMessages((m) => [
            ...m,
            {
              role: 'system',
              at: Date.now(),
              text: '服务端会话已重置（后端重启动过）：以上历史仅供查看，多轮上下文从本轮重新开始',
            },
          ])
        }
      }

      try {
        const s = await api.createSession()
        setSession(s)
        t0Ref.current = Date.now()
        setState('listening')
      } catch (e) {
        setError((e as Error).message)
        setState('error')
      } finally {
        setConnecting(false)
      }
    },
    [resetLip],
  )

  useEffect(() => {
    void resetSession()
    return () => closeRef.current?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 会话 + 对话历史落 localStorage（轻档持久化）：刷新/重开页面后恢复显示
  useEffect(() => {
    if (!session) return
    const keep = messages.filter((m) => m.role !== 'system').slice(-PERSIST_LIMIT)
    savePersisted({ sessionId: session.session_id, messages: keep })
  }, [session, messages])

  /** 把"已产出但尚未落地"的部分回复落成正式消息。
   *  为什么必须有：回复文本是逐 token 显示在临时的 `streamText` 里，**只有 `done` 事件才会把它
   *  落进 messages**。一旦 `done` 到不了——被**打断**（interrupt 关流）、或 SSE 传输中断——
   *  `streamText` 会在下一轮被清掉，而它从未进过 messages → 用户看到"上一轮的回复凭空消失"。 */
  const flushPartial = useCallback((reason: string) => {
    const partial = bufRef.current.trim()
    setStreamText(null)
    if (partial) {
      setMessages((m) => [...m, { role: 'digital', text: `${partial}（${reason}）`, at: Date.now() }])
    }
  }, [])

  /** 一轮对话：文本 → SSE（真实 ASR 端点 → DeepSeek 流式） */
  const sendTurn = useCallback(
    (text: string) => {
      if (!session) return
      closeRef.current?.()

      setMessages((m) => [...m, { role: 'user', text, at: Date.now() }])
      bufRef.current = ''
      setStreamText('')
      setState('thinking')
      seqRef.current = {}
      t0Ref.current = Date.now()
      setAudioStats(null)
      mutedRef.current = false // 新一轮：解除打断静默期
      stopPlayback() // 上一轮若还在播（含打断后残留），先干净停掉

      closeRef.current = openStream(
        session.session_id,
        {
          onEvent: (type: SseEventType, payload: Record<string, unknown>) => {
            const atMs = Date.now() - t0Ref.current
            const seq = typeof payload.seq === 'number' ? payload.seq : null

            // seq 连续性校验（SPEC §3.5 第 3 条）：每种媒体流各自计数
            let seqGap = false
            if (seq !== null && SEQ_TYPES.includes(type)) {
              const last = seqRef.current[type]
              seqGap = last !== undefined && seq !== last + 1
              seqRef.current[type] = seq
            }
            setEvents((list) => [...list, { seq: list.length, type, atMs, payload, seqGap }])

            if (type === 'thinking') setState('thinking')
            else if (type === 'brain_token') {
              bufRef.current += String(payload.token ?? '')
              setStreamText(bufRef.current)
            } else if (type === 'interrupted') {
              // 后端已确认打断：本地立刻停播 + 停口型（不等 /interrupt 往返）
              mutedRef.current = true
              stopPlayback()
              resetLip()
              setState('interrupted')
            } else if (type === 'tts_audio') {
              if (mutedRef.current) return // 打断静默期：丢弃在途音频
              setState('speaking')
              const b64 = String(payload.audio_b64 ?? '')
              const startMs = Number(payload.start_ms ?? 0)
              if (b64) {
                playPcmChunk(b64, startMs)
                const ms = Number(payload.end_ms ?? 0)
                const w = (payload.words as unknown[] | undefined)?.length ?? 0
                setAudioStats((s) => ({
                  chunks: (s?.chunks ?? 0) + 1,
                  ms: Math.max(s?.ms ?? 0, ms),
                  words: (s?.words ?? 0) + w,
                }))
              } else {
                const w = (payload.words as unknown[] | undefined)?.length ?? 0
                setAudioStats((s) => ({ chunks: s?.chunks ?? 0, ms: s?.ms ?? 0, words: (s?.words ?? 0) + w }))
              }
            } else if (type === 'lip_frame') {
              if (mutedRef.current) return
              setState('speaking')
            } else if (type === 'lip_video') {
              // ADR-005 默认路径：整句 H.264 片段，按音频时钟排队播放
              if (mutedRef.current) return
              setState('speaking')
              const b64 = String(payload.video_b64 ?? '')
              if (b64) {
                const bytes = b64ToBytes(b64)
                const url = URL.createObjectURL(new Blob([bytes], { type: 'video/mp4' }))
                enqueueLip({
                  seq: Number(payload.seq ?? 0),
                  startMs: Number(payload.start_ms ?? 0),
                  durationMs: Number(payload.duration_ms ?? 0),
                  nFrames: Number(payload.n_frames ?? 0),
                  fps: Number(payload.fps ?? 25),
                  url,
                  bytes: bytes.length,
                  lateMs: null,
                })
              }
            } else if (type === 'error') {
              setState('error')
              setError(`${payload.code}: ${payload.message}`)
              const partial = bufRef.current.trim()
              if (partial) setMessages((m) => [...m, { role: 'digital', text: partial, at: Date.now() }])
              setStreamText(null)
              stopPlayback()
              closeRef.current?.()
            } else if (type === 'done') {
              closeRef.current?.() // 一轮结束即关流，避免 EventSource 自动重连
              // 占位流（无 text 的空跑 / 断线重连）不是一轮真实回答：直接忽略
              if (payload.placeholder === true) return
              const answer = String(payload.answer ?? bufRef.current ?? '')
              if (answer) {
                setMessages((m) => [...m, { role: 'digital', text: answer, at: Date.now() }])
              } else {
                setMessages((m) => [
                  ...m,
                  {
                    role: 'system',
                    at: Date.now(),
                    text: '本轮无回复（SSE 连接中断或空占位流）—— 若反复出现请看链路工作台的事件流',
                  },
                ])
              }
              setStreamText(null)
              const ttft = typeof payload.ttft_ms === 'number' ? payload.ttft_ms : null
              const total = Number(payload.duration_ms ?? atMs)
              const tokens = Number(payload.tokens ?? 0)
              setLastTurn({ ttft, total, tokens })
              setState((payload.state_after as SessionState) ?? 'listening')
              setMessages((m) => [
                ...m,
                {
                  role: 'system',
                  at: Date.now(),
                  text: `SSE done · tokens ${tokens} · LLM TTFT ${ttft ?? '—'}ms · TTS 首包 ${
                    payload.tts_first_packet_ms ?? '—'
                  }ms · word 时间戳 ${Number(payload.tts_words ?? 0)} 个 · 总耗时 ${total}ms · state_after=${
                    payload.state_after ?? '?'
                  } · audio 分片 ${payload.total_seq_audio ?? 0} / 口型帧 ${payload.total_seq_lip ?? 0}`,
                },
              ])
            }
          },
          onError: (e) => {
            setError(e.message)
            flushPartial('连接中断') // done 不会来了，已产出的不能丢
          },
        },
        text,
      )
    },
    [session, stopPlayback, resetLip, flushPartial, enqueueLip, playPcmChunk],
  )

  /** 打断：本地立刻停播/停口型/关流，再通知后端（DESIGN-打断 §3.3 规则3）。 */
  const interrupt = useCallback(async () => {
    if (!session) return
    mutedRef.current = true // ① 静默期：丢弃在途分片（防止停播后又重建播放器出声）
    stopPlayback() // ② 数字人立刻闭嘴，不等后端往返
    resetLip()
    closeRef.current?.() // ③ 关流：不再接收新分片
    closeRef.current = null
    flushPartial('已被打断') // ④ 已产出的半句必须落地：done 不会再来了
    await api.interrupt(session.session_id).catch((e: Error) => setError(e.message))
    setState('interrupted')
    window.setTimeout(() => {
      void api.interruptDone(session.session_id).catch(() => undefined)
      setState('listening')
    }, 600)
  }, [session, stopPlayback, resetLip, flushPartial])

  // 麦克风回调（ADR-004：前端做端点与打断；远端只做识别）
  useEffect(() => {
    micCbRef.current = {
      onInterim: (t) => setLiveText(t),
      onEndpoint: (t) => {
        setLiveText('')
        sendTurn(t)
      },
      onLevel: (r) => setMicLevel(r),
      onBargeIn: () => {
        const now = Date.now()
        if (state !== 'speaking' || now - bargeAtRef.current < 2000) return
        bargeAtRef.current = now
        void interrupt()
      },
      onError: (m) => setError(m),
    }
  })

  // FR-06 自动打断：数字人播报期间保持麦克风监听（barge 模式，只跑 VAD、不上行音频）。
  // 为什么必须单独开一路：对话采集在静音端点后即释放音轨，不重新监听就永远采集不到插话，
  // 自动打断会退化成"只有手动按钮"。监听期间必须不上行音频，否则数字人自己的声音会被当成用户说话。
  const wantBargeRef = useRef(false)
  useEffect(() => {
    const wantBarge = state === 'speaking' && !recording
    wantBargeRef.current = wantBarge
    if (wantBarge) {
      // start 是异步的（getUserMedia + worklet）：**完成后必须再核对一次期望值**。
      // 否则播报很快结束时会「start 还没落地、清理已经跑过」→ 这次监听再没人释放
      // → modeRef 永远非 null → 之后麦克风再也开不起来（实测现象："只能用一次"）。
      void startMicRaw('barge').then(() => {
        if (!wantBargeRef.current) void stopMonitor()
      })
    } else {
      void stopMonitor() // 内部自带守卫（非 barge 直接返回），无条件调用是安全的
    }
  }, [state, recording, monitoring, startMicRaw, stopMonitor])

  /** 开麦（learner 视角只有一个按钮：开麦/结束） */
  const startMic = useCallback(async () => {
    setLiveText('')
    // ① 先让数字人彻底闭嘴再开麦克风。**不能只开麦**：若数字人还在播（或播尾音），
    //    麦克风会把扬声器里的上一轮回答收进去——AEC 在外放/大音量下消不干净——
    //    ASR 就把旧内容识别成本轮输入，LLM 照着再答一遍（"第二轮重复上一轮内容"）。
    stopPlayback()
    // ② 仍在播报态时按语义通知后端打断（DESIGN-打断 §3.3 规则3）
    if (state === 'speaking') void interrupt()
    // ③ 再开麦克风
    await startMicRaw()
  }, [state, interrupt, startMicRaw, stopPlayback])

  /** 对话页的麦克风 toggle：两侧都清 liveText（结束侧清掉"没说话就点结束"的残留） */
  const toggleMic = useCallback(() => {
    setLiveText('')
    if (recording) {
      stopMic()
      return
    }
    void startMic()
  }, [recording, stopMic, startMic])

  return {
    session,
    state,
    events,
    messages,
    streamText,
    lastTurn,
    metrics,
    error,
    audioStats,
    liveText,
    micLevel,
    recording,
    monitoring,
    lipClip,
    lipStats,
    connecting,
    sendTurn,
    interrupt,
    resetSession,
    startMic,
    stopMic,
    stopMonitor,
    toggleMic,
  }
}
