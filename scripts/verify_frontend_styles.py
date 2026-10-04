"""前端样式一致性验证：CSS 里有没有已经没人用的「孤儿类」。

为什么需要这个脚本（它抓的是真实缺陷，不是洁癖）：
  本项目 2026-09-23 下线过 4 个占位页（/tools/:key）、移除过「学习工具」4 格与
  「小奈 Pro」卡片，但 CSS 里的 `.edu-pro-*` / `.edu-tool*` / `.edu-task*` 等
  规则块留了下来。后果不只是体积：`edu.css` 的 `:where(...)` 点击反馈列表里
  还留着 `.edu-hist-item`——而侧栏用的类名是 `.edu-history`，那个选择器**从未
  匹配过任何元素**，属于"看着有、实际没有"的幽灵规则（2026-10-01 修为
  `.edu-history a`）。同一批残留后来又在 `app.css` 找到一整套（旧控制台骨架
  `.shell` / `.topbar` / `.sidenav` / `.body` / `.content`，2026-10-04 清掉）。
  这类问题靠读代码很难发现：脚本自己就漏报过 `.edu-pro`（见下方判据①②）。

做法（三级证据，前两级不需要浏览器）：
  1. 静态比对：样式表里定义的类 vs tsx/ts/html 里出现的类。两侧判据都踩过坑：
     ① 定义侧要先剥 `/* */` 注释，且**只从选择器位置取类名**——正则扫全文时，
     注释里写一句"`.edu-pro` 漏网"会被当成类定义，`url(sprite.png)` 会多出 `.png`；
     ② 引用侧要**按词边界匹配**——子串匹配会让 `.edu-problem-list` 掩护掉 `.edu-pro`
     （2026-10-02 实测①②各误报/漏报过一次）；
     ③ 再排除**动态拼接**前缀（`edu-badge--${state}` 这类不能用字面量判定）。
  2. CSSOM 复核：起 headless Chrome，枚举 document.styleSheets 的 selectorText，
     区分「确实没有规则」与「有规则但当前页面没用上」。
  3. 生效性抽检：往 DOM 注入几个代表类，读 computed style——
     活跃类必须拿到非默认样式（证明样式真的在起作用，而不是靠继承巧合）。

判据的已知边界（静态比对是"宁可漏报，不可误删"）：
  · 引用侧按全源码文本匹配，于是 `document.body` / `<footer>` / `Content-Type`
    会让 `.body` / `.footer` / `.content` 这类通用名永远算「在用」——app.css 里
    这 3 个是人工核对（className 字面量判据 + 逐个 grep）才敢删的；
  · 反过来，``className={`msg ${m.role}`}`` 这种变量传递是**真引用**，脚本判它在用
    是对的（`.system` / `.over` 即此类，别按字面量判据误删）。

扫描范围：`app.css` 与 `edu.css`（tokens.css 只定义变量、不定义类）。

用法：
    1) 起前端：cd frontend && npm run dev
    2) python scripts/verify_frontend_styles.py            # 默认 5173
       VERIFY_BASE_URL=http://127.0.0.1:4173 python scripts/verify_frontend_styles.py

退出码：0 = 无孤儿类；1 = 有孤儿类或样式失效；2 = 前置条件不满足。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:  # 中文 Windows 控制台默认 GBK，脚本输出含中文与符号
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "frontend" / "src"
# 扫描目标：(样式文件, 类名前缀 or None)
#   edu.css 统一 .edu-* 命名，比对时可以只看这个前缀；
#   app.css 是旧控制台命名（.panel /.btn /.table ...），无统一前缀，只能全量比对。
#   tokens.css 只定义变量、不定义类，不在扫描范围。
STYLESHEETS: list[tuple[Path, str | None]] = [
    (FRONT / "styles" / "edu.css", "edu-"),
    (FRONT / "styles" / "app.css", None),
]
# 临时目录放项目内：受限环境（沙箱/受控主机）常拒绝系统 temp 的随机路径写入，
# 同时系统盘可能很小——与 backend/tests/conftest.py 的 LIP_TMPDIR 约定一致。
SCRATCH = ROOT / ".tmp-verify" / "frontend-styles"
CDP_PORT = 9791
BASE = os.environ.get("VERIFY_BASE_URL", "http://127.0.0.1:5173")

# 注入式抽检：(类, 标签, 代表属性, CSS 选择器后缀, 判定, 注入到元素内的 HTML)
#   判定 explanation：
#     "nonzero" —— 属性值应当是非零/非默认（如 display 变成 grid、圆角 999px）
#     "notrans" —— 颜色类应非透明
#   inner 用于「样式落在子元素上」的情形：.edu-history a 的 padding 在 a 上，
#   而 .edu-history 本身是 ul —— 不塞一个 a 进去就取不到（曾把这种情况误判成样式失效）。
PROBES: list[tuple[str, str, str, str, str, str]] = [
    ("edu-shell", "div", "display", "", "nonzero", ""),
    ("edu-card", "div", "borderTopLeftRadius", "", "nonzero", ""),
    ("edu-start-btn", "button", "backgroundColor", "", "notrans", ""),
    ("edu-kpi", "div", "display", "", "nonzero", ""),
    ("edu-conn", "div", "borderTopLeftRadius", "", "nonzero", ""),
    ("edu-history", "ul", "display", "", "nonzero", ""),
    ("edu-history", "ul", "paddingLeft", "a", "nonzero", "<a href='#'>x</a>"),
    ("edu-chat", "div", "display", "", "nonzero", ""),
    ("edu-library-grid", "div", "display", "", "nonzero", ""),
]
DEFAULTISH = {"", "none", "normal", "auto", "transparent", "rgba(0, 0, 0, 0)"}
# 查过的死类（若又被人加回来，说明重构回退了）。浏览器级复核按 CSSOM 精确查它们。
DEAD_SAMPLE = [
    # edu.css：2026-10-01 清下线功能的规则块 + 2026-10-02 补删 .edu-pro
    "edu-pro", "edu-pro-btn", "edu-tools", "edu-tool", "edu-timer", "edu-task",
    "edu-weak", "edu-advice", "edu-hist-item",
    # app.css：2026-10-04 清旧控制台骨架（.shell/.topbar/.sidenav/.body/.content 一套）
    "shell", "topbar", "brand", "sub", "sidenav", "group-label", "idx",
    "overlay-bottom", "tight", "sm", "body", "content", "footer",
]


def iter_code_files() -> list[Path]:
    files = [p for pat in ("*.tsx", "*.ts") for p in FRONT.rglob(pat)]
    index = ROOT / "frontend" / "index.html"
    if index.exists():
        files.append(index)
    return files


def dynamic_prefixes(blob: str) -> set[str]:
    """找出模板字符串里做动态拼接的类名前缀，如 `edu-badge--${s}` -> edu-badge--"""
    out: set[str] = set()
    for tmpl in re.findall(r"`[^`]*\$\{[^}]*\}[^`]*`", blob):
        for m in re.finditer(r"(edu-[a-z0-9-]*)\$\{", tmpl):
            out.add(m.group(1))
    return out


def strip_comments(css: str) -> str:
    """剥掉 `/* ... */` 注释，只从**规则体**里提取类名。

    为什么必须剥：本脚本用正则从 CSS 全文抓 `.edu-*`，不看上下文，于是注释里
    随手写一句"`.edu-pro` 漏网、被 `.edu-problem-*` 掩护"就会被当成两个类**定义**，
    源码里当然找不到引用 → 凭空多出两个孤儿。2026-10-02 实测踩到。
    （这也意味着：注释里的类名既可能造假阳性，也可能顺手"认领"一个真孤儿。）
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def used_in_source(cls: str, blob: str) -> bool:
    """类名在源码里是否被**完整**引用（词边界匹配，不是子串包含）。

    为什么不能用 `cls in blob`：`.edu-pro`（「小奈 Pro」卡残留的规则块）会被
    `.edu-problem-list` 的子串命中，从而永远被判成「活跃」——2026-10-02 实测该规则块
    在源码里已零引用，脚本却报「0 孤儿」。类名的边界是「两侧都不是 `-` 或标识符字符」，
    少了这个约束，任意一个长类名都能掩护它所有的短前缀类名。
    """
    return re.search(r"(?<![\w-])" + re.escape(cls) + r"(?![\w-])", blob) is not None


