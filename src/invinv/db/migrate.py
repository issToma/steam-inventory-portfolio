"""Bring the database schema up to date, so that starting a new version needs no extra step."""

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).parent / "migrations"


def upgrade_database() -> None:
    """Apply every pending Alembic migration (to INVINV_DATABASE_URL, see migrations/env.py)."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    command.upgrade(config, "head")
