import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def load_policy() -> dict:
    with Path(os.environ["THORAX_POLICY_PATH"]).open("rb") as stream:
        return tomllib.load(stream)


@dataclass(frozen=True)
class InferenceSettings:
    inference_url: str
    inference_token: str
    provider_mode: str
    policy: dict

    @classmethod
    def from_environment(cls) -> "InferenceSettings":
        settings = cls(
            os.environ["INFERENCE_BASE_URL"].rstrip("/"),
            os.environ["INFERENCE_SERVICE_TOKEN"],
            os.environ["AI_PROVIDER_MODE"],
            load_policy(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.provider_mode not in {"mock", "real"}:
            raise ValueError("AI_PROVIDER_MODE must be mock or real")
        endpoint = urlparse(self.inference_url)
        if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username or endpoint.path or endpoint.query or endpoint.fragment:
            raise ValueError("Inference base URL must be an HTTP origin")
        if len(self.inference_token) < self.policy["security"]["secret_min_chars"] or any(c in self.inference_token for c in "<>"):
            raise ValueError("Invalid inference token")


@dataclass(frozen=True)
class Settings:
    database_url: str
    session_secret: str
    idempotency_secret: str
    app_origin: str
    image_root: Path
    fixture_root: Path
    inference_url: str
    inference_token: str
    provider_mode: str
    policy: dict
    insecure_cookie: bool = False

    @classmethod
    def from_environment(cls) -> "Settings":
        inference = InferenceSettings.from_environment()
        settings = cls(
            database_url=os.environ["DATABASE_URL"],
            session_secret=os.environ["SESSION_SECRET"],
            idempotency_secret=os.environ["IDEMPOTENCY_HASH_SECRET"],
            app_origin=os.environ["APP_ORIGIN"].rstrip("/"),
            image_root=Path(os.environ["IMAGE_STORAGE_ROOT"]).resolve(),
            fixture_root=Path(os.environ["FIXTURE_ROOT"]).resolve(),
            inference_url=inference.inference_url,
            inference_token=inference.inference_token,
            provider_mode=inference.provider_mode,
            policy=inference.policy,
            insecure_cookie=os.environ.get("ALLOW_INSECURE_LOOPBACK_COOKIE", "false").lower() == "true",
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        InferenceSettings(self.inference_url, self.inference_token, self.provider_mode, self.policy).validate()
        origin = urlparse(self.app_origin)
        if origin.scheme not in {"http", "https"} or not origin.hostname or origin.path:
            raise ValueError("APP_ORIGIN must be an HTTP origin")
        if self.insecure_cookie and origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Insecure cookies require a loopback origin")
        if origin.scheme != "https" and not self.insecure_cookie:
            raise ValueError("HTTP requires explicit loopback cookie configuration")
        secrets = (self.session_secret, self.idempotency_secret, self.inference_token)
        if min(map(len, secrets)) < self.policy["security"]["secret_min_chars"] or any("<" in value or ">" in value for value in secrets):
            raise ValueError("Application secrets are too short")
        if len(set(secrets)) != len(secrets):
            raise ValueError("Application secrets must be independent")
        if not 1 <= self.policy["security"]["password_min_bytes"] <= self.policy["security"]["password_max_bytes"] <= 72:
            raise ValueError("Password limits must fit bcrypt's UTF-8 byte limit")
        if not 4 <= self.policy["security"]["bcrypt_rounds"] <= 31:
            raise ValueError("Invalid bcrypt cost")
        if not 0 < self.policy["jobs"]["heartbeat_seconds"] < self.policy["jobs"]["lease_seconds"] < self.policy["jobs"]["deadline_seconds"]:
            raise ValueError("Worker heartbeat, lease and deadline must be ordered")
        for section in ("limits", "security", "jobs"):
            if any(isinstance(v, (int, float)) and v <= 0 for v in self.policy[section].values()):
                raise ValueError(f"Invalid positive setting in {section}")
