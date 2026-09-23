import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { HealthResponse } from '../types'
import { IDLE_CLIP, STAGE_CLIPS } from '../data/stageClips'
import type { StageClip } from '../components/AvatarStage'
import { IconBot, IconGauge, IconRefresh, IconVideo, IconWarn } from '../components/edu/Icons'

/**
 * 数字人资产台（`/library`）
 * ---------------------------------------------------------------------------
 * 定位：对标企业级数字人平台后台的「形象 / 驱动 / 对话 / 产物」四件套
 * （腾讯云智能数智人的后台即为此四项；Synthesia 对应 Avatar Library + Brand Kit）。
 *
 * 数据口径 —— 本页**只展示能溯源的真数据**：
 *   1. 形象与产物：`data/stageClips.ts` 里的真实云 GPU MuseTalk 产物（含分辨率/帧率/体积）
 *   2. 服务状态：`GET /api/v1/health`（后端真实探测口型服务连通性）
 *   3. 口型推理能力：`POST /api/v1/lip/infer` 的存在与当前可用性（由 health 的
 *      `lip_service.reachable` 决定）
 *
 * 明确不做（守 ADR-009「不做查不到来源的数字」）：
 *   · 不写死人格与音色的"猜测值"。它们的生效值在后端 `config.yaml`
 *     （`llm.persona` / `tts.voice`），当前没有只读端点暴露给前端 →
 *     本页如实说明这一点，而不是把文档里的名字当成运行时事实印在界面上。
 *   · 不做"点一下生成讲解视频"的按钮。`/lip/infer` 需要 `audio_pcm_16k_b64` 入参，
 *     本页没有音频输入源，做出来只能是个假按钮；改为展示该能力的**依赖与当前可用性**，
 *     并用它真实产出过的产物作为证据。
 */

