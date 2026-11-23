from sqlalchemy import select

from thorax.domain.errors import DomainError
from thorax.infrastructure.db.models import Assignment, Session, Study, User


class AccessPolicy:
    @staticmethod
    def user(repo, actor_id: int, role: str):
        user = repo.get(User, actor_id, lock=True, shared=True)
        if not user or not user.is_active:
            raise DomainError("UNAUTHENTICATED", 401)
        if user.role != role:
            raise DomainError("ROLE_FORBIDDEN", 403)
        return user

    def patient(self, repo, actor_id: int, patient_id: int):
        self.user(repo, actor_id, "doctor")
        assignment = repo.session.scalar(select(Assignment).where(
            Assignment.doctor_id == actor_id, Assignment.patient_id == patient_id,
        ).with_for_update(read=True))
        if not assignment or assignment.revoked_at:
            raise DomainError("RESOURCE_NOT_FOUND", 404)
        return assignment

    def study(self, repo, actor_id: int, study_id: int):
        study = repo.get(Study, study_id)
        if not study:
            self.user(repo, actor_id, "doctor")
            raise DomainError("RESOURCE_NOT_FOUND", 404)
        self.patient(repo, actor_id, study.patient_id)
        return study

    def session(self, repo, actor_id: int, session_id: int, *, lock: bool = True):
        session = repo.get(Session, session_id)
        if not session or session.doctor_id != actor_id:
            self.user(repo, actor_id, "doctor")
            raise DomainError("RESOURCE_NOT_FOUND", 404)
        self.patient(repo, actor_id, session.patient_id)
        return repo.get(Session, session_id, lock=lock)
