"""Scheduled additional issuers cannot reassign existing global filing keys."""
import copy
import json
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from etl import jobs
import test_backfill_mode
from test_pipeline import MemoryStore


SHARED = "0002012383-26-003750"
OWN = "0001660134-26-000100"
OLD = "0001660134-26-000099"


def filing(accession, form="SCHEDULE 13G/A"):
    return {"a": accession, "f": form, "d": "2026-11-04", "r": "", "p": "primary.xml", "i": "", "x": "Ownership report"}


def source_inputs(ticker="OKTA", with_shared=True):
    inputs = test_backfill_mode.FactsOnlyModeTests().sources(jobs.BY_TICKER[ticker]["cik"])
    sub = json.loads(inputs["submissions"])
    sub.update(name="Okta, Inc." if ticker == "OKTA" else "Apple Inc.", tickers=[ticker])
    rows = ([filing(SHARED)] if with_shared else []) + [filing(OWN)]
    mapping = {"form": "f", "filingDate": "d", "reportDate": "r", "accessionNumber": "a",
               "primaryDocument": "p", "items": "i", "primaryDocDescription": "x"}
    sub["filings"]["recent"] = {key: [r[field] for r in rows] for key, field in mapping.items()}
    inputs["submissions"] = json.dumps(sub).encode()
    return inputs


class FilingStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.filings = {SHARED: {"accession": SHARED, "ticker": "BLK", "form": "SCHEDULE 13G/A",
                               "filed": "2026-11-04", "url": "https://example.test/original", "updated_at": "original"}}
        self.lookups = []
        self.lookup_error = None
    def put_raw(self, path, body, **kwargs):
        self.writes.append(("raw", path))
    def select(self, table, query):
        if table != "filings":
            return []
        self.lookups.append(query)
        if self.lookup_error:
            raise self.lookup_error
        ids = parse_qs(query)["accession"][0][4:-1].split(",")
        return copy.deepcopy([self.filings[a] for a in ids if a in self.filings])
    def upsert(self, table, rows, *args, **kwargs):
        rows = list(rows)
        if table == "filings":
            self.filings.update({r["accession"]: copy.deepcopy(r) for r in rows})
        self.db.append((table, copy.deepcopy(rows)))


