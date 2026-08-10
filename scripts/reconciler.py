"""
Background failure-detection sweep. Runs as its own process (or its own
container in docker-compose) so a control-plane restart doesn't pause
failure detection, and so RECONCILE_INTERVAL_SECONDS can be tuned
independently of request-handling load.

Usage:
    python -m scripts.reconciler
"""
import time

import requests

from app.config import settings

CONTROL_PLANE_URL = "http://localhost:8000"  # override below for docker-compose


def run_forever(base_url: str = CONTROL_PLANE_URL, interval: int | None = None):
    interval = interval or settings.reconcile_interval_seconds
    print(f"[reconciler] polling {base_url}/reconcile every {interval}s")

    while True:
        try:
            resp = requests.post(f"{base_url}/reconcile", timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if result["dead_nodes"] or result["requeued"] or result["rescheduled"]:
                print(f"[reconciler] {result}")
        except requests.RequestException as e:
            print(f"[reconciler] control plane unreachable: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    import os
    # In docker-compose, the control plane is reachable by service name,
    # not localhost - CONTROL_PLANE_URL env var overrides the default.
    url = os.environ.get("CONTROL_PLANE_URL", CONTROL_PLANE_URL)
    run_forever(base_url=url)