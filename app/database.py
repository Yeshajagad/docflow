"""
DB session/engine setup.

Why this file is separate from models.py: engine creation needs
connection-string-specific args (SQLite needs `check_same_thread=False`
because FastAPI can hit it from different threads; Postgres doesn't).
Keeping that branching here means models.py stays pure schema, and
main.py never has to know which database it's talking to.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a session, always closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create tables if they don't exist. Called once at app startup."""
    # Import models here (not at module top) so they register on Base.metadata
    # before create_all runs, without creating a circular import with models.py.
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
