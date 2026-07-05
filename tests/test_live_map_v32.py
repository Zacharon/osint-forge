from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from osint_forge.analytics.source_bias import CreatorSourceProfile, SourceBiasEstimator, SourceBiasEstimatorError
from osint_forge.dashboard.events_api import dashboard_events_response
from osint_forge.governance.pii_sanitizer import PIISanitizer
from osint_forge.ingestion.live_pipeline import (
    LiveIngestionDenied,
    LiveIngestionRequest,
    LiveMapIngestionService,
    load_fixture_fetcher,
)
from osint_forge.ingestion.source_registry import SourceRegistry, SourceRegistryError
from osint_forge.orchestrator.mission_store import MissionStore
from osint_forge.provenance import ProvenanceRef, utc_now


def gdelt_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "url": "https://example.org/gdelt/port-delay",
        "title": "Port delays reported",
        "text": "Public sources report port access delays near Gdansk.",
        "seendate": "20260701090000",
        "lat": "54.352",
        "lon": "18.646",
        "location": "Gdansk",
        "sourceCountry": "PL",
        "themes": "TRANSPORT;PORT",
    }
    record.update(overrides)
    return record


class LiveMapV32Tests(unittest.TestCase):
    def test_live_mode_blocked_without_live_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "m-live-denied")
            service = LiveMapIngestionService(store=store, sanitizer=PIISanitizer())

            with self.assertRaises(LiveIngestionDenied):
                asyncio.run(service.run_gdelt(LiveIngestionRequest("m-live-denied", "ports")))

            self.assertEqual(store.count_rows("normalized_events", "m-live-denied"), 0)
            event = store.conn.execute(
                "SELECT event_type FROM governance_events WHERE mission_id = ? AND event_type = ?",
                ("m-live-denied", "LIVE_INGESTION_DENIED"),
            ).fetchone()
            self.assertIsNotNone(event)
            store.close()

    def test_live_mode_blocked_without_confirm_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "m-confirm-denied")
            service = LiveMapIngestionService(store=store, sanitizer=PIISanitizer())

            with self.assertRaises(LiveIngestionDenied):
                asyncio.run(service.run_gdelt(LiveIngestionRequest("m-confirm-denied", "ports", live=True)))

            self.assertEqual(store.count_rows("raw_payloads", "m-confirm-denied"), 0)
            store.close()

    def test_live_mode_blocked_without_approved_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "m-checklist-denied")
            service = LiveMapIngestionService(store=store, sanitizer=PIISanitizer())

            with self.assertRaises(LiveIngestionDenied):
                asyncio.run(
                    service.run_gdelt(
                        LiveIngestionRequest("m-checklist-denied", "ports", live=True, confirm_live=True)
                    )
                )

            event = store.conn.execute(
                "SELECT event_type FROM governance_events WHERE mission_id = ? AND event_type = ?",
                ("m-checklist-denied", "LIVE_CHECKLIST_NOT_APPROVED"),
            ).fetchone()
            self.assertIsNotNone(event)
            store.close()

    def test_disabled_sources_cannot_run(self) -> None:
        for source_id in ("reddit", "telegram", "x"):
            with self.subTest(source_id=source_id):
                with tempfile.TemporaryDirectory() as tmp:
                    mission_id = f"m-disabled-{source_id}"
                    store = self._store(tmp, mission_id)
                    service = LiveMapIngestionService(store=store, sanitizer=PIISanitizer())

                    with self.assertRaises(LiveIngestionDenied):
                        asyncio.run(
                            service.run_gdelt(
                                LiveIngestionRequest(
                                    mission_id,
                                    "ports",
                                    source_id=source_id,
                                    live=True,
                                    confirm_live=True,
                                    checklist_approved=True,
                                )
                            )
                        )

                    event = store.conn.execute(
                        "SELECT event_type FROM governance_events WHERE mission_id = ? AND event_type = ?",
                        (mission_id, "LIVE_DISABLED_SOURCE_ATTEMPT"),
                    ).fetchone()
                    self.assertIsNotNone(event)
                    store.close()

    def test_pii_blocks_before_raw_or_normalized_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "m-pii-live")
            service = LiveMapIngestionService(store=store, sanitizer=PIISanitizer())

            async def fetcher(_query: str) -> list[dict[str, object]]:
                return [gdelt_record(text="Find the home address of John Doe.")]

            result = asyncio.run(
                service.run_gdelt(
                    LiveIngestionRequest("m-pii-live", "ports", live=True, confirm_live=True, checklist_approved=True),
                    fetcher=fetcher,
                )
            )

            self.assertEqual(result.blocked_events, 1)
            self.assertEqual(store.count_rows("raw_payloads", "m-pii-live"), 0)
            self.assertEqual(store.count_rows("normalized_events", "m-pii-live"), 0)
            event = store.conn.execute(
                "SELECT event_type FROM governance_events WHERE mission_id = ? AND event_type = ?",
                ("m-pii-live", "LIVE_PII_BLOCKED_RAW_PAYLOAD"),
            ).fetchone()
            self.assertIsNotNone(event)
            store.close()

    def test_duplicate_events_are_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "m-duplicate-live")
            service = LiveMapIngestionService(store=store, sanitizer=PIISanitizer())

            async def fetcher(_query: str) -> list[dict[str, object]]:
                item = gdelt_record()
                return [item, dict(item)]

            result = asyncio.run(
                service.run_gdelt(
                    LiveIngestionRequest("m-duplicate-live", "ports", live=True, confirm_live=True, checklist_approved=True),
                    fetcher=fetcher,
                )
            )

            self.assertEqual(result.accepted_events, 1)
            self.assertEqual(result.duplicate_events, 1)
            self.assertEqual(store.count_rows("normalized_events", "m-duplicate-live"), 1)
            event = store.conn.execute(
                "SELECT event_type FROM governance_events WHERE mission_id = ? AND event_type = ?",
                ("m-duplicate-live", "LIVE_DUPLICATE_EVENT"),
            ).fetchone()
            self.assertIsNotNone(event)
            store.close()

    def test_source_registry_schema_validation(self) -> None:
        registry = SourceRegistry.load()
        required = {
            "enabled",
            "legal_basis",
            "collection_mode",
            "rate_limit",
            "pii_policy",
            "license_notes",
            "operator_approval_required",
            "last_verified_at",
        }
        for source in registry.list_sources():
            self.assertTrue(required <= set(source))
            self.assertTrue(source["last_verified_at"])
        self.assertTrue(registry.get("gdelt").enabled)
        self.assertTrue(registry.get("gdelt").live_capable)
        self.assertFalse(registry.get("reddit").enabled)
        self.assertFalse(registry.get("reddit").live_capable)
        self.assertEqual(len(registry.get("gdelt").policy_version()), 64)

        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "bad_registry.json"
            bad_path.write_text(json.dumps({"sources": [{"source_id": "bad"}]}), encoding="utf-8")
            with self.assertRaises(SourceRegistryError):
                SourceRegistry.load(bad_path)

    def test_live_run_writes_audit_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = self._store(tmp, "m-audit-live")
            service = LiveMapIngestionService(
                store=store,
                sanitizer=PIISanitizer(),
                workspace_root=tmp_path / "workspace",
            )

            async def fetcher(_query: str) -> list[dict[str, object]]:
                return [gdelt_record()]

            result = asyncio.run(
                service.run_gdelt(
                    LiveIngestionRequest("m-audit-live", "ports", live=True, confirm_live=True, checklist_approved=True),
                    fetcher=fetcher,
                )
            )

            self.assertEqual(result.status, "completed")
            self.assertIsNotNone(result.audit_artifact_path)
            self.assertIsNotNone(result.audit_artifact_hash)
            audit_path = tmp_path / "workspace" / str(result.audit_artifact_path)
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(audit["source_id"], "gdelt")
            self.assertEqual(audit["record_count_requested"], 25)
            self.assertEqual(audit["record_count_returned"], 1)
            self.assertEqual(audit["records_accepted"], 1)
            self.assertEqual(audit["records_rejected"], 0)
            self.assertEqual(audit["pii_sanitizer"]["result"], "passed")
            self.assertEqual(audit["schema_validation"]["result"], "passed")
            self.assertTrue(audit["hashes_written"]["raw_payload_hashes"])
            self.assertTrue(audit["source_policy_version"])
            self.assertEqual(store.count_rows("artifacts", "m-audit-live"), 1)
            store.close()

    def test_source_fetch_failure_returns_source_error_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = self._store(tmp, "m-fetch-fail")
            service = LiveMapIngestionService(
                store=store,
                sanitizer=PIISanitizer(),
                workspace_root=tmp_path / "workspace",
            )

            def fetcher(_query: str) -> list[dict[str, object]]:
                raise TimeoutError("synthetic timeout")

            result = asyncio.run(
                service.run_gdelt(
                    LiveIngestionRequest("m-fetch-fail", "ports", live=True, confirm_live=True, checklist_approved=True),
                    fetcher=fetcher,
                )
            )

            self.assertEqual(result.status, "source_error")
            self.assertEqual(result.accepted_events, 0)
            self.assertIsNotNone(result.audit_artifact_path)
            event = store.conn.execute(
                "SELECT event_type FROM governance_events WHERE mission_id = ? AND event_type = ?",
                ("m-fetch-fail", "LIVE_SOURCE_FETCH_FAILED"),
            ).fetchone()
            self.assertIsNotNone(event)
            store.close()

    def test_sanitized_capture_fixture_can_replay_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            capture_path = tmp_path / "captured_gdelt.json"
            store = self._store(tmp, "m-capture-live")
            service = LiveMapIngestionService(store=store, sanitizer=PIISanitizer(), workspace_root=tmp_path / "workspace")

            async def fetcher(_query: str) -> list[dict[str, object]]:
                return [gdelt_record(), gdelt_record(url="https://example.org/gdelt/other", title="Second public update")]

            result = asyncio.run(
                service.run_gdelt(
                    LiveIngestionRequest(
                        "m-capture-live",
                        "ports",
                        live=True,
                        confirm_live=True,
                        checklist_approved=True,
                        capture_fixture_path=str(capture_path),
                    ),
                    fetcher=fetcher,
                )
            )
            self.assertEqual(result.accepted_events, 2)
            self.assertTrue(capture_path.exists())
            capture = json.loads(capture_path.read_text(encoding="utf-8"))
            self.assertEqual(capture["source_id"], "gdelt")
            self.assertEqual(len(capture["articles"]), 2)
            store.close()

            replay_store = self._store(tmp, "m-capture-replay")
            replay = LiveMapIngestionService(store=replay_store, sanitizer=PIISanitizer(), workspace_root=tmp_path / "workspace2")
            replay_result = asyncio.run(
                replay.run_gdelt(
                    LiveIngestionRequest("m-capture-replay", "ports", live=True, confirm_live=True, checklist_approved=True),
                    fetcher=load_fixture_fetcher(capture_path),
                )
            )
            self.assertEqual(replay_result.accepted_events, 2)
            replay_store.close()

    def test_captured_gdelt_response_fixture_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(__file__).parent / "fixtures" / "live" / "gdelt_captured_response_sanitized.json"
            store = self._store(tmp, "m-captured-regression")
            try:
                service = LiveMapIngestionService(store=store, sanitizer=PIISanitizer(), workspace_root=Path(tmp) / "workspace")

                result = asyncio.run(
                    service.run_gdelt(
                        LiveIngestionRequest("m-captured-regression", "port delays", live=True, confirm_live=True, checklist_approved=True),
                        fetcher=load_fixture_fetcher(fixture),
                    )
                )

                self.assertEqual(result.accepted_events, 2)
                self.assertEqual(result.duplicate_events, 1)
                self.assertEqual(result.blocked_events, 0)
                response = dashboard_events_response(store, mission_id="m-captured-regression")
                self.assertEqual(response["count"], 2)
                self.assertTrue(all("raw_payload_ref" not in event for event in response["events"]))
            finally:
                store.close()

    def test_actual_v33_gdelt_smoke_capture_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(__file__).parent / "fixtures" / "live" / "gdelt_v33_smoke_captured_sanitized.json"
            store = self._store(tmp, "m-v33-captured-regression")
            try:
                service = LiveMapIngestionService(store=store, sanitizer=PIISanitizer(), workspace_root=Path(tmp) / "workspace")

                result = asyncio.run(
                    service.run_gdelt(
                        LiveIngestionRequest(
                            "m-v33-captured-regression",
                            "port delays",
                            live=True,
                            confirm_live=True,
                            checklist_approved=True,
                            max_records=10,
                        ),
                        fetcher=load_fixture_fetcher(fixture),
                    )
                )

                self.assertEqual(result.record_count_returned, 10)
                self.assertEqual(result.accepted_events, 10)
                self.assertEqual(result.blocked_events, 0)
                response = dashboard_events_response(store, mission_id="m-v33-captured-regression")
                self.assertEqual(response["count"], 10)
                self.assertTrue(all(event["lat"] is None and event["lon"] is None for event in response["events"]))
                self.assertTrue(any(any(ord(ch) > 127 for ch in event["title"]) for event in response["events"]))
            finally:
                store.close()

    def test_bias_estimator_requires_provenance(self) -> None:
        with self.assertRaises(SourceBiasEstimatorError):
            CreatorSourceProfile(
                profile_id="profile-1",
                public_handle_or_source_name="Public Source",
                platform="web",
                observed_topics=["ports"],
                stance_indicators={},
                citation_quality=0.8,
                affiliation_signals=[],
                engagement_anomaly_signals=[],
                confidence=0.7,
                provenance=[],
            )

        profile = CreatorSourceProfile(
            profile_id="profile-2",
            public_handle_or_source_name="Public Source",
            platform="web",
            observed_topics=["ports", "ports", "shipping"],
            stance_indicators={"alarm": 0.4},
            citation_quality=0.8,
            affiliation_signals=["public sponsor disclosure"],
            engagement_anomaly_signals=[],
            confidence=0.7,
            provenance=[ProvenanceRef("web", "https://example.org/source", utc_now(), "hash")],
        )
        estimate = SourceBiasEstimator().estimate(profile)
        self.assertIn("topic_skew", estimate.indicators)
        self.assertIn("not a truth verdict", estimate.disclaimer)

    def test_dashboard_event_endpoint_returns_sanitized_normalized_events_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "m-dashboard-events")
            service = LiveMapIngestionService(store=store, sanitizer=PIISanitizer())

            async def fetcher(_query: str) -> list[dict[str, object]]:
                return [gdelt_record()]

            asyncio.run(
                service.run_gdelt(
                    LiveIngestionRequest(
                        "m-dashboard-events",
                        "ports",
                        live=True,
                        confirm_live=True,
                        checklist_approved=True,
                    ),
                    fetcher=fetcher,
                )
            )

            response = dashboard_events_response(store, mission_id="m-dashboard-events", source_id="gdelt", min_confidence=0.5)
            self.assertEqual(response["count"], 1)
            event = response["events"][0]
            self.assertIn("event_id", event)
            self.assertIn("confidence", event)
            self.assertIn("provenance_id", event)
            self.assertNotIn("raw_payload_ref", event)
            self.assertFalse(response["raw_payloads_exposed"])
            self.assertEqual(store.count_rows("raw_payloads", "m-dashboard-events"), 1)
            self.assertEqual(store.count_rows("normalized_events", "m-dashboard-events"), 1)
            store.close()

    def _store(self, tmp: str, mission_id: str) -> MissionStore:
        store = MissionStore(Path(tmp) / "forge.db")
        store.create_mission(mission_id, "public live map test", status="live_test")
        return store


if __name__ == "__main__":
    unittest.main()