export default function LibraryPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [clip, setClip] = useState<StageClip>(STAGE_CLIPS[0])

  const load = async () => {
    setLoading(true)
    try {
      const h = await api.health()
      setHealth(h)
      setErr(null)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // 与 AppShell 的 health 轮询同频（10s），自己拉一份：本页是路由子树，拿不到 AppShell 的 props
    const t = setInterval(() => void load(), 10_000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const lip = health?.lip_service
  const lipOk = !!lip?.reachable

  return (
    <div className="edu-library">
      <header className="edu-page-head">
        <div>
          <h1>
            <IconBot size={18} />
            数字人资产台
          </h1>
          <p>
            形象、产物与服务状态。这里的每一项都能在仓库或接口里找到来源 —— 查不到来源的数字
            不往这放。
          </p>
        </div>
        <button className="edu-ghost" onClick={() => void load()} disabled={loading}>
          <IconRefresh size={13} /> {loading ? '探测中…' : '刷新状态'}
        </button>
      </header>

      <div className="edu-library-grid">
        {/* ---------- 形象 ---------- */}
        <section className="edu-card edu-lib-avatar">
          <div className="edu-card-head">
            <h2>数字人形象</h2>
            <span className="edu-card-sub">通用虚拟形象 · 不做真人克隆（PRD §2 非目标）</span>
          </div>
          <div className="edu-lib-preview">
            <video key={clip.src} src={clip.src} autoPlay loop muted playsInline />
          </div>
          <div className="edu-lib-current">
            <div className="edu-lib-current-name">{clip.label}</div>
            <div className="edu-lib-current-note">{clip.note}</div>
          </div>
          <div className="edu-lib-clips">
            {STAGE_CLIPS.map((c) => (
              <button
                key={c.src}
                className={`edu-lib-clip${c.src === clip.src ? ' on' : ''}`}
                onClick={() => setClip(c)}
                title={c.note}
              >
                {c.src === IDLE_CLIP ? '待机' : c.label.split(' · ')[0]}
              </button>
            ))}
          </div>
          <p className="edu-lib-hint">
            以上均为云 GPU（RTX 4090）MuseTalk 真实推理产物，随仓库放在{' '}
            <code>frontend/public/media/</code>。列表第 0 项即默认待机素材 —— 必须是**真静止**
            的静帧循环，否则待机时会出现"嘴巴一直在动"。
          </p>
        </section>

        {/* ---------- 服务状态 ---------- */}
        <section className="edu-card">
          <div className="edu-card-head">
            <h2>
              <IconGauge size={15} /> 服务状态
            </h2>
            <span className="edu-card-sub">GET /api/v1/health · 每 10 秒探测</span>
          </div>

          {err ? (
            <div className="edu-lib-error">
              <IconWarn size={14} /> 后端不可达：{err}
            </div>
          ) : !health ? (
            <p className="edu-muted-line">正在探测…</p>
          ) : (
            <ul className="edu-lib-status">
              <li>
                <span className="edu-lib-status-key">backend API</span>
                <span className="edu-lib-status-val ok">{health.api === 'up' ? '在线' : health.api}</span>
              </li>
              <li>
                <span className="edu-lib-status-key">口型推理服务</span>
                <span className={`edu-lib-status-val ${lipOk ? 'ok' : 'off'}`}>
                  {lipOk ? '可用' : '不可用'}
                </span>
              </li>
              <li>
                <span className="edu-lib-status-key">服务地址</span>
                <span className="edu-lib-status-val mono">{lip?.url || '—'}</span>
              </li>
              <li>
                <span className="edu-lib-status-key">运行模式</span>
                <span className="edu-lib-status-val mono">{lip?.mode || '—'}</span>
              </li>
              <li>
                <span className="edu-lib-status-key">活跃会话</span>
                <span className="edu-lib-status-val">{health.sessions_active}</span>
              </li>
              <li>
                <span className="edu-lib-status-key">已挂载报告</span>
                <span className="edu-lib-status-val">{health.reports_present}</span>
              </li>
            </ul>
          )}

          {!err && health && !lipOk && (
            <p className="edu-lib-hint warn">
              口型服务不可用时，对话仍可进行（语音与文字正常），只退化为**无口型视频**。
              恢复方式见 <code>docs/RUNBOOK-环境记录.md</code> 的一键恢复脚本。
            </p>
          )}
        </section>

        {/* ---------- 口型推理能力 ---------- */}
        <section className="edu-card">
          <div className="edu-card-head">
            <h2>
              <IconVideo size={15} /> 讲解产物能力
            </h2>
            <span className="edu-card-sub">POST /api/v1/lip/infer</span>
          </div>
          <div className="edu-lib-cap">
            <div className="edu-lib-cap-row">
              <span>当前状态</span>
              <strong className={lipOk ? 'ok' : 'off'}>
                {lipOk ? '可调用' : '依赖未就绪'}
              </strong>
            </div>
            <div className="edu-lib-cap-row">
              <span>依赖</span>
              <span>
                云 GPU MuseTalk（{lipOk ? '已连通' : '实例未启动'}）· 单 GPU 串行推理 · 输出
                H.264 整句片段（ADR-005）
              </span>
            </div>
            <div className="edu-lib-cap-row">
              <span>输入</span>
              <span className="mono">audio_pcm_16k_b64（文本经 TTS 合成后送入口型）</span>
            </div>
            <div className="edu-lib-cap-row">
              <span>产出证据</span>
              <span>上左侧三个片段即该能力的真实产出，非示意图</span>
            </div>
          </div>
          <p className="edu-lib-hint">
            本页不放"点一下生成讲解视频"的按钮：该接口需要音频入参，本页没有音频输入源，
            做出来只会是个点不动的假控件。
          </p>
        </section>

        {/* ---------- 待接入 ---------- */}
        <section className="edu-card edu-pending-card">
          <div className="edu-card-head">
            <h2>待接入能力</h2>
            <span className="edu-card-sub">后端尚无对应端点，故不展示数值</span>
          </div>
          <ul className="edu-pending">
            <li>
              <div className="edu-pending-name">人格与音色的生效值</div>
              <div className="edu-pending-need">
                需要 <code>GET /api/v1/config</code>（只读、白名单字段）：把{' '}
                <code>config.yaml</code> 里实际生效的 <code>llm.persona</code> /{' '}
                <code>llm.model</code> / <code>tts.voice</code> 暴露出来
              </div>
              <div className="edu-pending-use">
                在此之前，本页不写死人格与音色的名字 —— 文档里写的和运行时生效的可能不是一回事，
                印在界面上就是在制造查不到来源的事实。要亲眼核对当前人格，可看{' '}
                <Link to="/studio">链路工作台</Link> 的接口返回。
              </div>
            </li>
            <li>
              <div className="edu-pending-name">形象库与定制</div>
              <div className="edu-pending-need">
                需要对象存储 + 形象元数据表（形象名 / 分辨率 / 适用场景标签）
              </div>
              <div className="edu-pending-use">
                对标 duix.ai 的预训练模型库（冷焰=虚拟客服 / 艾米莉亚=外语教学，形象自带场景标签）
                与百度曦灵的三档形象（2D 精品 / 2D 小样本 / 照片数字人）
              </div>
            </li>
          </ul>
        </section>
      </div>

      <p className="edu-insights-scope">
        口径说明：本页**不读取也不展示任何密钥**。所有状态来自公开只读端点与随仓库的产物文件。
      </p>
    </div>
  )
}
