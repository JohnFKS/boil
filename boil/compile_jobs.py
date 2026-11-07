"""材料编译的后台任务：提交、进度、事件流、取消、结果取回。

一份长材料要调用模型十几次，单次可能一两分钟；批次已按波次并发，但整轮
仍不可能在一个 HTTP 请求里等完。同步请求必然先被浏览器掐断，而服务端线程
还在跑，用户重试又生成一个新包 —— 这就是「导入材料后一直转圈、再点还是
不行」的根因。把编译挪到后台并返回 job_id，前端
轮询进度：网络断开、页面刷新、重复点击都还能接着拿回同一次编译的结果。

进度不只是「第几批」：每个阶段、每批失败、每次降级都会记成事件并打到 stderr，
前端与日志看到的是同一份事实，排障不用猜。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
import traceback
from collections import OrderedDict
from typing import Any, Callable

from .utils import new_id, now_iso

Progress = Callable[..., None]
Runner = Callable[[Progress], dict[str, Any]]

MAX_JOBS = 32
MAX_EVENTS = 120
MAX_WARNINGS = 60
RESULT_TTL = 3600.0

PHASES = {
    "queued": "排队中", "prepare": "整理材料", "outline": "通读全篇",
    "batch": "分批编译", "extract": "抽取知识点", "validate": "整理校验",
    "rules": "本地编译", "done": "已完成", "error": "失败", "cancelled": "已取消",
}


class JobCancelled(BaseException):
    """在批次之间打断编译。

    刻意继承 BaseException：编译器把模型失败 `except Exception` 降级成本地
    编译器是正常行为，但「用户点了取消」不能被当成一次可降级的失败，否则
    取消之后还会再发一次长模型调用。
    """


_LOCK = threading.RLock()
_JOBS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def _safe(text: Any) -> str:
    """上游报错内容偶尔会带 Key，日志与 API 都不能原文照抄。"""
    out = re.sub(r"(sk|ak|key|token|github_pat)[-_=]([A-Za-z0-9_-]{6,})",
                 lambda m: f"{m.group(1)}-…{m.group(2)[-4:]}", str(text or ""), flags=re.I)
    return out.replace("\r", " ").replace("\n", " ")[:600]


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _material_stats(payload: dict[str, Any]) -> dict[str, Any]:
    docs = payload.get("documents") or []
    chars = len(str(payload.get("text") or ""))
    if isinstance(docs, list):
        chars += sum(len(str(d.get("text") or "")) for d in docs if isinstance(d, dict))
        docs = len(docs)
    else:
        docs = 0
    return {"documents": docs, "chars": chars,
            "use_llm": bool(payload.get("use_llm", True)),
            "coverage": str(payload.get("coverage") or "standard")}


def _log(job_id: str, level: str, message: str, detail: str = "", elapsed: float = 0.0) -> None:
    line = f"[compile] job={job_id} +{elapsed:6.1f}s {level.upper():5s} {_safe(message)}"
    if detail:
        line += f" | {_safe(detail)}"
    print(line, file=sys.stderr, flush=True)


def _error_payload(exc: BaseException, phase: str = "", tb: str = "") -> dict[str, str]:
    detail = getattr(exc, "detail", "") or ""
    errors = getattr(exc, "errors", None) or []
    if errors and not detail:
        detail = "；".join(str(e) for e in errors[:5])
    return {
        "code": str(getattr(exc, "code", "") or type(exc).__name__),
        "message": _safe(str(getattr(exc, "message", "")) or str(exc) or "编译失败")[:400],
        "detail": _safe(detail)[:600],
        "hint": _safe(getattr(exc, "hint", ""))[:300],
        "phase": phase,
        "traceback": _safe(tb)[:1800],
    }


def _sweep() -> None:
    """只清理已结束的任务，正在跑的绝不能被淘汰。"""
    now = time.monotonic()
    for job_id in list(_JOBS):
        job = _JOBS[job_id]
        ended = job.get("ended")
        if ended and now - ended > RESULT_TTL:
            _JOBS.pop(job_id, None)


def _trim() -> None:
    _sweep()
    while len(_JOBS) > MAX_JOBS:
        victim = next((k for k, v in _JOBS.items()
                       if v["state"] not in ("queued", "running")), None)
        if victim is None:
            break
        _JOBS.pop(victim, None)


def _percent(job: dict[str, Any]) -> int:
    if job["state"] == "done":
        return 100
    if job["total"] > 0:
        return min(100, max(0, round(job["current"] / job["total"] * 100)))
    return 0


def _public(job: dict[str, Any], *, with_result: bool = False) -> dict[str, Any]:
    elapsed = time.monotonic() - job["started"] if job["state"] in ("queued", "running") \
        else (job.get("ended", time.monotonic()) - job["started"])
    out = {
        "job_id": job["id"],
        "state": job["state"],
        "phase": job["phase"],
        "phase_label": PHASES.get(job["phase"], job["phase"]),
        "current": job["current"],
        "total": job["total"],
        "percent": _percent(job),
        "message": job["message"],
        "elapsed": round(elapsed, 1),
        "started_at": job["started_at"],
        "finished_at": job.get("finished_at"),
        "cancellable": job["state"] in ("queued", "running"),
        "material": job["material"],
        "events": list(job["events"]),
        "warnings": list(job["warnings"]),
        "stats": dict(job["stats"]),
    }
    if job["state"] == "error":
        out["error"] = job.get("error") or {"message": "编译失败"}
    if with_result and job["state"] == "done":
        out["result"] = job.get("result")
    return out


def _event(job: dict[str, Any], level: str, phase: str, message: str,
           detail: str = "", current: int | None = None, total: int | None = None) -> dict:
    """记录一条事件；完全相同的连续进度不重复刷屏，告警和错误一律留痕。"""
    elapsed = round(time.monotonic() - job["started"], 1)
    cur = job["current"] if current is None else current
    tot = job["total"] if total is None else total
    event = {"at": now_iso(), "elapsed": elapsed, "level": level, "phase": phase,
             "phase_label": PHASES.get(phase, phase), "current": cur, "total": tot,
             "message": _safe(message)[:300], "detail": _safe(detail)[:500]}
    prev = job["events"][-1] if job["events"] else None
    duplicated = (prev and level == "info" and
                  (prev["phase"], prev["current"], prev["message"], prev["level"])
                  == (phase, cur, event["message"], level))
    if not duplicated:
        job["events"].append(event)
        if len(job["events"]) > MAX_EVENTS:
            del job["events"][:len(job["events"]) - MAX_EVENTS]
        if level == "warn" and len(job["warnings"]) < MAX_WARNINGS:
            job["warnings"].append(
                f"[{PHASES.get(phase, phase)} {cur}/{tot}] {event['message']}"
                + (f"：{event['detail']}" if event["detail"] else ""))
        if level == "warn":
            job["stats"]["warnings"] += 1
        elif level == "error":
            job["stats"]["errors"] += 1
    return event


def submit(payload: dict[str, Any], runner: Runner) -> dict[str, Any]:
    """同一份材料已有任务在跑时直接复用，避免重试把一次编译变成 N 个包。"""
    fp = _fingerprint(payload)
    material = _material_stats(payload)
    with _LOCK:
        _sweep()
        for job in _JOBS.values():
            if job["fingerprint"] == fp and job["state"] in ("queued", "running"):
                _log(job["id"], "info", "重复提交，复用正在进行的任务")
                return _public(job)
        job_id = new_id("job")
        job = {
            "id": job_id,
            "fingerprint": fp,
            "material": material,
            "state": "queued",
            "phase": "queued",
            "current": 0,
            "total": 0,
            "message": "正在排队…",
            "started": time.monotonic(),
            "started_at": now_iso(),
            "finished_at": None,
            "cancel": threading.Event(),
            "result": None,
            "error": None,
            "ended": None,
            "events": [],
            "warnings": [],
            "stats": {"warnings": 0, "errors": 0, "batches": 0, "units": 0},
        }
        _JOBS[job_id] = job
        _event(job, "info", "queued", "任务已提交",
               f"{material['documents']} 份材料 · {material['chars']:,} 字符 · "
               f"{'真实模型' if material['use_llm'] else '本地编译器'} · {material['coverage']} 档")
        _trim()
    _log(job_id, "info", "任务已提交",
         f"documents={material['documents']} chars={material['chars']} use_llm={material['use_llm']}")
    thread = threading.Thread(target=_run, args=(job_id, runner),
                              name=f"boil-compile-{job_id[:8]}", daemon=True)
    thread.start()
    with _LOCK:
        return _public(_JOBS[job_id])


def _run(job_id: str, runner: Runner) -> None:
    last_phase = "prepare"

    def progress(phase: str, current: int = 0, total: int = 0, message: str = "",
                 level: str = "info", detail: str = "") -> None:
        nonlocal last_phase
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is None or job["cancel"].is_set():
                raise JobCancelled()
            last_phase = phase
            new_total = max(0, int(total))
            new_current = max(0, int(current))
            # 质检等收尾阶段用 1/1 表示「一个阶段」，不能把分批总数 10 冲成 1，
            # 否则完成瞬间进度会从「已完成 10/10 批」倒退成 1/1。
            if phase in ("validate", "extract", "rules") and job["total"] > new_total:
                new_total = job["total"]
                new_current = job["total"] if phase == "validate" else new_current
            job.update(state="running", phase=phase, current=new_current,
                       total=new_total, message=message or job["message"])
            _event(job, level, phase, message or PHASES.get(phase, phase), detail)
            cur, tot = job["current"], job["total"]
            elapsed = time.monotonic() - job["started"]
        _log(job_id, level, f"phase={phase} {cur}/{tot} "
             f"{message or PHASES.get(phase, phase)}", detail, elapsed)

    try:
        progress("prepare", 0, 0, "正在整理材料…")
        result = runner(progress)
    except JobCancelled:
        _finish(job_id, "cancelled", message="编译已取消")
    except BaseException as exc:                 # 后台线程里的异常不能吞，要交给前端展示
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        tb = traceback.format_exc()
        _finish(job_id, "error", error=_error_payload(exc, last_phase, tb),
                message=f"编译失败：{str(exc)[:160]}")
        _log(job_id, "error", f"{type(exc).__name__}: {exc}", tb[-1200:])
    else:
        _finish(job_id, "done", result=result, message="编译完成")


def _finish(job_id: str, state: str, *, result=None, error=None, message="") -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        elapsed = time.monotonic() - job["started"]
        if state == "done":
            job["phase"] = "done"
            job["current"] = job["total"] or job["current"]
            pack = (result or {}).get("pack") or {}
            job["stats"]["units"] = len(pack.get("units") or [])
            job["stats"]["batches"] = int((pack.get("source") or {}).get("batches") or 0)
            # 编译结束时才拿得到的 warnings 也要并进事件流；编译过程中已实时
            # 上报过的（质检告警、批次失败）不重复记一遍。
            existing = "\n".join(job["warnings"])
            for warning in (result or {}).get("warnings") or []:
                text = str(warning)
                if text and text not in existing and len(job["warnings"]) < MAX_WARNINGS:
                    _event(job, "warn", "validate", text)
        job.update(state=state, result=result, error=error,
                   message=message or job["message"],
                   finished_at=now_iso(), ended=time.monotonic())
        level = "error" if state == "error" else "info"
        _event(job, level, state, message or PHASES.get(state, state),
               (error or {}).get("detail", "") if error else "")
        summary = (f"units={job['stats']['units']} batches={job['stats']['batches']} "
                   f"warnings={len(job['warnings'])} errors={job['stats']['errors']}")
        _trim()
    _log(job_id, level, message or state.upper(), summary, elapsed)


def status(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return _public(job, with_result=True) if job else None


def cancel(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        if job["state"] in ("queued", "running"):
            job["cancel"].set()
            job["message"] = "正在取消…（当前批次结束后停止）"
            _event(job, "warn", job["phase"], "用户请求取消编译")
        return _public(job)


def active_count() -> int:
    with _LOCK:
        return sum(1 for j in _JOBS.values() if j["state"] in ("queued", "running"))
