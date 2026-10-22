import pytest
from pydantic import ValidationError

from app.core.config import Settings

BASE = {
    "database_url": "postgresql+psycopg://x:x@db/x",
    "redis_url": "redis://r:6379/0",
    "jwt_secret": "s" * 32,
    "http_user_agent": "ua",
}


def make(**kw) -> Settings:
    return Settings(_env_file=None, **{**BASE, **kw})


def test_embedding_dims_must_be_512():
    with pytest.raises(ValidationError, match="embedding_dims must be 512"):
        make(embedding_dims=1536)
    assert make(embedding_dims=512).embedding_dims == 512


def test_model_defaults(monkeypatch):
    for k in ("MODEL_FAST", "MODEL_SMART"):
        monkeypatch.delenv(k, raising=False)
    s = make()
    assert s.model_fast == "gpt-5-nano"
    assert s.model_smart == "gpt-6-luna"
    assert (s.reasoning_effort_fast, s.reasoning_effort_smart) == ("minimal", "low")
    assert (s.llm_timeout_s, s.llm_max_retries) == (60, 2)
    assert (s.analysis_lookback_days, s.triage_cap, s.analysis_cap) == (14, 400, 120)
    assert (s.briefing_min_importance, s.briefing_max_signals) == (25, 25)
    assert s.rag_rerank_default is False
    assert s.briefing_from == "FrogEye <briefing@frogeye.local>"


def test_an_old_env_file_with_a_removed_key_still_loads(monkeypatch, tmp_path):
    """TAVILY_API_KEY is no longer a setting; an .env that still sets it loads all the same."""
    env = tmp_path / ".env"
    env.write_text("TAVILY_API_KEY=old\n", encoding="utf-8")
    monkeypatch.setenv("TAVILY_API_KEY", "old")
    s = Settings(_env_file=env, **BASE)
    assert not hasattr(s, "tavily_api_key")
