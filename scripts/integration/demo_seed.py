import hashlib
import json
import secrets
import shutil
from pathlib import Path

from sqlalchemy import select

from thorax.inference.contract import InferenceContract
from thorax.infrastructure.db.models import User


class DemoSeeder:
    def __init__(self, config):
        self.config = config

    def seed(self, container, source, assets, passwords, mock_path):
        fixture = {"fixture_key": self.config["demo"]["fixture_key"], "patients": [], "studies": [], "images": [], "past_reports": []}
        for patient in source["patients"]:
            fixture["patients"].append(patient | {"sex": {"Female": "F", "Male": "M"}[patient["sex"]]})
        prepared = {}
        for study in source["studies"]:
            fixture["studies"].append({k: study[k] for k in ("id", "patient_id", "study_date", "indication")})
            for image in study["images"]:
                if image["view"] not in prepared:
                    prepared[image["view"]] = container.images.prepare(assets / f"{image['view'].lower()}.png", container.settings.policy["inference"]["preprocessing_version"])
                metadata = prepared[image["view"]].copy()
                image_key = f"{image['id']}-{metadata['file_path']}"
                target = container.images.resolve(image_key)
                if not target.exists():
                    shutil.copyfile(container.images.resolve(metadata["file_path"]), target)
                metadata["file_path"] = image_key
                fixture["images"].append({"id": image["id"], "study_id": study["id"], "view": image["view"], **metadata})
        reports = {row["id"]: row for row in source["history"]}
        fixture["past_reports"] = [row | {"source_key": f"synthetic-web-{row['id']}"} for row in reports.values()]
        accounts = []
        for prefix, role, password_key in (("admin", "admin", "admin_password"), ("doctor", "doctor", "doctor_password"), ("second_doctor", "doctor", "doctor_password")):
            with container.database.unit_of_work() as uow:
                email = self.config["demo"][f"{prefix}_email"]
                user = uow.session.scalar(select(User).where(User.email == email))
                if user is None:
                    user = uow.repository.add(User(name=self.config["demo"][f"{prefix}_name"], email=email, role=role, password_hash=container.auth.hash_password(passwords[password_key])))
                accounts.append(user.id)
        self.import_fixture(container, accounts[0], fixture)
        for index, patient in enumerate(fixture["patients"]):
            container.admin.assign(accounts[0], {"doctor_id": accounts[1] if index % 2 == 0 else accounts[2], "patient_id": patient["id"]}, secrets.token_urlsafe())
        container.admin.assign(accounts[0], {"doctor_id": accounts[2], "patient_id": fixture["patients"][0]["id"]}, secrets.token_urlsafe())
        mock = self.config["mock"]
        artifact = {"id": "synthetic-integration", "version": mock["artifact_version"], "sha256": hashlib.sha256(json.dumps(mock, sort_keys=True).encode()).hexdigest()}
        empty_views = set(mock.get("empty_views", ()))
        common = {"artifact": artifact, "latency_ms": 0}
        responses = {"capabilities": {"provider_mode": "mock", "ready": True, "can_detect": True, "can_ask_roi": True, "model_artifacts": [artifact], "labels": [mock["region_label"]]}, "operations": {
            "analyze": {meta["sha256"]: common | {"findings": [] if view in empty_views else [{"local_id": "f1", "text": mock["finding"], "location": None}]} for view, meta in prepared.items()},
            "detect": {meta["sha256"]: common | {"coordinate_system": InferenceContract.COORDINATES, "detections": [] if view in empty_views else [{"local_id": "d1", "label": mock["region_label"], "bbox_xyxy": mock["bbox_xyxy"]}]} for view, meta in prepared.items()},
            "ask": {"default": common | {"answer": mock["answer"]}},
            "draft_report": {"default": common | {"comparison": "", "impression": mock["impression"], "recommendation": "", "source_item_ids": []}},
        }}
        bundle = self.config["demo"].get("supplemental_bundle")
        added = self.seed_supplement(container, bundle, accounts) if bundle else None
        if added:
            for item in added["images"]:
                digest = item["sha256"]
                responses["operations"]["analyze"][digest] = common | {"findings": []}
                responses["operations"]["detect"][digest] = common | {
                    "coordinate_system": InferenceContract.COORDINATES, "detections": [],
                }
            print(json.dumps({"phase": "supplement_loaded", "studies": len(added["studies"]),
                              "images": len(added["images"])}), flush=True)
        mock_path.write_text(json.dumps(responses, indent=2), encoding="utf-8")
        print(json.dumps({"phase": "prepared", "patients": len(fixture["patients"]), "studies": len(fixture["studies"]), "images": len(fixture["images"])}), flush=True)

    @staticmethod
    def import_fixture(container, admin_id, fixture):
        container.settings.fixture_root.mkdir(parents=True, exist_ok=True)
        manifest = container.settings.fixture_root / f"{fixture['fixture_key']}.json"
        payload = json.dumps(fixture, sort_keys=True, indent=2)
        if manifest.exists() and manifest.read_text(encoding="utf-8") != payload:
            raise ValueError("Fixture differs from the existing demo manifest. Use a separate data directory or Compose project.")
        if not manifest.exists():
            manifest.write_text(payload, encoding="utf-8")
        container.admin.import_fixture(admin_id, fixture["fixture_key"], secrets.token_urlsafe())

    def seed_supplement(self, container, bundle, accounts):
        fixture_dir = Path(__file__).resolve().parent / "fixtures"
        bundle_dir = (fixture_dir / bundle).resolve()
        if not bundle_dir.is_relative_to(fixture_dir):
            raise ValueError("Supplemental bundle must be inside the fixture directory")
        fixture = json.loads((bundle_dir / "fixture.json").read_text(encoding="utf-8"))
        paths = [(bundle_dir / item["asset"]).resolve() for item in fixture["images"]]
        if not all(path.is_relative_to(bundle_dir) for path in paths):
            raise ValueError("Supplemental image must be inside its bundle")
        if not all(path.is_file() for path in paths):
            print("Optional public radiographs are unavailable. Run scripts/integration/fetch_public_radiographs.py.", flush=True)
            return None
        images = []
        for item, path in zip(fixture["images"], paths, strict=True):
            metadata = container.images.prepare(path, container.settings.policy["inference"]["preprocessing_version"])
            if metadata["input_sha256"] != item["input_sha256"]:
                raise ValueError(f"Supplemental image checksum mismatch: {path.name}")
            images.append({key: item[key] for key in ("id", "study_id", "view")} | metadata)
        fixture["images"] = images
        self.import_fixture(container, accounts[0], fixture)

        readers = set(accounts[1:])
        emails = self.config["demo"].get("supplemental_readers", ())
        if emails:
            with container.database.unit_of_work() as uow:
                readers.update(uow.session.scalars(select(User.id).where(
                    User.email.in_(emails), User.role == "doctor", User.is_active.is_(True),
                )))
        for doctor_id in sorted(readers):
            for patient in fixture["patients"]:
                container.admin.assign(accounts[0], {"doctor_id": doctor_id, "patient_id": patient["id"]}, secrets.token_urlsafe())
        return fixture