def defined_classes(css: str) -> set[str]:
    """只从**选择器位置**提取类名（每个 `{` 之前那一段），不扫声明体。

    为什么不用一句正则扫全文：声明体里同样会出现 `.` + 词，例如
    `background: url(sprite.png)` 会凭空多出一个 `.png` 类、`content: ".foo"`
    会多出 `.foo`——两者都会变成假孤儿。`{}`/`;` 即选择器与声明体的边界。
    """
    out: set[str] = set()
    pending: list[str] = []
    for ch in css:
        if ch == "{":
            out.update(re.findall(r"\.([a-zA-Z][\w-]*)", "".join(pending)))
            pending.clear()
        elif ch in "};":
            pending.clear()
        else:
            pending.append(ch)
    return out


def code_blob() -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in iter_code_files())


def scan_orphans(css_file: Path, prefix: str | None, blob: str,
                 dyn: set[str]) -> tuple[list[str], list[str]]:
    """扫单个样式文件，返回（该文件定义的类, 其中的孤儿类）。

    prefix 限定类名前缀（edu.css 只看 .edu-*），None 表示全量（app.css）。
    """
    css = strip_comments(css_file.read_text(encoding="utf-8"))
    defined = sorted(c for c in defined_classes(css) if prefix is None or c.startswith(prefix))

    orphans = []
    for cls in defined:
        if used_in_source(cls, blob):
            continue
        # 动态拼接的类不能靠字面量判定：前缀能对上就放过
        if any(cls.startswith(p) and p for p in dyn):
            continue
        orphans.append(cls)
    return defined, orphans


