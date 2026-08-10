import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./jobs.db")

# `check_same_thread` is a SQLite driver argument — psycopg has no such
# keyword and raises TypeError on connect, so it cannot be passed
# unconditionally. `pool_pre_ping` is the mirror image: pointless for a
# local file, but it is what keeps a pooled connection from being handed
# out dead after the server restarts or an idle timeout closes it.
_IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _IS_SQLITE else {},
    pool_pre_ping=not _IS_SQLITE,
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