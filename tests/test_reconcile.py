"""
Stage 5 tests: proves a job on a dead node gets requeued with backoff,
picked back up once backoff elapses, and dead-lettered once retries are
exhausted - the actual failover path the whole project is built around.
"""
import os
import time

os.environ["DATABASE_URL"] = "sqlite:///./test_stage5.db"
os.environ["MAX_RETRIES"] = "2"

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Job, Node
from app.heartbeat import InMemoryHeartbeatTracker
import app.heartbeat as heartbeat_module
import app.reconcile as reconcile_module
from app.config import settings
from app.main import app

client = TestClient(app)
client.__enter__()


@pytest.fixture(autouse=True)
def fast_ttl_tracker(monkeypatch):
    test_tracker = InMemoryHeartbeatTracker(ttl_seconds=1)
    monkeypatch.setattr(heartbeat_module, "tracker", test_tracker)
    monkeypatch.setattr(reconcile_module, "tracker", test_tracker)
    monkeypatch.setattr("app.main.tracker", test_tracker)
    yield


@pytest.fixture(autouse=True)
def small_max_retries(monkeypatch):
    # os.environ["MAX_RETRIES"] above doesn't retroactively affect the
    # already-built `settings` singleton (see test_heartbeat.py note on
    # import-order), so patch the singleton attribute directly instead.
    monkeypatch.setattr(settings, "max_retries", 2)
    yield


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    # Shrink backoff so tests don't need to sleep tens of seconds.
    monkeypatch.setattr(reconcile_module, "BACKOFF_BASE_SECONDS", 1)
    yield


@pytest.fixture(autouse=True)
def clean_db():
    db = SessionLocal()
    db.query(Job).delete()
    db.query(Node).delete()
    db.commit()
    db.close()
    yield


def test_job_requeued_with_backoff_after_node_dies():
    client.post("/nodes", json={"id": "node-1", "capacity": 100})
    job = client.post("/jobs", json={"url": "https://www.sec.gov/example"}).json()
    assert job["status"] == "SCHEDULED"

    time.sleep(1.2)  # let node-1's heartbeat expire
    result = client.post("/reconcile").json()

    assert job["id"] in result["requeued"]
    updated = client.get(f"/jobs/{job['id']}").json()
    assert updated["status"] == "PENDING"
    assert updated["retries"] == 1
    assert updated["next_retry_at"] is not None


def test_job_reschedules_once_backoff_elapses_and_node_is_healthy():
    client.post("/nodes", json={"id": "node-1", "capacity": 100})
    job = client.post("/jobs", json={"url": "https://www.sec.gov/example"}).json()

    time.sleep(1.2)
    client.post("/reconcile")  # node-1 dies, job requeued with ~2s backoff (1 * 2^1)

    # re-register node-1 (fresh heartbeat) so it's ACTIVE and eligible again
    client.post("/nodes", json={"id": "node-1", "capacity": 100})

    time.sleep(2.2)  # let backoff elapse
    result = client.post("/reconcile").json()

    assert job["id"] in result["rescheduled"]
    final = client.get(f"/jobs/{job['id']}").json()
    assert final["status"] == "SCHEDULED"
    assert final["assigned_node_id"] == "node-1"


def test_job_dead_lettered_after_max_retries():
    """
    Rather than looping real sleep/reconcile cycles (flaky - backoff grows
    each retry, making timing brittle), we set up the exact state that
    should trigger dead-lettering: a job already at retries == MAX_RETRIES,
    stuck on a node whose heartbeat has expired. One reconcile pass should
    dead-letter it rather than requeue it again.
    """
    client.post("/nodes", json={"id": "node-1", "capacity": 100})

    db = SessionLocal()
    job = Job(
        url="https://www.sec.gov/example",
        status="SCHEDULED",
        assigned_node_id="node-1",
        weight=10.0,
        retries=settings.max_retries,  # already at the limit
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    time.sleep(1.2)  # let node-1's heartbeat expire
    result = client.post("/reconcile").json()

    assert job_id in result["dead_lettered"]
    final = client.get(f"/jobs/{job_id}").json()
    assert final["status"] == "FAILED"
    assert "max retries" in final["error"]