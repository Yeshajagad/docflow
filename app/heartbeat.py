"""
Heartbeat / liveness tracking.

Why Redis SETEX specifically: TTL-based key expiry means "is this node
alive" is answered by a single EXISTS check with zero manual polling or
cron-style sweeping inside this class - Redis does the expiry bookkeeping
for us. The in-memory fallback exists so local dev (Stage 1's whole
point: SQLite + zero external services) doesn't require standing up
Redis just to test failover logic.

Both implementations share the same interface, so nothing above this
module (main.py, the reconciler) needs to know or care which one is
actually running.
"""
import time
from abc import ABC, abstractmethod

from app.config import settings


class HeartbeatTracker(ABC):
    @abstractmethod
    def beat(self, node_id: str) -> None:
        """Record a fresh heartbeat for node_id, resetting its TTL."""
        raise NotImplementedError

    @abstractmethod
    def is_alive(self, node_id: str) -> bool:
        """True if node_id has beaten within the last heartbeat_ttl_seconds."""
        raise NotImplementedError


class InMemoryHeartbeatTracker(HeartbeatTracker):
    """Local-dev fallback: a dict of node_id -> expiry timestamp."""

    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._expiry: dict[str, float] = {}

    def beat(self, node_id: str) -> None:
        self._expiry[node_id] = time.time() + self.ttl

    def is_alive(self, node_id: str) -> bool:
        expiry = self._expiry.get(node_id)
        return expiry is not None and expiry > time.time()


class RedisHeartbeatTracker(HeartbeatTracker):
    """Production tracker: Redis SETEX does the TTL expiry for us."""

    def __init__(self, redis_url: str, ttl_seconds: int):
        import redis  # imported lazily so redis-py isn't required for local dev
        self.client = redis.from_url(redis_url)
        self.ttl = ttl_seconds

    def _key(self, node_id: str) -> str:
        return f"docflow:heartbeat:{node_id}"

    def beat(self, node_id: str) -> None:
        self.client.setex(self._key(node_id), self.ttl, "1")

    def is_alive(self, node_id: str) -> bool:
        return self.client.exists(self._key(node_id)) == 1


def build_tracker() -> HeartbeatTracker:
    """
    Tries Redis first if REDIS_URL is configured; falls back to in-memory
    if Redis isn't reachable (e.g. REDIS_URL set but the container isn't
    up yet), so a misconfigured Redis never hard-crashes the app - it just
    quietly degrades to single-process heartbeat tracking.
    """
    if settings.redis_url:
        try:
            tracker = RedisHeartbeatTracker(settings.redis_url, settings.heartbeat_ttl_seconds)
            tracker.client.ping()
            return tracker
        except Exception:
            pass
    return InMemoryHeartbeatTracker(settings.heartbeat_ttl_seconds)


# Module-level singleton - every request/reconcile pass shares one tracker
# instance so the in-memory fallback's state is actually shared, not reset
# per-request.
tracker = build_tracker()