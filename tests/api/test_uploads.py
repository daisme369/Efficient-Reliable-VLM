"""Patient creation and image upload tests for thorax_api, using generated synthetic images only."""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tests.api.dbtarget import fresh_db  # noqa: E402

HAS_API_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx", "PIL"))
WEB = "http://127.0.0.1:3000"


def synthetic_image(fmt: str, size=(64, 72), mode="L", **save) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new(mode, size, 90).save(out, format=fmt, **save)
    return out.getvalue()


@unittest.skipUnless(HAS_API_DEPS, "requires requirements/api.txt (fastapi, httpx, pillow)")
class UploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from thorax_api.accounts import AccountStore
        from thorax_api.app import create_app
        from thorax_api.catalog import seed_demo

        cls.directory = tempfile.TemporaryDirectory()
        db = fresh_db(cls.directory.name)
        accounts = AccountStore(db)
        for user_id, email, role in ((1, "doctor@example.test", "doctor"), (2, "doctor2@example.test", "doctor"), (3, "admin@example.test", "admin"),
                                     (6, "tech@example.test", "technician")):
            accounts.create(email, f"User {user_id}", role, "synthetic-pass-123", user_id=user_id)
        cls.app = create_app(db, [WEB], image_root=str(Path(cls.directory.name) / "images"))
        seed_demo(cls.app.state.catalog)
        clients = []
        for email in ("doctor@example.test", "doctor2@example.test", "admin@example.test", "tech@example.test"):
            client = TestClient(cls.app, headers={"Origin": WEB})
            response = client.post("/api/v1/auth/login", json={"email": email, "password": "synthetic-pass-123"})
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            clients.append(client)
        cls.doctor, cls.doctor2, cls.admin, cls.tech = clients

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def new_study(self, client, patient_id, data, media="image/png", **params):
        query = {"study_date": "2026-11-01", "indication": "Synthetic upload check", "view": "PA", "permitted": "yes", **params}
        return client.post(f"/api/v1/patients/{patient_id}/studies", params=query, content=data, headers={"Content-Type": media})

    def test_technician_marks_study_urgent(self):
        r = self.new_study(self.tech, 2, synthetic_image("PNG"), priority="urgent")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["priority"], "urgent")
        listed = self.doctor.get("/api/v1/patients/2/studies").json()["items"]
        self.assertEqual(next(s for s in listed if s["id"] == r.json()["id"])["priority"], "urgent")
        self.assertEqual(self.doctor.get("/api/v1/patients").json()["items"][0]["id"], 2)  # a patient with an unread urgent study comes first

    def test_priority_defaults_to_routine_and_rejects_unknown(self):
        self.assertEqual(self.new_study(self.tech, 1, synthetic_image("PNG")).json()["priority"], "routine")
        self.assertEqual(self.new_study(self.tech, 1, synthetic_image("PNG"), priority="stat").status_code, 422)

    def test_technician_registers_and_pushes_a_study_and_the_assigned_doctor_reads_it(self):
        created = self.tech.post("/api/v1/admin/patients", json={"name": "Synthetic Upload Patient", "birth_year": 1970, "sex": "Other"})
        self.assertEqual(created.status_code, 201)
        pid = created.json()["resource_id"]
        self.assertRegex(created.json()["code"], r"^PX-\d{4}$")
        self.assertEqual(self.doctor.post("/api/v1/admin/patients", json={"name": "x"}).status_code, 403)
        self.assertEqual(self.admin.post("/api/v1/admin/patients", json={"name": "x", "birth_year": 1800}).status_code, 422)
        self.assertEqual(self.tech.post("/api/v1/admin/assignments", json={"doctor_id": 1, "patient_id": pid}).status_code, 403)  # admins assign

        jpeg = synthetic_image("JPEG")
        self.assertEqual(self.new_study(self.doctor, 1, jpeg, "image/jpeg").status_code, 403)  # doctors read; they do not upload
        response = self.new_study(self.tech, pid, jpeg, "image/jpeg")
        self.assertEqual(response.status_code, 201)
        study = response.json()
        self.assertEqual((study["patient_id"], study["image_count"], study["images"][0]["media_type"]), (pid, 1, "image/png"))
        self.assertEqual(self.doctor.get(f"/api/v1/patients/{pid}/studies").status_code, 404)  # not assigned yet
        self.assertEqual(self.tech.get(study["images"][0]["content_url"]).status_code, 403)  # intake staff do not read images
        self.assertEqual(self.tech.post(f"/api/v1/studies/{study['id']}/sessions").status_code, 403)
        self.assertEqual(self.admin.post("/api/v1/admin/assignments", json={"doctor_id": 1, "patient_id": pid}).status_code, 201)
        self.assertEqual(self.doctor.get(f"/api/v1/patients/{pid}/studies").json()["items"][0]["id"], study["id"])
        content = self.doctor.get(study["images"][0]["content_url"])
        self.assertEqual((content.status_code, content.content[:8]), (200, b"\x89PNG\r\n\x1a\n"))
        actions = [event["action"] for event in self.admin.get("/api/v1/admin/events").json()["items"]]
        self.assertIn("study_uploaded", actions)
        self.assertIn("patient_created", actions)

    def test_uploads_are_reencoded_without_metadata(self):
        from PIL import Image

        exif = Image.Exif()
        exif[0x010E] = "Secret Name"  # ImageDescription
        exif[0x0112] = 6  # Orientation: rotate 90 degrees on display
        response = self.new_study(self.admin, 3, synthetic_image("JPEG", size=(64, 40), exif=exif.tobytes()), "image/jpeg")
        self.assertEqual(response.status_code, 201)
        image = response.json()["images"][0]
        self.assertEqual((image["width"], image["height"]), (40, 64))  # orientation applied
        stored = self.app.state.catalog.image_bytes(self.app.state.catalog.image(image["id"]))
        self.assertNotIn(b"Secret Name", stored)

    def test_upload_validation(self):
        png = synthetic_image("PNG")
        cases = [
            (self.new_study(self.tech, 1, png, permitted="no"), 422),
            (self.new_study(self.tech, 1, png, view="CT"), 422),
            (self.new_study(self.tech, 1, png, study_date="2999-01-01"), 422),
            (self.new_study(self.tech, 1, png, indication=""), 422),
            (self.new_study(self.tech, 1, synthetic_image("GIF", mode="P"), "image/gif"), 415),
            (self.new_study(self.tech, 1, b"not an image", "image/png"), 422),
            (self.new_study(self.tech, 1, synthetic_image("PNG", size=(4100, 4100))), 422),
            (self.new_study(self.tech, 1, b"\0" * (10 * 1024 * 1024 + 1)), 413),
            (self.new_study(self.doctor2, 3, png), 403),  # doctors do not upload
            (self.new_study(self.tech, 999999, png), 404),  # no such patient
        ]
        self.assertEqual([response.status_code for response, _ in cases], [status for _, status in cases])
        self.assertEqual(self.new_study(self.tech, 1, synthetic_image("PNG", mode="I;16")).status_code, 201)

    def test_images_are_fixed_once_a_reading_starts(self):
        study = self.new_study(self.tech, 2, synthetic_image("PNG")).json()
        lateral = synthetic_image("PNG", size=(50, 60))
        url = f"/api/v1/studies/{study['id']}/images"
        added = self.tech.post(url, params={"view": "LAT", "permitted": "yes"}, content=lateral, headers={"Content-Type": "image/png"})
        self.assertEqual((added.status_code, added.json()["image_count"]), (201, 2))
        self.assertEqual(self.doctor.post(f"/api/v1/studies/{study['id']}/sessions").status_code, 201)
        locked = self.tech.post(url, params={"view": "LAT", "permitted": "yes"}, content=lateral, headers={"Content-Type": "image/png"})
        self.assertEqual((locked.status_code, locked.json()["error"]["code"]), (409, "STUDY_IN_READING"))


    def test_uploaded_studies_can_be_edited_and_deleted_until_a_reading_starts(self):
        study = self.new_study(self.tech, 4, synthetic_image("PNG", size=(33, 44))).json()  # unique bytes: files are shared by content
        self.assertTrue(study["editable"])
        url = f"/api/v1/studies/{study['id']}"
        self.assertEqual(self.doctor.patch(url, json={"study_date": "2026-10-30", "indication": "x"}).status_code, 403)
        edited = self.tech.patch(url, json={"study_date": "2026-10-30", "indication": "  Corrected indication "})
        self.assertEqual((edited.status_code, edited.json()["study_date"], edited.json()["indication"]), (200, "2026-10-30", "Corrected indication"))
        self.assertEqual(self.tech.patch(url, json={"study_date": "2999-01-01", "indication": "x"}).status_code, 422)

        second = self.tech.post(f"{url}/images", params={"view": "LAT", "permitted": "yes"}, content=synthetic_image("PNG", size=(30, 40)),
                                  headers={"Content-Type": "image/png"}).json()["images"]
        first_id, second_id = second[0]["id"], second[1]["id"]
        self.assertEqual(self.tech.delete(f"{url}/images/{second_id}").json()["image_count"], 1)
        last = self.tech.delete(f"{url}/images/{first_id}")
        self.assertEqual((last.status_code, last.json()["error"]["code"]), (409, "LAST_IMAGE"))
        self.assertEqual(self.doctor2.delete(url).status_code, 403)  # doctors do not manage uploads

        # The admin sees the patient's studies too, and can delete the study; its file goes with it.
        self.assertIn(study["id"], [s["id"] for s in self.admin.get("/api/v1/patients/4/studies").json()["items"]])
        key = self.app.state.catalog.image(first_id)["storage_key"]
        self.assertEqual(self.admin.delete(url).status_code, 204)
        self.assertEqual(self.doctor.get(url).status_code, 404)
        with self.assertRaises(OSError):
            self.app.state.catalog.images.get(key)
        actions = [event["action"] for event in self.admin.get("/api/v1/admin/events").json()["items"]]
        self.assertTrue({"study_edited", "image_deleted", "study_deleted"} <= set(actions))

    def test_demo_and_read_studies_are_locked(self):
        demo = self.doctor.get("/api/v1/studies/2411").json()
        self.assertFalse(demo["editable"])
        self.assertEqual(self.tech.delete("/api/v1/studies/2411").json()["error"]["code"], "STUDY_LOCKED")
        study = self.new_study(self.tech, 5, synthetic_image("PNG")).json()
        self.assertEqual(self.doctor.post(f"/api/v1/studies/{study['id']}/sessions").status_code, 201)
        self.assertFalse(self.doctor.get(f"/api/v1/studies/{study['id']}").json()["editable"])
        self.assertEqual(self.tech.patch(f"/api/v1/studies/{study['id']}", json={"study_date": "2026-11-01", "indication": "x"}).status_code, 409)
        self.assertEqual(self.admin.delete(f"/api/v1/studies/{study['id']}").status_code, 409)


if __name__ == "__main__":
    unittest.main()
