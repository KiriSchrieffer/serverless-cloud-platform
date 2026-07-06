"""Worker-owned async database engine and session factory."""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from worker.app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
