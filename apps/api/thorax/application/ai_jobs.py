from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from thorax.domain.errors import DomainError
from thorax.infrastructure.db.models import AIRequest

from .auth import utc
from .commands import CommandService
from .projections import request_dto


class AIJobService(CommandService):
    def __init__(self, database, settings, clock, access, context, provider):
        super().__init__(database, settings, clock, access)
        self.context, self.provider = context, provider

    def submit(self, actor, session_id, command, revision, key, *, request_id=None):
        self.validate_key(key)
        with self.database.unit_of_work() as uow:
            session = self.access.session(uow.repository, actor, session_id)
            prior = uow.session.scalar(select(AIRequest).where(AIRequest.session_id == session.id, AIRequest.actor_id == actor, AIRequest.operation == command["operation"], AIRequest.idempotency_key == key))
            if prior:
                if prior.request_hash != self.request_hash(command):
                    raise DomainError("IDEMPOTENCY_CONFLICT")
                return request_dto(prior, session.revision)
        # Readiness is external I/O and must never run under a clinical DB lock.
        capabilities = self.provider.capabilities()
        if any(capabilities.get(key) is not True for key in ("ready", "can_detect", "can_ask_roi")) or capabilities.get("provider_mode") != self.settings.provider_mode:
            raise DomainError("AI_UNAVAILABLE", 503)
        try:
            with self.database.unit_of_work() as uow:
                repo = uow.repository
                session = self.access.session(repo, actor, session_id)
                prior = repo.session.scalar(select(AIRequest).where(AIRequest.session_id == session.id, AIRequest.actor_id == actor, AIRequest.operation == command["operation"], AIRequest.idempotency_key == key))
                if prior:
                    if prior.request_hash != self.request_hash(command):
                        raise DomainError("IDEMPOTENCY_CONFLICT")
                    return request_dto(prior, session.revision)
                domain = self.session_domain(session)
                domain.check_revision(revision)
                domain.assert_writable(repo.live_request(session.id) is not None)
                snapshot = self.context.build(repo, session, command, request_id=request_id)
                if command.get("retry_of"):
                    old = repo.get(AIRequest, command["retry_of"])
                    if not old or old.session_id != session.id:
                        raise DomainError("RESOURCE_NOT_FOUND", 404)
                    if old.status not in {"failed", "timed_out"} or not old.retryable or old.operation != command["operation"]:
                        raise DomainError("INVALID_RETRY")
                    old_input = {k: v for k, v in old.input_snapshot["submitted_input"].items() if k != "retry_of"}
                    if old_input != {k: v for k, v in command.items() if k != "retry_of"}:
                        raise DomainError("INVALID_RETRY")
                    if self.context.fingerprint(snapshot) != self.context.fingerprint(old.input_snapshot):
                        raise DomainError("CONTEXT_CHANGED")
                session.revision += 1
                session.updated_at = self.clock.now()
                job = repo.add(AIRequest(session_id=session.id, actor_id=actor, operation=command["operation"], idempotency_key=key, request_hash=self.request_hash(command), input_revision=session.revision, input_snapshot=snapshot, deadline_at=self.clock.now() + timedelta(seconds=self.settings.policy["jobs"]["deadline_seconds"])))
                repo.event(actor, session.id, "ai_queued", request_id=job.id, operation=job.operation,
                           correlation_id=snapshot["context"]["request_id"])
                return request_dto(job, session.revision)
        except IntegrityError as error:
            raise DomainError("AI_BUSY") from error

    def get(self, actor, session_id, request_id):
        with self.database.unit_of_work() as uow:
            session = self.access.session(uow.repository, actor, session_id, lock=False)
            job = uow.repository.get(AIRequest, request_id)
            if not job or job.session_id != session.id:
                raise DomainError("RESOURCE_NOT_FOUND", 404)
            return request_dto(job, session.revision)
