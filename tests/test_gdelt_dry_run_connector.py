from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from osint_forge.governance.pii_sanitizer import PIISanitizer
from osint_forge.ingestion.gdelt_connector import GDELTDryRunConnector
from osint_forge.orchestrator.mission_store import MissionStore


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gdelt"


class GDELTDryRunConnectorTests(unittest.TestCase):
    def test_fixture_ingestion_gates_pii_and_persists_only_safe_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MissionStore(Path(tmp) / "forge.db")
            store.create_mission("m-gdelt", "Analyze synthetic public event records")
            connector = GDELTDryRunConnector("m-gdelt", store, PIISanitizer())

            result = connector(FIXTURE_DIR)

            self.assertEqual(result["ingestion_summary"]["accepted_records"], 3)
            self.assertEqual(result["ingestion_summary"]["blocked_records"], 1)
            self.assertEqual(store.count_rows("ingested_records", "m-gdelt"), 3)
            self.assertEqual(store.count_rows("governance_events", "m-gdelt"), 1)
            self.assertEqual(len(result["raw_findings"]), 3)
            self.assertTrue(all(record["record_fingerprint"] for record in result["raw_findings"]))
            self.assertTrue(all(record["content_hash"] for record in result["raw_findings"]))

            persisted = "\n".join(
                row["record_json"]
                for row in store.conn.execute("SELECT record_json FROM ingested_records WHERE mission_id = ?", ("m-gdelt",))
            )
            self.assertNotIn("Sample Person", persisted)
            store.close()

    def test_repeated_fixture_ingestion_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MissionStore(Path(tmp) / "forge.db")
            store.create_mission("m-gdelt-repeat", "Analyze synthetic public event records")
            connector = GDELTDryRunConnector("m-gdelt-repeat", store, PIISanitizer())

            first = connector(FIXTURE_DIR)
            second = connector(FIXTURE_DIR)

            self.assertEqual(first["source_hashes"], second["source_hashes"])
            self.assertEqual(store.count_rows("ingested_records", "m-gdelt-repeat"), 3)
            self.assertEqual(store.count_rows("governance_events", "m-gdelt-repeat"), 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
