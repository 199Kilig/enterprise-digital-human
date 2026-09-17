"""真人麦克风 ASR 准确率复盘（采集 → 对齐 → 字准率 + 错例）。

**为什么要它**：固定测试集音频是 TTS 合成的（干净、无噪声），流式模型在它上面近乎全对——
所以"真机感受不好"**不可能**用那批音频定位。必须用真人麦克风的真实样本，把
「说了什么」和「识别成什么」逐条摆出来，才能判断是**模型精度**问题还是**参数/链路**问题。

三步走：
  1. 后端带采集开关启动（每轮语音会话的完整音频 + 识别文本自动落盘）：
         cd backend && ASR_DEBUG=1 PYTHONPATH=src .venv/Scripts/python.exe -m uvicorn api.routes:app --host 127.0.0.1 --port 8010
  2. 打开页面，**按 docs/eval/EVAL-测试集定义.md 的顺序**逐条念（每条一次语音会话：
     点"说话"→ 念 → 停顿等自动结束，或点"结束并发送"）。期望文本由本脚本从该文档解析。
  3. 跑本脚本，按**采集时间顺序**与测试集条目对齐：

         cd backend/eval && ../.venv/Scripts/python.exe verify_asr_mic_accuracy.py
         ../.venv/Scripts/python.exe verify_asr_mic_accuracy.py --start T1-02   # 从某条开始对齐
         ../.venv/Scripts/python.exe verify_asr_mic_accuracy.py --dir <别的采集目录>

采集目录：`backend/eval/reports/asr_capture/*.json`（wav 同名前缀，可回放核对）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from verify_asr_models import cer, normalize  # noqa: E402  同一口径，避免两套 CER 实现

EVAL_DOC = HERE.parents[1] / "docs" / "eval" / "EVAL-测试集定义.md"
CAPTURE_DIR = HERE / "reports" / "asr_capture"
OUT = HERE / "reports" / "asr_mic_accuracy.json"

# 测试集定义表格行：| T1-01 | 你们家运费怎么算？ | 运费政策（满额包邮/按件） |
ROW = re.compile(r"^\|\s*(T\d+-\d+)\s*\|\s*([^|]+?)\s*\|")


def load_expect(doc: Path) -> list[tuple[str, str]]:
    """从文档解析期望文本（单一事实来源，避免脚本里再抄一份会走样）。"""
    if not doc.exists():
        raise SystemExit(f"找不到测试集定义：{doc}")
    items: list[tuple[str, str]] = []
    for line in doc.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line.strip())
        if m and m.group(2) not in ("输入", "---"):  # 跳过表头与分隔线
            items.append((m.group(1), m.group(2)))
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(CAPTURE_DIR), help="采集目录（默认 reports/asr_capture）")
    ap.add_argument("--start", default=None, help="从哪条测试集开始对齐，如 T1-02（默认第一条）")
    ap.add_argument("--limit", type=int, default=0, help="最多比对几条（0=不限）")
    a = ap.parse_args()

    expect_all = load_expect(EVAL_DOC)
    start = 0
    if a.start:
        ids = [k for k, _ in expect_all]
        if a.start not in ids:
            raise SystemExit(f"--start 必须是测试集条目之一：{', '.join(ids[:6])} …")
        start = ids.index(a.start)
    expect = expect_all[start:]

    caps = sorted(Path(a.dir).glob("*.json"))
    if not caps:
        print(f"{a.dir} 里没有采集结果。\n先按脚本头部说明带 ASR_DEBUG=1 启动后端并念几句。")
        return 1
    if a.limit:
        caps = caps[: a.limit]

    print(f"采集 {len(caps)} 条 | 期望从 {expect[0][0]} 开始对齐\n")
    print(f"{'#':3s} {'条目':8s} {'期望':28s} {'识别':28s} {'字准率':>7s}")
    print("-" * 100)

    rows = []
    for i, cap in enumerate(caps):
        got = json.loads(cap.read_text(encoding="utf-8"))
        if i >= len(expect):
            print(f"{i + 1:<3d} {'(超出测试集)':8s} {'-':28s} {got.get('text', ''):28s}      -")
            continue
        key, exp = expect[i]
        hyp = got.get("text", "")
        c = cer(exp, hyp)
        rows.append({"id": key, "expect": exp, "hyp": hyp, "cer": round(c, 4),
                     "capture": cap.name, "duration_s": got.get("duration_s")})
        print(f"{i + 1:<3d} {key:8s} {exp:28s} {hyp:28s} {1 - c:>6.1%}")

    if not rows:
        return 1

    acc = sum(1 - r["cer"] for r in rows) / len(rows)
    print("\n" + "=" * 100)
    print(f"总体字准率：**{acc:.1%}**（{len(rows)} 条；CER 平均 {1 - acc:.3f}）")
    bad = sorted(rows, key=lambda r: r["cer"], reverse=True)[:5]
    print("\n最差的几条（先看这几条是不是「领域词 / 短句 / 数字」类）：")
    for r in bad:
        if r["cer"] <= 0:
            continue
        print(f"  {r['id']}  CER={r['cer']:.2f}  期望「{r['expect']}」→ 识别「{r['hyp']}」")
    if acc >= 0.95:
        print("\n判定：准确率不是主要问题 → '感受不好'更可能来自**交互节奏**（静音等待/interim 粒度/手动结束）")
    else:
        print("\n判定：准确率确实偏低 → 看错例集中在哪类词，再决定 two-pass / 热词 / 参数")

    OUT.write_text(json.dumps({"total": len(rows), "accuracy": round(acc, 4), "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
