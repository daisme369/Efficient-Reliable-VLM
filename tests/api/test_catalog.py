"""Worklist/catalog contract tests for thorax_api, using the synthetic demo seed only."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

HAS_API_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
WEB = "http://127.0.0.1:3000"


@unittest.skipUnless(HAS_API_DEPS, "requires requirements/api.txt (fastapi, httpx)")
class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from thorax_api.accounts import AccountStore
        from thorax_api.app import create_app
        from thorax_api.catalog import seed_demo

        cls.directory = tempfile.TemporaryDirectory()
        db = Path(cls.directory.name) / "thorax.sqlite3"
        accounts = AccountStore(db)
        for user_id, email, role in ((1, "doctor@example.test", "doctor"), (2, "doctor2@example.test", "doctor"), (3, "admin@example.test", "admin")):
            accounts.create(email, f"User {user_id}", role, "synthetic-pass-123", user_id=user_id)
        app = create_app(db, [WEB], image_root=str(Path(cls.directory.name) / "images"))
        seed_demo(app.state.catalog)
        seed_demo(app.state.catalog)  # idempotent
        cls.app = app
        cls.clients = {}
        for email in ("doctor@example.test", "doctor2@example.test", "admin@example.test"):
            client = TestClient(app, headers={"Origin": WEB})
            assert client.post("/api/v1/auth/login", json={"email": email, "password": "synthetic-pass-123"}).status_code == 200
            cls.clients[email] = client
        cls.doctor, cls.doctor2, cls.admin = cls.clients.values()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_worklist_matches_the_web_demo_fixture(self):
        body = self.doctor.get("/api/v1/patients").json()
        self.assertEqual([p["code"] for p in body["items"]], [f"PX-{1042 + i}" for i in range(10)])
        self.assertEqual(body["items"][0], {"id": 1, "code": "PX-1042", "name": "Eleanor Brooks", "birth_year": 1958, "sex": "Female", "study_count": 2})
        self.assertIsNone(body["next_cursor"])
        self.assertEqual([p["code"] for p in self.doctor2.get("/api/v1/patients").json()["items"]], ["PX-1042", "PX-1052", "PX-1053"])

    def test_search_and_signed_cursor_paging(self):
        self.assertEqual([p["name"] for p in self.doctor.get("/api/v1/patients", params={"search": "chen"}).json()["items"]], ["Marina Chen"])
        self.assertEqual(self.doctor.get("/api/v1/patients", params={"search": " "}).status_code, 422)
        first = self.doctor.get("/api/v1/patients", params={"limit": 4}).json()
        second = self.doctor.get("/api/v1/patients", params={"limit": 4, "cursor": first["next_cursor"]}).json()
        self.assertEqual([p["id"] for p in first["items"] + second["items"]], list(range(1, 9)))
        forged = first["next_cursor"].split(".")[0] + ".0000"
        self.assertEqual(self.doctor.get("/api/v1/patients", params={"cursor": forged}).status_code, 422)
        # A cursor is bound to the user who received it.
        self.assertEqual(self.doctor2.get("/api/v1/patients", params={"limit": 4, "cursor": first["next_cursor"]}).status_code, 422)
        self.assertEqual(self.doctor.get("/api/v1/patients", params={"limit": 101}).status_code, 422)

    def test_studies_and_study_detail(self):
        studies = self.doctor.get("/api/v1/patients/1/studies").json()["items"]
        self.assertEqual([(s["id"], s["study_date"], s["image_count"], s["status"]) for s in studies],
                         [(2411, "2026-11-06", 2, "new"), (2410, "2026-08-18", 1, "new")])
        detail = self.doctor.get("/api/v1/studies/2411").json()
        self.assertEqual([(i["id"], i["view"], i["content_url"]) for i in detail["images"]],
                         [(24110, "PA", "/api/v1/images/24110/content"), (24111, "LAT", "/api/v1/images/24111/content")])

    def test_image_content_is_the_verified_fixture(self):
        response = self.doctor.get("/api/v1/images/24110/content")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), "95f7a5c42b6dcc1c16ff975c60de27ad6d5870b205543ef78a6d025337c43332")

    def test_unassigned_and_missing_resources_look_the_same(self):
        for path in ("/api/v1/patients/11/studies", "/api/v1/studies/2521", "/api/v1/images/25210/content",
                     "/api/v1/patients/999/studies", "/api/v1/studies/abc", "/api/v1/studies/0012"):
            response = self.doctor.get(path)
            self.assertEqual((path, response.status_code, response.json()["error"]["code"]), (path, 404, "RESOURCE_NOT_FOUND"))
        self.assertEqual(self.doctor2.get("/api/v1/studies/2521").status_code, 200)

    def test_roles_and_authentication(self):
        self.assertEqual(self.admin.get("/api/v1/patients").json()["error"]["code"], "ROLE_FORBIDDEN")
        from fastapi.testclient import TestClient
        self.assertEqual(TestClient(self.app).get("/api/v1/patients").status_code, 401)


if __name__ == "__main__":
    unittest.main()
