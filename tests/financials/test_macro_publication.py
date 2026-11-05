import copy
import threading
import unittest
from unittest.mock import patch

from etl import common, jobs, macrojobs, mstore


class LeaseState:
    def __init__(self):
        self.lock = threading.Lock()
        self.owner = None
        self.acquisitions = []
        self.data = {"macro/registry.json": {}, "macro/releases.json": []}


class Store:
    def __init__(self, shared=None):
        self.shared = shared or LeaseState()
        self.reads, self.writes, self.db_writes = [], [], []
        self.on_read = None

    def rpc(self, name, args):
        if name not in ("acquire_etl_lease", "release_etl_lease"):
            raise AssertionError("Unexpected RPC " + name)
        self.assert_lock_name(args["lock_name"])
        with self.shared.lock:
            if name == "acquire_etl_lease":
                if self.shared.owner not in (None, args["owner"]):
                    return False
                self.shared.owner = args["owner"]
                self.shared.acquisitions.append(args["owner"])
                return True
            if self.shared.owner == args["owner"]:
                self.shared.owner = None
                return True
            return False

    @staticmethod
    def assert_lock_name(name):
        if name != "financial-aggregates":
            raise AssertionError("Wrong shared lease")

    def get_json(self, path):
        self.reads.append(path)
        if self.on_read:
            self.on_read(path)
        return copy.deepcopy(self.shared.data.get(path))

    def put_json(self, path, data, **kwargs):
        lease = getattr(self, "_aggregate_lease", None)
        if lease:
            lease.check()
        self.writes.append(path)
        self.shared.data[path] = copy.deepcopy(data)

    def select(self, table, query=""):
        self.reads.append((table, query))
        return []

    def insert(self, table, row, **kwargs):
        self.db_writes.append((table, row))
        return [{"id": 1}]

    def update(self, table, match, values):
        self.db_writes.append((table, values))

    def upsert(self, table, rows, *args, **kwargs):
        self.db_writes.append((table, list(rows)))


