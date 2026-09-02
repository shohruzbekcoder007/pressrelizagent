"""
Turn execution decoupled from the HTTP request that asked for it.

A press-release check is a long turn: convert a PDF, page through its claims,
then two register calls per figure. Minutes, not seconds. Held open that whole
time, the request is at the mercy of every idle timer between the browser and
here -- and when one of them fires, today's behaviour is the worst of both:
the caller sees a dropped connection *and* the work is abandoned mid-way.

So the work does not live in the request any more. Each turn runs on its own
thread and writes its result here; the request only watches. Three things
follow from that, and they are the whole point of this module:

  * **A dropped connection costs nothing.** The thread keeps going and the
    answer lands in the registry, ready for whoever asks next.
  * **A retry joins the work instead of duplicating it.** Same session, same
    message, job still running -- the second request attaches to the first.
    A gateway whose HTTP client gave up and retried therefore picks the
    original turn back up rather than starting a second one alongside it,
    which is what makes reconnecting free rather than twice the cost.
  * **One session runs one turn at a time.** Session history is read at the
    start of a turn and written at the end, so two overlapping turns on one
    session would interleave and lose one of the two. They queue instead.

This file is COPY'd into the image, so editing it needs `docker compose build
app`, not a restart.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger("app.jobs")

# Finished jobs stay reachable so a client that reconnects late still gets its
# answer; these two bounds keep that from being a leak.
_TTL_SECONDS = float(os.getenv("JOB_TTL_SECONDS") or 3600)
_MAX_JOBS = int(os.getenv("JOB_MAX") or 500)

_lock = threading.RLock()
_jobs: dict[str, "Job"] = {}
# One lock per session id, created on demand. Held for the whole turn.
_session_locks: dict[str, threading.Lock] = {}


def _fingerprint(session_id: Optional[str], message: str, reset: bool) -> str:
    raw = f"{session_id or ''}\x00{message}\x00{int(bool(reset))}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class Job:
    """One turn, running or finished."""

    __slots__ = (
        "id",
        "session_id",
        "fingerprint",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "result",
        "preview",
        "files",
        "_done",
    )

    def __init__(
        self,
        session_id: Optional[str],
        fingerprint: str,
        preview: str,
        files: Optional[list[str]],
    ) -> None:
        self.id = uuid.uuid4().hex[:16]
        self.session_id = session_id
        self.fingerprint = fingerprint
        self.status = "queued"  # queued | running | done
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.result: Optional[dict[str, Any]] = None
        self.preview = preview
        self.files = files or []
        # Threading.Event rather than polling a flag: a waiter blocks with a
        # timeout and wakes the moment the turn lands, so a fast turn is not
        # padded out to the next poll tick.
        self._done = threading.Event()

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.created_at, 1)

    def wait(self, timeout: Optional[float]) -> bool:
        """Block until finished. True if it finished, False on timeout."""
        return self._done.wait(timeout)

    def snapshot(self) -> dict[str, Any]:
        """What a status endpoint reports. Never the result itself."""
        return {
            "job_id": self.id,
            "session_id": self.session_id,
            "status": self.status,
            "elapsed": self.elapsed,
            "files": self.files or None,
            "soralgan": self.preview,
        }


def _evict_locked() -> None:
    """Drop finished jobs past their TTL, then oldest-first over the cap."""
    now = time.time()
    stale = [
        jid
        for jid, job in _jobs.items()
        if job.finished_at and now - job.finished_at > _TTL_SECONDS
    ]
    for jid in stale:
        _jobs.pop(jid, None)

    if len(_jobs) <= _MAX_JOBS:
        return
    finished = sorted(
        (j for j in _jobs.values() if j.finished_at),
        key=lambda j: j.finished_at or 0.0,
    )
    # Only finished jobs are evictable -- dropping a running one would orphan
    # a thread whose result nobody could ever collect.
    for job in finished[: len(_jobs) - _MAX_JOBS]:
        _jobs.pop(job.id, None)


def find_running(
    session_id: Optional[str], message: str, reset: bool
) -> Optional[Job]:
    """A still-running job for this exact request, if one exists."""
    if not session_id:
        # Without a session id two identical messages are not knowably the
        # same request -- they could be two people asking the same thing.
        return None
    fp = _fingerprint(session_id, message, reset)
    with _lock:
        for job in _jobs.values():
            if job.fingerprint == fp and job.status != "done":
                return job
    return None


def get(job_id: str) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id)


def latest_for_session(session_id: str) -> Optional[Job]:
    with _lock:
        matches = [j for j in _jobs.values() if j.session_id == session_id]
    return max(matches, key=lambda j: j.created_at) if matches else None


def _session_lock(session_id: Optional[str]) -> threading.Lock:
    key = session_id or "\x00anonymous"
    with _lock:
        lock = _session_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _session_locks[key] = lock
        return lock


def start(
    run: Callable[[], dict[str, Any]],
    *,
    session_id: Optional[str],
    message: str,
    reset: bool,
    files: Optional[list[str]] = None,
) -> Job:
    """Run `run()` on its own thread and return the job tracking it."""
    job = Job(
        session_id=session_id,
        fingerprint=_fingerprint(session_id, message, reset),
        preview=message[:120],
        files=files,
    )
    with _lock:
        _evict_locked()
        _jobs[job.id] = job

    def _worker() -> None:
        # Serialised per session: the turn reads history at its start and
        # writes it at its end, so overlapping turns would lose one of them.
        with _session_lock(session_id):
            job.status = "running"
            job.started_at = time.time()
            try:
                job.result = run()
            except Exception as exc:  # noqa: BLE001
                logger.error("job %s failed: %s", job.id, exc, exc_info=True)
                job.result = {
                    "success": False,
                    "response": None,
                    "error": "Ichki server xatosi. Iltimos keyinroq urinib ko'ring.",
                    "error_code": "internal",
                    "error_detail": str(exc)[:500],
                    "retryable": True,
                    "session_id": session_id,
                }
            finally:
                job.finished_at = time.time()
                job.status = "done"
                job._done.set()
                logger.info(
                    "job %s finished in %.1fs (session=%s)",
                    job.id,
                    job.elapsed,
                    session_id,
                )

    threading.Thread(target=_worker, name=f"turn-{job.id}", daemon=True).start()
    return job
