"""
Pluggable bin-packing policies + the scheduling entry point.

Why an ABC instead of just three functions: main.py, the reconciler
(Stage 5), and the test suite all need to pick a policy by name and call
it the same way. An interface makes "add a fourth policy later" a matter
of writing one class and registering it in POLICIES - nothing that calls
schedule_job() has to change.
"""
from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Node, NodeStatus, Job, JobStatus


class SchedulingPolicy(ABC):
    @abstractmethod
    def select_node(self, nodes: list[Node], job_weight: float) -> Node | None:
        """Pick a node for a job needing `job_weight` capacity, or None if no node fits."""
        raise NotImplementedError


class FirstFitPolicy(SchedulingPolicy):
    """Picks the first ACTIVE node (in query order) with enough free capacity.
    Cheapest to compute, but can leave capacity fragmented across the cluster."""

    def select_node(self, nodes, job_weight):
        for node in nodes:
            if node.capacity - node.current_load >= job_weight:
                return node
        return None


class BestFitPolicy(SchedulingPolicy):
    """Picks the ACTIVE node with the smallest sufficient free capacity - packs
    jobs as tightly as possible, minimizing wasted space per node."""

    def select_node(self, nodes, job_weight):
        candidates = [n for n in nodes if n.capacity - n.current_load >= job_weight]
        if not candidates:
            return None
        return min(candidates, key=lambda n: n.capacity - n.current_load)


class FairSharePolicy(SchedulingPolicy):
    """Picks the ACTIVE node with the *most* free capacity - spreads load evenly
    instead of packing tightly, trading utilization density for resilience
    (no single node absorbs a disproportionate share of the queue)."""

    def select_node(self, nodes, job_weight):
        candidates = [n for n in nodes if n.capacity - n.current_load >= job_weight]
        if not candidates:
            return None
        return max(candidates, key=lambda n: n.capacity - n.current_load)


POLICIES: dict[str, SchedulingPolicy] = {
    "first_fit": FirstFitPolicy(),
    "best_fit": BestFitPolicy(),
    "fair_share": FairSharePolicy(),
}


def get_policy(name: str) -> SchedulingPolicy:
    if name not in POLICIES:
        raise ValueError(f"Unknown scheduling policy '{name}'. Options: {list(POLICIES)}")
    return POLICIES[name]


def schedule_job(db: Session, job: Job, weight: float | None = None) -> Node | None:
    """
    Attempts to place `job` on an ACTIVE node with enough spare capacity,
    using the policy configured via DEFAULT_SCHEDULING_POLICY.

    Mutates job and node in place (status, assigned_node_id, current_load)
    but does NOT commit - callers own the transaction boundary, since this
    gets called both from a single-job HTTP request (Stage 3) and from a
    batch reconciliation sweep (Stage 5) where committing once at the end
    matters for consistency.

    Returns the assigned Node, or None if nothing currently fits - in that
    case the job is left/returned to PENDING so a later scheduling pass
    (new node registers, or capacity frees up) can pick it up.
    """
    if weight is None:
        weight = job.weight if job.weight is not None else 10.0
    job.weight = weight

    policy = get_policy(settings.default_scheduling_policy)
    active_nodes = db.query(Node).filter(Node.status == NodeStatus.ACTIVE.value).all()
    node = policy.select_node(active_nodes, weight)

    if node is None:
        job.status = JobStatus.PENDING.value
        job.assigned_node_id = None
        return None

    node.current_load += weight
    job.assigned_node_id = node.id
    job.status = JobStatus.SCHEDULED.value
    job.updated_at = datetime.utcnow()
    return node
