"""Backup/restore check and percentile math of scripts/verify_demo.py (TAS-26), on a tiny synthetic database."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("verify_demo", ROOT / "scripts/verify_demo.py")
verify_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_demo)


class VerifyDemoTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db, self.images = self.root / "thorax.sqlite3", self.root / "thorax-images"
        (self.images / "demo").mkdir(parents=True)
        data = b"synthetic image bytes"
        (self.images / "demo/pa.png").write_bytes(data)
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE images (id INTEGER PRIMARY KEY, storage_key TEXT, sha256 TEXT)")
            db.execute("CREATE TABLE reports (id INTEGER PRIMARY KEY, status TEXT, approved_by INTEGER, approved_at TEXT)")
            db.execute("INSERT INTO images VALUES (1, 'demo/pa.png', ?)", (hashlib.sha256(data).hexdigest(),))
            db.execute("INSERT INTO reports VALUES (1, 'final', 1, '2026-11-19T00:00:00Z')")

    def test_backup_restores_and_detects_tampering(self):
        out = self.root / "backup"
        self.assertEqual(verify_demo.backup(self.db, self.images, out)["image_files"], 1)
        restored = verify_demo.check(out / "thorax.sqlite3", out / "thorax-images")
        self.assertEqual((restored["passed"], restored["images_checked"], restored["final_reports"]), (True, 1, 1))
        (out / "thorax-images/demo/pa.png").write_bytes(b"changed")
        self.assertFalse(verify_demo.check(out / "thorax.sqlite3", out / "thorax-images")["passed"])
        with self.assertRaises(FileExistsError):
            verify_demo.backup(self.db, self.images, out)  # never overwrite an existing backup

    def test_unsigned_final_report_and_missing_database_fail(self):
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE reports SET approved_by = NULL")
        self.assertIn("1 final reports lack a signature", verify_demo.check(self.db, self.images)["problems"])
        self.assertFalse(verify_demo.check(self.root / "absent.sqlite3", self.images)["passed"])

    def test_nearest_rank_percentiles(self):
        values = list(range(1, 21))
        self.assertEqual((verify_demo.nearest_rank(values, 50), verify_demo.nearest_rank(values, 95)), (10, 19))


if __name__ == "__main__":
    unittest.main()
