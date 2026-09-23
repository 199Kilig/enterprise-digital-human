"""前端重构后的页面验证（真跑，不是"能编译就行"）。

验三件事：
  1. `/insights` 的统计是**真算出来的** —— 往 localStorage 塞已知的对话记录，
     reload 后断言页面显示的数字与塞入数据一致（若显示的是写死的假数字，会失败）。
  2. 浅色 / 深色下 `--shadow-brand` 与 `--halo-brand` 的 computed 值**不同**
     （P4 把硬编码 rgba 提成 token，深色块的覆盖必须真的生效）。
  3. `/library` 能渲染，且在后端不可达时显示**降级态**（不假装在线）。

用法：先 `npm run preview -- --port 4173`，再 `python scripts/verify_frontend_pages.py`
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.parse

BASE = "http://127.0.0.1:4173"
CDP_PORT = 9444


def find_chrome() -> str:
    for c in [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ]:
        if os.path.exists(c):
            return c
    raise RuntimeError("找不到 Chrome")


def wait_port(port: int, timeout: float = 20.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def main() -> int:
    if not wait_port(4173, 15):
        print("✗ preview (4173) 未就绪，请先跑 npm run preview -- --port 4173")
        return 2

    chrome = find_chrome()
    profile = os.path.join(tempfile.gettempdir(), "chrome-verify-pages")
    shutil.rmtree(profile, ignore_errors=True)
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--window-size=1440,1000",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        import asyncio
        import websockets

        if not wait_port(CDP_PORT, 20):
            print("✗ Chrome CDP 未就绪")
            return 2

        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=5) as r:
            targets = json.load(r)
        ws = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")

        async def run() -> int:
            async with websockets.connect(ws, max_size=64 * 1024 * 1024) as conn:
                n = 0

                async def cmd(method, **params):
                    nonlocal n
                    n += 1
                    await conn.send(json.dumps({"id": n, "method": method, "params": params}))
                    while True:
                        msg = json.loads(await conn.recv())
                        if msg.get("id") == n:
                            return msg

                async def ev(expr):
                    r = await cmd(
                        "Runtime.evaluate",
                        expression=expr,
                        returnByValue=True,
                        awaitPromise=True,
                    )
                    res = r.get("result", {})
                    if "exceptionDetails" in res:
                        raise RuntimeError(str(res["exceptionDetails"])[:300])
                    return res.get("result", {}).get("value")

                await cmd("Runtime.enable")
                await cmd("Page.enable")

                failures = []

                # ---------- 1. /insights 统计真算 ----------
                await cmd("Page.navigate", url=f"{BASE}/insights")
                time.sleep(2.5)

                # 塞入已知的对话记录：3 条提问（其中 2 条归一化后同题）+ 1 条回答
                now_ms = int(time.time() * 1000)
                seeded = {
                    "sessionId": "verify-sess",
                    "messages": [
                        {"role": "user", "text": "通分是什么？", "at": now_ms - 300_000},
                        {"role": "digital", "text": "通分是……", "at": now_ms - 280_000},
                        {"role": "user", "text": "通分是什么", "at": now_ms - 120_000},
                        {"role": "user", "text": "帮我出两道练习题", "at": now_ms - 60_000},
                    ],
                }
                await ev(
                    "window.localStorage.setItem('dh.console.session.v1',"
                    + json.dumps(json.dumps(seeded, ensure_ascii=False))
                    + ")"
                )
                await cmd("Page.navigate", url=f"{BASE}/insights")
                time.sleep(2.5)

                kpis = await ev(
                    "Array.from(document.querySelectorAll('.edu-kpi')).map(el => ({"
                    "  label: el.querySelector('.edu-kpi-label')?.textContent?.trim() || '',"
                    "  value: el.querySelector('.edu-kpi-value')?.textContent?.trim() || ''"
                    "}))"
                )
                topics = await ev(
                    "Array.from(document.querySelectorAll('.edu-toplist li')).map(el => ({"
                    "  name: el.querySelector('.edu-topbar-label')?.textContent?.trim() || '',"
                    "  count: el.querySelector('.edu-topbar-count')?.textContent?.trim() || ''"
                    "}))"
                )
                print("  /insights KPI:", json.dumps(kpis, ensure_ascii=False))
                print("  /insights 话题:", json.dumps(topics, ensure_ascii=False))

                # 断言：提问轮次 = 3
                turns = next((k["value"] for k in kpis if "提问轮次" in k["label"]), None)
                if turns != "3":
                    failures.append(f"提问轮次应为 3（塞了 3 条 user），实际 {turns!r}")
                # 断言：今日提问 = 3
                today = next((k["value"] for k in kpis if "今日提问" in k["label"]), None)
                if today != "3":
                    failures.append(f"今日提问应为 3，实际 {today!r}")
                # 断言：归一化聚类生效 —— 「通分是什么？」与「通分是什么」应聚成 1 条、计数 2
                if not topics:
                    failures.append("话题列表为空（应至少有 1 条）")
                else:
                    top = topics[0]
                    if top["count"] != "2 次":
                        failures.append(f"TOP1 计数应为 '2 次'（归一化后同题），实际 {top['count']!r}")
                    if "通分" not in top["name"]:
                        failures.append(f"TOP1 名称应含「通分」，实际 {top['name']!r}")

                # ---------- 2. 浅色 / 深色 token 覆盖 ----------
                read_tokens = (
                    "(() => { const cs = getComputedStyle(document.documentElement);"
                    "  return { theme: document.documentElement.getAttribute('data-theme') || 'light',"
                    "    shadowBrand: cs.getPropertyValue('--shadow-brand').trim(),"
                    "    haloBrand: cs.getPropertyValue('--halo-brand').trim(),"
                    "    videoBg: cs.getPropertyValue('--video-bg').trim() }; })()"
                )
                await ev("document.documentElement.setAttribute('data-theme','light')")
                light = await ev(read_tokens)
                await ev("document.documentElement.setAttribute('data-theme','dark')")
                time.sleep(0.3)
                dark = await ev(read_tokens)
                print("  浅色 token:", json.dumps(light, ensure_ascii=False))
                print("  深色 token:", json.dumps(dark, ensure_ascii=False))

                if light["shadowBrand"] == dark["shadowBrand"]:
                    failures.append("--shadow-brand 浅/深色相同（深色覆盖没生效）")
                if light["haloBrand"] == dark["haloBrand"]:
                    failures.append("--halo-brand 浅/深色相同（深色覆盖没生效）")
                if not light["shadowBrand"] or not dark["shadowBrand"]:
                    failures.append("--shadow-brand 取不到值（token 名写错或未生效）")
                if light["videoBg"] != "#000" and light["videoBg"] != "rgb(0, 0, 0)":
                    failures.append(f"--video-bg 应解析为黑，实际 {light['videoBg']!r}")

                # ---------- 3. /library 渲染与降级态 ----------
                await cmd("Page.navigate", url=f"{BASE}/library")
                time.sleep(3.0)
                lib = await ev(
                    "(() => ({"
                    "  hasHead: !!document.querySelector('.edu-page-head h1'),"
                    "  cards: document.querySelectorAll('.edu-library-grid .edu-card').length,"
                    "  videoSrc: document.querySelector('.edu-lib-preview video')?.getAttribute('src') || '',"
                    "  statusKeys: Array.from(document.querySelectorAll('.edu-lib-status-key')).map(e => e.textContent.trim()),"
                    "  hasError: !!document.querySelector('.edu-lib-error'),"
                    "  pending: document.querySelectorAll('.edu-pending li').length"
                    "}))()"
                )
                print("  /library:", json.dumps(lib, ensure_ascii=False))
                if not lib["hasHead"]:
                    failures.append("/library 页头未渲染")
                if lib["cards"] < 4:
                    failures.append(f"/library 卡片数应 ≥4，实际 {lib['cards']}")
                if not lib["videoSrc"].startswith("/media/"):
                    failures.append(f"形象预览未绑定真实产物，src={lib['videoSrc']!r}")
                if lib["pending"] < 2:
                    failures.append("/library 待接入能力应列出 ≥2 项")

                # ---------- 4. 旧占位路由必须已下线 ----------
                await cmd("Page.navigate", url=f"{BASE}/tools/mistakes")
                time.sleep(2.0)
                gone = await ev(
                    "(() => ({ path: location.pathname,"
                    "  hasPh: !!document.querySelector('.edu-ph'),"
                    "  h1: document.querySelector('h1')?.textContent?.trim() || '' }))()"
                )
                print("  /tools/mistakes:", json.dumps(gone, ensure_ascii=False))
                if gone["hasPh"]:
                    failures.append("/tools/:key 占位页仍在（应已下线并重定向首页）")
                if gone["path"] != "/":
                    failures.append(f"/tools/mistakes 应重定向到 /，实际 {gone['path']!r}")

                print()
                if failures:
                    print(f"✗ 失败 {len(failures)} 项：")
                    for f in failures:
                        print("   -", f)
                    return 1
                print("✓ 全部通过（4 组断言）")
                return 0

        return asyncio.run(run())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
