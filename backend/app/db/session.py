"""Async SQLAlchemy engine, sessions, and FastAPI dependency."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a database session, rolling back failed requests and always closing."""
    async with async_session_factory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise


get_db_session = get_db