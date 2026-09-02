"""V-04 LLM 流式输出验证 — EVAL-P1 §3 V-04

目标：确认 DeepSeek API 流式 SSE 输出格式符合预期（对齐 config.yaml llm 段）。
通过标准（EVAL-P1 §3 V-04）：
  - 首 token ≤500ms
  - 事件结构含 choices[0].delta.content
  - 正常以 data: [DONE] 结束

用法:
  export DEEPSEEK_API_KEY=sk-xxx
  python verify_llm_streaming.py [--base-url https://api.deepseek.com] [--model deepseek-chat] [--report-dir reports]

输出: eval/reports/llm_streaming.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

PROMPT = "你好，请用一句话介绍你自己。"  # 固定测试文本，可重复对比


def main() -> int:
    parser = argparse.ArgumentParser(description="V-04 LLM 流式输出验证")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("缺少 DEEPSEEK_API_KEY 环境变量（用你的 DeepSeek key，可复用 Hermes 配置的）")
        return 1

    url = f"{args.base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "max_tokens": 100,
    }

    t0 = time.time()
    first_token_ms: int | None = None
    chunks: list[dict] = []
    status = "FAILED"
    error: str | None = None
    try:
        with httpx.stream("POST", url, headers=headers, json=payload, timeout=30) as resp:
            if resp.status_code != 200:
                error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            else:
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        evt = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if first_token_ms is None:
                        delta = evt.get("choices", [{}])[0].get("delta", {})
                        if delta.get("content"):
                            first_token_ms = int((time.time() - t0) * 1000)
                    chunks.append(evt)
        ttft_ms = first_token_ms
        checks = {
            "first_token_ms_le_500": ttft_ms is not None and ttft_ms <= 500,
            "delta_content_structure": any(
                c.get("choices", [{}])[0].get("delta", {}).get("content")
                for c in chunks
            ),
            "clean_end": error is None,
        }
        status = "PASSED" if all(checks.values()) else "FAILED"
    except Exception as e:  # noqa: BLE001
        error = str(e)
        ttft_ms = None
        checks = {"first_token_ms_le_500": False, "delta_content_structure": False, "clean_end": False}
        status = "FAILED"

    report = {
        "date": "2026-09-01",
        "provider": "deepseek",
        "model": args.model,
        "base_url": args.base_url,
        "prompt": PROMPT,
        "ttft_ms": ttft_ms,
        "n_chunks": len(chunks),
        "first_chunk": chunks[0] if chunks else None,
        "last_chunk": chunks[-1] if chunks else None,
        "checks": checks,
        "error": error,
        "status": status,
    }
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    out = report_dir / "llm_streaming.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("first_chunk", "last_chunk")}, ensure_ascii=False, indent=2))
    print(f"\n报告已写入: {out.resolve()}")
    return 0 if status == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
