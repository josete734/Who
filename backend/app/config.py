from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Auth
    auth_token: str = Field(..., min_length=16)
    login_password: str = "Neo123$"

    # Infra
    database_url: str = "postgresql+asyncpg://osint:changemeosint@postgres:5432/osint"
    redis_url: str = "redis://redis:6379/0"
    searxng_url: str = "http://searxng:8080"

    # LLMs
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-pro"
    ollama_api_key: str = ""
    ollama_base_url: str = "https://ollama.com"
    ollama_model: str = "gpt-oss:120b"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    default_llm: str = "gemini"  # gemini | ollama | openai

    # Optional APIs
    github_token: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "osint-tool/1.0"
    companies_house_key: str = ""
    rapidapi_key: str = ""
    shodan_api_key: str = ""
    urlscan_api_key: str = ""
    leakix_api_key: str = ""
    hunter_api_key: str = ""
    numverify_api_key: str = ""

    # Limits
    default_timeout_minutes: int = 20
    max_concurrent_collectors: int = 30
    default_language: str = "es"

    sherlock_timeout: int = 30
    maigret_timeout: int = 60

    public_domain: str = "who.worldmapsound.com"

    @property
    def redis_db(self) -> int:
        try:
            return int(self.redis_url.rsplit("/", 1)[-1])
        except ValueError:
            return 0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
