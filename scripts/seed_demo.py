"""Add demo submissions to a running dev server: one approved (a new material), one pending.

    uv run python scripts/seed_demo.py [--url http://localhost:8100]

PLATFORM_JWT_SECRET must match the server's. The data are synthetic and say so."""

from __future__ import annotations

import argparse
import os

import httpx
import numpy as np

from materials_db.auth import make_token


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8100")
    args = p.parse_args()
    secret = os.environ["PLATFORM_JWT_SECRET"]
    user = {"Authorization": f"Bearer {make_token(secret, sub='demo-user', name='Demo User')}"}
    mod = {"Authorization": f"Bearer {make_token(secret, sub='demo-mod', name='Demo Moderator', role='moderator')}"}
    wl = np.linspace(450, 900, 46)
    rows = [[float(w), float(1.45 + 3000 / w**2), 0.0] for w in wl]
    base = {"license": "CC0-1.0", "reference": "Synthetic demo data for development (not a measurement), 2026",
            "comments": "Synthetic Cauchy dispersion for testing the platform."}
    with httpx.Client(base_url=args.url, timeout=30) as c:
        a = c.post("/v1/submissions", headers=user, json={
            **base, "material_name": "Demo polymer", "category": "polymer",
            "dataset_name": "Demo 2026: synthetic Cauchy film", "data": rows}).raise_for_status().json()
        c.post(f"/v1/submissions/{a['id']}/review", headers=mod,
               json={"decision": "approve", "notes": "demo"}).raise_for_status()
        b = c.post("/v1/submissions", headers=user, json={
            **base, "material_id": "sio2", "dataset_name": "Demo 2026: pending synthetic film",
            "data": rows}).raise_for_status().json()
    print("approved:", a["id"], "pending:", b["id"])


if __name__ == "__main__":
    main()
