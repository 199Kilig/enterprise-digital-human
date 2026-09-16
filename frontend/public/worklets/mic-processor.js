// 麦克风采集 AudioWorklet（ADR-004：P1 的 VAD/端点由前端做，后端只做识别）
// AudioContext 以 16kHz 创建，故此处拿到的已是链路基准采样率，无需重采样。
// 每累积 ~100ms（1600 样本）向主线程发一帧，并附带该帧 RMS（静音/打断判定用）。
//
// ⚠️ RMS 口径（2026-09-15 修正）：必须按**整帧**（FRAME 个样本）计算。
//    原实现把 `let sum = 0` 放在 process() 内部、并写 `rms = Math.sqrt(sum / ch.length)`：
//      · sum 每次 process() 调用（128 样本 = render quantum）就被重置
//      · 1600 ÷ 128 = 12.5 不是整数 → 实际参与计算的只有「跨越帧边界那次调用中前 ~65 个样本」
//      · 分母却写死 128
//    ⇒ 等价于「~4ms 窗口 RMS」，且系统性低估 ~19%，**完全漏检 50ms 级瞬态**
//      （同一段 0.2 幅度脉冲：旧实现看到 0.0022，正确实现看到 0.1414）。
//    证据：backend/eval/reports/asr_mic_check.md §2 + eval/reports/asr_endpoint_calibration.json
const FRAME = 1600; // 100ms @16k

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = new Float32Array(FRAME);
    this._n = 0;
    this._sumSq = 0; // 帧内平方和：随样本累加、发帧时清零（不能放在 process() 的局部变量里）
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      for (let i = 0; i < ch.length; i++) {
        const s = ch[i];
        this._buf[this._n++] = s;
        this._sumSq += s * s;
        if (this._n === FRAME) {
          const rms = Math.sqrt(this._sumSq / FRAME); // 分子分母同为整帧口径
          const out = this._buf.slice(0);
          this.port.postMessage({ frame: out, rms }, [out.buffer]);
          this._n = 0;
          this._sumSq = 0;
        }
      }
    }
    return true; // 保持处理器存活
  }
}

registerProcessor('mic-processor', MicProcessor);
