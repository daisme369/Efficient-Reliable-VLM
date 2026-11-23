from copy import deepcopy

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from thorax.domain.annotation import PixelBox
from thorax.domain.errors import DomainError
from thorax.domain.findings import FindingSet, INCLUDED_ACTIONS
from thorax.domain.report import Report as DomainReport
from thorax.infrastructure.db.models import AIRequest, Assignment, Box, Event, Findings, Image, Message, PastReport, Patient, Report, Session, Study

from .commands import CommandService
from .pagination import Pagination
from .projections import box_dto, box_snapshot, fields, finding_dto, message_dto, report_dto, request_dto


class ReadingService(CommandService):
    def __init__(self, database, settings, clock, access, images, capabilities):
        super().__init__(database, settings, clock, access)
        self.images, self.capabilities = images, capabilities
        self.limits = settings.policy["limits"]
        self.pagination = Pagination(settings.idempotency_secret, self.limits)

    def patients(self, actor, search=None, cursor=None, limit=None):
        search = (search or "").strip()
        if len(search) > self.limits["search_chars"]:
            raise DomainError("INVALID_SEARCH", 422)
        with self.database.unit_of_work() as uow:
            repo = uow.repository
            self.access.user(repo, actor, "doctor")
            query = select(Patient).join(Assignment, Assignment.patient_id == Patient.id).where(Assignment.doctor_id == actor, Assignment.revoked_at.is_(None))
            if search:
                query = query.where(func.lower(Patient.name).contains(search.casefold(), autoescape=True) | func.lower(Patient.code).contains(search.casefold(), autoescape=True))
            rows, next_cursor = self.pagination.page(repo, query, f"patients:{actor}:{search}", cursor, limit, columns=[Patient.id])
            return {"items": [fields(p, "id", "code", "name", "birth_year", "sex") | {"study_count": repo.session.scalar(select(func.count()).select_from(Study).where(Study.patient_id == p.id))} for p in rows], "next_cursor": next_cursor}

    def study_dto(self, repo, actor, study, *, detail=False):
        sessions = repo.all(Session, Session.study_id == study.id)
        own = next((s for s in sessions if s.doctor_id == actor), None)
        status = "final" if any(s.status == "final" for s in sessions) else "in_progress" if sessions else "new"
        images = repo.all(Image, Image.study_id == study.id, order_by=[Image.id])
        result = fields(study, "id", "patient_id", "study_date", "indication") | {"image_count": len(images), "status": status, "own_session_id": own.id if own else None, "own_session_status": own.status if own else None}
        if detail:
            result["images"] = [self.image_dto(i) for i in images]
        return result

    @staticmethod
    def image_dto(image):
        return fields(image, "id", "study_id", "view", "width", "height", "sha256", "media_type") | {"content_url": f"/api/v1/images/{image.id}/content"}

    def studies(self, actor, patient_id, cursor=None, limit=None):
        with self.database.unit_of_work() as uow:
            self.access.patient(uow.repository, actor, patient_id)
            rows, next_cursor = self.pagination.page(uow.repository, select(Study).where(Study.patient_id == patient_id), f"studies:{actor}:{patient_id}", cursor, limit, columns=[Study.study_date, Study.id])
            return {"items": [self.study_dto(uow.repository, actor, row) for row in rows], "next_cursor": next_cursor}

    def study(self, actor, study_id):
        with self.database.unit_of_work() as uow:
            study = self.access.study(uow.repository, actor, study_id)
            return self.study_dto(uow.repository, actor, study, detail=True)

    def image(self, actor, image_id):
        with self.database.unit_of_work() as uow:
            image = uow.repository.get(Image, image_id)
            if not image:
                self.access.user(uow.repository, actor, "doctor")
                raise DomainError("RESOURCE_NOT_FOUND", 404)
            self.access.study(uow.repository, actor, image.study_id)
            return self.images.read(image), image.media_type

    def history(self, repo, session):
        study = repo.get(Study, session.study_id)
        return repo.all(PastReport, PastReport.patient_id == session.patient_id, PastReport.report_date < study.study_date, order_by=[PastReport.report_date.desc(), PastReport.id.desc()], limit=self.limits["history_reports"])

    @staticmethod
    def finding_domain(row):
        return FindingSet(row.items, row.ai_items, row.no_abnormality_confirmed, row.status)

    def workspace(self, actor, session_id):
        with self.database.unit_of_work() as uow:
            if uow.session.bind.dialect.name == "postgresql":
                uow.session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session = self.access.session(uow.repository, actor, session_id, lock=False)
            return self._workspace(uow.repository, session)

    def _workspace(self, repo, session):
        patient, study = repo.get(Patient, session.patient_id), repo.get(Study, session.study_id)
        latest = repo.latest_findings(session.id)
        boxes = [box_dto(b) for b in repo.all(Box, Box.session_id == session.id, order_by=[Box.id])]
        reports = repo.all(Report, Report.session_id == session.id, order_by=[Report.id.desc()])
        jobs = repo.all(AIRequest, AIRequest.session_id == session.id, order_by=[AIRequest.id.desc()], limit=1)
        initial = repo.all(AIRequest, AIRequest.session_id == session.id, AIRequest.operation == "analyze", AIRequest.status == "succeeded", limit=1)
        messages, cursor = self._messages(repo, session, None, self.limits["message_page_max"])
        blockers = []
        if not latest:
            blockers.append({"gate": "gate1", "code": "ANALYSIS_REQUIRED", "resource_type": "session", "resource_id": session.id, "message": "Analyze this study first"})
        elif latest.status != "approved":
            try:
                domain = self.finding_domain(latest)
                domain.validate({b["id"]: b for b in boxes}, {i.id for i in repo.all(Image, Image.study_id == session.study_id)})
                domain.approve(boxes)
            except DomainError as error:
                blockers.append({"gate": "gate1", "code": error.code, "resource_type": "findings", "resource_id": latest.id, "message": error.code.replace("_", " ")})
        live = repo.live_request(session.id)
        if not latest or latest.status != "approved":
            blockers.append({"gate": "gate2", "code": "GATE1_REQUIRED", "resource_type": "session", "resource_id": session.id, "message": "Approve current findings first"})
        if not any(r.status == "draft" for r in reports) and session.status != "final":
            blockers.append({"gate": "gate2", "code": "REPORT_REQUIRED", "resource_type": "report", "resource_id": None, "message": "Generate a report draft"})
        if live:
            blockers.extend({"gate": gate, "code": "AI_BUSY", "resource_type": "ai_request", "resource_id": live.id, "message": "An AI request is running"} for gate in ("gate1", "gate2"))
        return {
            "session": fields(session, "id", "doctor_id", "patient_id", "study_id", "status", "revision"),
            "patient": fields(patient, "id", "code", "name", "birth_year", "sex"), "study": self.study_dto(repo, session.doctor_id, study),
            "images": [self.image_dto(i) for i in repo.all(Image, Image.study_id == study.id, order_by=[Image.id])],
            "history": [fields(h, "id", "report_date", "text") for h in self.history(repo, session)],
            "latest_findings": finding_dto(latest) if latest else None, "reports": [report_dto(r) for r in reports], "boxes": boxes,
            "messages": {"items": messages, "next_cursor": cursor}, "live_request_id": live.id if live else None,
            "latest_ai_request": request_dto(jobs[0], session.revision) if jobs else None, "blockers": blockers, "capabilities": self.capabilities,
            "initial_analysis": request_dto(initial[0], session.revision) if initial else None,
        }

    def open(self, actor, study_id):
        for attempt in range(2):
            try:
                with self.database.unit_of_work() as uow:
                    repo = uow.repository
                    study = self.access.study(repo, actor, study_id)
                    session = repo.session.scalar(select(Session).where(Session.study_id == study.id, Session.doctor_id == actor))
                    created = session is None
                    if created:
                        session = repo.add(Session(patient_id=study.patient_id, study_id=study.id, doctor_id=actor))
                        repo.event(actor, session.id, "session_created")
                    repo.event(actor, session.id, "study_opened")
                    result = self._workspace(repo, session)
                return result, created
            except IntegrityError:
                if attempt:
                    raise

    def _messages(self, repo, session, cursor, limit):
        rows, next_cursor = self.pagination.page(repo, select(Message).where(Message.session_id == session.id, Message.deleted_at.is_(None)), f"messages:{session.doctor_id}:{session.id}", cursor, limit, columns=[Message.id], message=True)
        return [message_dto(repo, m) for m in reversed(rows)], next_cursor

    def messages(self, actor, session_id, cursor=None, limit=None):
        with self.database.unit_of_work() as uow:
            session = self.access.session(uow.repository, actor, session_id, lock=False)
            rows, next_cursor = self._messages(uow.repository, session, cursor, limit)
            return {"items": rows, "next_cursor": next_cursor}

    def events(self, actor, session_id, cursor=None, limit=None):
        with self.database.unit_of_work() as uow:
            session = self.access.session(uow.repository, actor, session_id, lock=False)
            rows, next_cursor = self.pagination.page(uow.repository, select(Event).where(Event.session_id == session.id), f"events:{actor}:{session_id}", cursor, limit, columns=[Event.id])
            return {"items": [fields(e, "id", "type", "created_at", "user_id", "data") for e in rows], "next_cursor": next_cursor}

    def review_version(self, repo, session):
        latest = repo.latest_findings(session.id)
        if not latest:
            raise DomainError("ANALYSIS_REQUIRED")
        if latest.status == "approved":
            latest = repo.add(Findings(session_id=session.id, version=latest.version + 1, status="in_review", items=deepcopy(latest.items), ai_items=deepcopy(latest.ai_items), no_abnormality_confirmed=latest.no_abnormality_confirmed))
            for report in repo.all(Report, Report.session_id == session.id, Report.status == "draft"):
                report.status = "stale"
            session.status = "active"
            repo.event(session.doctor_id, session.id, "findings_version_created", findings_version=latest.version)
        latest.status = "in_review"
        return latest

    def _box(self, repo, session, box_id, *, allow_deleted=False):
        box = repo.get(Box, box_id)
        if not box or box.session_id != session.id or (box.deleted_at and not allow_deleted):
            raise DomainError("RESOURCE_NOT_FOUND", 404)
        return box

    def _box_limits(self, payload):
        for key, maximum in (("label", self.limits["label_chars"]), ("note", self.limits["note_chars"])):
            if payload.get(key) is not None and len(payload[key]) > maximum:
                raise DomainError("VALIDATION_ERROR", 422)

    def create_box(self, actor, session_id, payload, revision, key):
        def operation(repo, session):
            image = repo.get(Image, payload["image_id"])
            if not image or image.study_id != session.study_id:
                raise DomainError("RESOURCE_NOT_FOUND", 404)
            PixelBox(*payload["bbox_xyxy"]).validate(image.width, image.height)
            self._box_limits(payload)
            self.review_version(repo, session)
            return "box", repo.add(Box(session_id=session.id, study_id=session.study_id, image_id=image.id, **dict(zip(("x1", "y1", "x2", "y2"), payload["bbox_xyxy"])), label=payload.get("label"), note=payload.get("note"), source="doctor", created_by=actor, review_action="added"))
        return self.execute(actor, session_id, "box_created", payload, revision, key, operation, required_key=True, status=201)

    def edit_box(self, actor, session_id, box_id, payload, revision, key):
        def operation(repo, session):
            box = self._box(repo, session, box_id)
            self._box_limits(payload)
            image = repo.get(Image, box.image_id)
            if "bbox_xyxy" in payload:
                PixelBox(*payload["bbox_xyxy"]).validate(image.width, image.height)
            changed = any(payload[k] != box_dto(box)[k] for k in payload if k != "review_action")
            action = payload.get("review_action", box.review_action)
            if box.source == "doctor" and action not in {"added", "rejected"}:
                raise DomainError("INVALID_REVIEW_ACTION", 422)
            if box.source == "ai":
                if changed and payload.get("review_action") == "accepted":
                    raise DomainError("EDITED_ACTION_REQUIRED", 422)
                if changed:
                    action = "edited"
                original = box.ai_original
                proposed = box_dto(box) | payload
                corrected = any(proposed[k] != original.get(k) for k in ("bbox_xyxy", "label", "note"))
                if (action == "accepted" and corrected) or (action == "edited" and not corrected):
                    raise DomainError("INVALID_REVIEW_ACTION", 422)
            self.review_version(repo, session)
            for name, value in payload.items():
                if name == "bbox_xyxy":
                    for k, v in zip(("x1", "y1", "x2", "y2"), value):
                        setattr(box, k, v)
                elif name != "review_action":
                    setattr(box, name, value)
            box.review_action, box.revision, box.updated_at = action, box.revision + 1, self.clock.now()
            return "box", box
        return self.execute(actor, session_id, f"box_edited:{box_id}", payload, revision, key, operation)

    def delete_box(self, actor, session_id, box_id, revision, key):
        def operation(repo, session):
            box = self._box(repo, session, box_id, allow_deleted=True)
            if box.deleted_at:
                return "box", box, False
            if not box.deleted_at:
                self.review_version(repo, session)
                box.deleted_at, box.review_action, box.revision = self.clock.now(), "rejected", box.revision + 1
            return "box", box
        return self.execute(actor, session_id, f"box_deleted:{box_id}", {}, revision, key, operation)

    def save_findings(self, actor, session_id, payload, revision, key):
        def operation(repo, session):
            if len(payload["items"]) > self.limits["finding_count"] or any(len(i["text"]) > self.limits["finding_chars"] or len(i["location"] or "") > self.limits["location_chars"] for i in payload["items"]):
                raise DomainError("VALIDATION_ERROR", 422)
            latest = self.review_version(repo, session)
            candidate = FindingSet(payload["items"], latest.ai_items, payload["no_abnormality_confirmed"], "in_review")
            candidate.validate({b.id: box_dto(b) for b in repo.all(Box, Box.session_id == session.id)}, {i.id for i in repo.all(Image, Image.study_id == session.study_id)})
            latest.items, latest.no_abnormality_confirmed = deepcopy(candidate.items), candidate.no_abnormality_confirmed
            return "findings", latest
        return self.execute(actor, session_id, "findings_edited", payload, revision, key, operation)

    def approve_findings(self, actor, session_id, payload, revision, key):
        def operation(repo, session):
            latest = repo.latest_findings(session.id)
            if not latest or latest.version != payload["findings_version"]:
                raise DomainError("FINDINGS_VERSION_MISMATCH")
            if latest.status == "approved":
                return "findings", latest, False
            boxes = [box_dto(b) for b in repo.all(Box, Box.session_id == session.id)]
            domain = self.finding_domain(latest)
            domain.validate({b["id"]: b for b in boxes}, {i.id for i in repo.all(Image, Image.study_id == session.study_id)})
            domain.approve(boxes)
            latest.annotation_snapshot = [box_snapshot(b) for b in repo.all(Box, Box.session_id == session.id) if not b.deleted_at and b.review_action in INCLUDED_ACTIONS]
            latest.status, latest.approved_by, latest.approved_at = "approved", actor, self.clock.now()
            session.status = "findings_approved"
            return "findings", latest
        return self.execute(actor, session_id, "gate1_approved", payload, revision, key, operation, required_key=True)

    def _report(self, repo, session, report_id):
        report = repo.get(Report, report_id)
        if not report or report.session_id != session.id:
            raise DomainError("RESOURCE_NOT_FOUND", 404)
        latest = repo.latest_findings(session.id)
        if not latest:
            raise DomainError("GATE1_REQUIRED")
        return report, latest, DomainReport(report.status, report.findings_version, report.content)

    def edit_report(self, actor, session_id, report_id, payload, revision, key):
        def operation(repo, session):
            report, latest, domain = self._report(repo, session, report_id)
            domain.assert_current(latest.version, latest.status)
            if any(len(v) > self.limits["report_chars"] for v in payload.values()):
                raise DomainError("VALIDATION_ERROR", 422)
            report.content = report.content | payload
            report.updated_at = self.clock.now()
            return "report", report
        return self.execute(actor, session_id, f"report_edited:{report_id}", payload, revision, key, operation)

    def approve_report(self, actor, session_id, report_id, payload, revision, key):
        def operation(repo, session):
            report, latest, domain = self._report(repo, session, report_id)
            if payload["findings_version"] != latest.version:
                raise DomainError("REPORT_STALE")
            domain.finalize(latest.version, latest.status)
            report.status = "final"
            report.approved_by, report.approved_at = actor, self.clock.now()
            # Persist the report while the parent is writable; finality commits atomically.
            repo.session.flush([report])
            session.status = "final"
            return "report", report
        return self.execute(actor, session_id, f"gate2_approved:{report_id}", payload, revision, key, operation, required_key=True)
