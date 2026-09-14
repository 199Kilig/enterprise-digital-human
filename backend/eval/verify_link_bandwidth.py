"""链路带宽对照测量（换网络后重跑同一命令即可对比）。

背景：本项目口型帧走「本机 ↔ 云 GPU」跨机链路。实测该链路稳定在 ~1MB/s，
      而同一条链路上云机自身有 3.5~18 MB/s —— 怀疑瓶颈在本机侧网络（如校园网内网限制）。
      本脚本把「本机出口」与「本机↔云」分开测，便于换网后定位。

用法（在仓库根目录或 backend/eval 下均可）：
    python backend/eval/verify_link_bandwidth.py
    python backend/eval/verify_link_bandwidth.py --mb 20 --label campus

输出：RTT / 本机出口(国内镜像) / 本机↔云 单流 / 本机↔云 4 路并发 / 上传
      每项与「判定」一栏给出可对比的结论；同一 label 多次运行会追加到 reports/link_bandwidth.json
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOST_DEFAULT = "connect.nmb1.seetacloud.com"
PORT_DEFAULT = 47618
USER_DEFAULT = "root"
REPORT = Path(__file__).resolve().parent / "reports" / "link_bandwidth.json"
CDN_URLS = [
    "https://mirrors.aliyun.com/ubuntu/ls-lR.gz",
    "https://mirrors.ustc.edu.cn/ubuntu/ls-lR.gz",
]
SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "ConnectTimeout=15",
]


def tcp_rtt(host: str, port: int, n: int = 5) -> float | None:
    """TCP 连接往返（ICMP 常被拦，用 TCP 握手测）。返回中位毫秒。"""
    samples = []
    for _ in range(n):
        try:
            t0 = time.perf_counter()
            with socket.create_connection((host, port), timeout=10):
                samples.append((time.perf_counter() - t0) * 1000)
        except OSError:
            return None
    if not samples:
        return None
    samples.sort()
    return samples[len(samples) // 2]


def local_egress_test(limit_mb: float = 5.0, timeout: float = 45.0) -> dict:
    """本机直连国内镜像站下载：既测速，也测「本机公网是否可用」。"""
    for url in CDN_URLS:
        t0 = time.time()
        got = 0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                while got < limit_mb * 1024 * 1024:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    got += len(chunk)
        except Exception as exc:  # noqa: BLE001
            print(f"    {url[:48]:50s} 失败 {type(exc).__name__}: {str(exc)[:70]}")
            continue
        dt = time.time() - t0
        mb = got / 1048576
        return {"ok": True, "url": url, "mb": round(mb, 2), "seconds": round(dt, 2),
                "mbps": round(mb / dt, 2) if dt else 0.0}
    return {"ok": False, "url": None, "mb": 0.0, "seconds": 0.0, "mbps": 0.0}


def _scp(args: list[str], timeout: float = 300.0) -> bool:
    try:
        return subprocess.run(["scp", *SSH_OPTS, *args], capture_output=True,
                              timeout=timeout, check=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def cloud_transfer(host: str, port: int, user: str, remote: str, mb: float, parallel: int) -> dict:
    """本机←云 下载：单流 或 N 路并发聚合。返回 MB/s。"""
    tmp = Path(tempfile.gettempdir()) / "link_bw"
    tmp.mkdir(parents=True, exist_ok=True)
    for f in tmp.glob("p*.bin"):
        f.unlink(missing_ok=True)

    if parallel == 1:
        dst = str(tmp / "single.bin").replace("\\", "/")
        t0 = time.perf_counter()
        ok = _scp(["-P", str(port), f"{user}@{host}:{remote}", dst])
        dt = time.perf_counter() - t0
        size = os.path.getsize(dst) / 1048576 if ok and os.path.exists(dst) else 0
        return {"ok": ok, "mb": round(size, 2), "seconds": round(dt, 2),
                "mbps": round(size / dt, 2) if dt > 0 else 0.0}

    # 并发：把 remote 拆成 parallel 份（调用方保证存在 bw{N}_i.bin）
    per = mb / parallel
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = []
        for i in range(1, parallel + 1):
            src = f"{user}@{host}:/tmp/bw{int(per)}m_{i}.bin"
            dst = str(tmp / f"p{i}.bin").replace("\\", "/")
            futs.append(pool.submit(_scp, ["-P", str(port), src, dst]))
        oks = [f.result() for f in futs]
    dt = time.perf_counter() - t0
    total = sum(os.path.getsize(tmp / f"p{i}.bin") for i in range(1, parallel + 1)
                if (tmp / f"p{i}.bin").exists()) / 1048576
    return {"ok": all(oks), "mb": round(total, 2), "seconds": round(dt, 2),
            "mbps": round(total / dt, 2) if dt > 0 else 0.0}


def prepare_remote(host: str, port: int, user: str, mb: int) -> bool:
    script = (f"dd if=/dev/urandom of=/tmp/bw{mb}m.bin bs=1M count={mb} 2>/dev/null; "
              f"for i in 1 2 3 4; do dd if=/dev/urandom of=/tmp/bw{mb // 4}m_$i.bin bs=1M count={mb // 4} 2>/dev/null; done; "
              f"ls -la /tmp/bw{mb}m.bin")
    try:
        r = subprocess.run(["ssh", *SSH_OPTS, "-p", str(port), f"{user}@{host}", script],
                           capture_output=True, timeout=120, check=False)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=HOST_DEFAULT)
    ap.add_argument("--port", type=int, default=PORT_DEFAULT)
    ap.add_argument("--user", default=USER_DEFAULT)
    ap.add_argument("--mb", type=int, default=20)
    ap.add_argument("--label", default="", help="标注本次网络（如 campus / home / hotspot）")
    ap.add_argument("--cdn-mb", type=float, default=5.0, help="本机出口测试下载量（MB），越大越准越慢")
    a = ap.parse_args()

    print(f"链路带宽测量  label={a.label or '(未标注)'}  目标={a.user}@{a.host}:{a.port}\n")
    result: dict = {"label": a.label, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "host": f"{a.host}:{a.port}", "mb": a.mb}

    print("[1/5] TCP RTT（5 次取中位）")
    rtt = tcp_rtt(a.host, a.port)
    result["rtt_ms"] = None if rtt is None else round(rtt, 1)
    print(f"    RTT = {result['rtt_ms']} ms\n")

    print(f"[2/5] 本机出口带宽（直连国内镜像，下载 {a.cdn_mb:g}MB，不经云）")
    egress = local_egress_test(limit_mb=a.cdn_mb)
    result["local_egress"] = egress
    print(f"    本机出口 = {egress['mbps']} MB/s（{'可用' if egress['ok'] else '不可用/被拦'})\n")

    print(f"[3/5] 准备云侧测试文件（{a.mb}MB）")
    if not prepare_remote(a.host, a.port, a.user, a.mb):
        print("    失败：SSH 不通")
        return 1
    print("    就绪\n")

    print(f"[4/5] 本机←云 单流（{a.mb}MB，原生 OpenSSH scp，重复 2 次测稳定性）")
    singles = []
    for k in range(2):
        s = cloud_transfer(a.host, a.port, a.user, f"/tmp/bw{a.mb}m.bin", a.mb, 1)
        singles.append(s)
        print(f"    第{k + 1}次: {s['mbps']} MB/s（{s['mb']}MB / {s['seconds']}s）")
    result["cloud_single_runs"] = singles
    # 用较快的一次代表链路能力（慢的一次可能撞上瞬时拥塞）；同时记录离散度用于稳定性判定
    single = max(singles, key=lambda s: s["mbps"])
    peak = max(s["mbps"] for s in singles)
    low = min(s["mbps"] for s in singles)
    spread = (peak - low) / peak if peak > 0 else 0.0
    result["cloud_single"] = single
    result["single_spread"] = round(spread, 3)
    print(f"    → 取较快值 {single['mbps']} MB/s；离散度 {spread * 100:.0f}%\n")

    print("[5/5] 本机←云 4 路并发（看聚合是否放大）")
    par = cloud_transfer(a.host, a.port, a.user, "", a.mb, 4)
    result["cloud_parallel4"] = par
    print(f"    {par['mbps']} MB/s（{par['mb']}MB / {par['seconds']}s）\n")

    subprocess.run(["ssh", *SSH_OPTS, "-p", str(a.port), f"{a.user}@{a.host}",
                    f"rm -f /tmp/bw{a.mb}m.bin /tmp/bw{a.mb // 4}m_*.bin"],
                   capture_output=True, check=False)

    # ---- 判定 ----
    # ⚠️ 纪律：单次测量不足以判定瓶颈机制。实测同一链路几分钟内可差 3~5 倍（见 RUNBOOK 坑 15），
    #    因此先看离散度，不稳定就明确标注「仅供参考」，不下结论。
    print("=" * 62)
    print("判定")
    print("=" * 62)
    cs = single["mbps"]
    print(f"  本机出口        : {'可用 ' + str(egress['mbps']) + ' MB/s' if egress['ok'] else '被拦/不可用'}")
    print(f"  本机↔云 单流    : {cs} MB/s（离散度 {spread * 100:.0f}%）")
    print(f"  本机↔云 4 路并发: {par['mbps']} MB/s")

    if spread > 0.30:
        print(f"  ⚠️ 单流重复测量离散度 {spread * 100:.0f}% > 30% —— **链路本身不稳定**，")
        print("     下面的机制判定不可信；请换时段/换网络复测，或看 history 里的多次记录。")
    else:
        if par["mbps"] > cs * 1.6:
            print("  → 并发明显放大：瓶颈是**单流窗口/复用**（可优化传输方式，如分片并发拉取）")
        elif par["mbps"] <= cs * 1.3:
            print("  → 并发不放大：瓶颈是**链路硬性限速**（换协议无用，只能减载荷）")
        else:
            print("  → 并发放大不明显（1.3~1.6×）：证据不足以判定机制，需更多样本")
    if rtt is not None and rtt > 100:
        print(f"  ⚠️ RTT {rtt:.0f}ms 偏高：高时延链路上单流天然吃亏（BDP 限制），并发通常有收益")
    print("  参考：云机自身 下行 6.6~18 MB/s / 上行 3.5~4.0 MB/s（2026-09-14 实测）")
    print(f"  载荷换算：逐帧 JPEG 15.5MB/8s 音频 → {15.5 / max(cs, 0.01):.1f}s ；"
          f"H.264 CRF26 387KB → {0.387 / max(cs, 0.01):.2f}s")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    history = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else []
    history.append(result)
    REPORT.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已追加到 {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