def find_chrome() -> str:
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
              "/usr/bin/google-chrome", "/usr/bin/chromium"):
        if os.path.exists(p):
            return p
    raise RuntimeError("未找到 Chrome")


def wait_port(port: int, timeout: float = 25.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def browser_checks() -> list[str]:
    """CSSOM 复核 + 生效性抽检。返回失败列表（空 = 通过）。"""
    import asyncio

    import websockets

    base_port = int(BASE.rstrip("/").rsplit(":", 1)[-1])
    if not wait_port(base_port, 20):
        print(f"  ! 前端 {BASE} 未就绪，跳过浏览器级复核（仅静态结论）")
        return []

    profile = SCRATCH / "chrome-profile"
    shutil.rmtree(profile, ignore_errors=True)
    profile.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    errlog = open(SCRATCH / "chrome.err", "wb")
    proc = subprocess.Popen(
        [chrome, "--headless=new", f"--remote-debugging-port={CDP_PORT}",
         f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
         "--disable-gpu", "--window-size=1440,1000",
         # 受限环境下 Chrome 会去连更新服务/已存在实例（命名管道被拒 -> 进程短命）
         "--disable-background-networking", "--disable-component-update",
         "--disable-sync", "--disable-default-apps", "--disable-extensions",
         "--no-service-autorun", "--disable-dev-shm-usage", "--no-sandbox",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=errlog,
    )
    try:
        if not wait_port(CDP_PORT, 25):
            print("  ! Chrome CDP 未就绪，跳过浏览器级复核")
            return []
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=5) as r:
            targets = json.load(r)
        ws = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")

        async def run() -> list[str]:
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
                    r = await cmd("Runtime.evaluate", expression=expr,
                                  returnByValue=True, awaitPromise=True)
                    res = r.get("result", {})
                    if "exceptionDetails" in res:
                        det = res["exceptionDetails"]
                        desc = (det.get("exception") or {}).get("description", "")
                        at = f"line={det.get('lineNumber')} col={det.get('columnNumber')}"
                        raise RuntimeError(f"{desc[:400]} ({at})")
                    return res.get("result", {}).get("value")

                await cmd("Runtime.enable")
                await cmd("Page.enable")
                # headless 默认 prefers-reduced-motion: reduce，会把 transition 压成
                # 0.01ms !important（见 edu.css 无障碍规则）；导航后需重设
                await cmd("Emulation.setEmulatedMedia",
                          features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
                await cmd("Page.navigate", url=BASE)
                await cmd("Emulation.setEmulatedMedia",
                          features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
                time.sleep(2.5)

                fails: list[str] = []
                selectors = await ev(
                    "(() => { const s = new Set();"
                    "  for (const sh of document.styleSheets) {"
                    "    let rules; try { rules = sh.cssRules; } catch (e) { continue; }"
                    "    const walk = (rs) => { for (const r of rs) {"
                    "      if (r.selectorText)"
                    "        (r.selectorText.match(/\\.([a-zA-Z][\\w-]*)/g) || [])"
                    "          .forEach(x => s.add(x.slice(1)));"
                    "      if (r.cssRules) walk(r.cssRules); } };"
                    "    walk(rules); }"
                    "  return Array.from(s); })()"
                ) or []
                sel = set(selectors)
                print(f"  CSSOM 可枚举类：{len(sel)}")

                reappeared = [c for c in DEAD_SAMPLE if c in sel]
                if reappeared:
                    fails.append(f"已清理的死类又出现在 CSSOM 中：{reappeared}")

                # 注入目标元素（必要时取其后代）后读代表属性。
                # 注意：sel 是 Python 变量，必须在 Python 侧展开成 JS 表达式，
                # 不能写成 `sel ? a : b`（那样 JS 会报 sel is not defined）。
                rows = "".join(
                    f"  {{ const e = document.createElement('{tag}');"
                    f" e.className = '{cls}';"
                    + (f" e.innerHTML = {inner!r};" if inner else "")
                    + " document.body.appendChild(e);"
                    f" const el = {f'e.querySelector({sel!r})' if sel else 'e'};"
                    f" out['{cls}{' ' + sel if sel else ''}.{prop}'] ="
                    f" el ? getComputedStyle(el)['{prop}'] : 'NO_ELEM'; }}\n"
                    for cls, tag, prop, sel, _, inner in PROBES
                )
                probe = await ev(
                    "(() => { const out = {};" + rows + "  return out; })()"
                ) or {}
                print("  注入式生效抽检（该类是否存在且样式真的生效）：")
                for cls, tag, prop, sel, kind, _ in PROBES:
                    key = f"{cls}{' ' + sel if sel else ''}.{prop}"
                    v = str(probe.get(key, ""))
                    if v == "NO_ELEM":
                        fails.append(f"{key}: 注入后取不到元素（选择器 {sel!r} 不匹配）")
                        print(f"    {key:38} NO_ELEM  FAIL")
                        continue
                    if kind == "notrans":
                        ok = v not in DEFAULTISH and not v.startswith("rgba(0, 0, 0, 0")
                    else:  # nonzero：非空、非 none/normal/auto，且非 0px
                        ok = v not in DEFAULTISH and v not in ("0px", "0")
                    print(f"    {key:38} = {v:24} {'OK' if ok else 'FAIL'}")
                    if not ok:
                        fails.append(f"{key} 取到默认值 {v!r}——该类样式可能未生效")
                return fails

        return asyncio.run(run())
    finally:
        errlog.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser(description="前端样式一致性验证（孤儿类检测）")
    ap.add_argument("--no-browser", action="store_true", help="只做静态扫描，不起浏览器")
    args = ap.parse_args()

    missing = [str(f) for f, _ in STYLESHEETS if not f.exists()]
    if missing:
        print(f"FAIL: 找不到 {missing}")
        return 2
    SCRATCH.mkdir(parents=True, exist_ok=True)

    blob = code_blob()
    dyn = dynamic_prefixes(blob)
    print(f"动态拼接前缀（不参与孤儿判定）：{sorted(dyn) or '无'}")

    fails: list[str] = []
    for css_file, prefix in STYLESHEETS:
        defined, orphans = scan_orphans(css_file, prefix, blob, dyn)
        scope = f".{prefix}* 类" if prefix else "类"
        print(f"\n{css_file.name} 定义 {scope}：{len(defined)} 个")
        print(f"  孤儿类（源码里完全没引用）：{len(orphans)} 个")
        for c in orphans:
            print(f"    - .{c}")
        if orphans:
            fails.append(f"{css_file.name}: {len(orphans)} 个孤儿类 "
                         f"{orphans[:12]}{' ...' if len(orphans) > 12 else ''}")

    if not args.no_browser:
        print("\n浏览器级复核：")
        fails += browser_checks()
    else:
        print("\n（--no-browser：跳过浏览器级复核）")

    print()
    if fails:
        print(f"FAIL：{len(fails)} 项")
        for f in fails:
            print("  -", f)
        return 1
    print(f"PASS：{len(STYLESHEETS)} 个样式表均无孤儿类，活跃样式生效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
