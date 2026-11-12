import hashlib
import hmac
import io
import json
import os
import time
from copy import deepcopy
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from thorax.config import InferenceSettings
from thorax.domain.errors import DomainError
from thorax.inference.contract import InferenceBudget, InferenceContract
from thorax.inference.schemas import InboundContext
from thorax.presentation.body_limit import BodyLimitMiddleware
from thorax.telemetry import OperationalLog


class InferenceAPI:
    def __init__(self, settings):
        self.settings = settings
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.app.add_middleware(BodyLimitMiddleware, max_bytes=settings.policy["limits"]["image_bytes"] + settings.policy["limits"]["payload_bytes"])
        self.app.add_api_route("/internal/v1/capabilities", self.capabilities, methods=["GET"])
        for path in InferenceContract.PATHS.values():
            self.app.add_api_route(f"/internal/v1/{path}", self.invoke, methods=["POST"])

    def authorized(self, request):
        return hmac.compare_digest(request.headers.get("authorization", "").encode(), f"Bearer {self.settings.inference_token}".encode())

    async def capabilities(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "UNAUTHORIZED"}, status_code=401)
        try:
            result = InferenceContract.capabilities(self.settings, await run_in_threadpool(self.provider_capabilities))
            return InferenceContract.validate_capabilities(result, self.settings,
                required=("ready", "can_detect", "can_ask_roi"), operations=tuple(InferenceContract.PATHS))
        except (DomainError, ValueError):
            return JSONResponse({"error": "AI_UNAVAILABLE"}, status_code=503)

    async def invoke(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "UNAUTHORIZED"}, status_code=401)
        operation = request.url.path.rsplit("/", 1)[-1].replace("-", "_")
        started = time.monotonic()
        request_id = error_code = model_version = None
        status = 500
        limits = self.settings.policy["limits"]
        try:
            budget = InferenceBudget.from_header(request.headers.get(InferenceContract.DEADLINE_HEADER),
                                                 self.settings.policy["jobs"]["deadline_seconds"])
            if operation == "draft_report":
                body = await request.body()
                if len(body) > limits["payload_bytes"]:
                    raise ValueError()
                context, image = json.loads(body), None
            else:
                async with request.form(max_files=1, max_fields=1, max_part_size=limits["payload_bytes"]) as form:
                    upload, context_text = form["image"], form["context"]
                    if not isinstance(upload, UploadFile) or upload.content_type != InferenceContract.MEDIA or not isinstance(context_text, str):
                        raise ValueError()
                    image = await upload.read(limits["image_bytes"] + 1)
                    if not image or len(image) > limits["image_bytes"] or len(context_text.encode()) > limits["payload_bytes"]:
                        raise ValueError()
                    context = json.loads(context_text)
            if not isinstance(context, dict):
                raise ValueError()
            InboundContext.validate(operation, context, limits)
            request_id = context["request_id"]
            if image is not None:
                with Image.open(io.BytesIO(image)) as source:
                    if (source.format != "PNG" or max(source.size) > limits["image_side"]
                            or source.width * source.height > limits["image_pixels"]):
                        raise ValueError("Invalid canonical image")
            budget.remaining()
            output = await run_in_threadpool(self.provider_invoke, operation, context, image, budget)
            budget.remaining()
            if not isinstance(output, dict) or not InferenceContract.valid_artifact(output.get("artifact")):
                raise DomainError("AI_INVALID_OUTPUT", 422)
            model_version = output["artifact"]["version"]
            status = 200
            return output
        except DomainError as error:
            error_code = error.code
            status = {"AI_UNAVAILABLE": 503, "AI_DEADLINE_EXCEEDED": 504}.get(error.code, 422)
            return JSONResponse({"error": error_code}, status_code=status)
        except (KeyError, ValueError, TypeError, HTTPException, UnidentifiedImageError, Image.DecompressionBombError):
            status, error_code = 422, "AI_INVALID_INPUT"
            return JSONResponse({"error": error_code}, status_code=status)
        except Exception:
            error_code = "AI_UNAVAILABLE"
            return JSONResponse({"error": error_code}, status_code=status)
        finally:
            OperationalLog.write("inference", request_id=request_id, operation=operation, status=status,
                                 latency_ms=int((time.monotonic() - started) * 1000),
                                 error_code=error_code, model_version=model_version)


class MockInference(InferenceAPI):
    def __init__(self, settings, responses):
        if settings.provider_mode != "mock":
            raise ValueError("The mock service cannot be started in real mode")
        self.responses = responses
        super().__init__(settings)

    def provider_capabilities(self):
        return self.responses["capabilities"]

    def provider_invoke(self, operation, context, image, budget):
        budget.remaining()
        image_hash = hashlib.sha256(image).hexdigest() if image is not None else None
        templates = self.responses["operations"][operation]
        output = deepcopy(templates[image_hash] if image_hash in templates else templates["default"])
        delay = output.pop("delay_seconds", 0)
        if type(delay) not in {int, float} or not 0 <= delay <= self.settings.policy["jobs"]["deadline_seconds"]:
            raise ValueError("Invalid mock delay")
        if delay:
            available = budget.remaining()
            time.sleep(min(delay, available))
            if delay >= available:
                raise DomainError("AI_DEADLINE_EXCEEDED", 504)
            budget.remaining()
        error = output.pop("error", None)
        if error is not None:
            if error not in {"AI_UNAVAILABLE", "AI_INVALID_OUTPUT", "AI_CONTEXT_TOO_LARGE"}:
                raise ValueError("Invalid mock error")
            raise DomainError(error)
        if operation == "draft_report":
            output["source_item_ids"] = [i["id"] for i in context["approved_findings"]]
        return output


def create_app():
    OperationalLog.configure()
    settings = InferenceSettings.from_environment()
    if settings.provider_mode == "real":
        from thorax.inference.medgemma import RealInference
        return RealInference.from_environment(settings).app
    responses = json.loads(Path(os.environ["THORAX_MOCK_RESPONSES_PATH"]).read_text(encoding="utf-8"))
    return MockInference(settings, responses).app
