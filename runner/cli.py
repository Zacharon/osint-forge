"""CLI for the minimal governed runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runner.approval import DECISIONS, GovernedReviewPromoter, ReviewDecisionError
from runner.harness import GovernedSkillRunner, RunnerError


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "approve":
        return _approve_main(argv[1:])

    parser = argparse.ArgumentParser(description="Minimal governed runner for OSINT Forge skill dry runs")
    parser.add_argument("--skill", required=True, help="Path to the skill markdown file")
    parser.add_argument("--project-root", default=".", help="Project root containing runs/, schemas/, and memory_checkpoints/")
    parser.add_argument(
        "--allow-live-ingestion",
        action="store_true",
        help="Accepted for future compatibility, but dry-run governance still keeps live ingestion disabled.",
    )
    args = parser.parse_args(argv)

    try:
        runner = GovernedSkillRunner(Path(args.project_root))
        result = runner.run(args.skill, allow_live_ingestion=args.allow_live_ingestion)
    except RunnerError as exc:
        print(f"runner error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


def _approve_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Promote a reviewed dry run into a governed execution plan artifact")
    parser.add_argument("--run-id", required=True, help="Run ID under runs/ to review")
    parser.add_argument("--project-root", default=".", help="Project root containing runs/ and schemas/")
    parser.add_argument(
        "--decision",
        choices=DECISIONS,
        default="approved",
        help="Review decision to record. Approved creates a governed execution plan artifact.",
    )
    parser.add_argument("--reviewer", default="human", help="Reviewer name to record in the decision artifact")
    parser.add_argument("--note", default="", help="Decision note or requested change")
    args = parser.parse_args(argv)

    try:
        promoter = GovernedReviewPromoter(Path(args.project_root))
        result = promoter.decide(args.run_id, decision=args.decision, reviewer=args.reviewer, note=args.note)
    except ReviewDecisionError as exc:
        print(f"runner error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0
