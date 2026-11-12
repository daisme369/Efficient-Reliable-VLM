import hashlib
import hmac
import secrets
from datetime import UTC, timedelta

import bcrypt
from sqlalchemy import func, select

from thorax.domain.errors import DomainError
from thorax.infrastructure.db.models import AuthSession, Event, User


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class AuthService:
    def __init__(self, database, settings, clock):
        self.database, self.settings, self.clock = database, settings, clock
        self.policy = settings.policy["security"]
        self.dummy = self.hash_password(secrets.token_urlsafe(self.policy["password_max_bytes"])[:self.policy["password_max_bytes"]])

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def keyed_hash(self, text: str) -> str:
        return hmac.new(self.settings.idempotency_secret.encode(), text.encode(), hashlib.sha256).hexdigest()

    def csrf(self, token: str) -> str:
        return hmac.new(self.settings.session_secret.encode(), token.encode(), hashlib.sha256).hexdigest()

    def hash_password(self, password: str) -> str:
        raw = password.encode()
        if not self.policy["password_min_bytes"] <= len(raw) <= self.policy["password_max_bytes"]:
            raise DomainError("INVALID_PASSWORD_LENGTH", 422)
        return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=self.policy["bcrypt_rounds"])).decode()

    @staticmethod
    def user_dto(user: User) -> dict:
        return {k: getattr(user, k) for k in ("id", "name", "email", "role", "is_active")}

    def login(self, email: str, password: str, address: str) -> tuple[dict, str]:
        email = email.strip().casefold()
        current = self.clock.now()
        login_key, address_key = self.keyed_hash(email), self.keyed_hash(address)
        error = None
        with self.database.unit_of_work() as uow:
            repo = uow.repository
            for field, value in (("login_key", login_key), ("address_key", address_key)):
                attempts = uow.session.scalar(select(func.count()).select_from(Event).where(
                    Event.type == "login_failed", Event.created_at >= current - timedelta(seconds=self.policy["login_window_seconds"]),
                    Event.data[field].as_string() == value,
                ))
                if attempts >= self.policy["login_attempts"]:
                    raise DomainError("RATE_LIMITED", 429)
            user = uow.session.scalar(select(User).where(User.email == email).with_for_update(read=True))
            raw = password.encode()
            valid_length = len(raw) <= self.policy["password_max_bytes"]
            valid = bcrypt.checkpw(raw if valid_length else b"invalid", (user.password_hash if user else self.dummy).encode())
            if not valid or not valid_length or not user or not user.is_active:
                repo.event(None, None, "login_failed", login_key=login_key, address_key=address_key)
                error = DomainError("UNAUTHENTICATED", 401)
            else:
                token = secrets.token_urlsafe(self.policy["token_bytes"])
                expires = current + timedelta(minutes=self.policy["idle_minutes"])
                repo.add(AuthSession(user_id=user.id, token_hash=self.digest(token), csrf_hash=self.digest(self.csrf(token)), last_activity_at=current, expires_at=expires))
                repo.event(user.id, None, "login_succeeded")
                result = self.user_dto(user) | {"csrf_token": self.csrf(token), "expires_at": expires}
        if error:
            raise error
        return result, token

    def authenticate(self, token: str | None, *, activity: bool = False, csrf: str | None = None) -> dict:
        if not token:
            raise DomainError("UNAUTHENTICATED", 401)
        with self.database.unit_of_work() as uow:
            login = uow.session.scalar(select(AuthSession).where(AuthSession.token_hash == self.digest(token)))
            if not login:
                raise DomainError("UNAUTHENTICATED", 401)
            user = uow.repository.get(User, login.user_id, lock=True, shared=True)
            if not user or not user.is_active or login.revoked_at or utc(login.expires_at) <= self.clock.now():
                raise DomainError("UNAUTHENTICATED", 401)
            if csrf is not None and not hmac.compare_digest(login.csrf_hash, self.digest(csrf)):
                raise DomainError("CSRF_INVALID", 403)
            if activity:
                login.last_activity_at = self.clock.now()
                login.expires_at = self.clock.now() + timedelta(minutes=self.policy["idle_minutes"])
            return self.user_dto(user) | {"csrf_token": self.csrf(token), "expires_at": utc(login.expires_at)}

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.database.unit_of_work() as uow:
            login = uow.session.scalar(select(AuthSession).where(AuthSession.token_hash == self.digest(token)))
            if login:
                uow.repository.get(User, login.user_id, lock=True, shared=True)
                login = uow.session.scalar(select(AuthSession).where(AuthSession.id == login.id).with_for_update().execution_options(populate_existing=True))
            if login and login.revoked_at is None:
                login.revoked_at = self.clock.now()
                uow.repository.event(login.user_id, None, "logout")
