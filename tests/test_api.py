from __future__ import annotations

import io
import json
import os
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from osint_forge.api import ForgeApi, canonical_event, events_feed
from osint_forge.api.projection import safe_source_url
from osint_forge.ontology import OntologyLink, OntologyObject, SQLiteOntologyStore
from osint_forge.provenance import ProvenanceRef

FIXTURE = Path(__file__).parent / "fixtures" / "contracts" / "live_map_events.json"
JSON_TYPES = {"string": str, "number": (int, float), "boolean": bool, "array": list, "object": dict, "null": type(None)}


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def fixture_store(store=None):
    store = store or SQLiteOntologyStore(":memory:")
    for item in load_fixture()["objects"]:
        store.upsert_object(OntologyObject.from_dict(item))
    return store


def fixture_now() -> datetime:
    return datetime.fromisoformat(load_fixture()["now"].replace("Z", "+00:00"))


def type_errors(record: dict, spec: dict, label: str) -> list[str]:
    errors = []
    for key, allowed in spec.items():
        if key not in record:
            errors.append(f"{label}.{key} missing")
            continue
        value = record[key]
        ok = any(
            isinstance(value, JSON_TYPES[t]) and not (t == "number" and isinstance(value, bool))
            for t in allowed
        )
        if not ok:
            errors.append(f"{label}.{key} is {type(value).__name__}, expected {allowed}")
    return errors


class LiveMapContractTests(unittest.TestCase):
    def make_store(self):
        return SQLiteOntologyStore(":memory:")

    def setUp(self) -> None:
        self.fixture = load_fixture()
        self.store = fixture_store(self.make_store())

    def tearDown(self) -> None:
        self.store.close()

    def test_feed_reproduces_conformance_fixture(self) -> None:
        feed = events_feed(self.store, self.fixture["query"], now=fixture_now())
        self.assertEqual(json.loads(json.dumps(feed)), self.fixture["expected"])

    def test_feed_matches_contract_schema_and_enums(self) -> None:
        feed = events_feed(self.store, {}, now=fixture_now())
        schema = self.fixture["schema"]
        errors = type_errors(feed, schema["envelope"], "envelope")
        errors += type_errors(feed["filters"], schema["filters"], "filters")
        for event in feed["canonicalEvents"]:
            errors += type_errors(event, schema["canonicalEvent"], event["id"])
            for key in ("recordKind", "severityLabel", "status", "mapDisplayStatus"):
                if event[key] not in schema["enums"][key]:
                    errors.append(f"{event['id']}.{key}={event[key]!r}")
        for event in feed["events"]:
            errors += type_errors(event, schema["legacyEvent"], "legacy " + event["id"])
            if event["uiSeverity"] not in schema["enums"]["uiSeverity"]:
                errors.append(f"legacy {event['id']}.uiSeverity")
        self.assertEqual(errors, [])

    def test_restricted_old_and_non_event_objects_are_not_projected(self) -> None:
        ids = [e["id"] for e in events_feed(self.store, {}, now=fixture_now())["canonicalEvents"]]
        self.assertEqual(ids, ["event:usgs-us7000abcd", "event:notice-hormuz-advisory"])
        wide = events_feed(self.store, {"hours": "720"}, now=fixture_now())
        self.assertIn("event:old-port-delay", [e["id"] for e in wide["canonicalEvents"]])
        self.assertNotIn("event:restricted-draft", [e["id"] for e in wide["canonicalEvents"]])

    def test_zero_coordinates_stay_mapped(self) -> None:
        wide = events_feed(self.store, {"hours": "720"}, now=fixture_now())
        old = next(e for e in wide["canonicalEvents"] if e["id"] == "event:old-port-delay")
        self.assertEqual((old["latitude"], old["longitude"], old["geographic"]), (0.0, 0.0, True))

    def test_filters_and_invalid_params(self) -> None:
        feed = events_feed(self.store, {"domain": "maritime"}, now=fixture_now())
        self.assertEqual([e["id"] for e in feed["canonicalEvents"]], ["event:notice-hormuz-advisory"])
        feed = events_feed(self.store, {"country": "irn"}, now=fixture_now())
        self.assertEqual([e["id"] for e in feed["canonicalEvents"]], ["event:usgs-us7000abcd"])
        feed = events_feed(self.store, {"hours": "5", "recordKind": "bogus", "country": "<x>"}, now=fixture_now())
        self.assertEqual(feed["hours"], 24)
        self.assertEqual(feed["filters"]["recordKind"], None)
        self.assertEqual(len(feed["warnings"]), 2)

    def test_source_urls_with_credentials_or_other_schemes_are_dropped(self) -> None:
        self.assertIsNone(safe_source_url("https://user:pw@example.org/x"))
        self.assertIsNone(safe_source_url("javascript:alert(1)"))
        self.assertIsNone(safe_source_url("ftp://example.org"))
        self.assertEqual(safe_source_url("https://example.org/a"), "https://example.org/a")

    def test_confidence_is_scaled_and_zero_is_kept(self) -> None:
        obj = OntologyObject(
            id="event:zero",
            type="event",
            label="Zero confidence",
            properties={"confidence": 0},
            provenance=[ProvenanceRef(source="t", locator="z", observedAt="2026-10-18T00:00:00Z")],
        )
        self.assertEqual(canonical_event(obj)["confidence"], 0.0)


