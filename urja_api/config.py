from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """From URJA_* env vars, or a .env file."""

    model_config = SettingsConfigDict(env_prefix="URJA_", env_file=".env", extra="ignore")

    portal_base_url: str = "https://urja-ops.flockenergy.tech"
    portal_email: str
    portal_password: SecretStr

    # portal starts 429ing around 120/min, login included. stay under that.
    portal_requests_per_minute: int = 100
    portal_timeout_seconds: float = 20.0
    portal_max_attempts: int = 4

    # meters and the network don't move much. consumption is fetched per request.
    catalog_ttl_seconds: int = 900
    consumption_ttl_seconds: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()
