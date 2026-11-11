from __future__ import annotations

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id with library defaults (RFC 9106 low-memory profile)

# A short list of very common passwords; production deployments should add a breached-password check.
_COMMON = {
    "password", "password1", "password123", "123456789", "1234567890", "qwerty123", "letmein123",
    "welcome123", "iloveyou123", "admin12345", "northbridge", "northbridge1", "passw0rd123",
}

MIN_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        # Still spend comparable time to reduce account-enumeration timing signals.
        try:
            _hasher.verify(_DUMMY, password)
        except VerificationError:
            pass
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problems(password: str, *, email: str = "", full_name: str = "") -> list[str]:
    problems: list[str] = []
    if len(password) < MIN_LENGTH:
        problems.append(f"Use at least {MIN_LENGTH} characters.")
    if len(password) > 128:
        problems.append("Use at most 128 characters.")
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 3:
        problems.append("Mix at least three of: lowercase, uppercase, digits, symbols.")
    lowered = password.lower()
    if lowered in _COMMON:
        problems.append("This password is too common.")
    local = email.split("@")[0].lower()
    if len(local) >= 4 and local in lowered:
        problems.append("Do not include your email address.")
    for part in full_name.lower().split():
        if len(part) >= 4 and part in lowered:
            problems.append("Do not include your name.")
            break
    return problems


_DUMMY = _hasher.hash("dummy-password-for-timing")
