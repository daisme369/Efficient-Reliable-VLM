from thorax.domain.annotation import PixelBox
from thorax.infrastructure.db.models import AIRequest


def fields(row, *names):
    return {name: getattr(row, name) for name in names}


def box_dto(box):
    return fields(box, "id", "image_id", "label", "note", "source", "review_action", "revision", "deleted_at", "ai_request_id") | {"bbox_xyxy": PixelBox(box.x1, box.y1, box.x2, box.y2).as_list()}


def box_snapshot(box):
    return {"box_id": box.id, **{k: v for k, v in box_dto(box).items() if k not in {"id", "deleted_at"}}}


def finding_dto(row):
    return fields(row, "id", "version", "status", "items", "ai_items", "no_abnormality_confirmed", "annotation_snapshot", "approved_by", "approved_at")


def report_dto(row):
    return fields(row, "id", "findings_version", "status", "content", "approved_by", "approved_at", "updated_at")


def request_dto(row, revision):
    provenance = {k: v for k, v in row.provenance.items() if k != "provider_outputs"}
    provenance.update(started_at=row.started_at, completed_at=row.completed_at)
    return {
        "request_id": row.id, "operation": row.operation, "status": row.status,
        "poll_url": f"/api/v1/sessions/{row.session_id}/ai-requests/{row.id}",
        "session_revision": revision, "deadline_at": row.deadline_at, "retry_of": row.retry_of,
        "error": {"code": row.error_code, "message": row.error_code.replace("_", " ").capitalize(), "retryable": row.retryable} if row.error_code else None,
        "provenance": provenance,
        "output_refs": row.result or {}, "submitted_input": row.input_snapshot["submitted_input"],
    }


def message_dto(repo, row):
    request = repo.get(AIRequest, row.ai_request_id)
    return fields(row, "id", "role", "text", "box_id", "box_snapshot", "model_version", "latency_ms", "created_at") | {"image_id": request.input_snapshot["submitted_input"]["image_id"]}
