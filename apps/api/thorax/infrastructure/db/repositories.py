from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AIRequest, Event, Findings


class Repository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, model, identity: int, *, lock: bool = False, shared: bool = False):
        query = select(model).where(model.id == identity)
        if lock:
            query = query.with_for_update(read=shared)
        return self.session.scalar(query.execution_options(populate_existing=True))

    def all(self, model, *conditions, order_by=None, limit: int | None = None):
        query = select(model).where(*conditions)
        if order_by is not None:
            query = query.order_by(*order_by)
        if limit is not None:
            query = query.limit(limit)
        return list(self.session.scalars(query))

    def add(self, row):
        self.session.add(row)
        self.session.flush()
        return row

    def latest_findings(self, session_id: int):
        return self.session.scalar(select(Findings).where(Findings.session_id == session_id).order_by(Findings.version.desc()).limit(1))

    def live_request(self, session_id: int):
        return self.session.scalar(select(AIRequest).where(AIRequest.session_id == session_id, AIRequest.status.in_(("queued", "running"))))

    def event(self, actor_id: int | None, session_id: int | None, kind: str, **data):
        return self.add(Event(user_id=actor_id, session_id=session_id, type=kind, data=data))
