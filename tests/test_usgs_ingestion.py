from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from osint_forge.api import ForgeApi, events_feed
from osint_forge.ingestion import usgs
from osint_forge.ingestion.source_registry import SourceRegistry, SourceRegistryError
from osint_forge.ingestion.usgs import UsgsFeedError, ingest_usgs_feed, severity_from_magnitude, usgs_source_url
from osint_forge.ontology import SQLiteOntologyStore

FIXTURE = Path(__file__).parent / "fixtures" / "usgs" / "summary_feed.json"
RETRIEVED = "2026-10-18T06:00:00Z"
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def feed() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def feature(data: dict, feature_id: str) -> dict:
    return next(f for f in data["features"] if f.get("id") == feature_id)


class UsgsIngestionTests(unittest.TestCase):
    def make_store(self):
        return SQLiteOntologyStore(":memory:")

    def setUp(self) -> None:
        self.store = self.make_store()

    def tearDown(self) -> None:
        self.store.close()

    def test_first_load_creates_events_and_rejects_bad_features(self) -> None:
        summary = ingest_usgs_feed(self.store, feed(), retrieved_at=RETRIEVED)
        self.assertEqual((summary.created, summary.revised, summary.unchanged), (4, 0, 0))
        self.assertEqual(
            sorted(r["reason"] for r in summary.rejected),
            ["missing-coordinates", "missing-stable-id"],
        )
        quake = self.store.get_object("event:usgs-us7000fx01")
        self.assertEqual((quake.lat, quake.lon, quake.valid_from), (26.84, 56.42, "2026-10-18T03:12:00.000Z"))
        self.assertEqual(quake.properties["verification_status"], "primary-confirmed")
        self.assertEqual(quake.properties["country_name"], "Iran")
        self.assertEqual(quake.properties["severity"], 61)
        self.assertEqual(quake.provenance[0].locator, "usgs:us7000fx01")

    def test_reload_is_a_no_op_and_corrections_revise(self) -> None:
        ingest_usgs_feed(self.store, feed(), retrieved_at=RETRIEVED)
        again = ingest_usgs_feed(self.store, feed(), retrieved_at="2026-10-18T07:00:00Z")
        self.assertEqual((again.created, again.revised, again.unchanged), (0, 0, 4))

        corrected = feed()
        f = feature(corrected, "ak0fx02")
        f["properties"].update({"mag": 3.2, "status": "reviewed", "updated": 1792309999000})
        result = ingest_usgs_feed(self.store, corrected, retrieved_at="2026-10-18T08:00:00Z")
        self.assertEqual((result.revised, result.unchanged), (1, 3))
        history = self.store.object_history("event:usgs-ak0fx02")
        self.assertEqual([h["object"]["properties"]["magnitude"] for h in history], [2.9, 3.2])

    def test_out_of_order_correction_is_ignored(self) -> None:
        newer = feed()
        feature(newer, "us7000fx03")["properties"].update({"mag": 4.8, "updated": 1792399999000})
        ingest_usgs_feed(self.store, newer, retrieved_at=RETRIEVED)
        result = ingest_usgs_feed(self.store, feed(), retrieved_at="2026-10-18T09:00:00Z")
        self.assertEqual(result.stale, 1)
        self.assertEqual(self.store.get_object("event:usgs-us7000fx03").properties["magnitude"], 4.8)

    def test_deleted_feature_is_withdrawn_from_the_feed_but_kept_in_history(self) -> None:
        ingest_usgs_feed(self.store, feed(), retrieved_at=RETRIEVED)
        deleted = feed()
        f = feature(deleted, "us7000fx01")
        f["properties"].update({"status": "deleted", "updated": 1792390000000})
        f["geometry"] = None
        result = ingest_usgs_feed(self.store, deleted, retrieved_at="2026-10-18T10:00:00Z")
        self.assertEqual(result.withdrawn, 1)
        obj = self.store.get_object("event:usgs-us7000fx01")
        self.assertTrue(obj.properties["withdrawn"])
        self.assertEqual(len(self.store.object_history("event:usgs-us7000fx01")), 2)
        ids = [e["id"] for e in events_feed(self.store, {}, now=NOW)["canonicalEvents"]]
        self.assertNotIn("event:usgs-us7000fx01", ids)
        repeat = ingest_usgs_feed(self.store, deleted, retrieved_at="2026-10-18T11:00:00Z")
        self.assertEqual(repeat.withdrawn, 0)

    def test_feed_projection_matches_live_map_usgs_semantics(self) -> None:
        ingest_usgs_feed(self.store, feed(), retrieved_at=RETRIEVED)
        events = {e["id"]: e for e in events_feed(self.store, {}, now=NOW)["canonicalEvents"]}
        self.assertEqual(len(events), 4)
        quake = events["event:usgs-us7000fx01"]
        self.assertEqual((quake["provider"], quake["category"], quake["domain"]), ("usgs", "earthquake", "natural-disaster"))
        self.assertEqual((quake["confidence"], quake["severityLabel"]), (96.0, "moderate"))
        self.assertEqual(quake["sourceUrl"], "https://earthquake.usgs.gov/earthquakes/eventpage/us7000fx01")
        self.assertEqual(events["event:usgs-ak0fx02"]["sourceUrl"], usgs.USGS_LANDING_URL)
        self.assertEqual(events["event:usgs-ak0fx02"]["confidence"], 88.0)
        null_island = events["event:usgs-nc7000fx06"]
        self.assertEqual((null_island["latitude"], null_island["longitude"], null_island["geographic"]), (0.0, 0.0, True))
        domain = events_feed(self.store, {"domain": "natural-disaster"}, now=NOW)
        self.assertEqual(len(domain["canonicalEvents"]), 4)

    def test_invalid_feeds_are_refused(self) -> None:
        with self.assertRaises(UsgsFeedError):
            ingest_usgs_feed(self.store, {"type": "Feature"})
        with self.assertRaises(UsgsFeedError):
            ingest_usgs_feed(self.store, {"type": "FeatureCollection", "features": [{}] * (usgs.MAX_FEATURES + 1)})
        with self.assertRaises(UsgsFeedError):
            ingest_usgs_feed(self.store, {"type": "FeatureCollection", "features": [{"id": "x"}]})
        empty = ingest_usgs_feed(self.store, {"type": "FeatureCollection", "features": []})
        self.assertEqual(empty.created, 0)

    def test_helpers(self) -> None:
        self.assertEqual(severity_from_magnitude(None), 15)
        self.assertEqual(severity_from_magnitude(9.5), 100)
        self.assertEqual(usgs_source_url("https://user:pw@earthquake.usgs.gov/x"), usgs.USGS_LANDING_URL)
        self.assertEqual(usgs_source_url("http://earthquake.usgs.gov/x"), usgs.USGS_LANDING_URL)
        with self.assertRaises(UsgsFeedError):
            usgs.fetch_usgs_feed("https://example.com/feed.geojson")

    def test_latest_withdrawal_prevents_superseded_active_record_resurfacing(self) -> None:
        original = copy.deepcopy(feed()["features"][0])
        def load(item):
            return ingest_usgs_feed(self.store, {"type": "FeatureCollection", "features": [item]}, retrieved_at=RETRIEVED)
        load(original)
        deleted = copy.deepcopy(original)
        deleted["properties"].update(status="deleted", updated=original["properties"]["updated"] + 10000)
        deleted["geometry"] = None
        load(deleted)
        deleted["properties"]["updated"] += 20000
        self.assertEqual(load(deleted).withdrawn, 1)
        history = self.store.object_history("event:usgs-" + original["id"])
        self.assertEqual(len(history), 3)
        self.assertEqual(load(deleted).unchanged, 1)
        superseded = copy.deepcopy(original)
        superseded["properties"]["updated"] += 20000
        self.assertEqual(load(superseded).stale, 1)
        self.assertTrue(self.store.get_object("event:usgs-" + original["id"]).properties["withdrawn"])
        self.assertEqual(events_feed(self.store, now=NOW)["canonicalEvents"], [])
        # A genuinely newer source restoration is allowed under the same identity.
        restored = copy.deepcopy(original)
        restored["properties"]["updated"] += 40000
        self.assertEqual(load(restored).revised, 1)
        self.assertEqual(len(self.store.object_history("event:usgs-" + original["id"])), 4)

    def test_missing_update_cannot_bypass_source_ordering(self) -> None:
        ingest_usgs_feed(self.store, feed(), retrieved_at=RETRIEVED)
        before = self.store.object_history("event:usgs-us7000fx01")
        for status in ("reviewed", "deleted"):
            for invalid in (None, "not-a-time"):
                item = copy.deepcopy(feed()["features"][0])
                item["properties"].update(status=status, updated=invalid, mag=9.0)
                with self.assertRaises(UsgsFeedError):
                    ingest_usgs_feed(self.store, {"type": "FeatureCollection", "features": [item]}, retrieved_at=RETRIEVED)
                self.assertEqual(self.store.object_history("event:usgs-us7000fx01"), before)

    def test_api_preserves_allowlisted_measurements_and_source_identity(self) -> None:
        ingest_usgs_feed(self.store, feed(), retrieved_at=RETRIEVED)
        stored = self.store.get_object("event:usgs-us7000fx01")
        stored.properties["raw_payload"] = {"internal_fixture_note": "must not be projected"}
        self.store.upsert_object(stored)
        envelope = events_feed(self.store, now=NOW)
        for records in (envelope["canonicalEvents"], envelope["events"]):
            quake = next(e for e in records if e["id"] == "event:usgs-us7000fx01")
            self.assertEqual(quake["metadata"]["usgsId"], "us7000fx01")
            self.assertEqual(quake["metadata"]["magnitude"], 5.8)
            self.assertEqual(quake["metadata"]["depthKm"], 10.0)
            self.assertFalse(quake["metadata"]["tsunami"])
            self.assertEqual(quake["metadata"]["feltReports"], 12)
            self.assertEqual(quake["metadata"]["rawStatus"], "reviewed")
            self.assertNotIn("raw_payload", quake["metadata"])

    def test_missing_summary_member_is_not_a_withdrawal(self) -> None:
        ingest_usgs_feed(self.store, feed(), retrieved_at=RETRIEVED)
        ingest_usgs_feed(self.store, {"type": "FeatureCollection", "features": []}, retrieved_at=RETRIEVED)
        self.assertEqual(len(events_feed(self.store, now=NOW)["canonicalEvents"]), 4)


