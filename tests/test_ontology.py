from __future__ import annotations

import os
import unittest
import uuid

from osint_forge.ontology import (
    OntologyError,
    OntologyLink,
    OntologyObject,
    PostgresOntologyStore,
    SQLiteOntologyStore,
    object_id,
)
from osint_forge.provenance import ProvenanceRef, utc_now


def prov(locator: str) -> ProvenanceRef:
    return ProvenanceRef(source="test", locator=locator, observedAt=utc_now(), contentHash="hash-" + locator)


def hormuz() -> OntologyObject:
    return OntologyObject(
        id="place:strait-of-hormuz",
        type="place",
        label="Strait of Hormuz",
        properties={"kind": "chokepoint"},
        provenance=[prov("portwatch:chokepoint6")],
        lat=26.57,
        lon=56.25,
    )


class OntologyModelTests(unittest.TestCase):
    def test_object_id_helper_builds_typed_ids(self) -> None:
        self.assertEqual(object_id("place", "Strait of Hormuz"), "place:Strait-of-Hormuz")

    def test_private_people_are_not_an_object_type(self) -> None:
        with self.assertRaises(OntologyError):
            OntologyObject(id="person:someone", type="person", label="Someone", provenance=[prov("x")])

    def test_object_requires_provenance_matching_id_and_valid_coordinates(self) -> None:
        with self.assertRaises(OntologyError):
            OntologyObject(id="place:a", type="place", label="A", provenance=[])
        with self.assertRaises(OntologyError):
            OntologyObject(id="event:a", type="place", label="A", provenance=[prov("a")])
        with self.assertRaises(OntologyError):
            OntologyObject(id="place:a", type="place", label="A", provenance=[prov("a")], lat=95.0, lon=0.0)
        with self.assertRaises(OntologyError):
            OntologyObject(id="place:a", type="place", label="A", provenance=[prov("a")], lat=10.0)

    def test_zero_coordinates_are_valid(self) -> None:
        obj = OntologyObject(id="place:null-island", type="place", label="Null Island", provenance=[prov("a")], lat=0.0, lon=0.0)
        self.assertEqual((obj.lat, obj.lon), (0.0, 0.0))

    def test_analytic_links_need_evidence_but_structural_links_do_not(self) -> None:
        with self.assertRaises(OntologyError):
            OntologyLink(type="supports", from_id="document:a", to_id="claim:b", provenance=[prov("a")])
        link = OntologyLink(type="located_at", from_id="facility:port", to_id="place:hormuz", provenance=[prov("a")])
        self.assertTrue(link.id.startswith("link:"))

    def test_possible_association_cannot_carry_high_confidence(self) -> None:
        with self.assertRaises(OntologyError):
            OntologyLink(
                type="possible_association",
                from_id="vessel:a",
                to_id="organization:b",
                evidence_ids=["document:c"],
                confidence=0.9,
                provenance=[prov("a")],
            )

    def test_link_period_must_be_ordered(self) -> None:
        with self.assertRaises(OntologyError):
            OntologyLink(
                type="located_at",
                from_id="vessel:a",
                to_id="place:b",
                valid_from="2026-02-01",
                valid_to="2026-01-01",
                provenance=[prov("a")],
            )

    def test_retrieval_time_does_not_change_content_hash(self) -> None:
        first = hormuz()
        second = hormuz()
        second.provenance[0].observedAt = "2030-01-01T00:00:00Z"
        self.assertEqual(first.content_hash(), second.content_hash())


