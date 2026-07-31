"""Shared test fixtures for backend API tests.

Uses an in-memory SQLite database with shared-cache mode so all connections
(even across worker threads spawned by TestClient) see the same tables and data.
Creates a fresh DB session per request (matching production get_db behaviour).
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Import models to register their tables with Base.metadata BEFORE creating the engine.
from database import Base, get_db
from models import (
    User,
    Organization,
    OrganizationMembership,
    ApiKey,
    Worker,
    File,
    Batch,
    BatchAssignment,
    PasswordResetToken,
)


# ─── Shared in-memory engine ─────────────────────────────────

import sqlite3

def _shared_memory_creator():
    """Return a connection to a shared in-memory SQLite database."""
    return sqlite3.connect(
        "file::memory:?cache=shared",
        uri=True,
        check_same_thread=False,  # TestClient runs sync endpoints in worker threads
    )


@pytest.fixture(scope="session")
def _engine():
    """Single engine for the whole test session with all tables created."""
    eng = create_engine("sqlite://", creator=_shared_memory_creator)
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)
    eng.dispose()


# ─── get_db override factory ─────────────────────────────────

def _make_override(engine):
    """Return a generator that creates a fresh session per request."""
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    return override


# ─── Seeding helpers ─────────────────────────────────────────

def _seed_user(engine, email, password, full_name, platform_role="user"):
    """Create a user with an org + owner membership. Returns (user_id, platform_role)."""
    from auth import hash_password as _hash

    # expire_on_commit=False keeps attributes valid after session closes
    SM = sessionmaker(bind=engine, expire_on_commit=False)
    db = SM()
    try:
        user = User(
            email=email,
            password_hash=_hash(password),
            full_name=full_name,
            platform_role=platform_role,
            is_active=True,
        )
        db.add(user)
        db.flush()

        org = Organization(name=f"{full_name}'s Org")
        db.add(org)
        db.flush()

        membership = OrganizationMembership(
            org_id=org.id,
            user_id=user.id,
            role="owner",
        )
        db.add(membership)
        db.commit()
        # Return the live User object (attributes won't expire)
        return user
    finally:
        db.close()


# ─── Database session fixture (for direct DB assertions) ─────

@pytest.fixture
def db_session(_engine):
    """Fresh session for direct DB queries in tests."""
    db = sessionmaker(bind=_engine, expire_on_commit=False)()
    try:
        yield db
    finally:
        db.close()


# ─── Authenticated client fixtures ──────────────────────────

@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Reset rate-limit state so tests don't interfere with each other."""
    try:
        from rate_limit import _key_creation_timestamps
        _key_creation_timestamps.clear()
    except ImportError:
        pass
    yield


@pytest.fixture(scope="session")
def _test_user(_engine):
    """Seed a regular user into the shared DB (idempotent, session-scoped)."""
    SM = sessionmaker(bind=_engine, expire_on_commit=False)
    db = SM()
    try:
        existing = db.query(User).filter(User.email == "test@example.com").first()
        if existing is None:
            return _seed_user(_engine, "test@example.com", "testpassword123", "Test User")
        return existing
    finally:
        db.close()


@pytest.fixture(scope="session")
def _test_superuser(_engine):
    """Seed a superadmin user into the shared DB (idempotent, session-scoped)."""
    SM = sessionmaker(bind=_engine, expire_on_commit=False)
    db = SM()
    try:
        existing = db.query(User).filter(User.email == "super@test.com").first()
        if existing is None:
            return _seed_user(_engine, "super@test.com", "testpassword123", "Super Admin", platform_role="superadmin")
        return existing
    finally:
        db.close()


@pytest.fixture
def auth_client(_engine, _test_user):
    """TestClient authenticated as the regular test user via JWT."""
    from main import app
    from auth import create_access_token
    from fastapi.testclient import TestClient

    token = create_access_token(_test_user.id, _test_user.platform_role)

    override = _make_override(_engine)
    app.dependency_overrides[get_db] = override

    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        client.headers.update(headers)
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def superadmin_client(_engine, _test_superuser):
    """TestClient authenticated as the superadmin user via JWT."""
    from main import app
    from auth import create_access_token
    from fastapi.testclient import TestClient

    token = create_access_token(_test_superuser.id, _test_superuser.platform_role)

    override = _make_override(_engine)
    app.dependency_overrides[get_db] = override

    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        client.headers.update(headers)
        yield client

    app.dependency_overrides.clear()


# Backwards-compatible aliases (tests may reference test_user / test_superuser)

@pytest.fixture
def test_user(_test_user):
    return _test_user


@pytest.fixture
def test_superuser(_test_superuser):
    return _test_superuser
