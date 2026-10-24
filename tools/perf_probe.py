#!/usr/bin/env python3
"""性能基线：单机自托管能给到什么水平的一个数字记录。

测的是"这台机器上、这个数据量下"的真实体感，不是理想环境的理论吞吐：
- 单用户顺序延迟（p50/p95/p99）——用户点一下要等多久；
- 并发吞吐与错误率——页面一次加载并发打十几个接口会不会自己把自己打挂；
- 静态资源体积——首屏要从网络搬多少字节；
- 库体积与慢查询嫌疑——数据涨到几万行时哪个端点先变形。

用法：BOIL_APP=http://127.0.0.1:7861 python3 tools/perf_probe.py [--samples 40] [--jobs 10]
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import statistics
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from boil import config  # noqa: E402

APP = os.environ.get("BOIL_APP", "http://127.0.0.1:7861")
OUT = ROOT / "reports"

HOT_GET = [
    "/api/health", "/api/me", "/api/dashboard", "/api/advice", "/api/map",
    "/api/review?limit=20", "/api/notes", "/api/insights", "/api/search?q=复习",
    "/api/packs", "/api/releases", "/api/agent/capabilities", "/api/events?limit=30",
    "/api/settings/llm", "/api/settings/embeddings",
]
COLD_GET = ["/api/export", "/api/export/anki", "/api/units/duplicates"]
STATIC = ["/", "/css/app.css", "/js/app.js", "/js/agentOrb.js", "/js/views/unit.js"]


def origin():
    parts = urllib.parse.urlsplit(APP)
    return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


def timed(method: str, path: str, body: dict | None = None, conn: http.client.HTTPConnection | None = None):
    """返回 (毫秒, 状态码, 字节数, 错误)。连接可复用：测的是服务端，不是 TCP 握手。

    失败要留下证据而不是被吞掉：以前异常只换算成"字节数"，报表里就成了
    "40 次 0ms 的 200"，把工具自己的 bug 伪装成服务端性能。
    """
    payload = json.dumps(body).encode() if body is not None else None
    own = conn is None
    if own:
        conn = http.client.HTTPConnection(*origin(), timeout=60)
    path = urllib.parse.quote(path, safe="/?&=,:%")
    start = time.perf_counter()
    try:
        conn.request(method, path, body=payload,
                     headers={"Content-Type": "application/json"} if payload else {})
        resp = conn.getresponse()
        data = resp.read()
        return (time.perf_counter() - start) * 1000.0, resp.status, len(data), None
    except Exception as exc:
        return (time.perf_counter() - start) * 1000.0, None, 0, f"{type(exc).__name__}: {exc}"[:120]
    finally:
        if own:
            conn.close()


def call_with_retry(method, path, body=None):
    """连接被服务端关掉就换一条重来一次：这是工具的责任，不该算成服务端错误。"""
    conn = http.client.HTTPConnection(*origin(), timeout=60)
    try:
        ms, code, size, err = timed(method, path, body, conn=conn)
        if err:
            conn = http.client.HTTPConnection(*origin(), timeout=60)
            ms, code, size, err = timed(method, path, body, conn=conn)
        return ms, code, size, err
    finally:
        conn.close()


def percentile(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q / 100.0 * (len(ordered) - 1)))))
    return ordered[index]


def seed_goal():
    conn = http.client.HTTPConnection(*origin(), timeout=60)
    conn.request("GET", "/api/dashboard")
    data = json.loads(conn.getresponse().read())["data"]
    if not data.get("has_goal"):
        conn.request("POST", "/api/onboarding",
                     json.dumps({"name": "性能基线", "pack_id": "boil.xueshen.learning",
                                 "goal_title": "基线", "daily_minutes": 30,
                                 "deadline": "2026-12-31"}).encode(),
                     {"Content-Type": "application/json"})
        conn.getresponse().read()
    conn.close()


def sequential(paths, samples, method="GET", body=None):
    rows = []
    for path in paths:
        lat, sizes, fails = [], [], []
        conn = http.client.HTTPConnection(*origin(), timeout=60)
        for _ in range(samples):
            ms, code, size, err = timed(method, path, body, conn=conn)
            if err:      # 连接死了就换一条重跑这一次，不把工具的问题记在服务端头上
                conn.close()
                conn = http.client.HTTPConnection(*origin(), timeout=60)
                ms, code, size, err = timed(method, path, body, conn=conn)
            lat.append(ms)
            sizes.append(size)
            if err or code != 200:
                fails.append(err or f"HTTP {code}")
        conn.close()
        rows.append({"path": path, "n": len(lat),
                     "p50": round(percentile(lat, 50), 1),
                     "p95": round(percentile(lat, 95), 1),
                     "p99": round(percentile(lat, 99), 1),
                     "max": round(max(lat), 1),
                     "bytes_p50": int(percentile(sizes, 50)),
                     "errors": len(fails),
                     "first_error": fails[0] if fails else ""})
    return rows


def concurrent(paths, workers, rounds):
    """每个线程一条连接，反复打这批端点：模拟页面加载 + 用户在几个视图之间来回点。"""
    stats = {"ok": 0, "bad": {}, "lat": []}
    lock = threading.Lock()

    def worker():
        conn = http.client.HTTPConnection(*origin(), timeout=60)
        for _ in range(rounds):
            for path in paths:
                ms, code, _size, err = timed("GET", path, None, conn=conn)
                if err:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = http.client.HTTPConnection(*origin(), timeout=60)
                    ms, code, _size, err = timed("GET", path, None, conn=conn)
                with lock:
                    stats["lat"].append(ms)
                    if not err and code == 200:
                        stats["ok"] += 1
                    else:
                        key = err or f"HTTP {code}"
                        stats["bad"][key] = stats["bad"].get(key, 0) + 1
        conn.close()

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in [pool.submit(worker) for _ in range(workers)]:
            future.result()
    elapsed = time.perf_counter() - start
    total = stats["ok"] + sum(stats["bad"].values())
    return {"workers": workers, "requests": total, "seconds": round(elapsed, 2),
            "rps": round(total / elapsed, 1) if elapsed else 0,
            "p50": round(percentile(stats["lat"], 50), 1),
            "p95": round(percentile(stats["lat"], 95), 1),
            "errors": stats["bad"]}


def static_bytes():
    out = []
    for path in STATIC:
        ms, code, size, err = call_with_retry("GET", path)
        out.append({"path": path, "status": code if code is not None else err, "bytes": size,
                    "ms": round(ms, 1)})
    return out


def storage():
    db = Path(os.environ.get("BOIL_DATA_DIR", str(config.DATA_DIR))) / "boil.db"
    wal = db.with_suffix(".db-wal")
    size = db.stat().st_size if db.exists() else 0
    return {"db_bytes": size, "wal_bytes": wal.stat().st_size if wal.exists() else 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=4)
    args = parser.parse_args()

    conn = http.client.HTTPConnection(*origin(), timeout=60)
    conn.request("GET", "/api/health")
    meta = json.loads(conn.getresponse().read())["data"]
    conn.close()
    seed_goal()

    report = {
        "app": APP, "version": meta.get("version"), "build": meta.get("build"),
        "mode": meta.get("mode"), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sequential_hot": sequential(HOT_GET, args.samples),
        "sequential_cold": sequential(COLD_GET, max(3, args.samples // 8)),
        "concurrent": concurrent(HOT_GET[:8], args.workers, args.rounds),
        "static": static_bytes(),
        "storage": storage(),
    }

    # 写路径单独测：复习评分是全站最频繁、也最容易出并发问题的写
    conn = http.client.HTTPConnection(*origin(), timeout=60)
    conn.request("GET", "/api/review?limit=5")
    cards = json.loads(conn.getresponse().read())["data"]["cards"]
    conn.close()
    if cards:
        cid = urllib.parse.quote(cards[0]["id"])
        lat = [call_with_retry("POST", f"/api/review/{cid}", {"rating": 3}) for _ in range(min(12, args.samples))]
        report["write_review"] = {
            "n": len(lat), "errors": sum(1 for _ms, code, _size, err in lat if err or code != 200),
            "first_error": next((err or f"HTTP {code}" for _ms, code, _size, err in lat if err or code != 200), ""),
            "p50": round(percentile([m for m, _c, _s, _e in lat], 50), 1),
            "p95": round(percentile([m for m, _c, _s, _e in lat], 95), 1)}

    OUT.mkdir(exist_ok=True)
    (OUT / "perf.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Boil {report['version']} build {report['build']} · {report['mode']} · 样本 {args.samples}\n")
    print(f"{'端点':<34}{'p50':>8}{'p95':>8}{'p99':>8}{'max':>9}{'字节':>9}{'错':>4}")
    for row in report["sequential_hot"] + report["sequential_cold"]:
        print(f"{row['path']:<34}{row['p50']:>8}{row['p95']:>8}{row['p99']:>8}"
              f"{row['max']:>9}{row['bytes_p50']:>9}{row['errors']:>4}")
    print()
    for row in report["static"]:
        print(f"{'静态 ' + row['path']:<34}{'':>8}{'':>8}{'':>8}{row['ms']:>9}{row['bytes']:>9}")
    print()
    conc = report["concurrent"]
    print(f"并发 {conc['workers']} 连接 × {conc['requests']} 请求：{conc['rps']} req/s，"
          f"p50 {conc['p50']}ms / p95 {conc['p95']}ms，错误 {conc['errors'] or '无'}")
    if "write_review" in report:
        w = report["write_review"]
        print(f"复习写入：{w['n']} 次，p50 {w['p50']}ms / p95 {w['p95']}ms，错误 {w['errors']}")
    print(f"库体积：{report['storage']['db_bytes'] / 1024:.0f} KiB（WAL "
          f"{report['storage']['wal_bytes'] / 1024:.0f} KiB）")
    slow = [r for r in report["sequential_hot"] if r["p95"] > 120]
    failing = [r for r in report["sequential_hot"] + report["sequential_cold"] if r["errors"]]
    print()
    if slow:
        print("p95 超过 120ms 的热端点：" + ", ".join(f"{r['path']} {r['p95']}ms" for r in slow))
    if failing:
        print("有非 200 响应的端点：" + ", ".join(f"{r['path']}（{r['first_error']}）" for r in failing))
    if conc["errors"]:
        print("并发下出现错误：" + json.dumps(conc["errors"], ensure_ascii=False))
    if not (slow or failing or conc["errors"]):
        print("没有超过预算的热端点，并发下没有错误。")
    print(f"\n明细写入 {OUT / 'perf.json'}")


if __name__ == "__main__":
    main()