class StoreContract:
    """Shared behaviour every backend must pass."""

    def make_store(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def setUp(self) -> None:
        self.store = self.make_store()

    def tearDown(self) -> None:
        self.store.close()

    def seed(self) -> None:
        self.store.upsert_object(hormuz())
        self.store.upsert_object(
            OntologyObject(id="document:notice-1", type="document", label="Maritime notice 1", provenance=[prov("notice-1")])
        )
        self.store.upsert_object(
            OntologyObject(
                id="claim:hormuz-transit-restricted",
                type="claim",
                label="Transit through Hormuz is restricted",
                provenance=[prov("analyst")],
                valid_from="2026-10-01",
            )
        )

    def test_upsert_is_idempotent_and_revisions_on_change(self) -> None:
        self.assertEqual(self.store.upsert_object(hormuz()), (1, True))
        self.assertEqual(self.store.upsert_object(hormuz()), (1, False))
        changed = hormuz()
        changed.properties = {"kind": "chokepoint", "width_km": 39}
        self.assertEqual(self.store.upsert_object(changed), (2, True))
        history = self.store.object_history("place:strait-of-hormuz")
        self.assertEqual([h["revision"] for h in history], [1, 2])
        self.assertEqual(history[0]["object"]["properties"], {"kind": "chokepoint"})
        self.assertEqual(self.store.get_object("place:strait-of-hormuz").properties["width_km"], 39)

    def test_object_type_cannot_change_under_same_id(self) -> None:
        self.store.upsert_object(hormuz())
        with self.assertRaises(OntologyError):
            self.store.upsert_object(_retyped())

    def test_list_objects_filters_by_type_text_and_bbox(self) -> None:
        self.seed()
        self.assertEqual([o.id for o in self.store.list_objects(object_type="claim")], ["claim:hormuz-transit-restricted"])
        self.assertEqual(len(self.store.list_objects(text="hormuz")), 2)
        in_box = self.store.list_objects(bbox=(50.0, 20.0, 60.0, 30.0))
        self.assertEqual([o.id for o in in_box], ["place:strait-of-hormuz"])
        self.assertEqual(self.store.list_objects(bbox=(0.0, 0.0, 1.0, 1.0)), [])

    def test_links_require_known_endpoints_and_evidence(self) -> None:
        self.seed()
        with self.assertRaises(OntologyError):
            self.store.add_link(
                OntologyLink(
                    type="supports",
                    from_id="document:notice-1",
                    to_id="claim:hormuz-transit-restricted",
                    evidence_ids=["document:missing"],
                    provenance=[prov("a")],
                )
            )
        link = OntologyLink(
            type="supports",
            from_id="document:notice-1",
            to_id="claim:hormuz-transit-restricted",
            evidence_ids=["document:notice-1"],
            confidence=0.6,
            rationale="Official notice issued; issued is not closed.",
            provenance=[prov("analyst")],
        )
        self.assertTrue(self.store.add_link(link))
        self.assertFalse(self.store.add_link(link))
        out = self.store.links_for("document:notice-1", direction="out")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["evidence_ids"], ["document:notice-1"])
        self.assertEqual(self.store.links_for("claim:hormuz-transit-restricted", direction="in", link_types=["contradicts"]), [])

    def test_neighborhood_expands_by_depth(self) -> None:
        self.seed()
        self.store.add_link(
            OntologyLink(type="located_at", from_id="claim:hormuz-transit-restricted", to_id="place:strait-of-hormuz", provenance=[prov("a")])
        )
        self.store.add_link(
            OntologyLink(
                type="supports",
                from_id="document:notice-1",
                to_id="claim:hormuz-transit-restricted",
                evidence_ids=["document:notice-1"],
                provenance=[prov("a")],
            )
        )
        one = self.store.neighborhood("place:strait-of-hormuz", depth=1)
        self.assertEqual({o["id"] for o in one["objects"]}, {"place:strait-of-hormuz", "claim:hormuz-transit-restricted"})
        two = self.store.neighborhood("place:strait-of-hormuz", depth=2)
        self.assertEqual(len(two["objects"]), 3)
        self.assertEqual(len(two["links"]), 2)

    def test_every_write_is_audited(self) -> None:
        self.seed()
        actions = [row["action"] for row in self.store.audit_log("place:strait-of-hormuz")]
        self.assertEqual(actions, ["object.created"])


def _retyped() -> OntologyObject:
    # The model forbids a mismatched id prefix, so force one to prove the store also refuses it.
    obj = OntologyObject(id="facility:strait-of-hormuz", type="facility", label="x", provenance=[prov("y")])
    obj.id = "place:strait-of-hormuz"
    return obj


class SQLiteOntologyStoreTests(StoreContract, unittest.TestCase):
    def make_store(self):
        return SQLiteOntologyStore(":memory:")


@unittest.skipUnless(os.environ.get("FORGE_TEST_DATABASE_URL"), "set FORGE_TEST_DATABASE_URL to run PostgreSQL tests")
class PostgresOntologyStoreTests(StoreContract, unittest.TestCase):
    def make_store(self):
        import psycopg

        base = os.environ["FORGE_TEST_DATABASE_URL"]
        self.schema = "forge_test_" + uuid.uuid4().hex[:12]
        admin = psycopg.connect(base, autocommit=True)
        admin.execute(f"CREATE SCHEMA {self.schema}")
        admin.close()
        sep = "&" if "?" in base else "?"
        return PostgresOntologyStore(f"{base}{sep}options=-csearch_path%3D{self.schema}")

    def tearDown(self) -> None:
        import psycopg

        super().tearDown()
        admin = psycopg.connect(os.environ["FORGE_TEST_DATABASE_URL"], autocommit=True)
        admin.execute(f"DROP SCHEMA {self.schema} CASCADE")
        admin.close()


if __name__ == "__main__":
    unittest.main()
