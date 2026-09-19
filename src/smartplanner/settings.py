"""Explicit environment configuration; no provider credentials are needed in phase 1."""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_url: str
    origin: str = "http://localhost:8000"
    environment: str = "development"
    cookie_secure: bool = False
    session_hours: int = 24
    auth_requests_per_minute: int = 10

    def __post_init__(self):
        if not self.database_url:
            raise ValueError("DATABASE_URL is required.")
        origin = urlsplit(self.origin)
        if (origin.scheme not in {"http", "https"} or not origin.hostname
                or origin.path or origin.query or origin.fragment or origin.username):
            raise ValueError("APP_ORIGIN must be an exact http(s) origin without a path.")
        if self.environment not in {"development", "production"}:
            raise ValueError("APP_ENV must be development or production.")
        if self.environment == "production" and (not self.cookie_secure or origin.scheme != "https"):
            raise ValueError("Production requires HTTPS and secure session cookies.")
        if not self.cookie_secure and origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Insecure development cookies are allowed only on loopback origins.")
        if not 1 <= self.session_hours <= 168:
            raise ValueError("SESSION_HOURS must be between 1 and 168.")
        if not 1 <= self.auth_requests_per_minute <= 1000:
            raise ValueError("Invalid authentication request limit.")

    @classmethod
    def from_environment(cls):
        secure = os.getenv("SESSION_COOKIE_SECURE", "false").lower()
        if secure not in {"true", "false"}:
            raise ValueError("SESSION_COOKIE_SECURE must be true or false.")
        return cls(
            database_url=os.getenv("DATABASE_URL", ""),
            origin=os.getenv("APP_ORIGIN", "http://localhost:8000"),
            environment=os.getenv("APP_ENV", "development"),
            cookie_secure=secure == "true",
            session_hours=int(os.getenv("SESSION_HOURS", "24")),
        )
