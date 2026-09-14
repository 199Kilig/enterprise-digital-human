import { useCallback, useRef, useState } from 'react'

/** 链路基准：16kHz / 16bit 单声道（SPEC §2 TtsSegment.sample_rate / §4.4 上行格式） */
const SAMPLE_RATE = 16000
const CHUNK_SAMPLES = 9600 // 600ms，与 ASR chunk_stride 对齐（V-03 口径）
const SILENCE_RMS = 0.012 // 静音阈值（实测环境噪声约 0.002~0.008）
const SILENCE_MS = 1200 // 连续静音超过此时长 → 判定语句结束（ADR-004：前端 VAD）

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
  const [recording, setRecording] = useState(false)
  const ctxRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const nodeRef = useRef<AudioWorkletNode | null>(null)
  const pendingRef = useRef<Float32Array>(new Float32Array(0))
  const seqRef = useRef(0)
  const silentMsRef = useRef(0)
  const fullTextRef = useRef('')
  const stoppingRef = useRef(false)

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

  const stop = useCallback(
    async (sendEndpoint: boolean) => {
      if (stoppingRef.current) return
      stoppingRef.current = true
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
      setRecording(false)

      // 把残余样本与收尾标记送出 → 后端返回最终文本
      const tail = pendingRef.current
      pendingRef.current = new Float32Array(0)
      const res = await postChunk(tail.length ? toInt16Base64(tail) : '', true)
      const finalText = (res?.full_text || fullTextRef.current).trim()
      stoppingRef.current = false
      if (sendEndpoint && finalText) cbRef.current?.onEndpoint(finalText)
    },
    [postChunk, cbRef],
  )

  const start = useCallback(async () => {
    if (!sessionId || recording) return
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      })
      const ctx = new AudioContext({ sampleRate: SAMPLE_RATE })
      await ctx.audioWorklet.addModule('/worklets/mic-processor.js')
      const source = ctx.createMediaStreamSource(stream)
      const node = new AudioWorkletNode(ctx, 'mic-processor')

      pendingRef.current = new Float32Array(0)
      seqRef.current = 0
      silentMsRef.current = 0
      fullTextRef.current = ''
      stoppingRef.current = false

      node.port.onmessage = async (ev: MessageEvent) => {
        const raw = ev.data as { frame: Float32Array; rms: number }
        const frame = resample(raw.frame, ctx.sampleRate, SAMPLE_RATE)
        const rms = raw.rms
        cbRef.current?.onLevel(rms)

        // 打断检测（ADR-004：前端 VAD → HTTP /interrupt）：数字人说话时用户出声
        if (rms > SILENCE_RMS * 3) cbRef.current?.onBargeIn()

        silentMsRef.current = rms < SILENCE_RMS ? silentMsRef.current + 100 : 0
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

      source.connect(node)
      node.connect(ctx.destination) // 不播放，仅为保持图中节点被调度
      ctxRef.current = ctx
      streamRef.current = stream
      nodeRef.current = node
      setRecording(true)
    } catch (e) {
      cbRef.current?.onError(`麦克风开启失败：${(e as Error).message}（需允许麦克风权限）`)
      setRecording(false)
    }
  }, [sessionId, recording, postChunk, cbRef, stop])

  return { recording, start, stop: () => void stop(false) }
}