class FilingOwnershipTests(unittest.TestCase):
    def test_unowned_cross_issuer_batch_holds_each_alias_without_picking_an_owner(self):
        # The archived LITE and MRVL submissions both contain this Nov 2, 2026
        # Form 425. One database accession cannot be assigned by worker order.
        accession = "0001193125-26-443876"
        sb = FilingStore()
        rows = [{"accession": accession, "ticker": ticker, "form": "425"}
                for ticker in ("LITE", "MRVL")]
        rows.append({"accession": OWN, "ticker": "LITE", "form": "4"})
        kept, held = jobs.preserve_filing_owners(sb, rows)
        self.assertEqual(kept, [rows[-1]])
        self.assertEqual({row["ticker"] for row in held}, {"LITE", "MRVL"})
        self.assertTrue(all(row["owner_ticker"] is None and row["reason"] == "ambiguous_incoming_owner"
                            and row["candidate_tickers"] == ["LITE", "MRVL"] for row in held))
        self.assertTrue(all("unassigned" in jobs._held_filing_message(row) for row in held))

    def test_duplicate_batch_preserves_existing_owner_even_for_legacy_issuers(self):
        sb = FilingStore()
        sb.filings[SHARED]["ticker"] = "MSFT"
        rows = [{"accession": SHARED, "ticker": ticker} for ticker in ("AAPL", "MSFT")]
        kept, held = jobs.preserve_filing_owners(sb, rows)
        self.assertEqual(kept, [rows[1]])
        self.assertEqual(held, [{"accession": SHARED, "ticker": "AAPL", "owner_ticker": "MSFT"}])
        self.assertEqual(len(sb.lookups), 1)

    def test_identical_incoming_duplicates_collapse_but_conflicting_payloads_are_held(self):
        sb = FilingStore()
        row = {"accession": OWN, "ticker": "AAPL", "form": "4", "updated_at": "2026-11-05T18:00:00Z"}
        kept, held = jobs.preserve_filing_owners(sb, [row, {**row, "updated_at": "2026-11-05T18:00:01Z"}])
        self.assertEqual(kept, [row])
        self.assertEqual(held, [])
        kept, held = jobs.preserve_filing_owners(sb, [row, {**row, "form": "8-K"}])
        self.assertEqual(kept, [])
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["reason"], "conflicting_incoming_filing")

    def test_duplicate_legacy_owner_lookup_failure_refuses_the_batch(self):
        sb = FilingStore()
        sb.lookup_error = RuntimeError("ownership lookup unavailable")
        with self.assertRaisesRegex(RuntimeError, "ownership"):
            jobs.preserve_filing_owners(sb, [{"accession": SHARED, "ticker": ticker} for ticker in ("AAPL", "MSFT")])

    def test_light_sync_never_sends_duplicate_conflict_keys(self):
        sb = FilingStore()
        accession = "0001193125-26-443876"
        fl = [filing(accession, "425"), filing(OLD, "10-Q")]
        fund = {ticker: {"latest_accession": OLD, "material_accession": OLD, "pipeline_version": jobs.PIPELINE_VERSION}
                for ticker in ("LITE", "MRVL")}
        stats = {}
        original_upsert = sb.upsert
        def unique_upsert(table, rows, *args, **kwargs):
            if table == "filings":
                keys = [row["accession"] for row in rows]
                self.assertEqual(len(keys), len(set(keys)), "Postgres rejects duplicate ON CONFLICT keys in one batch")
            return original_upsert(table, rows, *args, **kwargs)
        sb.upsert = unique_upsert
        with patch.object(jobs.sec, "submissions", return_value={}), patch.object(jobs.sec, "filing_list", return_value=fl), patch("builtins.print"):
            changed = jobs.detect_changes(sb, ["LITE", "MRVL"], fund, time.time() + 30, ownership_stats=stats)
        self.assertEqual(changed, [])
        self.assertEqual(stats["held_filing_alias_count"], 2)
        self.assertNotIn(accession, sb.filings)
        self.assertIn("meta/check_cursor.json", sb.data)
        self.assertTrue(all(any(accession in warning for warning in fund[ticker]["warnings"]) for ticker in fund))

    def test_company_refresh_preserves_owner_and_reports_held_accession(self):
        sb = FilingStore()
        original = copy.deepcopy(sb.filings[SHARED])
        messages = []
        result = jobs.process_company(sb, "OKTA", facts_only=True, fetch_docs=False,
                                      source_inputs=source_inputs(), log=messages.append)
        self.assertEqual(sb.filings[SHARED], original)
        self.assertEqual(sb.filings[OWN]["ticker"], "OKTA")
        self.assertEqual(result["held_filing_aliases"], [{"accession": SHARED, "ticker": "OKTA", "owner_ticker": "BLK"}])
        self.assertTrue(any(SHARED in message and "BLK" in message for message in result["warnings"]))
        self.assertTrue(any(SHARED in message for message in messages))
        # The exact submissions-derived filing remains visible in the company
        # bundle; only the conflicting relational write is held.
        self.assertIn(SHARED, [r["a"] for r in sb.data["companies/OKTA.json"]["filings"]])

    def test_company_lookup_failure_or_missing_method_blocks_all_mutations(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                sb = FilingStore()
                if missing:
                    sb.select = None
                else:
                    sb.lookup_error = RuntimeError("ownership lookup unavailable")
                with self.assertRaisesRegex(RuntimeError, "ownership|lookup"):
                    jobs.process_company(sb, "OKTA", facts_only=True, fetch_docs=False,
                                         source_inputs=source_inputs(), log=lambda *_: None)
                self.assertEqual(sb.writes, [])
                self.assertEqual(sb.db, [])
                self.assertEqual(sb.filings[SHARED]["ticker"], "BLK")

    def detect(self, sb, stats=None):
        fl = [filing(SHARED), filing(OWN), filing(OLD, "10-Q")]
        fund = {"OKTA": {"latest_accession": OLD, "material_accession": OLD, "pipeline_version": jobs.PIPELINE_VERSION}}
        with patch.object(jobs.sec, "submissions", return_value={}), patch.object(jobs.sec, "filing_list", return_value=fl):
            result = jobs.detect_changes(sb, ["OKTA"], fund, time.time() + 30, ownership_stats=stats)
        return result, fund

    def test_light_sync_preserves_owner_and_reports_actual_upserts_and_aliases(self):
        sb = FilingStore()
        original = copy.deepcopy(sb.filings[SHARED])
        stats = {}
        with patch("builtins.print") as logged:
            changed, fund = self.detect(sb, stats)
        self.assertEqual(changed, [])
        self.assertEqual(sb.filings[SHARED], original)
        self.assertEqual(sb.filings[OWN]["ticker"], "OKTA")
        self.assertEqual(stats["held_filing_aliases"], [{"accession": SHARED, "ticker": "OKTA", "owner_ticker": "BLK"}])
        self.assertEqual(stats["held_filing_alias_count"], 1)
        self.assertTrue(any(SHARED in str(call) for call in logged.call_args_list))
        self.assertTrue(any(SHARED in message for message in fund["OKTA"]["warnings"]))
        writes = [r for table, rows in sb.db if table == "filings" for r in rows]
        self.assertEqual([r["accession"] for r in writes], [OWN])

    def test_light_sync_lookup_failure_or_missing_method_does_not_advance_cursor(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                sb = FilingStore()
                if missing:
                    sb.select = None
                else:
                    sb.lookup_error = RuntimeError("ownership lookup unavailable")
                with self.assertRaisesRegex(RuntimeError, "ownership|lookup"):
                    self.detect(sb, {})
                self.assertEqual(sb.db, [])
                self.assertEqual(sb.writes, [])
                self.assertNotIn("meta/check_cursor.json", sb.data)
                self.assertEqual(sb.filings[SHARED]["ticker"], "BLK")

    def test_legacy_company_refresh_does_not_require_a_filing_lookup(self):
        sb = FilingStore()
        def lookup(table, query):
            if table == 'filings':
                raise AssertionError('Legacy filing publication must not require an ownership lookup')
            return []  # Financial replacement safety performs its own fact lookup.
        sb.select = lookup
        jobs.process_company(sb, "AAPL", facts_only=True, fetch_docs=False,
                             source_inputs=source_inputs("AAPL", with_shared=False), log=lambda *_: None)
        self.assertEqual(sb.filings[OWN]["ticker"], "AAPL")
        self.assertEqual(sb.lookups, [])

    def test_explicit_offline_stage_retains_all_filings_without_live_lookup(self):
        sb = FilingStore()
        sb.stage_only = True
        sb.select = None
        result = jobs.process_company(sb, "OKTA", facts_only=True, fetch_docs=False,
                                      source_inputs=source_inputs(), log=lambda *_: None)
        self.assertEqual(sb.filings[SHARED]["ticker"], "OKTA")
        self.assertEqual(sb.filings[OWN]["ticker"], "OKTA")
        self.assertFalse(result.get("held_filing_aliases"))

    def test_owner_lookup_is_bounded_and_passes_through_same_owner_rows(self):
        sb = FilingStore()
        rows = [{"accession": f"0001660134-26-{i:06d}", "ticker": "OKTA"} for i in range(501)]
        sb.filings[rows[0]["accession"]] = dict(rows[0])
        helper = getattr(jobs, "preserve_filing_owners", None)
        self.assertIsNotNone(helper, "The shared filing owner filter is not implemented")
        kept, held = helper(sb, rows)
        self.assertEqual(kept, rows)
        self.assertEqual(held, [])
        self.assertEqual(len(sb.lookups), 3)
        self.assertTrue(all(len(parse_qs(q)["accession"][0][4:-1].split(",")) <= 250 for q in sb.lookups))


if __name__ == "__main__":
    unittest.main()
