"""Tests against a real Postgres: a separate database, migrated with Alembic once per run.

Set CS2M_TEST_DATABASE_URL to choose it; by default it is the app's database name with a
"_test" suffix. The tests are skipped when Postgres is not reachable.
"""

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from cs2market.db.session import SessionMaker, make_sessionmaker
from cs2market.db.tables import Base
from cs2market.settings import get_settings


def _test_url() -> str:
    if url := os.environ.get("CS2M_TEST_DATABASE_URL"):
        return url
    app_url = make_url(get_settings().database_url)
    return app_url.set(database=f"{app_url.database}_test").render_as_string(hide_password=False)


async def _ensure_database(url: str) -> None:
    target = make_url(url)
    admin = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    import asyncio

    url = _test_url()
    try:
        asyncio.run(_ensure_database(url))
    except OSError as exc:
        pytest.skip(f"Postgres is not reachable: {exc}")
    config = Config("alembic.ini")
    previous = os.environ.get("CS2M_DATABASE_URL")
    os.environ["CS2M_DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        command.upgrade(config, "head")
        yield url
    finally:
        if previous is None:
            os.environ.pop("CS2M_DATABASE_URL", None)
        else:
            os.environ["CS2M_DATABASE_URL"] = previous
        get_settings.cache_clear()


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """A session on an emptied database."""
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def sessions(database_url: str, session: AsyncSession) -> AsyncIterator[SessionMaker]:
    """A session factory on the same emptied database, for code that opens its own sessions."""
    engine = create_async_engine(database_url)
    try:
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()
