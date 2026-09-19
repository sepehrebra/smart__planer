"""Account contracts and password/session primitives."""

import hashlib
import secrets
from threading import BoundedSemaphore, Lock
from time import monotonic
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator
from pwdlib import PasswordHash

from .errors import AppError
from .models import Contract, PreferenceValues, Version


class Credentials(BaseModel):
    # Password whitespace is significant, unlike titles in planning contracts.
    model_config = ConfigDict(extra="forbid", frozen=True)
    email: EmailStr
    password: Annotated[SecretStr, Field(min_length=12, max_length=128)]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return str(value).strip().lower()

    @field_validator("password")
    @classmethod
    def nonblank_password(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("Password cannot consist only of whitespace.")
        return value


class Registration(Credentials):
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Choose a valid IANA timezone.") from exc
        return value


class UserView(Contract):
    id: UUID
    email: str
    timezone: str
    created_at: AwareDatetime


class SessionView(Contract):
    user: UserView
    csrf_token: str


class PreferenceRecord(PreferenceValues):
    user_id: UUID
    version: Version
    updated_at: AwareDatetime


class PreferencePut(Contract):
    expected_version: Version
    values: PreferenceValues


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def csrf_for_token(token: str) -> str:
    return hashlib.sha256(b"smartplanner-csrf-v1:" + token.encode("ascii")).hexdigest()


class Passwords:
    def __init__(self):
        self.hasher = PasswordHash.recommended()
        self.slots = BoundedSemaphore(2)
        self.dummy = self.hasher.hash(secrets.token_urlsafe(32))

    def run(self, operation):
        if not self.slots.acquire(blocking=False):
            raise AppError(429, "auth_busy", "چند لحظه بعد دوباره تلاش کنید.")
        try:
            return operation()
        finally:
            self.slots.release()

    def hash(self, password: str) -> str:
        return self.run(lambda: self.hasher.hash(password))

    def verify(self, password: str, encoded: str | None) -> bool:
        verified = self.run(lambda: self.hasher.verify(password, encoded or self.dummy))
        return bool(verified and encoded is not None)


class AuthLimiter:
    """Bounded per-process limiter; deployment-wide throttling is a later release gate."""

    def __init__(self, limit: int):
        self.limit = limit
        self.entries = {}
        self.lock = Lock()

    def check(self, client: str):
        now = monotonic()
        with self.lock:
            self.entries = {key: value for key, value in self.entries.items() if value[0] > now - 60}
            start, count = self.entries.get(client, (now, 0))
            if count >= self.limit or (client not in self.entries and len(self.entries) >= 4096):
                raise AppError(429, "auth_rate_limit", "تلاش‌های ورود زیاد است؛ یک دقیقه صبر کنید.")
            self.entries[client] = (start, count + 1)
