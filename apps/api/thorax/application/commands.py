import hashlib
import hmac
import json

from sqlalchemy import select

from thorax.domain.errors import DomainError
from thorax.domain.reading import ReadingSession, SessionBinding, SessionStatus
from thorax.infrastructure.db.models import Event


class CommandService:
    def __init__(self, database, settings, clock, access):
        self.database, self.settings, self.clock, self.access = database, settings, clock, access

    def request_hash(self, payload: dict) -> str:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return hmac.new(self.settings.idempotency_secret.encode(), data, hashlib.sha256).hexdigest()

    def validate_key(self, key: str | None, *, required: bool = True):
        if key is None and not required:
            return
        policy = self.settings.policy["security"]
        if key is None or not policy["idempotency_min_chars"] <= len(key) <= policy["idempotency_max_chars"] or any(not 32 <= ord(c) <= 126 for c in key):
            raise DomainError("INVALID_IDEMPOTENCY_KEY", 422)

    def replay(self, repo, actor_id, scope, key, payload):
        self.validate_key(key, required=False)
        if key is None:
            return None
        prior = repo.session.scalar(select(Event).where(Event.user_id == actor_id, Event.idempotency_scope == scope, Event.idempotency_key == key))
        if prior and prior.request_hash != self.request_hash(payload):
            raise DomainError("IDEMPOTENCY_CONFLICT")
        return prior.response if prior else None

    def record(self, repo, actor_id, session, scope, key, payload, resource_type, row, status=200, *, changed=True):
        receipt = {"resource_type": resource_type, "resource_id": row.id}
        if session:
            if changed:
                session.revision += 1
                session.updated_at = self.clock.now()
            receipt["session_revision"] = session.revision
        repo.add(Event(user_id=actor_id, session_id=session.id if session else None, type=scope.split(":")[0] if changed else "command_unchanged", data={"resource_type": resource_type, "resource_id": row.id}, idempotency_scope=scope if key else None, idempotency_key=key, request_hash=self.request_hash(payload) if key else None, http_status=status if key else None, response=receipt if key else None))
        return receipt

    @staticmethod
    def session_domain(session):
        return ReadingSession(SessionBinding(session.doctor_id, session.patient_id, session.study_id), SessionStatus(session.status), session.revision)

    def execute(self, actor_id, session_id, scope, payload, revision, key, handler, *, required_key=False, status=200):
        self.validate_key(key, required=required_key)
        with self.database.unit_of_work() as uow:
            repo = uow.repository
            session = self.access.session(repo, actor_id, session_id)
            scope = f"{scope}:{session_id}"
            prior = self.replay(repo, actor_id, scope, key, payload)
            if prior:
                return prior
            domain = self.session_domain(session)
            domain.check_revision(revision)
            domain.assert_writable(repo.live_request(session.id) is not None)
            kind, row, *changed = handler(repo, session)
            return self.record(repo, actor_id, session, scope, key, payload, kind, row, status, changed=changed[0] if changed else True)
