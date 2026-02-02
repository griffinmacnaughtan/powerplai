from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database (port 5433 to avoid conflict with local PostgreSQL)
    database_url: str = "postgresql+asyncpg://powerplai:powerplai_dev@localhost:5433/powerplai"

    # Anthropic
    anthropic_api_key: str = ""

    # ChromaDB
    chroma_host: str = "localhost"
    chroma_port: int = 8001

    # NHL API
    nhl_api_base: str = "https://api-web.nhle.com/v1"
    nhl_stats_api_base: str = "https://api.nhle.com/stats/rest/en"

    # App settings
    debug: bool = True
    log_level: str = "INFO"
    auto_update_enabled: bool = True  # Auto-update current season data on startup

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
