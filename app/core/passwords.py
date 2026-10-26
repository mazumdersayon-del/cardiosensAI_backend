"""Password hashing (argon2id) and the password policy."""

from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.errors import bad_request

MIN_LENGTH = 10
MAX_LENGTH = 128

_hasher = PasswordHasher()  # argon2id, library defaults (RFC 9106 low-memory profile)

COMMON = frozenset(
    line.strip().lower()
    for line in (Path(__file__).with_name("common_passwords.txt")).read_text().splitlines()
    if line.strip() and not line.startswith("#")
)


def check_policy(password: str, *, email: str | None = None) -> None:
    if len(password) < MIN_LENGTH:
        raise bad_request("weak_password", f"Use at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        raise bad_request("weak_password", f"Use at most {MAX_LENGTH} characters")
    lowered = password.lower()
    if lowered in COMMON or len(set(password)) < 4:
        raise bad_request("weak_password", "This password is too common. Choose another one")
    if email and lowered == email.lower().split("@")[0]:
        raise bad_request("weak_password", "Don't use your email address as your password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    if not stored_hash:
        _hasher.hash(password)  # spend the same time as a real check, so timing doesn't reveal the account
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)
