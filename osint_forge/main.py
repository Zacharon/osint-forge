"""CLI entry point for OSINT Forge."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from osint_forge.dashboard.events_api import dashboard_events_response
from osint_forge.governance.pii_sanitizer import PIISanitizer
from osint_forge.ingestion.live_adapters import AdapterError
from osint_forge.ingestion.live_pipeline import (
    LiveIngestionDenied,
    LiveIngestionRequest,
    LiveMapIngestionService,
    load_fixture_fetcher,
)
from osint_forge.ingestion.registry_inference import DirectoryHub, SourceDiscoveryEngine, SourceDiscoveryError
from osint_forge.ingestion.source_registry import SourceRegistry
from osint_forge.orchestrator.mission_store import MissionStore
from osint_forge.orchestrator.mission_supervisor import MissionSupervisor, run_dry_run_mission


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OSINT Forge v3.1+")
    parser.add_argument("--db", default="osint_forge.db")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("dry-run", help="Run a governed dry-run mission")
    run_p.add_argument("--mission", required=True)
    run_p.add_argument("--query", required=True)
    run_p.add_argument("--workspace", default=None)
    run_p.add_argument("--gdelt-fixture-dir", default=None)

    resume_p = sub.add_parser("resume", help="Resume and verify mission state")
    resume_p.add_argument("--mission", required=True)
    resume_p.add_argument("--query", default="")
    resume_p.add_argument("--workspace", default=None)

    report_p = sub.add_parser("report", help="Generate final report after reviews pass")
    report_p.add_argument("--mission", required=True)
    report_p.add_argument("--query", default="")
    report_p.add_argument("--workspace", default=None)

    live_p = sub.add_parser("live-map", help="Run governed v3.2 live map ingestion")
    live_p.add_argument("--mission", required=True)
    live_p.add_argument("--query", required=True)
    live_p.add_argument("--source", default="gdelt")
    live_p.add_argument("--workspace", default=None)
    live_p.add_argument("--source-registry", default=str(Path("config") / "source_registry.json"))
    live_p.add_argument("--max-records", type=int, default=25)
    live_p.add_argument("--live", action="store_true")
    live_p.add_argument("--confirm-live", action="store_true")
    live_p.add_argument("--checklist-approved", action="store_true")
    live_p.add_argument("--gdelt-live-fixture", default=None)
    live_p.add_argument("--capture-sanitized-fixture", default=None)

    events_p = sub.add_parser("events", help="Print sanitized dashboard events")
    events_p.add_argument("--mission", default=None)
    events_p.add_argument("--source", default=None)
    events_p.add_argument("--min-confidence", type=float, default=None)
    events_p.add_argument("--event-type", default=None)
    events_p.add_argument("--time-window-hours", type=float, default=None)
    events_p.add_argument("--limit", type=int, default=500)

    discover_p = sub.add_parser("discover-sources", help="Generate disabled source-registry proposals from an OSINT directory hub")
    discover_p.add_argument("--hub-url", required=True)
    discover_p.add_argument("--hub-id", required=True)
    discover_p.add_argument("--hub-kind", choices=["startme", "osint_framework", "github_list", "generic_directory"], default="generic_directory")
    discover_p.add_argument("--html-file", default=None, help="Use a local captured hub HTML file instead of fetching the URL")
    discover_p.add_argument("--previous-snapshot", default=None, help="Optional previous source graph JSON for daily diff output")

    args = parser.parse_args(argv)
    workspace = Path(args.workspace) if getattr(args, "workspace", None) else Path("workspace") / getattr(args, "mission", "dashboard")

    if args.command == "dry-run":
        result = asyncio.run(run_dry_run_mission(args.mission, args.query, workspace, args.db, args.gdelt_fixture_dir))
    elif args.command == "resume":
        supervisor = MissionSupervisor(args.mission, args.query, workspace_root=workspace, db_path=args.db)
        try:
            result = supervisor.resume()
        finally:
            supervisor.store.close()
    elif args.command == "report":
        supervisor = MissionSupervisor(args.mission, args.query, workspace_root=workspace, db_path=args.db)
        try:
            supervisor.resume()
            result = supervisor.generate_report()
        finally:
            supervisor.store.close()
    elif args.command == "live-map":
        store = MissionStore(args.db)
        store.create_mission(args.mission, args.query, status="live_ingestion")
        fetcher = load_fixture_fetcher(args.gdelt_live_fixture) if args.gdelt_live_fixture else None
        service = LiveMapIngestionService(
            store=store,
            sanitizer=PIISanitizer(use_spacy=False),
            registry=SourceRegistry.load(args.source_registry),
            workspace_root=workspace,
        )
        request = LiveIngestionRequest(
            mission_id=args.mission,
            query=args.query,
            source_id=args.source,
            live=args.live,
            confirm_live=args.confirm_live,
            checklist_approved=args.checklist_approved,
            max_records=args.max_records,
            capture_fixture_path=args.capture_sanitized_fixture,
        )
        try:
            result = asyncio.run(service.run_gdelt(request, fetcher=fetcher)).to_dict()
        except LiveIngestionDenied as exc:
            result = {
                "status": "governance_blocked",
                "source_id": args.source,
                "accepted_events": 0,
                "duplicate_events": 0,
                "blocked_events": 0,
                "event_ids": [],
                "reason": str(exc),
            }
        except AdapterError as exc:
            result = {
                "status": "source_error",
                "source_id": args.source,
                "accepted_events": 0,
                "duplicate_events": 0,
                "blocked_events": 0,
                "event_ids": [],
                "reason": str(exc),
            }
        finally:
            store.close()
    elif args.command == "events":
        store = MissionStore(args.db)
        try:
            result = dashboard_events_response(
                store,
                mission_id=args.mission,
                source_id=args.source,
                min_confidence=args.min_confidence,
                event_type=args.event_type,
                time_window_hours=args.time_window_hours,
                limit=args.limit,
            )
        finally:
            store.close()
    elif args.command == "discover-sources":
        hub = DirectoryHub(hub_id=args.hub_id, url=args.hub_url, kind=args.hub_kind)
        engine = SourceDiscoveryEngine()
        try:
            if args.html_file:
                html = Path(args.html_file).read_text(encoding="utf-8")
                snapshot = engine.discover_from_html(hub, html)
            else:
                snapshot = engine.discover_from_url(hub)
            result = snapshot.to_dict()
            if args.previous_snapshot:
                previous_data = json.loads(Path(args.previous_snapshot).read_text(encoding="utf-8"))
                previous_urls = {str(item["url"]): str(item.get("category") or "") for item in previous_data.get("links", [])}
                current_urls = {link.url: link.category for link in snapshot.links}
                result["diff"] = {
                    "added_urls": sorted(set(current_urls) - set(previous_urls)),
                    "removed_urls": sorted(set(previous_urls) - set(current_urls)),
                    "changed_categories": [
                        {"url": url, "previous_category": previous_urls[url], "current_category": current_urls[url]}
                        for url in sorted(set(previous_urls) & set(current_urls))
                        if previous_urls[url] != current_urls[url]
                    ],
                }
        except SourceDiscoveryError as exc:
            result = {"status": "source_discovery_failed", "hub_id": args.hub_id, "reason": str(exc)}

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
