"""Password, browser-session, and invitation token helpers."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from pwdlib import PasswordHash

from .config import get_settings

_password_hash = PasswordHash.recommended()


def normalize_email(email: str) -> str:
    """Return the canonical application representation of an email address."""

    return email.strip().lower()


def normalize_username(username: str) -> str:
    """Return the canonical case-insensitive representation of an account name."""

    return username.strip().lower()


def hash_password(password: str) -> str:
    """Hash a password with Argon2id through pwdlib."""

    return _password_hash.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against its Argon2id hash."""

    return _password_hash.verify(password, stored_hash)


def new_token() -> str:
    """Create an opaque browser or invitation token."""

    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Hash a raw token before persisting it."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry() -> datetime:
    """Return the configured expiration timestamp for a browser session."""

    return datetime.now(UTC) + timedelta(hours=get_settings().session_ttl_hours)


def invitation_expiry() -> datetime:
    """Return the configured expiration timestamp for an invitation."""

    return datetime.now(UTC) + timedelta(hours=get_settings().invite_ttl_hours)
