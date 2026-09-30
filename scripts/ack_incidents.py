#!/usr/bin/env python3
"""Acknowledge open incidents through the API as an operator, with a reason per cause.

Usage (on the Mac, credentials from the Keychain, never printed):
    scripts/ack_incidents.py --user joe --password "$(security find-generic-password -a joe -s fiboki-operator -w)" \
        --match worker_lease_contended "stale dev-up research worker (pid 48905) from 2026-09-27 held the lease; stopped 2026-09-29 12:21Z; alerts now dedupe per (lease, holder) (3a409db)" \
        --match data_stale "DATA_STALE measured the last bar's age, not the next bar's lateness; fixed 11ddfb9" \
        --match migration_revision "check read alembic_version which V2 never writes; ledger stamps schema_revision since 3a409db"

Goes through the web origin (http://localhost:3000, the /api proxy) so the
session cookie and CSRF header behave exactly as the browser's would.
Prints incident ids and outcomes only.
"""
from __future__ import annotations

import argparse
import sys

import httpx

BASE = "http://localhost:3000"
CSRF_COOKIE = "fiboki_csrf"
CSRF_HEADER = "X-Fiboki-CSRF"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--base", default=BASE)
    ap.add_argument(
        "--match", nargs=2, action="append", metavar=("SUBSTRING", "REASON"), required=True,
        help="acknowledge every open incident whose key/title contains SUBSTRING with REASON",
    )
    args = ap.parse_args()
    # The API refuses state-changing requests without a browser-like Origin.
    with httpx.Client(base_url=args.base, timeout=60.0, headers={"Origin": args.base}) as client:
        r = client.post("/api/auth/login", json={"username": args.user, "password": args.password})
        if r.status_code != 200:
            print(f"login failed: {r.status_code}", file=sys.stderr)
            return 1
        csrf = client.cookies.get(CSRF_COOKIE)
        page = client.get("/api/system/incidents", params={"status": "open", "limit": 200}).json()
        items = page.get("items") or page.get("data") or []
        done = 0
        for inc in items:
            key = f"{inc.get('key', '')} {inc.get('title', '')} {inc.get('event', '')}"
            for needle, reason in args.match:
                if needle in key:
                    resp = client.post(
                        f"/api/system/incidents/{inc['id']}/ack",
                        json={"reason": reason},
                        headers={CSRF_HEADER: csrf or ""},
                    )
                    print(f"{inc['id']}: {inc.get('severity')} {needle} -> {resp.status_code}")
                    done += resp.status_code == 200
                    break
        print(f"acknowledged {done} of {len(items)} open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