@unittest.skipUnless(os.environ.get("FORGE_TEST_DATABASE_URL"), "set FORGE_TEST_DATABASE_URL to run PostgreSQL tests")
class PostgresLiveMapContractTests(LiveMapContractTests):
    def make_store(self):
        import psycopg

        from osint_forge.ontology import PostgresOntologyStore

        base = os.environ["FORGE_TEST_DATABASE_URL"]
        self.schema = "forge_test_" + uuid.uuid4().hex[:12]
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f"CREATE SCHEMA {self.schema}")
        sep = "&" if "?" in base else "?"
        return PostgresOntologyStore(f"{base}{sep}options=-csearch_path%3D{self.schema}")

    def tearDown(self) -> None:
        import psycopg

        super().tearDown()
        with psycopg.connect(os.environ["FORGE_TEST_DATABASE_URL"], autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {self.schema} CASCADE")


class ForgeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = fixture_store()
        self.store.upsert_object(
            OntologyObject(
                id="document:usgs-page",
                type="document",
                label="USGS event page",
                provenance=[ProvenanceRef(source="usgs", locator="page", observedAt="2026-10-18T03:25:00Z", metadata={"extractor_note": "internal"})],
            )
        )
        self.store.add_link(
            OntologyLink(
                type="located_at",
                from_id="event:usgs-us7000abcd",
                to_id="place:strait-of-hormuz",
                provenance=[ProvenanceRef(source="usgs", locator="geo", observedAt="2026-10-18T03:25:00Z")],
            )
        )
        self.api = ForgeApi(self.store)

    def tearDown(self) -> None:
        self.store.close()

    def call(self, path: str, query: str = "", method: str = "GET") -> tuple[int, dict, dict | None]:
        captured: dict = {}

        def start_response(status: str, headers: list) -> None:
            captured["status"] = int(status.split()[0])
            captured["headers"] = dict(headers)

        environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query, "wsgi.input": io.BytesIO()}
        body = b"".join(self.api(environ, start_response))
        return captured["status"], captured["headers"], json.loads(body) if body else None

    def test_health_and_cors(self) -> None:
        status, headers, body = self.call("/health")
        self.assertEqual((status, body), (200, {"status": "ok"}))
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")

    def test_list_routes_filter_by_type_text_and_bbox(self) -> None:
        _, _, events = self.call("/events")
        self.assertEqual(events["count"], 4)
        _, _, places = self.call("/objects", "type=place")
        self.assertEqual([o["id"] for o in places["objects"]], ["place:strait-of-hormuz"])
        _, _, boxed = self.call("/objects", "bbox=56,26,57,27")
        self.assertEqual({o["id"] for o in boxed["objects"]}, {"event:usgs-us7000abcd", "place:strait-of-hormuz"})
        _, _, found = self.call("/search", "q=hormuz")
        self.assertEqual(found["count"], 2)
        self.assertEqual(self.call("/claims")[2]["count"], 0)

    def test_object_views(self) -> None:
        status, _, body = self.call("/objects/event%3Ausgs-us7000abcd")
        self.assertEqual((status, body["object"]["label"]), (200, "M 5.8 - 40 km SSE of Bandar Abbas, Iran"))
        _, _, history = self.call("/objects/event:usgs-us7000abcd/history")
        self.assertEqual([r["revision"] for r in history["revisions"]], [1])
        _, _, links = self.call("/objects/event:usgs-us7000abcd/links", "direction=out")
        self.assertEqual(links["count"], 1)
        _, _, hood = self.call("/objects/place:strait-of-hormuz/neighborhood", "depth=1")
        self.assertEqual({o["id"] for o in hood["objects"]}, {"place:strait-of-hormuz", "event:usgs-us7000abcd"})

    def test_provenance_metadata_is_not_served(self) -> None:
        _, _, body = self.call("/objects/document:usgs-page")
        self.assertNotIn("metadata", body["object"]["provenance"][0])

    def test_feed_route_serves_live_map_envelope(self) -> None:
        status, headers, body = self.call("/api/events", "hours=720")
        self.assertEqual(status, 200)
        self.assertIn("public", headers["Cache-Control"])
        self.assertEqual(body["mode"], "forge-ontology")
        self.assertEqual(len(body["events"]), len(body["canonicalEvents"]))

    def test_errors_are_structured_json(self) -> None:
        self.assertEqual(self.call("/objects/event:missing")[0], 404)
        self.assertEqual(self.call("/nope")[2]["error"], "not-found")
        self.assertEqual(self.call("/objects", "type=person")[0], 400)
        self.assertEqual(self.call("/objects", "bbox=1,2,3")[0], 400)
        self.assertEqual(self.call("/objects", "limit=abc")[0], 400)
        self.assertEqual(self.call("/search")[0], 400)
        self.assertEqual(self.call("/objects/event:usgs-us7000abcd/links", "direction=sideways")[0], 400)
        status, _, body = self.call("/objects", method="POST")
        self.assertEqual((status, body["error"]), (405, "method-not-allowed"))
        self.assertEqual(self.call("/api/events", method="OPTIONS")[0], 204)


if __name__ == "__main__":
    unittest.main()
