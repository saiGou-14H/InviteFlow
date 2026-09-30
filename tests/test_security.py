import pytest
from pydantic import ValidationError

from inviteflow.config import Settings
from inviteflow.security import digest, hash_password, normalize_username, verify_password


def options(**kwargs):
    return dict(_env_file=None, database_url=None, session_secret=None) | kwargs


@pytest.mark.parametrize(
    "origin",
    [
        "null",
        "https://example.com/path",
        "javascript:alert(1)",
        "https://user:secret@example.com",
        "https://example.com?q=1",
    ],
)
def test_invalid_origin_rejected(origin):
    with pytest.raises(ValidationError):
        Settings(**options(public_origin=origin))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"environment": "production"},
        {"database_url": "sqlite:///test.db"},
        {"database_url": "postgresql+asyncpg://localhost/test", "session_secret": "short"},
        {"user_session_ttl_seconds": 3600, "user_session_max_seconds": 60},
        {"admin_session_ttl_seconds": 3600, "admin_session_max_seconds": 60},
    ],
)
def test_insecure_runtime_rejected(kwargs):
    with pytest.raises(ValidationError):
        Settings(**options(**kwargs))


def test_production_requires_secure_origin_and_cookies():
    configured = options(
        environment="production",
        database_url="postgresql+asyncpg://localhost/test",
        session_secret="test-secret-" * 4,
        public_origin="https://example.com",
    )
    assert Settings(**configured).user_cookie_name == "__Host-if-user"
    with pytest.raises(ValidationError):
        Settings(**(configured | {"cookie_secure": False}))
    with pytest.raises(ValidationError):
        Settings(**(configured | {"public_origin": "http://example.com"}))


def test_config_secrets_are_redacted():
    settings = Settings(
        **options(
            database_url="postgresql+asyncpg://admin:hidden@localhost/test",
            session_secret="private-runtime-secret-01234567890",
        )
    )
    assert "hidden" not in repr(settings)
    assert "private-runtime-secret" not in repr(settings)


def test_argon2_passwords_and_domain_separation():
    password = "valid-test-password-123"
    encoded = hash_password(password)
    assert encoded.startswith("$argon2id$") and encoded != password
    assert verify_password(encoded, password)
    assert not verify_password(encoded, "wrong")
    assert not verify_password("not-a-valid-hash", password)
    assert digest("secret", "session:user", "token") != digest("secret", "session:admin", "token")
    assert normalize_username("  ＡＤＭＩＮ  ") == "admin"


@pytest.mark.parametrize("password", ["short", "x" * 257])
def test_short_and_oversized_password_rejected(password):
    with pytest.raises(ValueError):
        hash_password(password)
