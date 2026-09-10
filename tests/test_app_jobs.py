"""Unit tests for app.jobs decoupling engine."""

import time
import pytest
from app import jobs


def test_fingerprint_deterministic():
    fp1 = jobs._fingerprint("session-123", "Eksport hajmi", False)
    fp2 = jobs._fingerprint("session-123", "Eksport hajmi", False)
    assert fp1 == fp2
    assert len(fp1) == 16

    fp3 = jobs._fingerprint("session-123", "Import hajmi", False)
    assert fp1 != fp3


def test_job_lifecycle():
    def mock_worker():
        time.sleep(0.02)
        return {"success": True, "response": "100% tasdiqlandi"}

    job = jobs.start(
        mock_worker,
        session_id="test-session-42",
        message="Salom",
        reset=False,
    )
    assert job is not None
    assert job.id is not None
    assert job.status in ("queued", "running", "done")

    # Wait for completion
    job._done.wait(timeout=2.0)
    assert job.status == "done"
    assert job.result == {"success": True, "response": "100% tasdiqlandi"}
