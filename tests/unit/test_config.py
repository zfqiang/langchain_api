from app.core.config import Settings


def test_settings_normalize_statuses_and_ignore_empty_llm_key(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET_KEY", "s" * 48)
    monkeypatch.setenv("PAID_ORDER_STATUSES", '["PAID"," completed "]')
    monkeypatch.setenv("LLM_API_KEY", "")

    settings = Settings(_env_file=None)

    assert settings.normalized_paid_statuses == ("paid", "completed")
    assert settings.llm_api_key is None
