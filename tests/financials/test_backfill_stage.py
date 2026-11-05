import tempfile
import unittest
from pathlib import Path

from etl.backfill import StageStore, safe_path


class BackfillStageTests(unittest.TestCase):
    def test_staging_cannot_target_an_existing_company(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "new issuer"):
                StageStore(directory, "AAPL")

    def test_paths_cannot_replace_another_company_or_global_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = StageStore(directory, "OKTA")
            for bucket, path in [("public-data", "companies/AAPL.json"),
                                 ("public-data", "index.json"),
                                 ("raw", "sec/companyfacts/CIK0000320193.json.gz"),
                                 ("public-data", "companies/OKTA.json/../../index.json")]:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    stage.put_object(bucket, path, b"{}")
            self.assertEqual(list(Path(directory).rglob("*")), [])

    def test_table_scope_prevents_old_company_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = StageStore(directory, "OKTA")
            with self.assertRaisesRegex(ValueError, "another issuer"):
                stage.upsert("companies", [{"ticker": "AAPL"}], "ticker")
            with self.assertRaisesRegex(ValueError, "Unexpected table"):
                stage.upsert("kpi_values", [{"ticker": "OKTA"}], "ticker")
            self.assertEqual(stage.tables, {})

    def test_stage_path_rejects_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                safe_path(Path(directory), "../other-company.json")


if __name__ == "__main__":
    unittest.main()
