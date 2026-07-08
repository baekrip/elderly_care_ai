from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


engine = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


def init_db(database_url: str) -> None:
    global engine, AsyncSessionLocal
    engine = create_async_engine(database_url, future=True, echo=False)
    if database_url.startswith("sqlite"):
        _apply_sqlite_pragmas(engine.sync_engine)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def _apply_sqlite_pragmas(sync_engine: Any) -> None:
    @event.listens_for(sync_engine, "connect")
    def set_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


async def create_tables() -> None:
    if engine is None:
        raise RuntimeError("Database engine is not initialized")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    if AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized")
    async with AsyncSessionLocal() as session:
        yield session
