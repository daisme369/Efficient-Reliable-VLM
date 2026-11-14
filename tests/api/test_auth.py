"""Authentication contract tests for thorax_api, using synthetic accounts only."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

HAS_API_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
WEB = "http://127.0.0.1:3000"
DOCTOR_PASSWORD = "synthetic-doctor-pass"


@unittest.skipUnless(HAS_API_DEPS, "requires requirements/api.txt (fastapi, httpx)")
class AuthTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from thorax_api.accounts import AccountStore
        from thorax_api.app import create_app

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        db = Path(self.directory.name) / "accounts.sqlite3"
        self.store = AccountStore(db)
        self.store.create("doctor@example.test", "Dr. Synthetic", "doctor", DOCTOR_PASSWORD, user_id=1)
        self.store.create("admin@example.test", "Synthetic admin", "admin", "synthetic-admin-pass", user_id=3)
        self.now = 1_800_000_000.0
        self.app = create_app(db, [WEB], clock=lambda: self.now)
        self.client = TestClient(self.app, headers={"Origin": WEB})

    def login(self, email="doctor@example.test", password=DOCTOR_PASSWORD, client=None):
        return (client or self.client).post("/api/v1/auth/login", json={"email": email, "password": password})

    def other_client(self):
        from fastapi.testclient import TestClient
        return TestClient(self.app, headers={"Origin": WEB})

    def test_login_sets_httponly_cookie_and_returns_frontend_user_shape(self):
        response = self.login(email="  Doctor@Example.test ")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual({k: body[k] for k in ("id", "name", "email", "role", "is_active")},
                         {"id": 1, "name": "Dr. Synthetic", "email": "doctor@example.test", "role": "doctor", "is_active": True})
        self.assertRegex(body["csrf_token"], r"^[0-9a-f]{64}$")
        self.assertTrue(body["expires_at"].endswith("Z"))
        cookie = response.headers["set-cookie"]
        self.assertRegex(cookie, r"^thorax_session=[0-9a-f]{64}; Path=/; HttpOnly; SameSite=Lax$")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_wrong_password_and_unknown_email_get_the_same_generic_error(self):
        wrong = self.login(password="not-the-password")
        unknown = self.login(email="nobody@example.test")
        for response in (wrong, unknown):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error"]["code"], "UNAUTHENTICATED")
            self.assertEqual(response.json()["error"]["message"], "Email or password is incorrect.")
            self.assertNotIn("set-cookie", response.headers)

    def test_disabled_account_cannot_sign_in_and_loses_an_existing_login(self):
        self.assertEqual(self.login().status_code, 200)
        self.store.set_active("doctor@example.test", False)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        self.assertEqual(self.login().status_code, 401)

    def test_me_requires_a_valid_cookie(self):
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        self.client.cookies.set("thorax_session", "f" * 64)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        self.client.cookies.clear()
        self.login()
        me = self.client.get("/api/v1/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["email"], "doctor@example.test")

    def test_logout_needs_csrf_and_revokes_the_login(self):
        csrf = self.login().json()["csrf_token"]
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, 403)
        response = self.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 204)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_state_changing_requests_need_an_allowed_origin(self):
        from fastapi.testclient import TestClient
        foreign = TestClient(self.app, headers={"Origin": "https://evil.example"})
        self.assertEqual(self.login(client=foreign).json()["error"]["code"], "CSRF_INVALID")
        no_origin = TestClient(self.app)
        self.assertEqual(self.login(client=no_origin).status_code, 403)

    def test_new_login_revokes_the_previous_one(self):
        first = self.other_client()
        self.login(client=first)
        self.login()
        self.assertEqual(first.get("/api/v1/auth/me").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)

    def test_idle_expiry_and_activity_extension(self):
        self.login()
        self.now += 29 * 60
        self.assertEqual(self.client.get("/api/v1/auth/me", headers={"X-User-Activity": "1"}).status_code, 200)
        self.now += 29 * 60  # 58 min after login, 29 min after the last real activity
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)
        self.now += 2 * 60  # background checks do not extend the idle window
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_absolute_lifetime_caps_activity(self):
        self.login()
        for _ in range(4):  # steady activity keeps the idle window open: 116 min after login
            self.now += 29 * 60
            self.assertEqual(self.client.get("/api/v1/auth/me", headers={"X-User-Activity": "1"}).status_code, 200)
        self.now += 5 * 60  # 121 min: past the 2 h absolute lifetime despite recent activity
        self.assertEqual(self.client.get("/api/v1/auth/me", headers={"X-User-Activity": "1"}).status_code, 401)

    def test_rate_limit_per_address(self):
        for _ in range(5):
            self.login(password="not-the-password")
        limited = self.login()
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["error"]["code"], "RATE_LIMITED")
        self.assertGreaterEqual(int(limited.headers["retry-after"]), 1)
        self.now += 61
        self.assertEqual(self.login().status_code, 200)

    def test_request_validation(self):
        extra = self.client.post("/api/v1/auth/login", json={"email": "doctor@example.test", "password": DOCTOR_PASSWORD, "role": "admin"})
        self.assertEqual(extra.status_code, 422)
        not_json = self.client.post("/api/v1/auth/login", content="email=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(not_json.status_code, 422)
        self.assertEqual(self.login(password="x" * 73).status_code, 422)

    def test_users_change_their_own_password(self):
        csrf = self.login().json()["csrf_token"]
        url = "/api/v1/auth/password"
        self.assertEqual(self.client.post(url, json={"current_password": DOCTOR_PASSWORD, "new_password": "x" * 13}).status_code, 403)  # needs CSRF
        headers = {"X-CSRF-Token": csrf}
        wrong = self.client.post(url, json={"current_password": "not-the-password", "new_password": "a-new-password-1"}, headers=headers)
        self.assertEqual(wrong.json()["error"]["code"], "PASSWORD_INCORRECT")
        self.assertEqual(self.client.post(url, json={"current_password": DOCTOR_PASSWORD, "new_password": "short"}, headers=headers).status_code, 422)
        self.assertEqual(self.client.post(url, json={"current_password": DOCTOR_PASSWORD, "new_password": DOCTOR_PASSWORD}, headers=headers).status_code, 422)
        self.assertEqual(self.client.post(url, json={"current_password": DOCTOR_PASSWORD, "new_password": "a-new-password-1"}, headers=headers).status_code, 204)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)  # still signed in
        self.client.post("/api/v1/auth/logout", headers=headers)
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login(password="a-new-password-1").status_code, 200)

    def test_password_change_attempts_are_limited(self):
        headers = {"X-CSRF-Token": self.login().json()["csrf_token"]}
        for _ in range(5):
            self.client.post("/api/v1/auth/password", json={"current_password": "wrong-guess-123", "new_password": "a-new-password-1"}, headers=headers)
        limited = self.client.post("/api/v1/auth/password", json={"current_password": DOCTOR_PASSWORD, "new_password": "a-new-password-1"}, headers=headers)
        self.assertEqual(limited.status_code, 429)

    def test_unimplemented_endpoints_require_auth_first(self):
        self.assertEqual(self.client.get("/api/v1/not-built-yet").status_code, 401)
        self.login()
        self.assertEqual(self.client.get("/api/v1/not-built-yet").json()["error"]["code"], "NOT_IMPLEMENTED")


@unittest.skipUnless(HAS_API_DEPS, "requires requirements/api.txt (fastapi, httpx)")
class AccountStoreTests(unittest.TestCase):
    def test_password_hash_round_trip_and_rules(self):
        from thorax_api.accounts import AccountStore, hash_password, password_matches

        stored = hash_password("correct horse battery")
        self.assertTrue(stored.startswith("scrypt$131072$8$1$"))
        self.assertTrue(password_matches("correct horse battery", stored))
        self.assertFalse(password_matches("wrong horse battery", stored))
        self.assertFalse(password_matches("correct horse battery", None))
        with tempfile.TemporaryDirectory() as directory:
            store = AccountStore(Path(directory) / "a.sqlite3")
            with self.assertRaises(ValueError):
                store.create("short@example.test", "Short", "doctor", "too-short")
            with self.assertRaises(ValueError):
                store.create("nurse@example.test", "Nurse", "nurse", "long-enough-pass")


if __name__ == "__main__":
    unittest.main()
