"""
Job / Node state: SQLAlchemy models (DB truth) + Pydantic schemas (API contracts).

Design decisions worth knowing:

1. Status is a plain string column, not a DB-native enum. SQLite doesn't
   enforce Postgres-style enums anyway, and a string column means adding
   a new status later (e.g. RETRYING) is a one-line change, not a migration
   against a native enum type. We still get validation via the Python
   JobStatus enum used everywhere in application code.

2. `weight` lives on both Job (estimated cost) and Node (capacity +
   current_load) because the whole point of Stage 3's bin-packing is
   comparing "how much room is left on this node" against "how much does
   this job need" - that comparison is meaningless without both sides
   using the same unit.

3. `result` is a JSON column, not a separate table. Extraction output
   (Stage 6) is a variable-shape dict per filing - forcing it into
   relational columns would mean a schema migration every time we extract
   a new field. JSON here trades queryability for flexibility, which is
   the right trade for "structured output blob attached to a job."
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Float, DateTime, JSON
from sqlalchemy.orm import Mapped
from pydantic import BaseModel, Field, ConfigDict

from app.database import Base


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"       # created, not yet placed on a node
    SCHEDULED = "SCHEDULED"   # assigned to a node, not started
    RUNNING = "RUNNING"       # worker picked it up
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"         # dead-lettered after MAX_RETRIES


class NodeStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DEAD = "DEAD"             # heartbeat expired, reconciler flagged it


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------- ORM models

class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = Column(String, primary_key=True, default=_uuid)
    url: Mapped[str] = Column(String, nullable=False)
    status: Mapped[str] = Column(String, nullable=False, default=JobStatus.PENDING.value)

    weight: Mapped[float] = Column(Float, nullable=True)          # set by complexity.py (Stage 6)
    assigned_node_id: Mapped[str] = Column(String, nullable=True)

    retries: Mapped[int] = Column(Integer, nullable=False, default=0)
    result: Mapped[dict] = Column(JSON, nullable=True)
    error: Mapped[str] = Column(String, nullable=True)

    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = Column(String, primary_key=True)  # human-set NODE_ID, e.g. "node-1"
    status: Mapped[str] = Column(String, nullable=False, default=NodeStatus.ACTIVE.value)

    capacity: Mapped[float] = Column(Float, nullable=False, default=100.0)
    current_load: Mapped[float] = Column(Float, nullable=False, default=0.0)

    last_heartbeat: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    registered_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------- Pydantic schemas
# These are the API's request/response contracts. Kept separate from the ORM
# models above (rather than reusing them directly) so the API shape can stay
# stable even if we change a DB column name or add an internal-only field.

class JobCreate(BaseModel):
    url: str = Field(..., description="Public SEC EDGAR filing URL to extract")


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    url: str
    status: str
    weight: float | None = None
    assigned_node_id: str | None = None
    retries: int
    result: dict | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class NodeRegister(BaseModel):
    id: str = Field(..., description="Unique node identifier, e.g. 'node-1'")
    capacity: float = Field(default=100.0, description="Total resource weight this node can hold")


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    capacity: float
    current_load: float
    last_heartbeat: datetime
    registered_at: datetime


class JobResultUpdate(BaseModel):
    """Sent by a worker via PATCH /jobs/{id}/result when it finishes (Stage 7)."""
    status: JobStatus
    result: dict | None = None
    error: str | None = None
