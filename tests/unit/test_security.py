from pydantic import SecretStr

from app.core.config import Settings
from app.core.security import create_access_token, hash_password, verify_password


def test_password_hash_and_jwt_claims() -> None:
    password = "strong-password-123"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("wrong-password", hashed)

    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
        jwt_secret_key=SecretStr("s" * 48),
    )
    token = create_access_token(settings, user_id=42)
    assert isinstance(token, str)
    assert token.count(".") == 2
