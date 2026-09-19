import { useCallback, useRef, useState } from 'react'

/** 链路基准：16kHz / 16bit 单声道（SPEC §2 TtsSegment.sample_rate / §4.4 上行格式） */
const SAMPLE_RATE = 16000
const CHUNK_SAMPLES = 9600 // 600ms，与 ASR chunk_stride 对齐（V-03 口径）

// 静音阈值的**初始/上限**值（实测环境噪声约 0.002~0.008）。
// ⚠️ 不再作为判定阈值直接使用：固定 0.012 在底噪 ≥0.012 的环境（风扇/空调/临街/多人）
//    会让端点**永不触发**——用户说完话数字人不响应，且静默失效、不报错。
//    实际阈值由运行时底噪估计（floorRef）决定，见 onmessage 里的 FLOOR_MIN。
const SILENCE_RMS = 0.012
const FLOOR_MIN = 0.004 // 自适应阈值下限（=底噪下限×3）：防止估计过低把呼吸/键盘当语音
const FLOOR_MULT = 3.0 // 阈值 = 底噪估计 × 3
const SILENCE_MS = 1200 // 连续静音超过此时长 → 判定语句结束（ADR-004：前端 VAD）

// 打断判据（onBargeIn）：**相对底噪 + 连续帧确认**。
// 为什么不用固定阈值单帧触发：
//   · 固定 0.036 在底噪高的环境过松（噪声就能打断）、在底噪低的环境过紧；
//   · RMS 改整帧口径后，50ms 级瞬态（敲键盘/咳一声）会命中瞬时判据（离线标定实测 0.1414），
//     单帧触发即误打断 → 必须要求连续 2 帧（200ms）。
const BARGE_MULT = 8.0 // 判据 = 底噪估计 × 8
const BARGE_MIN = 0.03 // 绝对下限（干净环境下呼吸/键盘不应触发）
const BARGE_FRAMES = 2 // 连续超阈值帧数（100ms/帧）= 200ms 确认

/**
 * 采集模式（ADR-004：VAD/端点由前端做）
 *
 * - `dialog`：对话采集 —— 分片上行 ASR、静音超时判端点收尾、同时做打断检测
 * - `barge` ：打断监听 —— 数字人播报期间**只跑 VAD**（onBargeIn），不上行音频、不判端点
 *
 * 为什么需要 barge 模式：对话采集在静音端点后即释放音轨（`stop`），若不在播报期间重新开监听，
 * 用户插话根本采集不到 → FR-06 的自动打断形同虚设，只剩手动按钮（见
 * `docs/04-进度/PROGRESS-2026-09-15-实时链路接入与口型上云.md` 的阻塞节）。
 * 播报期间**必须只监听不上行**：否则数字人自己的声音会被当成"用户说话"送进 ASR。
 * 两种模式互斥（同一时刻只有一路采集）。
 */
export type MicMode = 'dialog' | 'barge'

export interface MicCallbacks {
  onInterim: (fullText: string) => void
  onEndpoint: (finalText: string) => void
  onLevel: (rms: number) => void
  onBargeIn: () => void
  onError: (message: string) => void
}

function toInt16Base64(samples: Float32Array): string {
  const buf = new ArrayBuffer(samples.length * 2)
  const view = new DataView(buf)
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  const bytes = new Uint8Array(buf)
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin)
}

/** 线性降采样：浏览器不给 16k 上下文时兜底（否则会以错误速率送 ASR，识别全错） */
function resample(input: Float32Array, from: number, to: number): Float32Array {
  if (from === to) return input
  const ratio = from / to
  const outLen = Math.floor(input.length / ratio)
  const out = new Float32Array(outLen)
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio
    const i0 = Math.floor(pos)
    const i1 = Math.min(i0 + 1, input.length - 1)
    out[i] = input[i0] + (input[i1] - input[i0]) * (pos - i0)
  }
  return out
}

