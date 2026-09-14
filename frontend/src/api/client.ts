/**
 * 后端接口客户端（对接真实实现，不 mock）
 * 契约来源：docs/02-方案/SPEC-接口与协议规范.md
 *   §5.1 POST /api/v1/session
 *   §5.2 POST /api/v1/session/{id}/interrupt
 *   §5.2.1 POST /api/v1/session/{id}/interrupt_done
 *   §5.3 DELETE /api/v1/session/{id}
 *   §3.1 GET  /api/v1/chat/stream  (SSE)
 *   §2.1 POST /api/v1/lip/infer
 *   另有本仓新增只读端点 /api/v1/metrics、/api/v1/eval/ledger、/api/v1/health
 */
import type {
  HealthResponse,
  LedgerResponse,
  LipInferResponse,
  MetricsResponse,
  SessionInfo,
  SseEventType,
} from '../types'

const BASE = '/api/v1'

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: unknown = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new Error(`${res.status} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`)
  }
  return (await res.json()) as T
}

export const api = {
  createSession: () =>
    fetch(`${BASE}/session`, { method: 'POST' }).then((r) => json<SessionInfo>(r)),

  closeSession: (sessionId: string) =>
    fetch(`${BASE}/session/${sessionId}`, { method: 'DELETE' }).then((r) => json<{ status: string }>(r)),

  interrupt: (sessionId: string) =>
    fetch(`${BASE}/session/${sessionId}/interrupt`, { method: 'POST' }).then((r) =>
      json<{ status: string; state?: string }>(r),
    ),

  interruptDone: (sessionId: string) =>
    fetch(`${BASE}/session/${sessionId}/interrupt_done`, { method: 'POST' }).then((r) =>
      json<{ status: string; session_id?: string }>(r),
    ),

  metrics: () => fetch(`${BASE}/metrics`).then((r) => json<MetricsResponse>(r)),

  ledger: () => fetch(`${BASE}/eval/ledger`).then((r) => json<LedgerResponse>(r)),

  health: () => fetch(`${BASE}/health`).then((r) => json<HealthResponse>(r)),

  lipInfer: (body: {
    audio_pcm_16k_b64: string
    avatar_frames_b64?: string[]
    fps?: number
    session_id?: string
  }) =>
    fetch(`${BASE}/lip/infer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ avatar_frames_b64: [], fps: 25, ...body }),
    }).then((r) => json<LipInferResponse>(r)),
}

export interface SseHandlers {
  onEvent: (type: SseEventType, payload: Record<string, unknown>) => void
  onOpen?: () => void
  onError?: (err: EventSourceError) => void
}

interface EventSourceError {
  message: string
}

const KNOWN_EVENTS: SseEventType[] = [
  'thinking',
  'interrupted',
  'tts_audio',
  'lip_frame',
  'lip_video',
  'brain_token',
  'done',
  'error',
]

/**
 * 建立 SSE 连接（SPEC §3.1）。
 * text 为 P1 文本输入通道（可选）：带上即触发后端真实链路（ASR端点→LLM 流式）。
 * 注意：EventSource 会自动重连，一轮结束需显式 close。
 */
export function openStream(sessionId: string, handlers: SseHandlers, text?: string): () => void {
  let url = `${BASE}/chat/stream?session_id=${encodeURIComponent(sessionId)}`
  if (text) url += `&text=${encodeURIComponent(text)}`
  const es = new EventSource(url)

  es.onopen = () => handlers.onOpen?.()

  for (const type of KNOWN_EVENTS) {
    es.addEventListener(type, (ev: MessageEvent) => {
      let payload: Record<string, unknown> = {}
      try {
        payload = JSON.parse(ev.data) as Record<string, unknown>
      } catch {
        payload = { raw: ev.data }
      }
      handlers.onEvent(type, payload)
    })
  }

  es.onerror = () => {
    // readyState 2 = CLOSED：后端结束流或断开；不把它当致命错误（EventSource 会重连）
    if (es.readyState === EventSource.CLOSED) {
      handlers.onError?.({ message: 'SSE 连接已关闭' })
    }
  }

  return () => es.close()
}