@unittest.skipUnless(os.environ.get("FORGE_TEST_DATABASE_URL"), "set FORGE_TEST_DATABASE_URL to run PostgreSQL tests")
class PostgresUsgsIngestionTests(UsgsIngestionTests):
    def make_store(self):
        import psycopg
        from osint_forge.ontology import PostgresOntologyStore

        base = os.environ["FORGE_TEST_DATABASE_URL"]
        self.schema = "forge_test_" + uuid.uuid4().hex[:12]
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f"CREATE SCHEMA {self.schema}")
        self.addCleanup(self.drop_schema)
        sep = "&" if "?" in base else "?"
        return PostgresOntologyStore(f"{base}{sep}options=-csearch_path%3D{self.schema}")

    def drop_schema(self):
        import psycopg
        with psycopg.connect(os.environ["FORGE_TEST_DATABASE_URL"], autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {self.schema} CASCADE")


class UsgsPersistedApiTests(unittest.TestCase):
    def test_fixture_cli_to_reopened_store_to_wsgi_contract(self) -> None:
        # Deterministic fixture only: neither live fetching nor hosted integration.
        from tests.test_api import load_fixture, type_errors

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "fixture.sqlite")
            with mock.patch("builtins.print"):
                usgs.main(["--db", path, "--file", str(FIXTURE)])
            store = SQLiteOntologyStore(path)
            try:
                app = ForgeApi(store)
                response = {}
                def start(status, headers):
                    response.update(status=status, headers=dict(headers))
                # Freeze only the projection clock so saved fixture events never age out.
                with mock.patch("osint_forge.api.app.events_feed", side_effect=lambda s, q: events_feed(s, q, now=NOW)):
                    body = json.loads(b"".join(app({"PATH_INFO": "/api/events", "REQUEST_METHOD": "GET"}, start)))
                self.assertEqual(response["status"], "200 OK")
                self.assertEqual(response["headers"]["Access-Control-Allow-Origin"], "*")
                schema = load_fixture()["schema"]
                errors = type_errors(body, schema["envelope"], "envelope")
                for key, kind in (("canonicalEvents", "canonicalEvent"), ("events", "legacyEvent")):
                    for item in body[key]:
                        errors += type_errors(item, schema[kind], item["id"])
                self.assertEqual(errors, [])
                self.assertEqual(len(body["canonicalEvents"]), 4)
                canonical = {e["id"]: e for e in body["canonicalEvents"]}
                legacy = {e["id"]: e for e in body["events"]}
                for source in feed()["features"]:
                    object_id = "event:usgs-" + source.get("id", "")
                    if object_id not in canonical:
                        continue
                    event, old, props = canonical[object_id], legacy[object_id], source["properties"]
                    self.assertEqual((event["longitude"], event["latitude"]), tuple(source["geometry"]["coordinates"][:2]))
                    self.assertEqual(old["occurredAt"], props["time"])
                    self.assertEqual(old["updatedAt"], props["updated"])
                    self.assertEqual(event["metadata"]["magnitude"], props["mag"])
                    self.assertEqual(event["independentSourceCount"], 1)
                    self.assertEqual(event["verificationStatus"], "primary-confirmed" if props["status"] == "reviewed" else "single-source")
                    obj = store.get_object(object_id)
                    self.assertEqual(obj.provenance[0].contentHash, usgs.stable_hash(source))
                # The HTTP surface stays read-only.
                list(app({"PATH_INFO": "/api/events", "REQUEST_METHOD": "POST"}, start))
                self.assertEqual(response["status"], "405 Method Not Allowed")
            finally:
                store.close()


class UsgsCliTests(unittest.TestCase):
    def test_live_run_needs_registry_confirmation(self) -> None:
        with mock.patch.object(usgs, "fetch_usgs_feed") as fetch:
            with self.assertRaises(SourceRegistryError):
                usgs.main(["--db", ":memory:", "--live"])
            fetch.assert_not_called()

    def test_registry_lists_usgs_as_live_capable_but_off_by_default(self) -> None:
        entry = SourceRegistry.load().get("usgs")
        self.assertTrue(entry.enabled and entry.live_capable)
        self.assertFalse(entry.live_enabled_default)
        self.assertEqual(entry.pii_risk, "low")

    def test_file_run_loads_fixture(self) -> None:
        with mock.patch("builtins.print") as printed:
            usgs.main(["--db", ":memory:", "--file", str(FIXTURE)])
        self.assertEqual(json.loads(printed.call_args[0][0])["created"], 4)


if __name__ == "__main__":
    unittest.main()
