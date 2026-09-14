// 麦克风采集 AudioWorklet（ADR-004：P1 的 VAD/端点由前端做，后端只做识别）
// AudioContext 以 16kHz 创建，故此处拿到的已是链路基准采样率，无需重采样。
// 每累积 ~100ms（1600 样本）向主线程发一帧，并附带该帧 RMS（静音检测用）。
const FRAME = 1600; // 100ms @16k

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = new Float32Array(FRAME);
    this._n = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      let sum = 0;
      for (let i = 0; i < ch.length; i++) {
        sum += ch[i] * ch[i];
        this._buf[this._n++] = ch[i];
        if (this._n === FRAME) {
          const rms = Math.sqrt(sum / ch.length);
          const out = this._buf.slice(0);
          this.port.postMessage({ frame: out, rms }, [out.buffer]);
          this._n = 0;
        }
      }
    }
    return true; // 保持处理器存活
  }
}

registerProcessor('mic-processor', MicProcessor);
