"""Async database utilities used exclusively by auth v2."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from core.settings import get_settings

_main_engine: Optional[AsyncEngine] = None
_central_engine: Optional[AsyncEngine] = None
_main_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None
_central_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def _build_mysql_async_url(
    host: str,
    user: str,
    password: str,
    db_name: str,
    port: int,
) -> str:
    encoded_password = quote_plus(password or "")
    return f"mysql+aiomysql://{user}:{encoded_password}@{host}:{port}/{db_name}?charset=utf8mb4"


def _to_async_url(url: str) -> str:
    if url.startswith("mysql+aiomysql://"):
        return url
    if url.startswith("mysql+pymysql://"):
        return url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    if url.startswith("mysql://"):
        return url.replace("mysql://", "mysql+aiomysql://", 1)
    return url


def _resolve_main_url() -> str:
    settings = get_settings()
    url = settings.DATABASE_MAIN_URL or settings.DATABASE_URL
    if url:
        return _to_async_url(url)

    if not settings.DB_NAME:
        raise RuntimeError("Main DB URL is not configured for auth v2")

    return _build_mysql_async_url(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        db_name=settings.DB_NAME,
        port=settings.DB_PORT,
    )


def _resolve_central_url() -> str:
    settings = get_settings()
    url = settings.DATABASE_CENTRAL_URL or settings.CENTRAL_DATABASE_URL
    if url:
        return _to_async_url(url)

    central_name = settings.CENTRAL_DB_NAME or settings.DB_CENTRAL
    if not central_name:
        raise RuntimeError("Central DB URL is not configured for auth v2")

    return _build_mysql_async_url(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        db_name=central_name,
        port=settings.DB_PORT,
    )


def get_main_async_engine() -> AsyncEngine:
    global _main_engine, _main_sessionmaker
    if _main_engine is None:
        _main_engine = create_async_engine(_resolve_main_url(), pool_pre_ping=True, pool_recycle=300)
        _main_sessionmaker = async_sessionmaker(
            bind=_main_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _main_engine


def get_central_async_engine() -> AsyncEngine:
    global _central_engine, _central_sessionmaker
    if _central_engine is None:
        _central_engine = create_async_engine(_resolve_central_url(), pool_pre_ping=True, pool_recycle=300)
        _central_sessionmaker = async_sessionmaker(
            bind=_central_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _central_engine


def _get_main_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _main_sessionmaker is None:
        get_main_async_engine()
    if _main_sessionmaker is None:
        raise RuntimeError("Main async sessionmaker is not initialized")
    return _main_sessionmaker


def _get_central_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _central_sessionmaker is None:
        get_central_async_engine()
    if _central_sessionmaker is None:
        raise RuntimeError("Central async sessionmaker is not initialized")
    return _central_sessionmaker


async def get_main_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for auth v2 main DB reads."""
    session = _get_main_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()


async def get_central_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for auth v2 central DB reads/writes."""
    session = _get_central_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()


@asynccontextmanager
async def main_session_context() -> AsyncGenerator[AsyncSession, None]:
    session = _get_main_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()


@asynccontextmanager
async def central_session_context() -> AsyncGenerator[AsyncSession, None]:
    session = _get_central_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()
