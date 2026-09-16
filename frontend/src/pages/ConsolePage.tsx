import { useCallback, useEffect, useRef, useState } from 'react'
import { api, openStream } from '../api/client'
import AvatarStage, { type StageClip, type StageMeta } from '../components/AvatarStage'
import ConversationPanel, { type ChatMessage } from '../components/ConversationPanel'
import EventStreamTable from '../components/EventStreamTable'
import LatencyWaterfall, { type WfRow } from '../components/LatencyWaterfall'
import StateStepper from '../components/StateStepper'
import { useMicCapture, type MicCallbacks } from '../hooks/useMicCapture'
import { useLipVideo } from '../hooks/useLipVideo'
import type { MetricsResponse, SessionInfo, SessionState, SseEventType, StreamEvent } from '../types'

/** base64 → 字节（口型片段解码用；片段约数百 KB，逐字符转换可接受）
 *  返回类型显式声明 Uint8Array<ArrayBuffer>：TS 5.7 起 Uint8Array 带 buffer 泛型，
 *  默认的 ArrayBufferLike 不能直接喂给 Blob（SharedArrayBuffer 不兼容）。 */
function b64ToBytes(b64: string): Uint8Array<ArrayBuffer> {
  const raw = atob(b64)
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

/** 口型产物：云 GPU MuseTalk 真实推理输出（V-01），随仓库放在 public/media/ */
const CLIPS: StageClip[] = [
  {
    label: 'V-01 normal · 60s 全长（1500 帧）',
    src: '/media/avatar_v01.mp4',
    note: '云 GPU 4090 · MuseTalk v1.0 离线批处理 · 输出 704×1216@25fps（跟随输入分辨率）· UNet 6.06 it/s · 峰值显存 4828MiB',
  },
  {
    label: 'V-01 realtime · 8s 短句（199 帧）',
    src: '/media/avatar_v01_short.mp4',
    note: '云 GPU 4090 · realtime 模式稳态 19.07fps（52.4ms/帧，RTF 0.763 —— 25fps 预算下每帧差 12.4ms）',
  },
]

/** 带 seq 的流式事件类型（SPEC §3.3：媒体事件才有 seq） */
const SEQ_TYPES: SseEventType[] = ['brain_token', 'tts_audio', 'lip_frame', 'lip_video']

/**
 * PCM 播放（SPEC §3.5 第 2 条：以音频时钟为基准调度）。
 * TTS 输出 16kHz/16bit 单声道 PCM，逐片排进 Web Audio 时间轴实现"首包即播"。
 */
interface AudioSink {
  ctx: AudioContext
  base: number
}

export default function ConsolePage() {
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [state, setState] = useState<SessionState>('idle')
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streamText, setStreamText] = useState<string | null>(null)
  const [lastTurn, setLastTurn] = useState<{ ttft: number | null; total: number; tokens: number } | null>(null)
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const closeRef = useRef<null | (() => void)>(null)
  const t0Ref = useRef(0)
  const seqRef = useRef<Record<string, number>>({})
  const bufRef = useRef('')
  const audioRef = useRef<AudioSink | null>(null)
  const [audioStats, setAudioStats] = useState<{ chunks: number; ms: number; words: number } | null>(null)
  const [liveText, setLiveText] = useState('')
  const [micLevel, setMicLevel] = useState(0)
  const micCbRef = useRef<MicCallbacks | null>(null)
  const bargeAtRef = useRef(0)
  const {
    recording,
    monitoring,
    start: startMic,
    stop: stopMic,
    stopMonitor,
  } = useMicCapture(session?.session_id ?? null, micCbRef)

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
      sink = { ctx, base: ctx.currentTime + 0.15 } // 150ms 起播余量，避免首片被丢弃
      audioRef.current = sink
    }
    const buffer = sink.ctx.createBuffer(1, i16.length, 16000)
    const ch = buffer.getChannelData(0)
    for (let i = 0; i < i16.length; i++) ch[i] = i16[i] / 32768
    const src = sink.ctx.createBufferSource()
    src.buffer = buffer
    src.connect(sink.ctx.destination)
    src.start(sink.base + startMs / 1000)
  }, [])

  // 指标（延迟预算 / 产物信息）：全部来自 eval/reports 真实报告
  useEffect(() => {
    api.metrics().then(setMetrics).catch((e: Error) => setError(e.message))
  }, [])

  const resetSession = useCallback(async () => {
    closeRef.current?.()
    setEvents([])
    setMessages([])
    setStreamText(null)
    setLastTurn(null)
    setError(null)
    seqRef.current = {}
    bufRef.current = ''
    resetLip()
    try {
      const s = await api.createSession()
      setSession(s)
      t0Ref.current = Date.now()
      setState('listening')
    } catch (e) {
      setError((e as Error).message)
      setState('error')
    }
  }, [])

  useEffect(() => {
    void resetSession()
    return () => closeRef.current?.()
  }, [resetSession])

  /** 一轮对话：文本 → SSE（真实 ASR端点 → DeepSeek 流式） */
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
      if (audioRef.current) {
        void audioRef.current.ctx.close().catch(() => undefined)
        audioRef.current = null
      }

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
            setEvents((list) => [
              ...list,
              { seq: list.length, type, atMs, payload, seqGap },
            ])

            if (type === 'thinking') setState('thinking')
            else if (type === 'brain_token') {
              bufRef.current += String(payload.token ?? '')
              setStreamText(bufRef.current)
            } else if (type === 'interrupted') setState('interrupted')
            else if (type === 'tts_audio') {
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
                // 仅有词时间戳、无音频的尾包
                const w = (payload.words as unknown[] | undefined)?.length ?? 0
                setAudioStats((s) => ({ chunks: s?.chunks ?? 0, ms: s?.ms ?? 0, words: (s?.words ?? 0) + w }))
              }
            } else if (type === 'lip_frame') setState('speaking')
            else if (type === 'lip_video') {
              // ADR-005 默认路径：整句 H.264 片段，按音频时钟排队播放
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
              closeRef.current?.()
            } else if (type === 'done') {
              closeRef.current?.() // 一轮结束即关流，避免 EventSource 自动重连
              const answer = String(payload.answer ?? bufRef.current)
              if (answer) {
                setMessages((m) => [...m, { role: 'digital', text: answer, at: Date.now() }])
              }
              setStreamText(null)
              const ttft = typeof payload.ttft_ms === 'number' ? payload.ttft_ms : null
              const total = Number(payload.duration_ms ?? atMs)
              const tokens = Number(payload.tokens ?? 0)
              const ttsFirst = payload.tts_first_packet_ms ?? '—'
              const ttsWords = Number(payload.tts_words ?? 0)
              setLastTurn({ ttft, total, tokens })
              setState((payload.state_after as SessionState) ?? 'listening')
              setMessages((m) => [
                ...m,
                {
                  role: 'system',
                  at: Date.now(),
                  text: `SSE done · tokens ${tokens} · LLM TTFT ${ttft ?? '—'}ms · TTS 首包 ${ttsFirst}ms · word 时间戳 ${ttsWords} 个 · 总耗时 ${total}ms · state_after=${
                    payload.state_after ?? '?'
                  } · audio 分片 ${payload.total_seq_audio ?? 0} / 口型帧 ${payload.total_seq_lip ?? 0}`,
                },
              ])
            }
          },
          onError: (e) => setError(e.message),
        },
        text,
      )
    },
    [session],
  )

  const handleInterrupt = async () => {
    if (!session) return
    await api.interrupt(session.session_id).catch((e: Error) => setError(e.message))
    setState('interrupted')
    window.setTimeout(() => {
      void api.interruptDone(session.session_id).catch(() => undefined)
      setState('listening')
    }, 600)
  }

  const budget: WfRow[] = (metrics?.latency_budget ?? []).map((b) => ({
    key: b.key,
    label: b.label,
    ms: b.measured_ms,
    targetMs: b.target_ms,
    note: b.note,
  }))

  const rt = metrics?.runtime
  const stageMeta: StageMeta[] = [
    { label: 'LLM', value: 'deepseek-chat（真实）' },
    { label: '本轮 LLM TTFT', value: lastTurn?.ttft != null ? `${lastTurn.ttft}ms` : '—' },
    { label: '本轮 tokens', value: lastTurn ? String(lastTurn.tokens) : '—' },
    { label: 'TTS', value: 'cosyvoice-v2（真实）' },
    { label: '本轮音频', value: audioStats ? `${(audioStats.ms / 1000).toFixed(2)}s / ${audioStats.words} 词` : '—' },
    { label: '口型', value: '未接入' },
    { label: '分辨率', value: rt?.output_resolution ?? '—' },
    { label: '峰值显存', value: rt?.peak_vram_mib ? `${rt.peak_vram_mib} MiB` : '—' },
  ]

  const convMessages = streamText
    ? [...messages, { role: 'digital' as const, text: streamText, at: Date.now() }]
    : messages

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
        void handleInterrupt()
      },
      onError: (m) => setError(m),
    }
  })

  // FR-06 自动打断：数字人播报期间保持麦克风监听（barge 模式，只跑 VAD、不上行音频）。
  // 为什么必须单独开一路：对话采集在静音端点后即释放音轨，不重新监听就永远采集不到插话，
  // 自动打断会退化成"只有手动按钮"（见 PROGRESS-2026-09-15 的阻塞节）。
  // 监听期间必须不上行音频，否则数字人自己的声音会被当成用户说话送进 ASR。
  useEffect(() => {
    if (state === 'speaking' && !recording) void startMic('barge')
    else if (monitoring) void stopMonitor()
  }, [state, recording, monitoring, startMic, stopMonitor])

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>会话工作台</h1>
          <div className="desc">
            听→想→说→演 四段流水线的实时视图：会话状态机（SPEC §4.1）、SSE 事件流（§3.3）、
            延迟打点瀑布（PRD FR-07）。所有读数来自后端真实接口与实测报告，未接入段明确标注。
          </div>
        </div>
        <div className="topbar-meta">
          <span className="kv">
            <span className="muted">session</span>
            <span className="num">{session ? session.session_id.slice(0, 8) : '—'}</span>
          </span>
        </div>
      </div>

      {error && (
        <div className="notice">
          <strong>链路错误</strong>
          <span>{error}</span>
        </div>
      )}

      <StateStepper state={state} speakingActive={state === 'speaking'} />

      <div className="grid-2">
        <div className="stack">
          <div className="panel">
            <div className="panel-head">
              <h2>数字人舞台</h2>
              <span className="hint">产物：云 GPU MuseTalk 推理输出（V-01）</span>
              <div className="grow" />
              <span className="badge">{rt?.gpu ?? 'GPU 未记录'}</span>
            </div>
            <div className="panel-body">
              <AvatarStage
                state={state}
                sessionId={session?.session_id ?? null}
                clips={CLIPS}
                liveClip={lipClip}
                lipStats={lipStats}
                meta={stageMeta}
                canInterrupt={state === 'speaking'}
                onInterrupt={handleInterrupt}
                onReconnect={() => void resetSession()}
              />
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>延迟打点瀑布</h2>
              <span className="hint">目标值见 DESIGN §5.1 延迟预算</span>
              <div className="grow" />
              <span className="badge">{metrics ? `${metrics.reports_found.length} 份报告` : '加载中'}</span>
            </div>
            <div className="panel-body">
              <LatencyWaterfall rows={budget} />
            </div>
          </div>
        </div>

        <div className="stack">
          <div className="notice info">
            <strong>链路现状</strong>
            <span>
              <b>「想」「说」已接真实服务</b>：DeepSeek 流式（TTFT 打点 + 多轮上下文）→
              CosyVoice v2 流式合成（16k PCM 首包即播 + word 级时间戳）。
              一轮结束状态机走完整转移（thinking → speaking → listening）。
              <b>「演」未接入</b>：舞台上播放的是云 GPU V-01 预生成产物，尚未与本轮音频做口型对齐
              （口型帧调度将以 TTS 时间戳为时钟基准，见 SPEC §4.3）。
            </span>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>对话</h2>
              <span className="hint">文本输入（语音上行见 P2）</span>
            </div>
            <ConversationPanel
              messages={convMessages}
              disabled={!session || state === 'thinking'}
              recording={recording}
              liveText={liveText}
              micLevel={micLevel}
              onSend={sendTurn}
              onMicToggle={() => (recording ? stopMic() : void startMic())}
            />
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>SSE 事件流</h2>
              <span className="hint">seq 连续性是丢包检测依据（SPEC §3.5）</span>
            </div>
            <EventStreamTable events={events} />
          </div>
        </div>
      </div>
    </div>
  )
}
