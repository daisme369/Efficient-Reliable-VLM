"""SQLite -> PostgreSQL copy (thorax_api.migrate). Runs only with THORAX_TEST_DATABASE_URL (tests/api/dbtarget.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tests.api import dbtarget  # noqa: E402
from tests.api.dbtarget import fresh_db  # noqa: E402

HAS_API_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
WEB = "http://127.0.0.1:3000"
PASSWORD = "synthetic-pass-123"


@unittest.skipUnless(HAS_API_DEPS and dbtarget.URL, "needs requirements/api.txt and THORAX_TEST_DATABASE_URL")
class MigrateTests(unittest.TestCase):
    def test_copies_every_table_and_the_app_keeps_working(self):
        from fastapi.testclient import TestClient
        from thorax_api.accounts import AccountStore
        from thorax_api.app import create_app
        from thorax_api.catalog import seed_demo
        from thorax_api.migrate import migrate

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "old.sqlite3"
            accounts = AccountStore(source)
            accounts.create("doctor@example.test", "Doctor One", "doctor", PASSWORD, user_id=1)
            accounts.create("doctor2@example.test", "Doctor Two", "doctor", PASSWORD, user_id=2)  # the demo seed assigns doctors 1 and 2
            app = create_app(source, [WEB], image_root=str(Path(directory) / "images"))
            seed_demo(app.state.catalog)
            doctor = TestClient(app, headers={"Origin": WEB})
            doctor.headers["X-CSRF-Token"] = doctor.post("/api/v1/auth/login", json={"email": "doctor@example.test", "password": PASSWORD}).json()["csrf_token"]
            opened = doctor.post("/api/v1/studies/2411/sessions").json()["session"]["id"]

            target = fresh_db(directory)
            counts = migrate(str(source), target)
            self.assertEqual((counts["users"], counts["patients"], counts["reading_sessions"]), (2, 12, 1))
            with self.assertRaises(ValueError):  # a second run must not mix data into a used database
                migrate(str(source), target)

            moved = TestClient(create_app(target, [WEB], image_root=str(Path(directory) / "images")), headers={"Origin": WEB})
            signed_in = moved.post("/api/v1/auth/login", json={"email": "doctor@example.test", "password": PASSWORD})
            self.assertEqual(signed_in.status_code, 200, signed_in.text)
            moved.headers["X-CSRF-Token"] = signed_in.json()["csrf_token"]
            self.assertEqual(moved.post("/api/v1/studies/2411/sessions").json()["session"]["id"], opened)  # same reading, same id
            created = moved.post("/api/v1/studies/2421/sessions").json()["session"]["id"]
            self.assertGreater(created, opened)  # new ids continue after the copied ones


if __name__ == "__main__":
    unittest.main()
