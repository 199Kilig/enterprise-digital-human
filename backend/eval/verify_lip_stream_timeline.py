"""口型链路 SSE 到达时间线验证（两种口径）。

用途：定位"后端已发出、下游却迟迟收到"这类缺口卡在哪一跳。
对应评估台账 2026-09-23 的三行（口型 RTF 优化 / 流式到达时间线）。

口径 A（socket，纯 Python）：直接读后端 SSE，记录每个事件的到达时刻。
    等价于 `curl -N ... | while read` 打时间戳，但无外部依赖。
    对照价值：它的到达时刻 = 后端把事件写进 socket 的时刻。

口径 B（浏览器，CDP）：headless Chrome 打开真实页面，注入探针钩
    `EventSource.prototype.addEventListener` 与 `URL.createObjectURL`，
    对比"事件到达 JS"与"前端可用 blob"两个时刻。
    对照价值：A 快而 B 慢 ⇒ 问题在后端下游；两者一致 ⇒ 读取链路清白。

用法：
    python backend/eval/verify_lip_stream_timeline.py                 # 只跑口径 A
    python backend/eval/verify_lip_stream_timeline.py --browser       # A + B（直连后端）
    python backend/eval/verify_lip_stream_timeline.py --browser --proxy   # B 走前端 dev 代理
    python backend/eval/verify_lip_stream_timeline.py --json         # 原始数据落 reports/

前置：后端已在 127.0.0.1:8010 运行；口径 B 需本机装了 Chrome；
      --proxy 需前端 dev server（默认 5173）已在运行。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

DEFAULT_BACKEND = "http://127.0.0.1:8010"
DEFAULT_FRONTEND = "http://127.0.0.1:5173"
QUESTION = "请用三句话介绍一下你自己，每句话都简短一些"
CDP_PORT = 9777
EVENTS = ["thinking", "brain_token", "tts_audio", "lip_video", "done", "error"]


# ---------------------------------------------------------------- 口径 A

def new_session(base: str) -> str:
    req = urllib.request.Request(f"{base}/api/v1/session", method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)["session_id"]


def timeline_socket(base: str) -> dict:
    """口径 A：逐行读 SSE，记录每个事件到达的相对毫秒。"""
    sid = new_session(base)
    url = (f"{base}/api/v1/chat/stream?session_id={sid}"
           f"&text={urllib.parse.quote(QUESTION)}")
    marks: list[dict] = []
    t0 = time.perf_counter()
    with urllib.request.urlopen(url, timeout=300) as r:
        event_type = None
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:") and event_type:
                marks.append({"at": round((time.perf_counter() - t0) * 1000),
                              "type": event_type,
                              "len": len(line) - 5})
                if event_type == "done":
                    break
    return {"session": sid, "base": base, "marks": marks,
            "totalMs": round((time.perf_counter() - t0) * 1000)}


# ---------------------------------------------------------------- 口径 B

PROBE = r"""
(() => {
  window.__esMarks = [];
  const oAdd = EventSource.prototype.addEventListener;
  EventSource.prototype.addEventListener = function (type, fn, ...rest) {
    const wrapped = function (ev) {
      try {
        window.__esMarks.push({ at: Math.round(performance.now()), type: String(type),
                                len: (ev && ev.data) ? ev.data.length : 0 });
      } catch (e) {}
      return fn.call(this, ev, ...rest);
    };
    return oAdd.call(this, type, wrapped, ...rest);
  };
  window.__blob = [];
  const ocreate = URL.createObjectURL.bind(URL);
  URL.createObjectURL = function (obj) {
    try {
      window.__blob.push({ at: Math.round(performance.now()),
                           size: obj && obj.size, type: obj && obj.type });
    } catch (e) {}
    return ocreate(obj);
  };
  window.__probeReady = true;
})();
"""


def _chrome_path() -> str:
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(p):
            return p
    raise RuntimeError("未找到 Chrome，可用 --browser 指定；或跳过口径 B")


def start_chrome() -> subprocess.Popen:
    prof = os.path.join(os.environ.get("TEMP", "."), "chrome-lip-timeline")
    p = subprocess.Popen([
        _chrome_path(), "--headless=new", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={prof}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--disable-extensions", "--autoplay-policy=no-user-gesture-required",
        "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=1):
                return p
        except Exception:
            time.sleep(0.5)
    p.terminate()
    raise RuntimeError("Chrome CDP 未就绪")


async def timeline_browser(page_url: str, base: str) -> dict:
    """口径 B：真实浏览器里的 EventSource 到达时刻 + createObjectURL 时刻。"""
    import websockets  # 延迟导入：只跑口径 A 时不需要

    proc = start_chrome()
    try:
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=6))
        page = [t for t in tabs if t.get("type") == "page"][0]
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
            n = 0

            async def cmd(m, p=None):
                nonlocal n
                n += 1
                await ws.send(json.dumps({"id": n, "method": m, "params": p or {}}))
                while True:
                    r = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                    if r.get("id") == n:
                        return r.get("result", {})

            async def ev(expr):
                r = await cmd("Runtime.evaluate",
                              {"expression": expr, "returnByValue": True, "awaitPromise": True})
                return r.get("result", {}).get("value")

            await cmd("Page.enable")
            await cmd("Runtime.enable")
            await cmd("Page.addScriptToEvaluateOnNewDocument", {"source": PROBE})
            await cmd("Page.navigate", {"url": page_url})
            await asyncio.sleep(14)  # 等页面挂载 + 探针就位

            filled = await ev("""(() => {
              const input = document.querySelector('.edu-chat-input input');
              if (!input) return 'no-input';
              const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
              setter.call(input, %s);
              input.dispatchEvent(new Event('input', { bubbles: true }));
              return 'ok';
            })()""" % json.dumps(QUESTION, ensure_ascii=False))
            if filled != "ok":
                raise RuntimeError(f"没找到对话输入框（{filled}）—— 页面结构可能变了")
            await asyncio.sleep(1)
            await ev("document.querySelector('.edu-chat-send')?.click()")

            last, stable = -1, 0
            for i in range(70):
                await asyncio.sleep(3)
                em = await ev("window.__esMarks?.length ?? 0") or 0
                bl = await ev("window.__blob?.length ?? 0") or 0
                cur = em + bl * 1000
                if cur == last and i > 3:
                    stable += 1
                    if stable >= 3:
                        break
                else:
                    stable = 0
                last = cur

            marks = json.loads(await ev("JSON.stringify(window.__esMarks)") or "[]")
            blobs = json.loads(await ev("JSON.stringify(window.__blob)") or "[]")
            return {"page": page_url, "marks": marks, "blobs": blobs}
    finally:
        proc.terminate()


# ---------------------------------------------------------------- 输出

def print_table(title: str, marks: list[dict]) -> None:
    print(f"\n=== {title} ===")
    if not marks:
        print("  （无事件）")
        return
    by: dict[str, list[int]] = {}
    for m in marks:
        by.setdefault(m["type"], []).append(m["at"])
    print(f"  {'类型':<14}{'首个':>9}{'末个':>9}{'条数':>6}")
    for t, arr in sorted(by.items(), key=lambda kv: kv[1][0]):
        print(f"  {t:<14}{arr[0]:>9}{arr[-1]:>9}{len(arr):>6}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=DEFAULT_BACKEND)
    ap.add_argument("--frontend", default=DEFAULT_FRONTEND)
    ap.add_argument("--browser", action="store_true", help="同时跑口径 B（真实浏览器）")
    ap.add_argument("--proxy", action="store_true", help="口径 B 走前端 dev 代理而非直连后端")
    ap.add_argument("--json", action="store_true", help="原始数据写到 reports/")
    args = ap.parse_args()

    out: dict = {}

    a = timeline_socket(args.backend)
    out["socket"] = a
    print(f"[A] 本机 socket 直连 {args.backend}")
    print_table(f"口径 A：事件到达时刻（ms，起算于请求发出）／总耗时 {a['totalMs']}ms", a["marks"])

    if args.browser:
        page_url = f"{args.frontend}/console"
        proxied = args.proxy
        b = asyncio.run(timeline_browser(page_url, args.frontend if proxied else args.backend))
        out["browser"] = b
        print(f"\n[B] 浏览器方式读取（页面 {page_url}，"
              f"{'经 Vite 代理' if proxied else '直连后端'}）")
        print_table("口径 B：EventSource 事件到达 JS 的时刻", b["marks"])
        vids = [x for x in b["blobs"] if (x.get("type") or "").startswith("video")]
        print(f"\n  前端 createObjectURL（video）共 {len(vids)} 个：")
        for i, x in enumerate(vids):
            print(f"    片#{i + 1:>2} @{x['at']:>7}ms  size={x['size']}")

        lv = [m["at"] for m in b["marks"] if m["type"] == "lip_video"]
        if lv and vids:
            print("\n=== 关键对比（数字越小越接近，说明无缺口）===")
            print(f"  浏览器收到 lip_video：{lv[0]} → {lv[-1]} ms")
            print(f"  前端创建 video blob ：{vids[0]['at']} → {vids[-1]['at']} ms")
            print(f"  本机 socket 对照    ："
                  f"{[m['at'] for m in a['marks'] if m['type'] == 'lip_video'][:1] or ['-']}"
                  f" → {[m['at'] for m in a['marks'] if m['type'] == 'lip_video'][-1:] or ['-']} ms")

    if args.json:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "lip_stream_timeline.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print(f"\n原始数据已写入 {p}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
