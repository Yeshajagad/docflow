"""
Stage 5 version: detection + retry + failover.

Extends Stage 4's dead-node detection with the actual recovery path:

1. DETECT   - unchanged from Stage 4: nodes whose heartbeat expired get
              marked DEAD.
2. REQUEUE  - jobs stuck on a dead node go back to PENDING with retries+1
              and an exponential backoff delay (next_retry_at), UNLESS
              they've already exhausted MAX_RETRIES, in which case they
              are dead-lettered (status=FAILED) instead of retried again.
3. RESCHEDULE - any PENDING job whose backoff has elapsed (next_retry_at
              is null or in the past) gets run back through the *same*
              schedule_job() used for brand-new jobs. This isn't a
              separate "recovery" code path - a requeued job is just a
              PENDING job again, and PENDING jobs get scheduled the same
              way regardless of whether they're new or recovering. That
              reuse is what makes failover "automatic" rather than a
              special case to maintain.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.heartbeat import tracker
from app.models import Node, NodeStatus, Job, JobStatus
from app.scheduler import schedule_job

BACKOFF_BASE_SECONDS = 5  # backoff = BASE * 2^retries, e.g. 10s, 20s, 40s...


def reconcile(db: Session) -> dict:
    now = datetime.utcnow()

    # ---- 1. Detect dead nodes ----
    dead_node_ids: list[str] = []
    active_nodes = db.query(Node).filter(Node.status == NodeStatus.ACTIVE.value).all()
    for node in active_nodes:
        if not tracker.is_alive(node.id):
            node.status = NodeStatus.DEAD.value
            node.current_load = 0.0  # its jobs are being requeued below; free the accounting
            dead_node_ids.append(node.id)

    # ---- 2. Requeue or dead-letter jobs stuck on those dead nodes ----
    requeued_job_ids: list[str] = []
    dead_lettered_job_ids: list[str] = []
    if dead_node_ids:
        stuck_jobs = db.query(Job).filter(
            Job.assigned_node_id.in_(dead_node_ids),
            Job.status.in_([JobStatus.SCHEDULED.value, JobStatus.RUNNING.value]),
        ).all()
        for job in stuck_jobs:
            dead_node_id = job.assigned_node_id
            if job.retries >= settings.max_retries:
                job.status = JobStatus.FAILED.value
                job.error = (
                    f"Node {dead_node_id} died; "
                    f"max retries ({settings.max_retries}) exhausted"
                )
                job.assigned_node_id = None
                dead_lettered_job_ids.append(job.id)
            else:
                job.retries += 1
                backoff = BACKOFF_BASE_SECONDS * (2 ** job.retries)
                job.next_retry_at = now + timedelta(seconds=backoff)
                job.status = JobStatus.PENDING.value
                job.assigned_node_id = None
                job.error = f"Node {dead_node_id} died; retry {job.retries}/{settings.max_retries} in {backoff}s"
                requeued_job_ids.append(job.id)

    # ---- 3. Reschedule any PENDING job whose backoff has elapsed ----
    rescheduled_job_ids: list[str] = []
    ready_jobs = (
        db.query(Job)
        .filter(Job.status == JobStatus.PENDING.value)
        .filter((Job.next_retry_at.is_(None)) | (Job.next_retry_at <= now))
        .all()
    )
    for job in ready_jobs:
        node = schedule_job(db, job)  # same code path as a brand-new job
        if node is not None:
            rescheduled_job_ids.append(job.id)

    db.commit()
    return {
        "dead_nodes": dead_node_ids,
        "requeued": requeued_job_ids,
        "dead_lettered": dead_lettered_job_ids,
        "rescheduled": rescheduled_job_ids,
    }