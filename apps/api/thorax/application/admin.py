import hashlib
import json
from datetime import date
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from thorax.domain.errors import DomainError
from thorax.infrastructure.db.models import Assignment, AuthSession, Event, Image, PastReport, Patient, Study, User

from .commands import CommandService
from .context import IdentityGuard
from .pagination import Pagination


class AdminService(CommandService):
    def __init__(self, database, settings, clock, access, auth, images):
        super().__init__(database, settings, clock, access)
        self.auth, self.images = auth, images
        self.pagination = Pagination(settings.idempotency_secret, settings.policy["limits"])

    def doctors(self, actor, cursor=None, limit=None):
        with self.database.unit_of_work() as uow:
            repo = uow.repository
            self.access.user(repo, actor, "admin")
            rows, cursor = self.pagination.page(repo, select(User).where(User.role == "doctor"), f"doctors:{actor}", cursor, limit, columns=[User.id])
            return {"items": [self.auth.user_dto(u) | {"assigned_patient_ids": [a.patient_id for a in repo.all(Assignment, Assignment.doctor_id == u.id, Assignment.revoked_at.is_(None))]} for u in rows], "next_cursor": cursor}

    def create_doctor(self, actor, payload, key):
        self.validate_key(key)
        try:
            with self.database.unit_of_work() as uow:
                repo = uow.repository
                self.access.user(repo, actor, "admin")
                prior = self.replay(repo, actor, "doctor_created", key, payload)
                if prior:
                    return prior
                name, email = payload["name"].strip(), payload["email"].strip().casefold()
                if not name or len(name) > self.settings.policy["limits"]["name_chars"] or len(email) > self.settings.policy["limits"]["email_chars"] or "@" not in email:
                    raise DomainError("VALIDATION_ERROR", 422)
                user = repo.add(User(name=name, email=email, role="doctor", password_hash=self.auth.hash_password(payload["password"])))
                return self.record(repo, actor, None, "doctor_created", key, payload, "doctor", user, 201)
        except IntegrityError as error:
            raise DomainError("EMAIL_IN_USE") from error

    def doctor_state(self, actor, doctor_id, payload, key):
        with self.database.unit_of_work() as uow:
            repo = uow.repository
            self.access.user(repo, actor, "admin")
            target = repo.get(User, doctor_id, lock=True)
            if not target or target.role != "doctor":
                raise DomainError("RESOURCE_NOT_FOUND", 404)
            prior = self.replay(repo, actor, f"doctor_state:{doctor_id}", key, payload)
            if prior:
                return prior
            target.is_active = payload["is_active"]
            if not target.is_active:
                for login in repo.all(AuthSession, AuthSession.user_id == target.id, AuthSession.revoked_at.is_(None)):
                    login.revoked_at = self.clock.now()
            return self.record(repo, actor, None, f"doctor_state:{doctor_id}", key, payload, "doctor", target)

    def catalogue(self, actor, search=None, cursor=None, limit=None):
        with self.database.unit_of_work() as uow:
            repo = uow.repository
            self.access.user(repo, actor, "admin")
            query = select(Patient)
            if search:
                if len(search) > self.settings.policy["limits"]["search_chars"]:
                    raise DomainError("VALIDATION_ERROR", 422)
                query = query.where(Patient.code.contains(search, autoescape=True))
            rows, cursor = self.pagination.page(repo, query, f"catalogue:{actor}:{search}", cursor, limit, columns=[Patient.id])
            return {"items": [{"id": p.id, "code": p.code, "assigned_doctor_ids": [a.doctor_id for a in repo.all(Assignment, Assignment.patient_id == p.id, Assignment.revoked_at.is_(None))]} for p in rows], "next_cursor": cursor}

    def assign(self, actor, payload, key):
        self.validate_key(key)
        with self.database.unit_of_work() as uow:
            repo = uow.repository
            self.access.user(repo, actor, "admin")
            doctor = repo.get(User, payload["doctor_id"], lock=True)
            if not doctor or doctor.role != "doctor" or not doctor.is_active or not repo.get(Patient, payload["patient_id"]):
                raise DomainError("RESOURCE_NOT_FOUND", 404)
            prior = self.replay(repo, actor, "assignment_granted", key, payload)
            if prior:
                return prior
            assignment = repo.session.scalar(select(Assignment).where(Assignment.doctor_id == doctor.id, Assignment.patient_id == payload["patient_id"]).with_for_update())
            if assignment is None:
                assignment = repo.add(Assignment(doctor_id=doctor.id, patient_id=payload["patient_id"]))
            assignment.revoked_at = None
            return self.record(repo, actor, None, "assignment_granted", key, payload, "assignment", assignment, 201)

    def revoke(self, actor, doctor_id, patient_id):
        with self.database.unit_of_work() as uow:
            repo = uow.repository
            self.access.user(repo, actor, "admin")
            assignment = repo.session.scalar(select(Assignment).where(Assignment.doctor_id == doctor_id, Assignment.patient_id == patient_id).with_for_update())
            if assignment and assignment.revoked_at is None:
                assignment.revoked_at = self.clock.now()
                repo.event(actor, None, "assignment_revoked", assignment_id=assignment.id)

    def fixture(self, key):
        if key not in self.settings.policy["fixtures"]["allowed_keys"]:
            raise DomainError("RESOURCE_NOT_FOUND", 404)
        target = (self.settings.fixture_root / f"{key}.json").resolve()
        if not target.is_relative_to(self.settings.fixture_root):
            raise DomainError("FIXTURE_INVALID", 422)
        try:
            data = target.read_bytes()
            if len(data) > self.settings.policy["limits"]["image_bytes"]:
                raise ValueError()
            manifest = json.loads(data)
            if set(manifest) != {"fixture_key", "patients", "studies", "images", "past_reports"} or manifest["fixture_key"] != key:
                raise ValueError()
            return manifest, hashlib.sha256(data).hexdigest()
        except (OSError, ValueError, TypeError) as error:
            raise DomainError("FIXTURE_INVALID", 422) from error

    def fixture_status(self, actor, key):
        manifest, checksum = self.fixture(key)
        with self.database.unit_of_work() as uow:
            self.access.user(uow.repository, actor, "admin")
            loaded = bool(uow.repository.all(Patient, Patient.fixture_key == key))
            return {"fixture_key": key, "checksum": checksum, "status": "loaded" if loaded else "not_loaded", **{f"{singular}_count": len(manifest[plural]) for singular, plural in (("patient", "patients"), ("study", "studies"), ("image", "images"))}}

    def import_fixture(self, actor, key, idem):
        self.validate_key(idem)
        try:
            with self.database.unit_of_work() as uow:
                repo = uow.repository
                self.access.user(repo, actor, "admin")
                prior = self.replay(repo, actor, "fixture_imported", idem, {"fixture_key": key})
                if prior:
                    return prior
                manifest, checksum = self.fixture(key)
                old = repo.session.scalar(select(Event).where(Event.type == "fixture_loaded", Event.data["fixture_key"].as_string() == key))
                if old and old.data["checksum"] != checksum:
                    raise DomainError("FIXTURE_CONFLICT")
                if not old:
                    patients = {}
                    for value in manifest["patients"]:
                        if set(value) != {"id", "code", "name", "birth_year", "sex"} or not 1900 <= value["birth_year"] <= self.clock.now().year or not value["name"].strip() or not value["code"].strip():
                            raise ValueError()
                        patient = repo.add(Patient(**value, fixture_key=key))
                        patients[patient.id] = patient
                    studies = {}
                    for value in manifest["studies"]:
                        value = value.copy()
                        value["study_date"] = date.fromisoformat(value["study_date"])
                        IdentityGuard.check(value["indication"], patients[value["patient_id"]])
                        if len(value["indication"]) > self.settings.policy["limits"]["question_chars"]:
                            raise ValueError()
                        study = repo.add(Study(**value, fixture_key=key))
                        studies[study.id] = study
                    image_studies = set()
                    for value in manifest["images"]:
                        if value["study_id"] not in studies:
                            raise ValueError()
                        image = Image(**value)
                        self.images.read(image)
                        repo.add(image)
                        image_studies.add(image.study_id)
                    if image_studies != studies.keys():
                        raise ValueError()
                    for value in manifest["past_reports"]:
                        value = value.copy()
                        value["report_date"] = date.fromisoformat(value["report_date"])
                        IdentityGuard.check(value["text"], patients[value["patient_id"]])
                        if not value["text"].strip() or len(value["text"]) > self.settings.policy["limits"]["history_chars"]:
                            raise ValueError()
                        repo.add(PastReport(**value))
                    repo.event(actor, None, "fixture_loaded", fixture_key=key, checksum=checksum)
                return self.record(repo, actor, None, "fixture_imported", idem, {"fixture_key": key}, "fixture", SimpleNamespace(id=key))
        except IntegrityError as error:
            raise DomainError("FIXTURE_CONFLICT") from error
        except (ValueError, TypeError, KeyError) as error:
            raise DomainError("FIXTURE_INVALID", 422) from error
