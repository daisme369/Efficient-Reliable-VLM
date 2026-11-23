import hashlib
import json
import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta

from sqlalchemy import select

from thorax.application.auth import utc
from thorax.application.context import IdentityGuard
from thorax.application.output_validation import OutputValidator
from thorax.domain.errors import DomainError
from thorax.infrastructure.db.models import AIRequest, Assignment, Box, Findings, Image, Message, Patient, Report, Session, Study, User
from thorax.telemetry import OperationalLog


class Worker:
    def __init__(self, database, settings, clock, access, provider, images):
        self.database, self.settings, self.clock, self.access = database, settings, clock, access
        self.provider, self.images = provider, images
        self.policy = settings.policy["jobs"]

    def claim(self):
        with self.database.unit_of_work() as uow:
            repo = uow.repository
            candidate = repo.session.scalar(select(AIRequest.id).where(AIRequest.status == "queued", AIRequest.deadline_at > self.clock.now()).order_by(AIRequest.id).limit(1))
            if candidate is None:
                return None
            job, session, authorized = self._locked(repo, candidate, skip_locked=True)
            if not job or job.status != "queued":
                return None
            if not authorized:
                self._finish(repo, job, session, "cancelled", "AUTHORIZATION_REVOKED")
                return None
            job.status, job.started_at = "running", self.clock.now()
            job.lease_until = self.clock.now() + timedelta(seconds=self.policy["lease_seconds"])
            repo.event(job.actor_id, job.session_id, "ai_started", request_id=job.id, operation=job.operation)
            return job.id

    def _locked(self, repo, job_id, *, skip_locked=False):
        job = repo.get(AIRequest, job_id)
        if not job:
            return None, None, False
        user = repo.get(User, job.actor_id, lock=True, shared=True)
        current = repo.get(Session, job.session_id)
        assignment = repo.session.scalar(select(Assignment).where(Assignment.doctor_id == job.actor_id, Assignment.patient_id == current.patient_id).with_for_update(read=True))
        session = repo.get(Session, current.id, lock=True)
        job = repo.session.scalar(select(AIRequest).where(AIRequest.id == job_id).with_for_update(skip_locked=skip_locked).execution_options(populate_existing=True))
        authorized = bool(user and user.is_active and assignment and assignment.revoked_at is None)
        return job, session, authorized

    def _finish(self, repo, job, session, status, code=None):
        job.status, job.error_code, job.completed_at, job.lease_until = status, code, self.clock.now(), None
        job.retryable = code in {"AI_UNAVAILABLE", "AI_DEADLINE_EXCEEDED", "WORKER_LOST"}
        session.revision += 1
        session.updated_at = self.clock.now()
        repo.event(job.actor_id, session.id, f"ai_{status}", request_id=job.id, operation=job.operation, error_code=code)

    def reap(self):
        with self.database.unit_of_work() as uow:
            ids = [j.id for j in uow.repository.all(AIRequest, AIRequest.status.in_(("queued", "running")))]
        for job_id in ids:
            with self.database.unit_of_work() as uow:
                repo = uow.repository
                job, session, authorized = self._locked(repo, job_id)
                if not job or job.status not in {"queued", "running"}:
                    continue
                if not authorized:
                    self._finish(repo, job, session, "cancelled", "AUTHORIZATION_REVOKED")
                elif utc(job.deadline_at) <= self.clock.now():
                    self._finish(repo, job, session, "timed_out", "AI_DEADLINE_EXCEEDED")
                elif job.status == "running" and job.lease_until and utc(job.lease_until) <= self.clock.now():
                    self._finish(repo, job, session, "failed", "WORKER_LOST")

    def heartbeat(self, job_id):
        with self.database.unit_of_work() as uow:
            job = uow.repository.get(AIRequest, job_id, lock=True)
            if job and job.status == "running":
                job.lease_until = self.clock.now() + timedelta(seconds=self.policy["lease_seconds"])

    def process(self, job_id):
        invocations, outputs = [], {}
        try:
            with self.database.unit_of_work() as uow:
                job, session, authorized = self._locked(uow.repository, job_id)
                if not job or job.status != "running":
                    return
                if not authorized:
                    self._finish(uow.repository, job, session, "cancelled", "AUTHORIZATION_REVOKED")
                    return
                if session.revision != job.input_revision or session.status == "final":
                    self._finish(uow.repository, job, session, "cancelled", "CONTEXT_CHANGED")
                    return
                snapshot, operation, deadline = deepcopy(job.input_snapshot), job.operation, utc(job.deadline_at)
                image = uow.repository.get(Image, snapshot["image_id"]) if snapshot["image_id"] else None
                patient = uow.repository.get(Patient, session.patient_id)
                IdentityGuard.check(snapshot["context"], patient)
            capabilities = self.provider.capabilities()
            if capabilities.get("provider_mode") != self.settings.provider_mode or not capabilities.get("ready"):
                raise DomainError("AI_UNAVAILABLE")
            validator = OutputValidator(self.settings.policy["limits"], capabilities)
            image_bytes = self.images.crop(image, snapshot["box_snapshot"]["bbox_xyxy"]) if snapshot["box_snapshot"] else self.images.read(image) if image else None
            operations = ("analyze", "detect") if operation == "analyze" else (operation,)
            for native_operation in operations:
                remaining = (deadline - self.clock.now()).total_seconds()
                if remaining <= 0:
                    raise DomainError("AI_DEADLINE_EXCEEDED")
                context = snapshot["context"] if native_operation != "detect" else {"request_id": snapshot["context"]["request_id"], "preprocessing_version": image.preprocessing_version}
                started = self.clock.now()
                invocation = {"operation": native_operation, "started_at": started.isoformat(), "status": "failed"}
                invocations.append(invocation)
                try:
                    output = self.provider.invoke(native_operation, context, image_bytes, remaining)
                    outputs[native_operation] = validator.validate(native_operation, output, snapshot, image)
                    invocation.update(status="succeeded", artifact=output["artifact"], output_hash=hashlib.sha256(json.dumps(output, sort_keys=True).encode()).hexdigest())
                except DomainError as error:
                    invocation["error_code"] = error.code
                    raise
                finally:
                    invocation.update(completed_at=self.clock.now().isoformat(), latency_ms=int((self.clock.now() - started).total_seconds() * 1000))
                    OperationalLog.write("worker_invocation", request_id=context["request_id"], job_id=job_id,
                                         operation=native_operation, status=invocation["status"],
                                         latency_ms=invocation["latency_ms"], error_code=invocation.get("error_code"),
                                         model_version=invocation.get("artifact", {}).get("version"))
            self.publish(job_id, outputs, invocations)
        except DomainError as error:
            self.fail(job_id, error.code, invocations, outputs)
        except Exception:
            # Native exceptions may contain model inputs; only persist a safe code.
            self.fail(job_id, "AI_INVALID_OUTPUT", invocations, {})

    def fail(self, job_id, code, invocations, outputs):
        with self.database.unit_of_work() as uow:
            repo = uow.repository
            job, session, authorized = self._locked(repo, job_id)
            if not job or job.status not in {"queued", "running"}:
                return
            if not authorized:
                code = "AUTHORIZATION_REVOKED"
            if session.revision != job.input_revision:
                code = "CONTEXT_CHANGED"
            status = "cancelled" if code in {"AUTHORIZATION_REVOKED", "CONTEXT_CHANGED"} else "timed_out" if code == "AI_DEADLINE_EXCEEDED" or utc(job.deadline_at) <= self.clock.now() else "failed"
            job.provenance = {"provider_mode": self.settings.provider_mode, "invocations": invocations, "provider_outputs": outputs if authorized and status != "cancelled" else {}}
            self._finish(repo, job, session, status, code)

    def publish(self, job_id, outputs, invocations):
        with self.database.unit_of_work() as uow:
            repo = uow.repository
            job, session, authorized = self._locked(repo, job_id)
            if not job or job.status != "running":
                return
            if not authorized:
                self._finish(repo, job, session, "cancelled", "AUTHORIZATION_REVOKED")
                return
            if session.revision != job.input_revision or session.status == "final":
                self._finish(repo, job, session, "cancelled", "CONTEXT_CHANGED")
                return
            if utc(job.deadline_at) <= self.clock.now():
                self._finish(repo, job, session, "timed_out", "AI_DEADLINE_EXCEEDED")
                return
            snapshot = job.input_snapshot
            if job.operation == "analyze":
                detections = outputs["detect"]["detections"]
                boxes = {}
                for item in detections:
                    original = {"bbox_xyxy": item["bbox_xyxy"], "label": item["label"], "note": None}
                    box = repo.add(Box(session_id=session.id, study_id=session.study_id, image_id=snapshot["image_id"], **dict(zip(("x1", "y1", "x2", "y2"), item["bbox_xyxy"])), label=item["label"], source="ai", created_by=session.doctor_id, ai_request_id=job.id, ai_original=original, review_action="pending"))
                    boxes[item["local_id"]] = box.id
                items = [{"id": f"ai-{job.id}-{i['local_id']}", "text": i["text"], "location": i["location"], "image_id": snapshot["image_id"], "source": "ai", "action": "pending", "box_ids": []} for i in outputs["analyze"]["findings"]]
                by_local = dict(zip([i["local_id"] for i in outputs["analyze"]["findings"]], items))
                for link in outputs["analyze"].get("evidence_links", []):
                    if set(link) != {"finding_local_id", "detection_local_id"} or link["finding_local_id"] not in by_local or link["detection_local_id"] not in boxes:
                        raise DomainError("AI_INVALID_OUTPUT")
                    by_local[link["finding_local_id"]]["box_ids"].append(boxes[link["detection_local_id"]])
                finding = repo.add(Findings(session_id=session.id, version=1, status="ai_proposed", items=items, ai_items=deepcopy(items)))
                job.result = {"findings_version": finding.version, "box_ids": list(boxes.values())}
            elif job.operation == "ask":
                output = outputs["ask"]
                rows = []
                for role, text in (("doctor", snapshot["context"]["question"]), ("ai", output["answer"])):
                    message = repo.add(Message(session_id=session.id, ai_request_id=job.id, role=role, text=text, box_id=snapshot["submitted_input"].get("box_id"), box_snapshot=snapshot["box_snapshot"], model_version=output["artifact"]["version"] if role == "ai" else None, latency_ms=output["latency_ms"] if role == "ai" else None))
                    rows.append(message.id)
                job.result = {"message_ids": rows}
            else:
                latest = repo.latest_findings(session.id)
                if not latest or latest.version != snapshot["source_findings_version"] or latest.status != "approved":
                    raise DomainError("CONTEXT_CHANGED")
                for old in repo.all(Report, Report.session_id == session.id, Report.status == "draft"):
                    old.status = "stale"
                repo.session.flush()
                patient, study = repo.get(Patient, session.patient_id), repo.get(Study, session.study_id)
                analyzed = repo.session.scalar(select(AIRequest).where(AIRequest.session_id == session.id, AIRequest.operation == "analyze", AIRequest.status == "succeeded"))
                from thorax.application.projections import fields
                source_items = snapshot["context"]["approved_findings"]
                content = {"patient": fields(patient, "id", "code", "name", "birth_year", "sex"), "study": fields(study, "id", "indication") | {"study_date": study.study_date.isoformat(), "image_ids": [i.id for i in repo.all(Image, Image.study_id == study.id)], "analyzed_image_id": analyzed.input_snapshot["image_id"]}, "findings": "\n".join(i["text"] for i in source_items) if source_items else self.settings.policy["report"]["normal_statement"], **{k: outputs["draft_report"][k] for k in ("comparison", "impression", "recommendation", "source_item_ids")}}
                report = repo.add(Report(session_id=session.id, findings_version=latest.version, ai_request_id=job.id, status="draft", content=content))
                job.result = {"report_id": report.id}
            job.provenance = {"provider_mode": self.settings.provider_mode, "image_sha256": snapshot["image_sha256"], "history_report_ids": snapshot["history_report_ids"], "message_ids": snapshot["message_ids"], "box_snapshot": snapshot["box_snapshot"], "source_findings_version": snapshot["source_findings_version"], "prompt_version": snapshot["context"]["prompt_version"], "preprocessing_version": self.settings.policy["inference"]["preprocessing_version"], "invocations": invocations, "provider_outputs": outputs}
            job.provenance.update(vlm_artifact=next((value["artifact"] for operation, value in outputs.items() if operation != "detect"), None), cv_artifact=outputs.get("detect", {}).get("artifact"), latency_ms=int((self.clock.now() - utc(job.created_at)).total_seconds() * 1000))
            self._finish(repo, job, session, "succeeded")

    def run(self, stop: threading.Event):
        OperationalLog.configure()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future, current, last_heartbeat = None, None, 0.0
            while not stop.is_set():
                try:
                    self.reap()
                    if future and future.done():
                        future.result()
                        future, current = None, None
                    if current and time.monotonic() - last_heartbeat >= self.policy["heartbeat_seconds"]:
                        self.heartbeat(current)
                        last_heartbeat = time.monotonic()
                    if future is None:
                        current = self.claim()
                        if current:
                            future = executor.submit(self.process, current)
                            last_heartbeat = time.monotonic()
                except Exception:
                    logging.getLogger(__name__).error("Worker cycle failed; retained jobs will be recovered")
                stop.wait(self.policy["poll_seconds"])


def main():
    from thorax.bootstrap import Container
    from thorax.config import Settings
    container = Container(Settings.from_environment())
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        container.worker.run(stop)
    except KeyboardInterrupt:
        stop.set()
    finally:
        container.close()


if __name__ == "__main__":
    main()
