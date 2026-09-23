"""在真实浏览器里验证 markdown 的 mermaid 块能否渲染（语法有效性的最终判据）。

为什么不用 Node + jsdom：mermaid 内部用 ESM `import DOMPurify from "dompurify"`，
在 Node ESM 下 `dompurify` 的 default 是**工厂函数**（不是实例），于是 `DOMPurify.sanitize`
不存在 → flowchart/gantt 全报 "DOMPurify.sanitize is not a function"，而 sequenceDiagram
因为不渲染 HTML 反而能过（假象）。浏览器里 mermaid.min.js 是完整 bundle，无此问题。

做法：抽出所有 ```mermaid 块 → 写一个 HTML（引同目录的 mermaid.min.js）→ headless Chrome
打开 → 逐块 mermaid.parse() → 读回每个块的结果 → 截图留证。

用法：
    python scripts/verify_mermaid_in_browser.py README.md [更多.md ...]
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CDP_PORT = 9788
SCRATCH = Path(os.environ.get("TEMP", ".")) / "mermaid-browser"
BLOCK_RE = re.compile(r"^\s*```mermaid\s*$([\s\S]*?)^\s*```\s*$", re.M)


def find_mermaid_js() -> Path | None:
    """找本地已装的 mermaid.min.js（优先 scratch 里的验证目录）。"""
    cands = [
        Path(os.environ.get("TEMP", ".")) / "mermaid-verify" / "node_modules" / "mermaid" / "dist" / "mermaid.min.js",
        Path(os.environ.get("LOCALAPPDATA", ".")) / "hermes" / "profiles" / "my-project" / "cache" / "scratch" / "mermaid-verify" / "node_modules" / "mermaid" / "dist" / "mermaid.min.js",
    ]
    for c in cands:
        if c.exists():
            return c
    return None


def chrome_path() -> str:
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(p):
            return p
    raise RuntimeError("未找到 Chrome/Edge")


HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{background:#fff;font:13px monospace;margin:12px} .ok{color:#137333} .bad{color:#c5221f}
figure{margin:0 0 18px} figcaption{color:#555;font-size:12px}</style></head>
<body><div id="out"></div><div id="figs"></div>
<script src="mermaid.min.js"></script>
<script>
const blocks = __BLOCKS__;
(async () => {
  const log = [];
  mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' });
  for (let i = 0; i < blocks.length; i++) {
    try {
      await mermaid.parse(blocks[i]);
      log.push({ i: i + 1, ok: true });
      try {
        const { svg } = await mermaid.render('fig' + i, blocks[i]);
        const d = document.createElement('figure');
        d.innerHTML = '<figcaption>图 ' + (i + 1) + '（已渲染）</figcaption>' + svg;
        document.getElementById('figs').appendChild(d);
      } catch (e) {
        log.push({ i: i + 1, renderError: String(e).split('\\n')[0] });
      }
    } catch (e) {
      log.push({ i: i + 1, ok: false, error: String(e.message || e).split('\\n')[0] });
    }
  }
  document.getElementById('out').textContent = JSON.stringify(log);
  window.__done = true;
})();
</script></body></html>"""


def main() -> int:
    files = sys.argv[1:] or ["README.md"]
    mjs = find_mermaid_js()
    if not mjs:
        print("[x] 没找到本地的 mermaid.min.js。先按下面装一次：")
        print('    mkdir -p "$TEMP/mermaid-verify" && cd "$TEMP/mermaid-verify"')
        print("    npm i mermaid@10 jsdom dompurify")
        return 2

    total, failed = 0, 0
    for f in files:
        src = Path(f).read_text(encoding="utf-8")
        blocks = [m.group(1) for m in BLOCK_RE.finditer(src)]
        print(f"\n{f}：发现 {len(blocks)} 个 mermaid 块")
        if not blocks:
            continue
        total += len(blocks)

        SCRATCH.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy(mjs, SCRATCH / "mermaid.min.js")
        html = HTML.replace("__BLOCKS__", json.dumps(blocks))
        (SCRATCH / "verify.html").write_text(html, encoding="utf-8")

        prof = SCRATCH / "profile"
        proc = subprocess.Popen([
            chrome_path(), "--headless=new", f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={prof}", "--no-first-run", "--no-default-browser-check",
            "--disable-gpu", "--allow-file-access-from-files",
            (SCRATCH / "verify.html").as_uri(),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            ws = None
            for _ in range(40):
                try:
                    tabs = json.load(urllib.request.urlopen(
                        f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2))
                    page = [t for t in tabs if t.get("type") == "page"]
                    if page:
                        url = page[0]["webSocketDebuggerUrl"]
                        break
                except Exception:
                    pass
                time.sleep(0.5)
            else:
                print("  ✗ CDP 未就绪")
                continue

            spec = importlib.util.find_spec("websockets")
            if spec is None:
                print("  ✗ 需要 websockets（pip install websockets）")
                continue
            import asyncio
            import websockets

            async def run() -> list:
                async with websockets.connect(url, max_size=64 * 1024 * 1024) as sock:
                    n = 0

                    async def cmd(m, p=None):
                        nonlocal n
                        n += 1
                        await sock.send(json.dumps({"id": n, "method": m, "params": p or {}}))
                        while True:
                            r = json.loads(await asyncio.wait_for(sock.recv(), timeout=60))
                            if r.get("id") == n:
                                return r.get("result", {})

                    async def ev(expr):
                        r = await cmd("Runtime.evaluate",
                                      {"expression": expr, "returnByValue": True, "awaitPromise": True})
                        return r.get("result", {}).get("value")

                    await cmd("Runtime.enable")
                    for _ in range(60):
                        if await ev("window.__done === true"):
                            break
                        await asyncio.sleep(1)
                    raw = await ev("document.getElementById('out').textContent")
                    # 截图留证
                    shot = await cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
                    data = shot.get("data")
                    if data:
                        (SCRATCH / f"verify_{Path(f).stem}.png").write_bytes(base64.b64decode(data))
                    return json.loads(raw) if raw else []

            results = asyncio.run(run())
            for r in results:
                if r.get("ok"):
                    note = "（渲染出错，但语法通过）" if r.get("renderError") else ""
                    print(f"  ✓ 块#{r['i']} {note}")
                else:
                    print(f"  ✗ 块#{r['i']} — {r.get('error')}")
                    failed += 1
            png = SCRATCH / f"verify_{Path(f).stem}.png"
            if png.exists():
                print(f"  截图：{png}")
        finally:
            proc.terminate()

    print(f"\n合计 {total} 块，语法失败 {failed} 块")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
