"""Application settings, loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_FSC_WATCHLIST = ["5305", "5306", "5310", "5330", "5340", "4730", "6145"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Core
    database_url: str = f"sqlite:///{(REPO_ROOT / 'data' / 'app.db').as_posix()}"
    redis_url: str = ""
    jwt_secret: str = "dev-insecure-jwt-secret-change-me-0123456789abcdef"
    cookie_secure: bool = False
    frontend_origin: str = "http://localhost:3000"
    demo_mode: bool = True
    log_level: str = "INFO"
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    # LLM
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_provider: str = ""
    llm_refusal_fallback: bool = True

    # Email
    email_provider: str = "console"
    resend_api_key: str = ""
    email_from: str = "DIBBS Bid Copilot <quotes@copilot.example.com>"
    email_reply_domain: str = "copilot.example.com"
    inbound_email_secret: str = "dev-inbound-secret"

    # External data
    sam_api_key: str = ""
    apify_token: str = ""
    apify_dibbs_actor: str = ""
    scraper_user_agent: str = "DIBBSBidCopilot/0.1 (+mailto:ops@copilot.example.com)"
    connector_mode: str = "fixture"  # fixture | live | apify
    dibbs_banner_mode: str = "browser"  # browser (Playwright) | form (plain POST)
    detail_fetch_limit: int = 600  # max record pages + PDFs fetched per ingest run
    raw_cache_dir: str = str(REPO_ROOT / "data" / "raw")
    fixtures_dir: str = str(BACKEND_ROOT / "tests" / "fixtures")
    default_fsc_watchlist: list[str] = Field(default_factory=lambda: list(DEFAULT_FSC_WATCHLIST))

    # Voice
    voice_enabled: bool = False
    voice_provider: str = "mock"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_agent_id: str = ""
    elevenlabs_phone_number_id: str = ""
    elevenlabs_webhook_secret: str = ""
    openai_api_key: str = ""
    public_api_base_url: str = "http://localhost:8000"

    # ML
    model_artifact_dir: str = str(REPO_ROOT / "ml" / "artifacts")

    # Deployment: seed the demo on first boot when the database has no organizations.
    auto_seed: bool = False
    # Live mode: on first boot, queue an initial load (recent RFQs + PDF history + train) on the worker.
    auto_backfill: bool = False
    # Live mode: nightly incremental backfill (more procurement history + award postings) on the bulk queue.
    nightly_backfill: bool = True
    # Ops: email this address (throttled) when data goes stale or a pipeline fails. Needs a real email provider.
    ops_alert_email: str = ""
    # Pricing research on production data, in-cluster: set to a new id to run app/services/research.py once.
    research_run: str = ""

    # Shown on the Terms and Privacy pages for questions, data export and deletion requests (empty = not shown).
    support_email: str = ""
    # Public demo: "Explore the demo" signs visitors in as a read-only viewer of the Atlas demo org.
    public_demo: bool = False
    # False = the seeded demo accounts (DEMO_CREDENTIALS.md) cannot log in with their published passwords;
    # bootstrap replaces their password hashes at every boot. Set false on any internet-facing deployment.
    demo_password_login: bool = True
    # Ensure this superadmin exists at boot (password re-applied each boot, so the variable is the source of truth).
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, v: str) -> str:
        """Hosted Postgres URLs (Railway, Heroku, ...) come as postgres:// or postgresql://; use psycopg 3."""
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def effective_llm_provider(self) -> str:
        if self.llm_provider:
            return self.llm_provider
        return "anthropic" if self.anthropic_api_key else "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()
