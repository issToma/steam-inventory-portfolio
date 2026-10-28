from functools import lru_cache
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Steam started answering 429 after ~12 requests in ~4 minutes (2026-10-26 probe).
# This floor is deliberately not configurable below this value.
MIN_REQUEST_INTERVAL_SECONDS = 15.0
# The adaptive pace (opt-in) may go faster, since it backs off on its own after a 429.
MIN_ADAPTIVE_INTERVAL_SECONDS = 5.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVINV_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://invinv:invinv@localhost:5432/invinv"

    host: str = "127.0.0.1"
    port: int = 8080

    request_interval_seconds: Annotated[float, Field(ge=MIN_REQUEST_INTERVAL_SECONDS)] = 20.0
    # Opt-in: start at request_interval_seconds and speed up while Steam answers normally,
    # down to adaptive_min_interval_seconds; a 429 slows it down again.
    adaptive_pacing: bool = False
    adaptive_min_interval_seconds: Annotated[float, Field(ge=MIN_ADAPTIVE_INTERVAL_SECONDS)] = 8.0
    user_agent: str = "invinv/0.1.0 (self-hosted Steam inventory price tracker)"

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
