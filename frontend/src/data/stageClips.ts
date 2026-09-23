/** 待机（idle）形象：**静帧循环** —— 取静音驱动产出中嘴唇最闭合的一帧（第 0 帧）编成 2s 静止循环。
 *  为什么用静帧而不是"最静窗口"：静音驱动 MuseTalk 的"最静 1 秒"仍残留口型微动
 *  （实测帧间 PSNR 37.2dB），待机时观感就是"嘴巴一直在动"。改静帧后实测 48.7dB（视觉静止），
 *  体积也从 2.07MB 降到 118KB。生成与校验口径见 RUNBOOK §3.19。
 *  为什么不做"实时渲染待机"：MuseTalk 是音频驱动的口型模型，**没有音频就没有输出**；
 *  让它常驻渲染待机只会白占一张 4090（实测说话时生成 9~89 fps 已接近吃满）。
 *  生成口径与校验方法见 RUNBOOK §3.19；素材本身是真实模型产物，仅做了选段与无缝拼接。 */
export const IDLE_CLIP = '/media/avatar_idle.mp4'

/** 数字人舞台产物（云 GPU MuseTalk 真实推理输出，随仓库放在 public/media/）。
 *  两个界面共用：/console 学习者对话页与 /studio 链路工作台都播放同一份真实产物，
 *  ⚠️ 列表**第 0 项就是默认播放项**：待机必须排第一，否则工作台一打开就循环讲话视频。 */
import type { StageClip } from '../components/AvatarStage'

export const STAGE_CLIPS: StageClip[] = [
  {
    label: '待机 · idle 静帧循环（50 帧 / 2s）',
    src: IDLE_CLIP,
    note: '云 GPU MuseTalk 静音驱动产出中取嘴唇最闭合帧 + 静帧编码（tune stillimage）· 帧间 PSNR 48.7dB 视觉静止 · 704×1216@25fps · 118KB',
  },
  {
    label: 'V-01 normal · 60s 全长（1500 帧）',
    src: '/media/avatar_v01.mp4',
    note: '云 GPU 4090 · MuseTalk v1.0 离线批处理 · 输出 704×1216@25fps（跟随输入分辨率）· UNet 6.06 it/s · 峰值显存 4828MiB —— 这是**讲话**产物，只作演示，不能当待机',
  },
  {
    label: 'V-01 realtime · 8s 短句（199 帧）',
    src: '/media/avatar_v01_short.mp4',
    note: '云 GPU 4090 · realtime 模式稳态 19.07fps（52.4ms/帧，RTF 0.763 —— 25fps 预算下每帧差 12.4ms）',
  },
]
