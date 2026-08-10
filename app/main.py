"""
Stage 3: wires the scheduler into job creation.

No weight estimation yet (that's Stage 6) - jobs use a placeholder
weight of 10.0 until the real HEAD-request-based estimator replaces it.
"""
from datetime import datetime
from app.heartbeat import tracker
from app.reconcile import reconcile

from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import init_db, get_db
from app.scheduler import schedule_job
from app.models import (
    Job, Node, JobStatus, NodeStatus,
    JobCreate, JobOut, NodeRegister, NodeOut,
)

app = FastAPI(title="DocFlow", version="0.5.0")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok", "stage": 5}


# ------------------------------------------------------------------- Jobs

@app.post("/jobs", response_model=JobOut, status_code=201)
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    """
    Create a job and immediately attempt to place it on a node via the
    configured scheduling policy. If the cluster is full, the job simply
    stays PENDING - Stage 5's reconciler will retry placing it later.
    """
    job = Job(url=payload.url, status=JobStatus.PENDING.value)
    db.add(job)
    db.flush()  # assigns job.id without committing yet

    schedule_job(db, job)

    db.commit()
    db.refresh(job)
    return job


@app.get("/jobs", response_model=list[JobOut])
def list_jobs(
    status: JobStatus | None = Query(default=None, description="Filter by job status"),
    db: Session = Depends(get_db),
):
    query = db.query(Job)
    if status is not None:
        query = query.filter(Job.status == status.value)
    return query.order_by(Job.created_at.desc()).all()


@app.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


# ------------------------------------------------------------------ Nodes

@app.post("/nodes", response_model=NodeOut, status_code=201)
def register_node(payload: NodeRegister, db: Session = Depends(get_db)):
    """
    Register (or re-register) a worker node. Idempotent by design: a
    worker calls this on startup every time, including after a restart,
    so re-registering an existing node.id just resets it to ACTIVE with
    a fresh heartbeat rather than erroring.
    """
    node = db.query(Node).filter(Node.id == payload.id).first()
    if node is None:
        node = Node(id=payload.id, capacity=payload.capacity)
        db.add(node)
    else:
        node.capacity = payload.capacity
        node.status = NodeStatus.ACTIVE.value
        node.last_heartbeat = datetime.utcnow()

    tracker.beat(payload.id) 

    db.commit()
    db.refresh(node)
    return node


@app.get("/nodes", response_model=list[NodeOut])
def list_nodes(db: Session = Depends(get_db)):
    return db.query(Node).order_by(Node.registered_at).all()


@app.get("/nodes/{node_id}", response_model=NodeOut)
def get_node(node_id: str, db: Session = Depends(get_db)):
    node = db.query(Node).filter(Node.id == node_id).first()
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    return node

@app.post("/nodes/{node_id}/heartbeat", response_model=NodeOut)
def send_heartbeat(node_id: str, db: Session = Depends(get_db)):
    """
    Called periodically by each worker (Stage 7) to prove it's alive.
    Resets the Redis/in-memory TTL and, if this node had previously been
    marked DEAD by the reconciler, revives it back to ACTIVE - a worker
    that comes back after a network blip should rejoin the cluster
    automatically, not require manual re-registration.
    """
    node = db.query(Node).filter(Node.id == node_id).first()
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

    tracker.beat(node_id)
    node.last_heartbeat = datetime.utcnow()
    if node.status == NodeStatus.DEAD.value:
        node.status = NodeStatus.ACTIVE.value
    db.commit()
    db.refresh(node)
    return node


@app.post("/reconcile")
def run_reconcile(db: Session = Depends(get_db)):
    """
    Sweeps for nodes whose heartbeat has expired and flags their stuck
    jobs. Meant to be called periodically by scripts/reconciler.py.
    """
    return reconcile(db)