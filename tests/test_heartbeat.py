"""
Stage 4 tests: proves heartbeat expiry is actually detected by /reconcile.

Note: we monkeypatch app.heartbeat.tracker directly with a 1-second-TTL
InMemoryHeartbeatTracker, rather than setting HEARTBEAT_TTL_SECONDS via
os.environ. Settings/tracker are module-level singletons created at
import time - across a shared pytest session another test file may have
already imported app.config before this file's env var is set, so the
env var would silently have no effect. Monkeypatching the singleton
itself is deterministic regardless of import order.
"""
import os
import time

os.environ["DATABASE_URL"] = "sqlite:///./test_stage4.db"

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Job, Node
from app.heartbeat import InMemoryHeartbeatTracker
import app.heartbeat as heartbeat_module
import app.reconcile as reconcile_module
from app.main import app

client = TestClient(app)
client.__enter__()


@pytest.fixture(autouse=True)
def fast_ttl_tracker(monkeypatch):
    """Swap in a 1-second-TTL tracker for every test in this file."""
    test_tracker = InMemoryHeartbeatTracker(ttl_seconds=1)
    monkeypatch.setattr(heartbeat_module, "tracker", test_tracker)
    monkeypatch.setattr(reconcile_module, "tracker", test_tracker)
    monkeypatch.setattr("app.main.tracker", test_tracker)
    yield


@pytest.fixture(autouse=True)
def clean_db():
    db = SessionLocal()
    db.query(Job).delete()
    db.query(Node).delete()
    db.commit()
    db.close()
    yield


def test_heartbeat_endpoint_keeps_node_alive():
    client.post("/nodes", json={"id": "node-1", "capacity": 100})
    resp = client.post("/nodes/node-1/heartbeat")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACTIVE"


def test_heartbeat_on_unregistered_node_404s():
    resp = client.post("/nodes/ghost/heartbeat")
    assert resp.status_code == 404


def test_reconcile_marks_node_dead_after_ttl_expires():
    client.post("/nodes", json={"id": "node-1", "capacity": 100})
    time.sleep(1.2)  # let the 1s TTL expire

    resp = client.post("/reconcile")
    body = resp.json()
    assert "node-1" in body["dead_nodes"]

    node = client.get("/nodes/node-1").json()
    assert node["status"] == "DEAD"


def test_reconcile_leaves_freshly_heartbeating_node_alive():
    client.post("/nodes", json={"id": "node-1", "capacity": 100})
    client.post("/nodes/node-1/heartbeat")  # fresh beat, well within TTL

    resp = client.post("/reconcile")
    assert "node-1" not in resp.json()["dead_nodes"]

    node = client.get("/nodes/node-1").json()
    assert node["status"] == "ACTIVE"


def test_reconcile_flags_stuck_jobs_on_dead_node():
    """
    Note: as of Stage 5, reconcile() doesn't just flag stuck jobs - it
    requeues them (with backoff) or dead-letters them. This test checks
    the Stage 5 contract; see test_reconcile.py for the full behavior.
    """
    client.post("/nodes", json={"id": "node-1", "capacity": 100})
    job = client.post("/jobs", json={"url": "https://www.sec.gov/example"}).json()
    assert job["assigned_node_id"] == "node-1"

    time.sleep(1.2)
    resp = client.post("/reconcile")
    body = resp.json()
    assert job["id"] in body["requeued"]

    requeued = client.get(f"/jobs/{job['id']}").json()
    assert requeued["status"] == "PENDING"
    assert "died" in requeued["error"]