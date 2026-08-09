"""
Stage 1 stub: just enough to prove the data layer boots correctly.
Real endpoints (POST /jobs, node registration, etc.) land in Stage 2 -
this file gets replaced, not appended to, when we get there.
"""
from fastapi import FastAPI

from app.database import init_db

app = FastAPI(title="DocFlow", version="0.1.0")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok", "stage": 1}
