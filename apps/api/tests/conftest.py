import io
import secrets
import sys
import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image as PILImage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from thorax.bootstrap import Container
from thorax.config import Settings
from thorax.infrastructure.db import Database
from thorax.infrastructure.db.models import Assignment, Base, Image, PastReport, Patient, Study, User


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 11, 7, tzinfo=UTC)

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class FakeProvider:
    def __init__(self):
        self.artifact = {"id": "synthetic-fixture", "version": "test-v1", "sha256": "a" * 64}
        self.calls = []
        self.failure = None
        self.empty = False
        self.on_call = None

    def capabilities(self):
        return {"provider_mode": "mock", "ready": True, "can_detect": True, "can_ask_roi": True, "model_artifacts": [self.artifact], "labels": ["synthetic-region"]}

    def invoke(self, operation, context, image, timeout):
        from thorax.domain.errors import DomainError
        self.calls.append((operation, context, image))
        if self.on_call:
            self.on_call(operation)
        if self.failure == operation:
            raise DomainError("AI_UNAVAILABLE")
        result = {"artifact": self.artifact, "latency_ms": 1}
        if operation == "analyze":
            result["findings"] = [] if self.empty else [{"local_id": "f1", "text": "Synthetic test observation.", "location": None}]
        elif operation == "detect":
            result.update(coordinate_system="original_pixels_xyxy", detections=[] if self.empty else [{"local_id": "d1", "label": "synthetic-region", "bbox_xyxy": [1.2, 2.3, 12.4, 13.5]}])
        elif operation == "ask":
            result["answer"] = "Synthetic test answer."
        else:
            result.update(comparison="", impression="Synthetic reviewed summary.", recommendation="", source_item_ids=[i["id"] for i in context["approved_findings"]])
        return result


@pytest.fixture
def backend(tmp_path):
    with (Path(__file__).parents[1] / "policy.toml").open("rb") as stream:
        policy = tomllib.load(stream)
    policy["security"]["bcrypt_rounds"] = 4
    settings = Settings("sqlite://", secrets.token_hex(), secrets.token_hex(), "http://localhost", tmp_path / "images", tmp_path / "fixtures", "http://unused", secrets.token_hex(), "mock", policy, True)
    database = Database(settings.database_url, test_mode=True)
    Base.metadata.create_all(database.engine)
    container = Container(settings, database=database, provider=FakeProvider(), clock=FakeClock())
    password = secrets.token_urlsafe()
    with database.unit_of_work() as uow:
        repo = uow.repository
        for identity, role in ((1, "admin"), (2, "doctor"), (3, "doctor")):
            repo.add(User(id=identity, name=f"Synthetic actor {identity}", email=f"actor{identity}@example.test", role=role, password_hash=container.auth.hash_password(password)))
        for identity, name in ((1, "Synthetic Person Alpha"), (2, "Synthetic Person Beta")):
            repo.add(Patient(id=identity, name=name, code=f"SYNTHETIC-{identity}", birth_year=1990, sex="F", fixture_key="thorax-demo-v1"))
            repo.add(Study(id=identity, patient_id=identity, study_date=date(2026, 10, 1), indication="Synthetic fixture only", fixture_key="thorax-demo-v1"))
            source = tmp_path / f"source-{identity}.png"
            PILImage.new("RGB", (32, 32), (identity, 0, 0)).save(source)
            metadata = container.images.prepare(source, policy["inference"]["preprocessing_version"])
            repo.add(Image(id=identity, study_id=identity, view="PA", **metadata))
            repo.add(Assignment(doctor_id=identity + 1, patient_id=identity))
        repo.add(Assignment(doctor_id=3, patient_id=1))
        for n in range(8):
            repo.add(PastReport(patient_id=1, report_date=date(2026, 9, n + 1), text=f"Synthetic earlier report {n}", source_key=f"history-{n}"))
        repo.add(PastReport(patient_id=1, report_date=date(2026, 10, 1), text="Same-day excluded fixture", source_key="same-day"))
        repo.add(PastReport(patient_id=2, report_date=date(2026, 9, 30), text="Other-patient excluded fixture", source_key="foreign"))
    container.test_password = password
    yield container
    container.close()
