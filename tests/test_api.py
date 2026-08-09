"""
Stage 2 tests: proves the CRUD API works before we build scheduling on
top of it in Stage 3. Uses a throwaway SQLite file so it never touches
your real docflow.db.
"""
import os
os.environ["DATABASE_URL"] = "sqlite:///./test_stage2.db"

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Job, Node
from app.main import app

# TestClient must be used as a context manager so FastAPI's startup event
# (which calls init_db() to create tables) actually fires.
client = TestClient(app)
client.__enter__()


@pytest.fixture(autouse=True)
def clean_db():
    """
    Wipe rows (not the file) between tests. Deleting/recreating the sqlite
    file mid-session leaves SQLAlchemy's pooled connection pointing at a
    stale file handle, which surfaces as spurious 'readonly database'
    errors - clearing rows through the same connection pool avoids that.
    """
    db = SessionLocal()
    db.query(Job).delete()
    db.query(Node).delete()
    db.commit()
    db.close()
    yield


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_and_get_job():
    resp = client.post("/jobs", json={"url": "https://www.sec.gov/example"})
    assert resp.status_code == 201
    job = resp.json()
    assert job["status"] == "PENDING"
    assert job["url"] == "https://www.sec.gov/example"

    resp2 = client.get(f"/jobs/{job['id']}")
    assert resp2.status_code == 200
    assert resp2.json()["id"] == job["id"]


def test_get_nonexistent_job_404():
    resp = client.get("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_list_jobs_filter_by_status():
    client.post("/jobs", json={"url": "https://www.sec.gov/a"})
    client.post("/jobs", json={"url": "https://www.sec.gov/b"})

    resp = client.get("/jobs", params={"status": "PENDING"})
    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) == 2
    assert all(j["status"] == "PENDING" for j in jobs)


def test_register_node_is_idempotent():
    resp1 = client.post("/nodes", json={"id": "node-1", "capacity": 100})
    assert resp1.status_code == 201
    assert resp1.json()["status"] == "ACTIVE"

    # re-register same node.id - should update, not duplicate
    resp2 = client.post("/nodes", json={"id": "node-1", "capacity": 150})
    assert resp2.status_code == 201
    assert resp2.json()["capacity"] == 150

    resp3 = client.get("/nodes")
    assert len(resp3.json()) == 1


def test_get_nonexistent_node_404():
    resp = client.get("/nodes/ghost-node")
    assert resp.status_code == 404