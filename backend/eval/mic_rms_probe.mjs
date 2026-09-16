// mic_rms_probe.mjs — 忠实执行 frontend/public/worklets/mic-processor.js 的 RMS 计算。
//
// 用途：离线把 PCM 按 128 样本（Web Audio render quantum）喂进真实的 AudioWorkletProcessor 实现，
//       收集它每次 postMessage 出来的 rms，用于与「100ms 窗口 RMS」做对比标定。
//
// 为什么需要它：mic-processor.js 每 1600 样本（100ms）发一帧，
//       但 rms = Math.sqrt(sum / ch.length)，其中 sum 是「本次 process() 调用」累加的平方和、
//       ch.length 固定为 128 —— 两者口径不一致，等价于极短窗口 RMS。本探针不做任何修正，
//       原样跑，避免我们手抄错逻辑（对比的再实现自然也会错）。
//
// 用法：node mic_rms_probe.mjs <mic-processor.js 路径> <pcm16le 原始文件> [render_quantum]
// 输出：{"frames": N, "rms": [...]}

import fs from 'node:fs'

const RENDER_QUANTUM = Number(process.argv[4] || 128)
const srcPath = process.argv[2]
const pcmPath = process.argv[3]

const captured = []

class AudioWorkletProcessor {
  constructor() {
    this.port = { postMessage: (msg) => captured.push(msg.rms) }
  }
}
globalThis.AudioWorkletProcessor = AudioWorkletProcessor
globalThis.registerProcessor = (_name, cls) => { globalThis.__processor = cls }

// 原文件无 import/export，间接 eval 使其在全局作用域执行（registerProcessor 被上面的 mock 接住）
;(0, eval)(fs.readFileSync(srcPath, 'utf8'))

if (!globalThis.__processor) {
  console.error('FAIL: registerProcessor 未被调用，源文件可能已改名/改结构')
  process.exit(2)
}

const buf = fs.readFileSync(pcmPath)
const n = Math.floor(buf.length / 2)
const samples = new Float32Array(n)
for (let i = 0; i < n; i++) samples[i] = buf.readInt16LE(i * 2) / 32768

const proc = new globalThis.__processor()
for (let off = 0; off < n; off += RENDER_QUANTUM) {
  const chunk = samples.subarray(off, Math.min(off + RENDER_QUANTUM, n))
  proc.process([[chunk]])
}

console.log(JSON.stringify({ frames: captured.length, rms: captured }))
