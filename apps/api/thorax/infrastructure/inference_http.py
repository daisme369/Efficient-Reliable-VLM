import json

import httpx

from thorax.domain.errors import DomainError
from thorax.inference.contract import InferenceBudget, InferenceContract


class HTTPInferenceProvider:
    def __init__(self, settings):
        self.settings = settings

    def capabilities(self, *, required=("ready", "can_detect", "can_ask_roi"), operations=tuple(InferenceContract.PATHS)):
        try:
            response = httpx.get(f"{self.settings.inference_url}/internal/v1/capabilities", headers=self._headers(), timeout=self.settings.policy["jobs"]["poll_seconds"])
            response.raise_for_status()
            return InferenceContract.validate_capabilities(response.json(), self.settings,
                                                           required=required, operations=operations)
        except (httpx.HTTPError, ValueError) as error:
            raise DomainError("AI_UNAVAILABLE", 503) from error

    def _headers(self):
        return {"Authorization": f"Bearer {self.settings.inference_token}"}

    def invoke(self, operation, context, image, timeout):
        budget = InferenceBudget(timeout)
        try:
            kwargs = {"json": context} if image is None else {"files": {"image": ("image.png", image, InferenceContract.MEDIA)}, "data": {"context": json.dumps(context)}}
            headers = self._headers() | {InferenceContract.DEADLINE_HEADER: budget.wire_deadline}
            with httpx.stream("POST", f"{self.settings.inference_url}/internal/v1/{InferenceContract.PATHS[operation]}", headers=headers, timeout=budget.remaining(), **kwargs) as response:
                if response.status_code == 504:
                    raise DomainError("AI_DEADLINE_EXCEEDED")
                if response.status_code != 422:
                    response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    budget.remaining()
                    body.extend(chunk)
                    if len(body) > self.settings.policy["limits"]["payload_bytes"]:
                        raise DomainError("AI_INVALID_OUTPUT")
                budget.remaining()
                value = json.loads(body)
                if response.status_code == 422:
                    code = value.get("error") if isinstance(value, dict) else None
                    raise DomainError("AI_CONTEXT_TOO_LARGE" if code == "AI_CONTEXT_TOO_LARGE" else "AI_INVALID_OUTPUT")
                return value
        except httpx.TimeoutException as error:
            raise DomainError("AI_DEADLINE_EXCEEDED") from error
        except httpx.HTTPStatusError as error:
            code = "AI_UNAVAILABLE" if error.response.status_code >= 500 else "AI_INVALID_OUTPUT"
            raise DomainError(code) from error
        except httpx.HTTPError as error:
            raise DomainError("AI_UNAVAILABLE") from error
        except ValueError as error:
            raise DomainError("AI_INVALID_OUTPUT") from error