export function useMicCapture(sessionId: string | null, cbRef: React.MutableRefObject<MicCallbacks | null>) {
  const [recording, setRecording] = useState(false) // dialog 采集中
  const [monitoring, setMonitoring] = useState(false) // barge 监听中
  const modeRef = useRef<MicMode | null>(null)
  const ctxRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const nodeRef = useRef<AudioWorkletNode | null>(null)
  const pendingRef = useRef<Float32Array>(new Float32Array(0))
  const seqRef = useRef(0)
  /** 采集代际：每次 release 递增，使**在途的 start()** 作废（见 start 里的核对）。
   *  没有它会出幽灵采集：getUserMedia/worklet 是异步的，等它返回时可能早已不该采集，
   *  却仍把 modeRef 设成非 null → 后续 start 全被守卫挡掉（"识别只能用一次"）。 */
  const epochRef = useRef(0)
  const silentMsRef = useRef(0)
  const fullTextRef = useRef('')
  const stoppingRef = useRef(false)
  // 环境底噪估计：**只降不升**（min 跟踪器）。
  // 降 = 捕捉安静帧得到真实底噪；不升 = 说话/瞬态永远不会把底噪顶高（否则阈值会随说话飙升）。
  // 判定阈值 = max(floor × 3, FLOOR_MIN)，取值区间 [0.004, 0.036]。
  const floorRef = useRef(SILENCE_RMS)
  const bargeFramesRef = useRef(0) // 连续超阈值帧计数（打断去抖）

  const postChunk = useCallback(
    async (pcm: string, end: boolean) => {
      if (!sessionId) return null
      try {
        const res = await fetch('/api/v1/asr/chunk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: sessionId,
            audio_pcm_16k_b64: pcm,
            seq: seqRef.current++,
            end,
          }),
        })
        if (!res.ok) throw new Error(`ASR ${res.status}`)
        return (await res.json()) as { full_text: string; is_final: boolean }
      } catch (e) {
        cbRef.current?.onError(`语音识别失败：${(e as Error).message}`)
        return null
      }
    },
    [sessionId, cbRef],
  )

  /** 释放采集资源（两种模式共用）。 */
  const release = useCallback(async () => {
    epochRef.current += 1 // 使所有在途 start() 作废（见 epochRef 注释）
    try {
      nodeRef.current?.disconnect()
      streamRef.current?.getTracks().forEach((t) => t.stop())
      const ctx = ctxRef.current
      if (ctx && ctx.state !== 'closed') await ctx.close()
    } catch {
      /* 资源释放失败不阻塞流程 */
    }
    nodeRef.current = null
    streamRef.current = null
    ctxRef.current = null
    modeRef.current = null
    setRecording(false)
    setMonitoring(false)
  }, [])

  /** 结束对话采集：把残余样本与收尾标记送出 → 后端返回最终文本 */
  const stop = useCallback(
    async (sendEndpoint: boolean) => {
      if (stoppingRef.current || modeRef.current !== 'dialog') return
      stoppingRef.current = true
      await release()

      const tail = pendingRef.current
      pendingRef.current = new Float32Array(0)
      const res = await postChunk(tail.length ? toInt16Base64(tail) : '', true)
      const finalText = (res?.full_text || fullTextRef.current).trim()
      stoppingRef.current = false
      if (sendEndpoint && finalText) cbRef.current?.onEndpoint(finalText)
    },
    [postChunk, cbRef, release],
  )

  /** 结束打断监听（数字人播报结束 / 已触发打断时调用）。 */
  const stopMonitor = useCallback(async () => {
    if (modeRef.current !== 'barge') return
    await release()
  }, [release])

  const start = useCallback(
    async (mode: MicMode = 'dialog') => {
      if (!sessionId) return
      // 已有采集在跑时：同模式直接返回；**不同模式抢占**（barge 监听让位给 dialog，反之亦然）。
      // ⚠️ 不能静默 return：barge 监听一旦因竞态没被释放，之后每次 start 都撞在这道守卫上
      //    → 麦克风永久失灵（实测现象："ASR 识别只能用一次"）。
      if (modeRef.current !== null) {
        if (modeRef.current === mode) return
        await release()
      }
      const myEpoch = ++epochRef.current
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        })
        const ctx = new AudioContext({ sampleRate: SAMPLE_RATE })
        await ctx.audioWorklet.addModule('/worklets/mic-processor.js')
        const source = ctx.createMediaStreamSource(stream)
        const node = new AudioWorkletNode(ctx, 'mic-processor')

        modeRef.current = mode
        pendingRef.current = new Float32Array(0)
        seqRef.current = 0
        silentMsRef.current = 0
        fullTextRef.current = ''
        stoppingRef.current = false
        floorRef.current = SILENCE_RMS
        bargeFramesRef.current = 0

        node.port.onmessage = async (ev: MessageEvent) => {
          const raw = ev.data as { frame: Float32Array; rms: number }
          const rms = raw.rms
          cbRef.current?.onLevel(rms)

          // 环境底噪跟踪（只降不升）→ 自适应静音阈值
          floorRef.current = Math.min(floorRef.current, rms)
          const silenceThresh = Math.max(floorRef.current * FLOOR_MULT, FLOOR_MIN)

          // 打断检测（ADR-004：前端 VAD → HTTP /interrupt）：两种模式都做。
          // 判据 = 相对底噪（floor×8，下限 0.03）+ 连续 2 帧（200ms）确认（见文件头常量注释）
          const bargeThresh = Math.max(floorRef.current * BARGE_MULT, BARGE_MIN)
          if (rms > bargeThresh) {
            bargeFramesRef.current += 1
            if (bargeFramesRef.current >= BARGE_FRAMES) {
              bargeFramesRef.current = 0
              cbRef.current?.onBargeIn()
            }
          } else {
            bargeFramesRef.current = 0
          }

          // 监听模式到此为止：不上行音频、不判端点
          // （上行会把数字人自己的声音喂给 ASR；判端点会误触发收尾）
          if (modeRef.current === 'barge') return

          const frame = resample(raw.frame, ctx.sampleRate, SAMPLE_RATE)
          silentMsRef.current = rms < silenceThresh ? silentMsRef.current + 100 : 0

          const merged = new Float32Array(pendingRef.current.length + frame.length)
          merged.set(pendingRef.current)
          merged.set(frame, pendingRef.current.length)
          pendingRef.current = merged

          while (pendingRef.current.length >= CHUNK_SAMPLES) {
            const piece = pendingRef.current.slice(0, CHUNK_SAMPLES)
            pendingRef.current = pendingRef.current.slice(CHUNK_SAMPLES)
            const res = await postChunk(toInt16Base64(piece), false)
            if (res && res.full_text !== fullTextRef.current) {
              fullTextRef.current = res.full_text
              cbRef.current?.onInterim(res.full_text)
            }
          }

          // 静音端点 → 自动收尾
          if (silentMsRef.current >= SILENCE_MS) void stop(true)
        }

        // 期间若已被释放/抢占（release 会递增 epoch），这次 start 作废：
        // 关掉刚拿到的资源后返回，绝不留下"幽灵采集"占住 modeRef。
        if (epochRef.current !== myEpoch) {
          stream.getTracks().forEach((t) => t.stop())
          if (ctx.state !== 'closed') void ctx.close()
          return
        }

        source.connect(node)
        node.connect(ctx.destination) // 不播放，仅为保持图中节点被调度
        ctxRef.current = ctx
        streamRef.current = stream
        nodeRef.current = node
        if (mode === 'barge') setMonitoring(true)
        else setRecording(true)
      } catch (e) {
        cbRef.current?.onError(`麦克风开启失败：${(e as Error).message}（需允许麦克风权限）`)
        await release()
      }
    },
    [sessionId, postChunk, cbRef, release, stop],
  )

  // ⚠️ 手动"结束"必须当作端点（sendEndpoint=true）：按钮文案承诺的是"点击结束**并发送**"
  //   （ConversationPanel 的 title），而旧实现传 false → onEndpoint 永不触发 → 文字发不出去，
  //   只留在 liveText 里，表现为"点了结束没反应，再点说话又看到上次的字"。
  return { recording, monitoring, start, stop: () => void stop(true), stopMonitor }
}
