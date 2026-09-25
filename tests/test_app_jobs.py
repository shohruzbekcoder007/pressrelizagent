"""Unit tests for app.jobs: the decoupled turn runner and its owner scoping."""

import threading
import time

from app import jobs


def test_fingerprint_deterministic():
    fp1 = jobs._fingerprint("shohruz", "session-123", "Eksport hajmi", False)
    fp2 = jobs._fingerprint("shohruz", "session-123", "Eksport hajmi", False)
    assert fp1 == fp2
    assert len(fp1) == 16

    assert fp1 != jobs._fingerprint("shohruz", "session-123", "Import hajmi", False)
    # Same session and message from another user is a different request.
    assert fp1 != jobs._fingerprint("aziza", "session-123", "Eksport hajmi", False)


def test_job_lifecycle():
    def mock_worker():
        time.sleep(0.02)
        return {"success": True, "response": "100% tasdiqlandi"}

    job = jobs.start(
        mock_worker,
        owner="shohruz",
        session_id="test-session-42",
        message="Salom",
        reset=False,
    )
    assert job.status in ("queued", "running", "done")
    assert job.wait(2.0)
    assert job.status == "done"
    assert job.result == {"success": True, "response": "100% tasdiqlandi"}


def _blocking_job(owner: str, session_id: str, message: str):
    """A job that stays running until the returned event is set."""
    release = threading.Event()

    def run():
        release.wait(5)
        return {"success": True, "response": f"{owner}-ning javobi"}

    job = jobs.start(
        run, owner=owner, session_id=session_id, message=message, reset=False
    )
    return job, release


def test_retry_attaches_only_to_the_same_owners_job():
    job, release = _blocking_job("shohruz", "chat-1", "maxfiy savol")
    try:
        assert jobs.find_running("shohruz", "chat-1", "maxfiy savol", False) is job
        # Another user replaying the same session id and message must start
        # their own turn, not join (and read) this one.
        assert jobs.find_running("aziza", "chat-1", "maxfiy savol", False) is None
    finally:
        release.set()
        job.wait(2)


def test_get_hides_other_users_jobs():
    job, release = _blocking_job("shohruz", "chat-2", "savol")
    release.set()
    assert job.wait(2)
    assert jobs.get(job.id, "shohruz") is job
    assert jobs.get(job.id, "aziza") is None


def test_latest_for_session_is_per_owner():
    job, release = _blocking_job("shohruz", "chat-3", "savol")
    release.set()
    job.wait(2)
    assert jobs.latest_for_session("shohruz", "chat-3") is job
    assert jobs.latest_for_session("aziza", "chat-3") is None


def test_same_session_id_of_two_users_does_not_serialise():
    """The session lock is per owner: one user's long turn on `chat-1` must
    not block another user's turn that happens to use the same id."""
    blocker, release = _blocking_job("shohruz", "chat-4", "uzun ish")
    try:
        other = jobs.start(
            lambda: {"success": True},
            owner="aziza",
            session_id="chat-4",
            message="tez ish",
            reset=False,
        )
        assert other.wait(2), "aziza's turn was queued behind shohruz's"
    finally:
        release.set()
        blocker.wait(2)


def test_job_ids_are_not_short():
    job, release = _blocking_job("shohruz", "chat-5", "x")
    release.set()
    job.wait(2)
    assert len(job.id) == 32
