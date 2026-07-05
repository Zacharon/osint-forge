"""Command-line interface for SQLite-backed human review decisions."""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from osint_forge.orchestrator.mission_store import MissionStore


def _list_pending(store: MissionStore, mission_id: str) -> int:
    store.expire_reviews()
    rows = store.conn.execute(
        """
        SELECT artifact_type, artifact_path, artifact_hash, requested_at, expires_at
        FROM review_requests
        WHERE mission_id = ? AND status = 'PENDING'
        ORDER BY requested_at
        """,
        (mission_id,),
    ).fetchall()
    if not rows:
        print(f"No pending reviews for mission {mission_id}")
        return 0
    for row in rows:
        print(f"{row['artifact_type']} {row['artifact_path']}")
        print(f"  hash: {row['artifact_hash']}")
        print(f"  requested: {datetime.fromtimestamp(row['requested_at'])}")
        print(f"  expires:   {datetime.fromtimestamp(row['expires_at'])}")
    return 0


def _latest_pending_hash(store: MissionStore, mission_id: str, artifact_path: str) -> str | None:
    row = store.conn.execute(
        """
        SELECT artifact_hash FROM review_requests
        WHERE mission_id = ? AND artifact_path = ? AND status = 'PENDING' AND expires_at > ?
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (mission_id, artifact_path, time.time()),
    ).fetchone()
    return row["artifact_hash"] if row else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OSINT Forge Human Review CLI")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--mission", required=True, help="Mission ID")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List pending reviews")
    for command in ("approve", "reject"):
        p = sub.add_parser(command, help=f"{command.title()} a pending artifact review")
        p.add_argument("artifact_path")
        p.add_argument("--artifact-hash", help="Expected artifact hash; defaults to latest pending for path")
        p.add_argument("--reviewer", required=True)
        p.add_argument("--reason", default="")

    args = parser.parse_args(argv)
    store = MissionStore(args.db)
    try:
        if args.command == "list":
            return _list_pending(store, args.mission)

        artifact_hash = args.artifact_hash or _latest_pending_hash(store, args.mission, args.artifact_path)
        if artifact_hash is None:
            print("No live pending review matched that mission and artifact path")
            return 2

        ok = store.update_review_status(
            mission_id=args.mission,
            artifact_path=args.artifact_path,
            artifact_hash=artifact_hash,
            status="APPROVED" if args.command == "approve" else "REJECTED",
            reviewer=args.reviewer,
            reason=args.reason,
            review_method="cli",
        )
        print("Updated review" if ok else "No matching pending, unexpired review was updated")
        return 0 if ok else 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
