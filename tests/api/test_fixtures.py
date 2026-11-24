"""Insert-only synthetic fixture import (TAS-9): validation, checksum no-op and conflict refusal."""

from __future__ import annotations

import contextlib
import copy
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tests.api.dbtarget import fresh_db  # noqa: E402
from tests.api import dbtarget  # noqa: E402
from thorax_api.db import connect  # noqa: E402

from thorax_api import catalog as catalog_module  # noqa: E402
from thorax_api.accounts import AccountStore  # noqa: E402
from thorax_api.catalog import Catalog, FixtureError, bundle_problems, demo_bundle, seed_demo  # noqa: E402
from thorax_api.storage import LocalImageStore  # noqa: E402



class FixtureImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = tempfile.TemporaryDirectory()
        cls.template_db = Path(cls.template.name) / "thorax.sqlite3"
        accounts = AccountStore(cls.template_db)
        for user_id in (1, 2):
            accounts.create(f"doctor{user_id}@example.test", f"Doctor {user_id}", "doctor", "synthetic-pass-123", user_id=user_id)

    @classmethod
    def tearDownClass(cls):
        cls.template.cleanup()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        if dbtarget.URL:
            self.db = fresh_db(self.root)
            accounts = AccountStore(self.db)
            for user_id in (1, 2):
                accounts.create(f"doctor{user_id}@example.test", f"Doctor {user_id}", "doctor", "synthetic-pass-123", user_id=user_id)
        else:
            self.db = self.root / "thorax.sqlite3"
            shutil.copy(self.template_db, self.db)
        self.catalog = Catalog(self.db, LocalImageStore(self.root / "images"))
        self.bundle = demo_bundle()

    def count(self, table):
        with connect(self.db) as db:
            return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def rejected(self, bundle, message):
        with self.assertRaisesRegex(FixtureError, message):
            seed_demo(self.catalog, bundle=bundle)
        self.assertEqual(self.count("patients"), 0)
        self.assertFalse((self.root / "images").exists(), "nothing is written for an invalid bundle")

    def test_demo_bundle_meets_the_fixture_definition(self):
        self.assertEqual(bundle_problems(self.bundle, {1, 2}), [])
        self.assertEqual(len(self.bundle["patients"]), 12)
        doctors = {d: {p for doctor, p in self.bundle["assignments"] if doctor == d} for d in (1, 2)}
        self.assertEqual(doctors[1] & doctors[2], {1})
        self.assertTrue(doctors[1] - doctors[2] and doctors[2] - doctors[1])
        per_patient = [sum(row[1] == p for row in self.bundle["history"]) for p in range(1, 13)]
        self.assertIn(0, per_patient)
        self.assertGreater(max(per_patient), 5)

    def test_wrong_media_type_and_corrupt_bytes_are_rejected(self):
        bundle = copy.deepcopy(self.bundle)
        key = bundle["images"][0][-1]
        bundle["files"][key] = b"\xff\xd8not a png"
        self.rejected(bundle, "do not match the declared media type")
        bundle["files"][key] = self.bundle["files"][key][:-12]
        self.rejected(bundle, "not a complete PNG")

    def test_oversized_image_is_rejected(self):
        with mock.patch.object(catalog_module, "MAX_IMAGE_BYTES", 1000):
            self.rejected(self.bundle, "exceeds the size limit")

    def test_metadata_chunk_is_rejected(self):
        bundle = copy.deepcopy(self.bundle)
        key = bundle["images"][0][-1]
        data = bundle["files"][key]
        text = b"Comment\x00Eleanor Brooks"
        chunk = len(text).to_bytes(4, "big") + b"tEXt" + text + b"\x00\x00\x00\x00"
        bundle["files"][key] = data[:33] + chunk + data[33:]  # after the 8-byte signature and IHDR
        self.rejected(bundle, "contains metadata")

    def test_missing_references_are_rejected(self):
        bundle = copy.deepcopy(self.bundle)
        del bundle["files"][bundle["images"][0][-1]]
        self.rejected(bundle, "references a missing file")
        bundle = copy.deepcopy(self.bundle)
        bundle["assignments"].append((99, 1))
        self.rejected(bundle, "missing doctor 99")
        bundle = copy.deepcopy(self.bundle)
        bundle["studies"].append((9999, 77, "2026-11-01", "Follow-up"))
        self.rejected(bundle, "study 9999 references a missing patient")

    def test_identity_text_in_narratives_is_rejected(self):
        bundle = copy.deepcopy(self.bundle)
        row = bundle["history"][0]
        bundle["history"][0] = (*row[:3], "Prior film for ELEANOR BROOKS.")
        self.rejected(bundle, "contains patient identity text")
        bundle = copy.deepcopy(self.bundle)
        row = bundle["studies"][0]
        bundle["studies"][0] = (*row[:3], "Follow-up for px-1042")
        self.rejected(bundle, "contains patient identity text")

    def test_same_checksum_is_a_no_op(self):
        self.assertEqual(seed_demo(self.catalog), "imported")
        counts = [self.count(t) for t in ("patients", "studies", "images", "assignments", "history_reports")]
        self.assertEqual(seed_demo(self.catalog), "unchanged")
        self.assertEqual([self.count(t) for t in ("patients", "studies", "images", "assignments", "history_reports")], counts)

    def test_changed_bundle_never_overwrites(self):
        seed_demo(self.catalog)
        changed = copy.deepcopy(self.bundle)
        changed["patients"][0] = (1, "PX-1042", "Renamed Patient", 1958, "Female")
        with self.assertRaisesRegex(FixtureError, "checksum differs"):
            seed_demo(self.catalog, bundle=changed)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT name FROM patients WHERE id = 1").fetchone()[0], "Eleanor Brooks")

    def test_stored_image_is_never_replaced(self):
        key = self.bundle["images"][0][-1]
        self.catalog.images.put(key, b"different bytes")
        with self.assertRaisesRegex(FixtureError, "stored image differs"):
            seed_demo(self.catalog)
        self.assertEqual(self.catalog.images.get(key), b"different bytes")
        self.assertEqual(self.count("patients"), 0)

    def test_database_seeded_before_receipts_is_accepted_only_when_identical(self):
        bundle = self.bundle
        with connect(self.db) as db:
            db.executemany("INSERT INTO patients VALUES (?, ?, ?, ?, ?)", bundle["patients"])
            db.executemany("INSERT INTO studies (id, patient_id, study_date, indication) VALUES (?, ?, ?, ?)", bundle["studies"])
            db.executemany("INSERT INTO images (id, study_id, view, width, height, media_type, storage_key, sha256) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, 'x')", bundle["images"])
            # An admin-created patient with an uploaded study (added after the seed) is not a conflict.
            db.execute("INSERT INTO patients VALUES (13, 'PX-1054', 'Uploaded Patient', 1980, 'Male')")
            db.execute("INSERT INTO studies (id, patient_id, study_date, indication) VALUES (9001, 13, '2026-11-18', 'Uploaded study')")
        self.assertEqual(seed_demo(self.catalog), "imported")
        self.assertEqual(seed_demo(self.catalog), "unchanged")
        self.assertGreater(self.count("history_reports"), 0)

    def test_database_missing_or_changing_a_fixture_row_is_refused(self):
        with connect(self.db) as db:
            db.executemany("INSERT INTO patients VALUES (?, ?, ?, ?, ?)", [(1, "PX-1042", "Someone Else", 1958, "Female")])
        with self.assertRaisesRegex(FixtureError, "existing patients differ"):
            seed_demo(self.catalog)
        self.assertEqual(self.count("fixture_imports"), 0)


if __name__ == "__main__":
    unittest.main()
