import hashlib
import hmac
import secrets
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
# Constant valid hash for timing-equivalent verification of unknown accounts.
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def digest(secret: str, purpose: str, value: str) -> str:
    return hmac.new(secret.encode(), (purpose + "\0" + value).encode(), hashlib.sha256).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def normalize_username(username: str) -> str:
    value = unicodedata.normalize("NFKC", username).strip().casefold()
    if not 1 <= len(value) <= 128 or any(ord(char) < 32 for char in value):
        raise ValueError("invalid username")
    return value


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("administrator password must contain 12 to 256 characters")
    return PASSWORD_HASHER.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False
