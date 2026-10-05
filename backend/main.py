from dotenv import load_dotenv
load_dotenv()

import asyncio
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import get_engine, Base
from routers import files, batches, workers, auth, users, organizations, pool, models as models_router
from models import User, Organization, OrganizationMembership
from auth import hash_password
from catalog_seed import seed_model_catalog
from migrations import ensure_schema
from sweeper import run_sweeper

Base.metadata.create_all(bind=get_engine())
ensure_schema()
seed_model_catalog()

# Comma-separated origins, or "*" for all. Browsers reject a wildcard origin
# when credentials are allowed, so the two settings are derived together
# rather than configured independently.
_CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
if not _CORS_ORIGINS:  # blank or all-empty value means "unset", not "block everything"
    _CORS_ORIGINS = ["*"]
_ALLOW_CREDS = "*" not in _CORS_ORIGINS

app = FastAPI(title="Batch AI Compute Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=_ALLOW_CREDS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,      prefix="/v1",      tags=["Auth"])
app.include_router(files.router,     prefix="/v1",      tags=["Files"])
app.include_router(batches.router,   prefix="/v1",      tags=["Batches"])
app.include_router(users.router,     prefix="/v1",      tags=["Users"])
app.include_router(organizations.router, prefix="/v1",  tags=["Organizations"])
app.include_router(models_router.router, prefix="/v1",  tags=["Models"])
app.include_router(pool.router,       prefix="/v1",      tags=["Pool"])
app.include_router(workers.router,   prefix="/workers",  tags=["Workers"])


@app.on_event("startup")
async def start_worker_sweeper():
    """Reclaim batches from workers whose heartbeats stopped"""
    asyncio.create_task(run_sweeper())


@app.on_event("startup")
def create_default_admin():
    """Ensure a default superadmin user exists with an owner org membership.

    Idempotent — skips everything if admin and membership already exist.
    If admin exists but membership was deleted, recreates the org + membership.
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == "admin@platform.com").first()
        if not admin:
            admin = User(
                email="admin@platform.com",
                password_hash=hash_password("admin"),
                full_name="Platform Admin",
                platform_role="superadmin",
                must_change_password=True,
            )
            db.add(admin)
            db.flush()

        # Ensure the owner membership still exists — guards against partial deletions
        has_owner_membership = db.query(OrganizationMembership).filter(
            OrganizationMembership.user_id == admin.id,
            OrganizationMembership.role == "owner",
        ).first()

        if not has_owner_membership:
            org = Organization(name="Platform Admin Org")
            db.add(org)
            db.flush()

            membership = OrganizationMembership(
                org_id=org.id,
                user_id=admin.id,
                role="owner",
            )
            db.add(membership)
            db.commit()

        if admin.must_change_password:
            print("Default superadmin ready: admin@platform.com / admin (change password on first login)")
    finally:
        db.close()


@app.get("/")
def health():
    return {"status": "ok"}
