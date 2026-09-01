"""生成测试音频：edge-tts 合成测试集文本 → 16kHz 单声道 wav（V-03/V-06 输入）

依赖（首次运行前安装，需联网）:
  uv pip install --python ../.venv/Scripts/python.exe edge-tts
  ffmpeg 需在 PATH（本机 WinGet 已装 8.1.2）

用法（从 backend/eval/ 运行）:
  python gen_test_audio.py                # 默认输出 ../../data/testset/audio/
  python gen_test_audio.py --out xxx      # 自定义输出目录
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

# 测试集抽样（EVAL-测试集定义 §3）：覆盖单轮/多轮/短指令
ITEMS = {
    "t1-01": "你们家运费怎么算？",
    "t1-02": "下单后多久能发货？",
    "t2-01": "我想退货，流程怎么走？",
    "t2-02": "退款一般多久到账？",
    "t6-01": "在吗？",
    "t6-02": "谢谢",
    "t3-01": "你们发货要多久？那运费呢？包邮的话几天能到？",
}


async def synth(text: str, out_mp3: Path) -> None:
    import edge_tts

    tts = edge_tts.Communicate(text, voice="zh-CN-XiaoxiaoNeural", rate="-10%")
    await tts.save(str(out_mp3))


def main() -> int:
    parser = argparse.ArgumentParser(description="edge-tts 合成测试集音频 → 16k wav")
    parser.add_argument("--out", default="../../data/testset/audio")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for name, text in ITEMS.items():
        mp3 = out / f"{name}.mp3"
        wav = out / f"{name}.wav"
        asyncio.run(synth(text, mp3))
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-ar", "16000", "-ac", "1", str(wav)],
            check=True,
        )
        mp3.unlink()
        print(f"{name}: {text!r} -> {wav.name}")

    print(f"\n完成，共 {len(ITEMS)} 条 → {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
