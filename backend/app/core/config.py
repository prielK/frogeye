from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    jwt_secret: str
    jwt_ttl_minutes: int = 720
    cookie_secure: bool = False
    timezone: str = "Asia/Jerusalem"
    http_user_agent: str
    sec_user_agent: str | None = None
    github_token: str | None = None
    # Low-footprint profile.
    allow_browser_ua: bool = True  # honour per-source browser_ua (3 bot-blocking vendor feeds)
    run_cooldown_minutes: int = 30  # minimum gap after a succeeded run before a manual one; 0 off
    # Body cap for one job-board (ATS) API response; a large board's JSON passes 2 MB.
    ats_max_bytes: int = 12_000_000
    # Job postings the tagger labels per run/backfill batch (newest first).
    posting_tag_cap: int = 300
    # A job board not listed in full for more than this many local days is stale: it
    # serves no current open roles, and the threat and the briefs read it as no board.
    board_stale_days: int = 3
    # A page-watch diff shorter than this (characters) is too small to be worth a model call.
    page_judge_min_diff_chars: int = 200
    # Sources (documents, about/careers snapshots) the facts extractor reads per run/backfill.
    fact_extract_cap: int = 80
    # Competitor intelligence (app/intel/profile.py). Overlap evidence is the sum of
    # severity weights (high 3, medium 2, low 1) of jfrog_impact entries in 180 days: high from
    # overlap_high_evidence, medium from overlap_medium_evidence, low from 1.
    overlap_high_evidence: int = 6
    overlap_medium_evidence: int = 3
    # Threat score = overlap x momentum factor x hiring factor: high from threat_high_score,
    # medium from threat_medium_score, else low; "insufficient" below threat_min_signals
    # visible signals in 180 days for a competitor with no job board.
    threat_high_score: float = 12.0
    threat_medium_score: float = 4.0
    threat_min_signals: int = 3
    # Product pressure (app/intel/products.py): the last 4 weeks' mean weekly threat weight
    # over the 9 weeks before; rising from pressure_rising_ratio, easing at or under
    # pressure_easing_ratio, else steady ("none" with no threat in 13 weeks).
    pressure_rising_ratio: float = 1.25
    pressure_easing_ratio: float = 0.8
    # Weekly briefs (app/intel/briefs.py): a competitor or JFrog product with fewer
    # public signals than brief_min_signals in the last brief_lookback_days gets no brief.
    brief_min_signals: int = 3
    brief_lookback_days: int = 180
    openai_api_key: str | None = None
    slack_webhook_url: str | None = None
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    demo_mode: bool = False
    model_fast: str = "gpt-5-nano"
    model_smart: str = "gpt-6-luna"
    embedding_model: str = "text-embedding-3-small"
    embedding_dims: int = 512
    llm_daily_budget_usd: float = 0.75
    llm_total_budget_usd: float = 9.0
    reasoning_effort_fast: str = "minimal"
    reasoning_effort_smart: str = "low"
    llm_timeout_s: int = 60
    llm_max_retries: int = 2
    analysis_lookback_days: int = 14
    triage_cap: int = 400
    analysis_cap: int = 120
    # Stage B (full cited analysis): importance floor and per-run cap.
    analysis_min_importance: int = 15
    deep_analysis_cap: int = 60
    # stage B starts no further signal once the day's remaining LLM budget is under
    # this, so the newsroom (about $0.005 a day) can still write the briefing.
    newsroom_reserve_usd: float = 0.05
    briefing_min_importance: int = 25
    briefing_max_signals: int = 25
    rag_rerank_default: bool = False
    briefing_from: str = "FrogEye <briefing@frogeye.local>"
    # Where readers open FrogEye: the briefing email and Slack link every story here.
    app_url: str = "http://localhost:8080"

    @field_validator("embedding_dims")
    @classmethod
    def _dims_fixed(cls, v: int) -> int:
        if v != 512:
            raise ValueError(
                "embedding_dims must be 512 (vector column size fixed by migration 0005)"
            )
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
