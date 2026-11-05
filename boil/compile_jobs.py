"""材料编译的后台任务：提交、进度、取消、结果取回。

一份长材料会串行调用模型十几次，每次可能一两分钟。同步 HTTP 请求必然先被
浏览器掐断，而服务端线程还在跑，用户重试又生成一个新包 —— 这就是「导入
材料后一直转圈、再点还是不行」的根因。把编译挪到后台并返回 job_id，前端
轮询进度：网络断开、页面刷新、重复点击都还能接着拿回同一次编译的结果。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from .utils import new_id, now_iso

Progress = Callable[..., None]
Runner = Callable[[Progress], dict[str, Any]]

MAX_JOBS = 32
RESULT_TTL = 3600.0


class JobCancelled(BaseException):
    """在批次之间打断编译。

    刻意继承 BaseException：编译器把模型失败 `except Exception` 降级成本地
    编译器是正常行为，但「用户点了取消」不能被当成一次可降级的失败，否则
    取消之后还会再发一次长模型调用。
    """


_LOCK = threading.RLock()
_JOBS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _error_payload(exc: BaseException) -> dict[str, str]:
    detail = getattr(exc, "detail", "") or ""
    errors = getattr(exc, "errors", None) or []
    if errors and not detail:
        detail = "；".join(str(e) for e in errors[:5])
    return {
        "code": getattr(exc, "code", "") or type(exc).__name__,
        "message": str(getattr(exc, "message", "")) or str(exc) or "编译失败",
        "detail": str(detail)[:500],
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


def _public(job: dict[str, Any], *, with_result: bool = False) -> dict[str, Any]:
    out = {
        "job_id": job["id"],
        "state": job["state"],
        "phase": job["phase"],
        "current": job["current"],
        "total": job["total"],
        "message": job["message"],
        "elapsed": round(time.monotonic() - job["started"], 1),
        "started_at": job["started_at"],
        "finished_at": job.get("finished_at"),
        "cancellable": job["state"] in ("queued", "running"),
    }
    if job["state"] == "error":
        out["error"] = job.get("error") or {"message": "编译失败"}
    if with_result and job["state"] == "done":
        out["result"] = job.get("result")
    return out


def submit(payload: dict[str, Any], runner: Runner) -> dict[str, Any]:
    """同一份材料已有任务在跑时直接复用，避免重试把一次编译变成 N 个包。"""
    fp = _fingerprint(payload)
    with _LOCK:
        _sweep()
        for job in _JOBS.values():
            if job["fingerprint"] == fp and job["state"] in ("queued", "running"):
                return _public(job)
        job_id = new_id("job")
        job = {
            "id": job_id,
            "fingerprint": fp,
            "state": "queued",
            "phase": "prepare",
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
        }
        _JOBS[job_id] = job
        _trim()
    thread = threading.Thread(target=_run, args=(job_id, runner),
                              name=f"boil-compile-{job_id[:8]}", daemon=True)
    thread.start()
    with _LOCK:
        return _public(_JOBS[job_id])


def _run(job_id: str, runner: Runner) -> None:
    def progress(phase: str, current: int = 0, total: int = 0, message: str = "") -> None:
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is None or job["cancel"].is_set():
                raise JobCancelled()
            job.update(state="running", phase=phase, current=max(0, int(current)),
                       total=max(0, int(total)), message=message or job["message"])

    try:
        progress("prepare", 0, 0, "正在整理材料…")
        result = runner(progress)
    except JobCancelled:
        _finish(job_id, "cancelled", message="编译已取消")
    except BaseException as exc:                 # 后台线程里的异常不能吞，要交给前端展示
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        _finish(job_id, "error", error=_error_payload(exc))
    else:
        _finish(job_id, "done", result=result, message="编译完成")


def _finish(job_id: str, state: str, *, result=None, error=None, message="") -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job.update(state=state, result=result, error=error,
                   message=message or job["message"],
                   finished_at=now_iso(), ended=time.monotonic())
        if state == "done":
            job["phase"] = "done"
            job["current"] = job["total"] or job["current"]
        _trim()


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
        return _public(job)


def active_count() -> int:
    with _LOCK:
        return sum(1 for j in _JOBS.values() if j["state"] in ("queued", "running"))
