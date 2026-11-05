"""Reviewed KPI repairs are exact, bounded, source-backed and resumable."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs

from etl import repair_kpi_evidence as repair


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class Store:
    def __init__(self, definitions, rows, doc, bundle):
        self.tables = {"kpi_definitions": definitions, "kpi_values": rows,
                       "ir_documents": [doc], "etl_leases": [{"lock_name": "financial-aggregates",
                       "owner": "owner", "expires_at": "2100-01-01T00:00:00+00:00"}]}
        self.objects = {("public-data", "kpis/TEST.json"): encoded(bundle),
                        ("public-data", "kpistd/t/TEST.json"): encoded({"ticker": "TEST", "map": [], "series": {}})}
        self.writes = []

    def select(self, table, query=""):
        rows = self.tables[table]
        for field, values in parse_qs(query).items():
            rule = values[0]
            if rule.startswith("eq."):
                rows = [r for r in rows if str(r.get(field)) == rule[3:]]
            elif rule.startswith("in.("):
                allowed = json.loads("[" + rule[4:-1] + "]")
                rows = [r for r in rows if r.get(field) in allowed]
        return copy.deepcopy(rows)

    def get_object(self, bucket, path, **kwargs):
        return self.objects.get((bucket, path))

    def put_object(self, bucket, path, body, **kwargs):
        self.objects[bucket, path] = body
        self.writes.append((bucket, path))

    def put_immutable_object(self, bucket, path, body):
        existing = self.objects.get((bucket, path))
        if existing is not None and existing != body:
            raise ValueError("immutable collision")
        if existing is None:
            self.put_object(bucket, path, body)


class EvidenceRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        raw = b"Dollar per barrel; Q1 2019 10.00; Q1 2020 20.00"
        source = Path(self.tmp.name) / "source.htm"
        source.write_bytes(raw)
        doc = {"id": "source", "ticker": "TEST", "url": "https://issuer.test/source.htm",
               "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        definition = {"ticker": "TEST", "kpi_id": "margin", "name": "Margin per barrel",
                      "category": "Operations", "subcategory": None, "unit": "USD",
                      "parent_id": None, "sums_to_parent": False, "source_kind": "ir",
                      "description": "Dollars per barrel", "sort": 1,
                      "updated_at": "2026-11-01T00:00:00+00:00"}
        rows, changes, proofs = [], [], {}
        points = {}
        for index, year in enumerate((2019, 2020), 1):
            row = {"ticker": "TEST", "kpi_id": "margin", "period_kind": "Q", "period_end": f"{year}-03-31",
                   "fiscal_label": f"Q1 FY{year % 100}", "value": index * 10_000_000, "unit": "USD",
                   "source_kind": "ir", "source_id": "source", "anchor": {"page": 1, "sheet": None,
                   "cell": None, "quote": f"{index * 10}.00"}, "derived": None, "reconciled": None,
                   "updated_at": "2026-11-01T00:00:00+00:00"}
            rows.append(row)
            after = dict(row, value=index * 10, unit="USD_per_barrel")
            pid = f"proof-{index}"
            proofs[pid] = {**doc, "source_id": "source", "local_original": str(source),
                           "reported_value": str(index * 10), "scale": 1, "unit": "USD_per_barrel",
                           "unit_context": "Dollar per barrel", "mapped_period": {"kind": "Q", "end": row["period_end"]}}
            changes.append({"before": row, "after": after, "proof_ids": [pid]})
            points[row["period_end"]] = {"v": row["value"], "doc": "source", "page": 1, "sheet": None,
                "cell": None, "quote": row["anchor"]["quote"], "label": row["fiscal_label"], "printed": str(index * 10)}
        self.manifest = {"schema_version": 1, "status": "review_only_not_applied", "ticker": "TEST",
            "prepared_at": "2026-11-05T00:00:00+00:00", "expected_count": 2,
            "expected_definitions": [definition], "row_corrections": changes,
            "definition_corrections": [{"before": definition, "after": dict(definition, unit="USD_per_barrel"),
                                         "proof_ids": list(proofs)}], "source_proofs": proofs,
            "expected_metric_row_sets": [{"ticker": "TEST", "kpi_id": "margin", "expected_count": 2, "rows": rows}]}
        bundle = {"ticker": "TEST", "generated": "old", "notes": "preserve", "groups": [{"category": "Operations",
            "items": [{"id": "margin", "name": "Margin per barrel", "unit": "USD", "values": {"Q": points}},
                      {"id": "unrelated", "unit": "count", "values": {"I": {"2026-01-01": {"v": 42, "doc": "other"}}}}]}]}
        self.store = Store(copy.deepcopy([definition]), copy.deepcopy(rows), doc, bundle)

    def test_plan_is_read_only_and_preserves_unrelated_public_content(self):
        stage = repair.plan_repair(self.store, self.manifest)
        self.assertEqual(self.store.writes, [])
        obj = stage["public_objects"][0]
        self.assertEqual(obj["before"]["notes"], obj["after"]["notes"])
        self.assertEqual(obj["before"]["groups"][0]["items"][1], obj["after"]["groups"][0]["items"][1])
        item = obj["after"]["groups"][0]["items"][0]
        self.assertEqual(item["unit"], "USD_per_barrel")
        self.assertEqual(item["values"]["Q"]["2019-03-31"]["v"], 10)

    def test_stale_before_or_unverified_source_is_rejected(self):
        for mutation in (lambda: self.store.tables["kpi_values"][0].update(value=7),
                         lambda: self.manifest["source_proofs"]["proof-1"].update(sha256="0" * 64)):
            with self.subTest(mutation=mutation):
                saved = copy.deepcopy((self.store.tables, self.manifest))
                mutation()
                with self.assertRaises(ValueError):
                    repair.plan_repair(self.store, self.manifest)
                self.store.tables, self.manifest = saved
        self.assertEqual(self.store.writes, [])

    def test_unit_change_requires_every_historical_row(self):
        old = copy.deepcopy(self.store.tables["kpi_values"][0]); old["period_end"] = "2018-03-31"
        self.store.tables["kpi_values"].append(old)
        with self.assertRaisesRegex(ValueError, "complete|coverage|count"):
            repair.plan_repair(self.store, self.manifest)

    def test_row_unit_change_without_matching_definition_cannot_publish(self):
        self.manifest["definition_corrections"] = []
        with self.assertRaisesRegex(ValueError, "unit"):
            repair.plan_repair(self.store, self.manifest)

    def test_segment_only_map_does_not_block_separate_unmapped_unit_correction(self):
        definition = dict(self.manifest["expected_definitions"][0], kpi_id="segment", name="Segment revenue")
        self.manifest["expected_definitions"].append(definition)
        self.store.tables["kpi_definitions"].append(copy.deepcopy(definition))
        before = dict(self.manifest["row_corrections"][0]["before"], kpi_id="segment")
        after = dict(before, period_end="2019-03-30")
        self.store.tables["kpi_values"].append(copy.deepcopy(before))
        self.manifest["row_corrections"].append({"before": before, "after": after, "proof_ids": ["segment"]})
        self.manifest["expected_count"] = 3
        proof = dict(self.manifest["source_proofs"]["proof-1"], unit="USD", scale=1000000,
                     mapped_period={"kind": "Q", "end": "2019-03-30"})
        self.manifest["source_proofs"]["segment"] = proof
        bundle = json.loads(self.store.objects["public-data", "kpis/TEST.json"])
        item = copy.deepcopy(bundle["groups"][0]["items"][0]); item["id"] = "segment"
        item["values"]["Q"].pop("2020-03-31")
        bundle["groups"][0]["items"].append(item)
        self.store.objects["public-data", "kpis/TEST.json"] = encoded(bundle)
        self.store.objects["public-data", "kpistd/t/TEST.json"] = encoded({"ticker": "TEST",
            "map": [{"kpi_id": "segment", "unit": "USD", "scope": "segment"}], "series": {}})
        stage = repair.plan_repair(self.store, self.manifest)
        self.assertEqual([o["path"] for o in stage["public_objects"]], ["kpis/TEST.json"])
        self.assertEqual(stage["publication_checks"][0]["path"], "kpistd/t/TEST.json")

    def test_destination_collision_never_merges_or_deletes_a_point(self):
        self.manifest["row_corrections"][0]["after"]["period_end"] = "2020-03-31"
        self.manifest["source_proofs"]["proof-1"]["mapped_period"]["end"] = "2020-03-31"
        with self.assertRaisesRegex(ValueError, "collision|duplicate"):
            repair.plan_repair(self.store, self.manifest)

    def test_unit_only_proof_can_retain_dates_but_cannot_authorize_a_date_move(self):
        for proof in self.manifest["source_proofs"].values():
            proof.pop("mapped_period")
            proof["retained_period_not_changed"] = True
        repair.plan_repair(self.store, self.manifest)
        self.manifest["row_corrections"][0]["after"]["period_end"] = "2019-03-30"
        with self.assertRaisesRegex(ValueError, "Source evidence"):
            repair.plan_repair(self.store, self.manifest)

    def test_source_identity_or_cell_change_is_not_allowlisted(self):
        for field, value in (("source_id", "other"), ("anchor", {"page": 1, "sheet": None, "cell": "C9", "quote": "10.00"})):
            with self.subTest(field=field):
                before = copy.deepcopy(self.manifest)
                self.manifest["row_corrections"][0]["after"][field] = value
                with self.assertRaises(ValueError):
                    repair.plan_repair(self.store, self.manifest)
                self.manifest = before

    def test_public_mismatch_is_not_overwritten(self):
        bundle = json.loads(self.store.objects["public-data", "kpis/TEST.json"])
        bundle["groups"][0]["items"][0]["values"]["Q"]["2019-03-31"]["v"] = 99
        self.store.objects["public-data", "kpis/TEST.json"] = encoded(bundle)
        with self.assertRaisesRegex(ValueError, "public"):
            repair.plan_repair(self.store, self.manifest)

    def test_standardized_numeric_impact_is_explicitly_out_of_scope(self):
        self.manifest["definition_corrections"] = []
        for change in self.manifest["row_corrections"]:
            change["after"]["unit"] = "USD"
        for proof in self.manifest["source_proofs"].values():
            proof["unit"] = "USD"
        self.store.objects["public-data", "kpistd/t/TEST.json"] = encoded({"ticker": "TEST", "map": [],
            "series": {"margin": {"kpi_id": "margin", "Q": [["2019-03-31", 10000000]], "A": []}}})
        with self.assertRaisesRegex(ValueError, "standardized numeric"):
            repair.plan_repair(self.store, self.manifest)

    def test_sql_contains_exact_comparisons_lease_guards_and_rollback(self):
        stage = repair.plan_repair(self.store, self.manifest)
        sql = repair.render_guarded_sql(stage, "owner")
        self.assertTrue(sql.startswith("BEGIN;"))
        self.assertIn("FOR UPDATE", sql)
        self.assertIn("financial-aggregates", sql)
        self.assertIn("IS DISTINCT FROM", sql)
        self.assertIn("destination collision", sql)
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))
        self.assertIn("USD_per_barrel", repair.render_guarded_sql(stage, "owner", rollback=True))

    def test_finalization_requires_archive_db_after_and_live_lease(self):
        stage = repair.plan_repair(self.store, self.manifest)
        repair.archive_stage(self.store, stage, "owner")
        with self.assertRaisesRegex(ValueError, "database"):
            repair.finalize_repair(self.store, stage, "owner")
        self.store.tables["kpi_values"] = copy.deepcopy(stage["database_after"]["kpi_values"])
        self.store.tables["kpi_definitions"] = copy.deepcopy(stage["database_after"]["kpi_definitions"])
        receipt = repair.finalize_repair(self.store, stage, "owner")
        self.assertEqual(receipt["status"], "verified")
        self.assertEqual(json.loads(self.store.objects["public-data", "kpis/TEST.json"]), stage["public_objects"][0]["after"])
        repair.finalize_repair(self.store, stage, "owner")  # Same sealed stage is resumable.
        self.store.tables["etl_leases"][0]["owner"] = "other"
        with self.assertRaisesRegex(ValueError, "lease"):
            repair.finalize_repair(self.store, stage, "owner")

    def test_stage_tampering_and_third_state_public_conflict_are_rejected(self):
        stage = repair.plan_repair(self.store, self.manifest)
        altered = copy.deepcopy(stage); altered["database_after"]["kpi_values"][0]["value"] = 999
        with self.assertRaisesRegex(ValueError, "seal"):
            repair.render_guarded_sql(altered, "owner")
        repair.archive_stage(self.store, stage, "owner")
        self.store.tables["kpi_values"] = copy.deepcopy(stage["database_after"]["kpi_values"])
        self.store.tables["kpi_definitions"] = copy.deepcopy(stage["database_after"]["kpi_definitions"])
        self.store.objects["public-data", "kpis/TEST.json"] = b'{"ticker":"TEST","changed":true}'
        with self.assertRaisesRegex(ValueError, "public"):
            repair.finalize_repair(self.store, stage, "owner")


if __name__ == "__main__":
    unittest.main()
