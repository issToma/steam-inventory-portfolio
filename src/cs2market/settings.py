from functools import lru_cache
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Steam started answering 429 after ~12 requests in ~4 minutes (2026-10-26 probe).
# This floor is deliberately not configurable below this value.
MIN_REQUEST_INTERVAL_SECONDS = 15.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CS2M_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://cs2market:cs2market@localhost:5432/cs2market"

    host: str = "127.0.0.1"
    port: int = 8080

    request_interval_seconds: Annotated[float, Field(ge=MIN_REQUEST_INTERVAL_SECONDS)] = 20.0
    user_agent: str = "cs2market/0.1.0 (self-hosted CS2 market price tracker)"

    collector_enabled: bool = True
    history_refresh_minutes: Annotated[int, Field(ge=30)] = 60
    fx_refresh_hours: Annotated[int, Field(ge=1)] = 24
    inventory_refresh_hours: Annotated[int, Field(ge=1)] = 6
    # A liquid, fairly expensive item, so that cent rounding barely affects the derived rate.
    fx_reference_item: str = "AK-47 | Redline (Field-Tested)"

    ui_storage_secret: str = "change-me"


@lru_cache
def get_settings() -> Settings:
    return Settings()
