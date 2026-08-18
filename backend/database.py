import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

_engine = None
_session_factory = None


def _database_url() -> str:
    """The configured URL, or a pointed failure.

    No fallback on purpose. A default pointing at a guessable local database
    with hardcoded credentials means a deploy that forgot the variable starts
    up and quietly reads and writes the wrong database instead of failing.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy backend/.env.example to backend/.env "
            "and point DATABASE_URL at your Postgres database "
            "(see docs/setup.md §0)."
        )
    return url


def get_engine():
    """The process-wide engine, built on first use.

    The pool is sized for one uvicorn process: request handlers run in a
    thread pool and the batch sweeper holds a session of its own, so the
    ceiling needs headroom above the worker count rather than matching it.
    `pool_pre_ping` costs a round-trip per checkout and buys immunity to
    connections killed underneath us by a server restart or an idle timeout.
    """
    global _engine
    if _engine is None:
        _engine = create_engine(
            _database_url(),
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def SessionLocal():
    """Open a session. A function rather than a bound `sessionmaker` so that
    importing this module never reaches for the configuration."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            autocommit=False, autoflush=False, bind=get_engine(),
        )
    return _session_factory()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
