import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# No fallback on purpose. A default pointing at a guessable local database
# with hardcoded credentials means a deploy that forgot the variable starts up
# and quietly reads and writes the wrong database instead of failing.
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy backend/.env.example to backend/.env "
        "and point DATABASE_URL at your Postgres database "
        "(see docs/setup.md \u00a70)."
    )

# The pool is sized for one uvicorn process: request handlers run in a thread
# pool and the batch sweeper holds a session of its own, so the ceiling needs
# headroom above the worker count rather than matching it. `pool_pre_ping`
# costs a round-trip per checkout and buys immunity to connections killed
# underneath us by a server restart or an idle timeout.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
