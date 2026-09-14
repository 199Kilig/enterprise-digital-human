/* SPEC §2 契约的类型镜像（前后端一致，改契约先改这里） */

export type SessionState =
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'interrupted'
  | 'error'
  | 'idle'

/** SPEC §3.3 事件类型 */
export type SseEventType =
  | 'thinking'
  | 'interrupted'
  | 'tts_audio'
  | 'lip_frame'
  | 'lip_video'
  | 'brain_token'
  | 'done'
  | 'error'

/** 前端记录的原始事件（事件流表格用） */
export interface StreamEvent {
  seq: number
  type: SseEventType
  atMs: number
  payload: Record<string, unknown>
  /** seq 连续性校验结果（SPEC §3.5 第 3 条） */
  seqGap: boolean
}

export interface SessionInfo {
  session_id: string
  created_at: string
}

/** 延迟打点：一段链路的耗时（PRD FR-07） */
export interface LatencySpan {
  stage: string
  label: string
  ms: number | null
  /** 目标值（DESIGN §5.1 延迟预算） */
  targetMs: number | null
  source: string
}

/** POST /api/v1/lip/infer 响应（SPEC §2.1，v1.2 起默认 transport=h264） */
export interface LipInferResponse {
  transport?: 'h264' | 'frames'
  /** transport=h264：整句 H.264(MP4) 片段 */
  video_b64?: string
  duration_ms?: number
  n_frames?: number
  fps?: number
  /** transport=frames（P1 兼容路径） */
  frames?: { frame_b64: string; pts_ms: number; frame_idx: number }[]
  gpu_memory_mb: number
  infer_fps: number
}

/** 指标看板数据（后端读 eval/reports/*.json） */
export interface MetricRow {
  dimension: string
  metric: string
  value: string | null
  target: string | null
  status: 'ok' | 'warn' | 'fail' | 'pending'
  scenario: string | null
  env: string | null
  source: string | null
  measuredAt: string | null
}

export interface MetricsResponse {
  generated_at: string
  reports_dir: string
  reports_found: string[]
  rows: MetricRow[]
  /** 延迟瀑布图数据（DESIGN §5.1 预算 + 实测） */
  latency_budget: LatencyBudget[]
  /** 口型产物运行信息（来自 V-01 报告） */
  runtime: RuntimeInfo
}

export interface LatencyBudget {
  key: string
  label: string
  measured_ms: number | null
  target_ms: number | null
  note: string
}

export interface RuntimeInfo {
  gpu: string | null
  image: string | null
  output_resolution: string | null
  output_fps: number | null
  peak_vram_mib: number | null
  realtime_fps: number | null
  realtime_rtf: number | null
}

/** 评估台账（后端解析 docs/eval-history.md） */
export interface LedgerRow {
  date: string
  metric: string
  value: string
  scope: string
  env: string
  source: string
}

export interface LedgerResponse {
  generated_at: string
  source_file: string
  rows: LedgerRow[]
}

/** 链路健康（各模块就绪状态，工作台顶部） */
export interface HealthResponse {
  api: 'up'
  lip_service: { url: string; reachable: boolean; mode: string | null }
  reports_present: number
  sessions_active: number
}
