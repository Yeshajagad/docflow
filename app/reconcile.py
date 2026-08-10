"""
Stage 4 version: detection only.

Walks every ACTIVE node, asks the heartbeat tracker if it's still alive,
and marks the ones that aren't as DEAD. Jobs stuck on a now-dead node get
flagged with an error message so they're visible via GET /jobs - but
they are NOT yet requeued or retried. That's Stage 5's job.
"""
from sqlalchemy.orm import Session

from app.heartbeat import tracker
from app.models import Node, NodeStatus, Job, JobStatus


def reconcile(db: Session) -> dict:
    dead_node_ids: list[str] = []
    flagged_job_ids: list[str] = []

    active_nodes = db.query(Node).filter(Node.status == NodeStatus.ACTIVE.value).all()
    for node in active_nodes:
        if not tracker.is_alive(node.id):
            node.status = NodeStatus.DEAD.value
            dead_node_ids.append(node.id)

    if dead_node_ids:
        stuck_jobs = db.query(Job).filter(
            Job.assigned_node_id.in_(dead_node_ids),
            Job.status.in_([JobStatus.SCHEDULED.value, JobStatus.RUNNING.value]),
        ).all()
        for job in stuck_jobs:
            job.error = f"Node {job.assigned_node_id} heartbeat expired"
            flagged_job_ids.append(job.id)

    db.commit()
    return {"dead_nodes": dead_node_ids, "flagged_jobs": flagged_job_ids}