class MacroPublicationTests(unittest.TestCase):
    def setUp(self):
        self.db = patch.dict(mstore._DB, {"ok": True}, clear=True)
        self.db.start()
        self.network = patch.object(common, "request", side_effect=AssertionError("Live I/O prohibited"))
        self.network.start()
        self.macro_network = patch.object(jobs.macro, "request", side_effect=AssertionError("Live I/O prohibited"))
        self.macro_network.start()

    def tearDown(self):
        self.macro_network.stop()
        self.network.stop()
        self.db.stop()

    def test_all_entry_points_refuse_a_competing_financial_lease_before_work(self):
        calls = [
            lambda sb: macrojobs.mdata_job(sb, "catalog", log=lambda *_: None),
            lambda sb: macrojobs.migrate_legacy(sb, log=lambda *_: None),
            lambda sb: macrojobs.legacy_store(sb, [], log=lambda *_: None),
            lambda sb: jobs.macro_job(sb, refresh_news=False, brief=False),
            lambda sb: mstore.save(sb, [], {}, log=lambda *_: None),
            lambda sb: mstore.save_releases(sb, [], log=lambda *_: None),
            lambda sb: mstore.publish_catalog(sb, {}, log=lambda *_: None),
        ]
        for index, call in enumerate(calls):
            with self.subTest(entry=index):
                shared = LeaseState()
                sb = Store(shared)
                with common.AggregateLease(Store(shared)):
                    with self.assertRaises(common.LeaseBusy):
                        call(sb)
                self.assertEqual(sb.reads, [])
                self.assertEqual(sb.writes, [])
                self.assertEqual(sb.db_writes, [])

    def test_two_actual_job_threads_cannot_publish_overlapping_catalogs(self):
        shared, entered, release = LeaseState(), threading.Event(), threading.Event()
        first, second = Store(shared), Store(shared)
        failures = []

        def pause(path):
            if path == "macro/registry.json" and not entered.is_set():
                entered.set()
                if not release.wait(3):
                    raise AssertionError("Test did not release first publisher")

        def run_first():
            try:
                macrojobs.mdata_job(first, "catalog", log=lambda *_: None)
            except Exception as error:
                failures.append(error)

        first.on_read = pause
        thread = threading.Thread(target=run_first)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(common.LeaseBusy):
                macrojobs.mdata_job(second, "catalog", log=lambda *_: None)
            self.assertEqual(second.reads, [])
            self.assertEqual(second.writes, [])
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(first.writes, ["macro/registry.json", "macro/catalog.json", "meta/status.json"])
        self.assertIsNone(shared.owner)

    def test_nested_legacy_migration_reuses_the_outer_owner(self):
        sb = Store()
        macrojobs.mdata_job(sb, "legacy", log=lambda *_: None)
        self.assertEqual(len(sb.shared.acquisitions), 1)
        self.assertIsNone(sb.shared.owner)
        self.assertIn("macro/catalog.json", sb.writes)

    def test_unavailable_lease_stops_before_source_reads(self):
        sb = Store()
        sb.rpc = lambda *_: (_ for _ in ()).throw(RuntimeError("missing RPC"))
        with self.assertRaisesRegex(RuntimeError, "lease unavailable"):
            macrojobs.mdata_job(sb, "catalog", log=lambda *_: None)
        self.assertEqual(sb.reads, [])
        self.assertEqual(sb.writes, [])

    def test_lease_loss_during_calendar_read_prevents_both_global_writes(self):
        sb = Store()

        def lose(path):
            if path == "macro/releases.json":
                lease = getattr(sb, "_aggregate_lease", None)
                if lease:
                    lease.lost = "test ownership loss"

        sb.on_read = lose
        with self.assertRaisesRegex(RuntimeError, "lease lost"):
            mstore.publish_catalog(sb, {}, log=lambda *_: None)
        self.assertEqual(sb.writes, [])

    def test_registry_read_failure_and_wrong_type_do_not_become_empty_registry(self):
        sb = Store()
        sb.get_json = lambda *_: (_ for _ in ()).throw(RuntimeError("registry unavailable"))
        with self.assertRaisesRegex(RuntimeError, "registry unavailable"):
            mstore.load_registry(sb)
        sb = Store()
        sb.shared.data["macro/registry.json"] = []
        with self.assertRaises(ValueError):
            mstore.load_registry(sb)

    def test_missing_registry_cannot_erase_an_existing_catalog(self):
        sb = Store()
        sb.shared.data.pop("macro/registry.json")
        sb.shared.data["macro/catalog.json"] = {"series": {"PEER": {"title": "Existing"}}}
        with self.assertRaisesRegex(ValueError, "registry"):
            macrojobs.mdata_job(sb, "catalog", log=lambda *_: None)
        self.assertEqual(sb.writes, ["meta/status.json"])

    def test_empty_persisted_registry_cannot_erase_an_existing_catalog(self):
        sb = Store()
        sb.shared.data["macro/catalog.json"] = {"series": {"PEER": {"title": "Existing"}}}
        with self.assertRaisesRegex(ValueError, "registry"):
            mstore.load_registry(sb)

    def test_partial_series_read_failure_cannot_replace_prior_history(self):
        sb = Store()
        sid = "TEST:OLD"
        prior = {sid: {"id": sid, "obs_count": 20, "last_date": "2026-10-31"}}
        sb.shared.data["macro/registry.json"] = copy.deepcopy(prior)
        sb.on_read = lambda path: (_ for _ in ()).throw(RuntimeError("history unavailable")) if path == "macro/s/TEST_OLD.json" else None
        incoming = [{"meta": {"id": sid, "frequency": "M"}, "obs": [("2026-11-30", "21")]}]
        with self.assertRaisesRegex(RuntimeError, "history unavailable"):
            mstore.save(sb, incoming, {}, partial=True, prev_reg=prior, log=lambda *_: None)
        self.assertEqual(sb.writes, [])
        self.assertEqual(sb.db_writes, [])

    def test_missing_registered_series_bundle_cannot_be_rebuilt_from_a_recent_window(self):
        for missing in (None, {}):
            with self.subTest(missing=missing):
                sb = Store()
                sid = "TEST:OLD"
                prior = {sid: {"id": sid, "obs_count": 20, "last_date": "2026-10-31"}}
                if missing is not None:
                    sb.shared.data["macro/s/TEST_OLD.json"] = missing
                incoming = [{"meta": {"id": sid, "frequency": "M"}, "obs": [("2026-11-30", "21")]}]
                with self.assertRaisesRegex(ValueError, "bundle"):
                    mstore.save(sb, incoming, {}, partial=True, prev_reg=prior, log=lambda *_: None)
                self.assertEqual(sb.writes, [])
                self.assertEqual(sb.db_writes, [])

    def test_release_read_error_prevents_catalog_and_calendar_replacements(self):
        for call in (lambda sb: mstore.publish_catalog(sb, {}, log=lambda *_: None),
                     lambda sb: mstore.save_releases(sb, [], log=lambda *_: None)):
            with self.subTest(call=call):
                sb = Store()
                sb.on_read = lambda path: (_ for _ in ()).throw(RuntimeError("calendar unavailable")) if path == "macro/releases.json" else None
                with self.assertRaisesRegex(RuntimeError, "calendar unavailable"):
                    call(sb)
                self.assertEqual(sb.writes, [])

    def test_missing_releases_cannot_erase_existing_catalog_calendar(self):
        sb = Store()
        sb.shared.data.pop("macro/releases.json")
        sb.shared.data["macro/catalog.json"] = {"series": {}, "calendar": [{"release": "BLS"}]}
        with self.assertRaisesRegex(ValueError, "releases"):
            mstore.publish_catalog(sb, {}, log=lambda *_: None)
        self.assertEqual(sb.writes, [])

    def test_new_store_can_initialize_without_existing_metadata(self):
        sb = Store()
        sb.shared.data.clear()
        self.assertEqual(mstore.load_registry(sb), {})
        self.assertEqual(mstore.publish_catalog(sb, {}, log=lambda *_: None), 0)
        self.assertEqual(sb.shared.data["macro/catalog.json"]["calendar"], [])

    def test_calendar_refresh_preserves_other_publishers_exactly(self):
        sb = Store()
        peer = {"release": "PEER", "release_date": "2099-03-01", "title": "Unchanged", "custom": [1, 2]}
        sb.shared.data["macro/releases.json"] = [copy.deepcopy(peer)]
        mstore.save_releases(sb, [{"release": "NEW", "release_date": "2099-02-01"}], log=lambda *_: None)
        self.assertEqual(sb.shared.data["macro/releases.json"][1], peer)


if __name__ == "__main__":
    unittest.main()
