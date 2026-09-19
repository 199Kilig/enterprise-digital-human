import AvatarStage, { type StageMeta } from '../components/AvatarStage'
import ConversationPanel from '../components/ConversationPanel'
import EventStreamTable from '../components/EventStreamTable'
import LatencyWaterfall, { type WfRow } from '../components/LatencyWaterfall'
import StateStepper from '../components/StateStepper'
import { STAGE_CLIPS } from '../data/stageClips'
import { useDuplexSession } from '../hooks/useDuplexSession'

/**
 * 链路工作台（技术详情界面）
 * ---------------------------------------------------------------------------
 * 这是「技术数据」的唯一归属地：会话状态机、SSE 事件流、延迟打点瀑布、口型片段与音画偏差、
 * 每轮的 token/TTFT 读数。学习者界面 `/console` **不展示**这些内容（只需要好观感），
 * 侧栏入口放在「工程视图」分组的最后，不喧宾夺主。
 */
export default function StudioPage() {
  const s = useDuplexSession()
  const budget: WfRow[] = (s.metrics?.latency_budget ?? []).map((b) => ({
    key: b.key,
    label: b.label,
    ms: b.measured_ms,
    targetMs: b.target_ms,
    note: b.note,
  }))
  const rt = s.metrics?.runtime

  const stageMeta: StageMeta[] = [
    { label: 'LLM', value: 'deepseek-chat（真实）' },
    { label: '本轮 LLM TTFT', value: s.lastTurn?.ttft != null ? `${s.lastTurn.ttft}ms` : '—' },
    { label: '本轮 tokens', value: s.lastTurn ? String(s.lastTurn.tokens) : '—' },
    { label: 'TTS', value: 'cosyvoice-v2（真实）' },
    {
      label: '本轮音频',
      value: s.audioStats ? `${(s.audioStats.ms / 1000).toFixed(2)}s / ${s.audioStats.words} 词` : '—',
    },
    { label: '口型', value: '未接入' },
    { label: '分辨率', value: rt?.output_resolution ?? '—' },
    { label: '峰值显存', value: rt?.peak_vram_mib ? `${rt.peak_vram_mib} MiB` : '—' },
  ]

  const convMessages = s.streamText
    ? [...s.messages, { role: 'digital' as const, text: s.streamText, at: Date.now() }]
    : s.messages

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>链路工作台</h1>
          <div className="desc">
            技术详情界面：听→想→说→演 四段流水线的实时视图——会话状态机（SPEC §4.1）、SSE 事件流（§3.3）、
            延迟打点瀑布（PRD FR-07）。所有读数来自后端真实接口与实测报告，未接入段明确标注。
            <br />
            学习者界面 <span className="num">/console</span> 不展示这些数据，只给对话与声音。
          </div>
        </div>
        <div className="topbar-meta">
          <span className="kv">
            <span className="muted">session</span>
            <span className="num">{s.session ? s.session.session_id.slice(0, 8) : '—'}</span>
          </span>
        </div>
      </div>

      {s.error && (
        <div className="notice">
          <strong>链路错误</strong>
          <span>{s.error}</span>
        </div>
      )}

      <StateStepper state={s.state} speakingActive={s.state === 'speaking'} />

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
                state={s.state}
                sessionId={s.session?.session_id ?? null}
                clips={STAGE_CLIPS}
                liveClip={s.lipClip}
                lipStats={s.lipStats}
                meta={stageMeta}
                canInterrupt={s.state === 'speaking'}
                onInterrupt={() => void s.interrupt()}
                onReconnect={() => void s.resetSession({ fresh: true })}
              />
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>延迟打点瀑布</h2>
              <span className="hint">目标值见 DESIGN §5.1 延迟预算</span>
              <div className="grow" />
              <span className="badge">{s.metrics ? `${s.metrics.reports_found.length} 份报告` : '加载中'}</span>
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
              <b>「想」「说」已接真实服务</b>：DeepSeek 流式（TTFT 打点 + 多轮上下文，人格见
              `config.yaml llm.persona`）→ CosyVoice v2 流式合成（16k PCM 首包即播 + word 级时间戳），
              送合成前经 <span className="num">brain/text_clean</span> 清理格式噪音（ADR-008）。
              一轮结束状态机走完整转移（thinking → speaking → listening）。
              <b>「演」未接入</b>：舞台上播放的是云 GPU V-01 预生成产物，尚未与本轮音频做口型对齐
              （口型帧调度将以 TTS 时间戳为时钟基准，见 SPEC §4.3）。
            </span>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>对话（调试）</h2>
              <span className="hint">与学习者界面同一条链路；此处保留 system 读数行</span>
            </div>
            <ConversationPanel
              messages={convMessages}
              disabled={!s.session || s.state === 'thinking'}
              recording={s.recording}
              liveText={s.liveText}
              micLevel={s.micLevel}
              onSend={s.sendTurn}
              onMicToggle={s.toggleMic}
            />
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>SSE 事件流</h2>
              <span className="hint">seq 连续性是丢包检测依据（SPEC §3.5）</span>
            </div>
            <EventStreamTable events={s.events} />
          </div>
        </div>
      </div>
    </div>
  )
}
