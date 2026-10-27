"""Seed the demo backend's fresh database with one user, their org, a
personal API key (the "customer") and a worker key (forge-runner).
Prints shell assignments for setup.sh to save as keys.env."""
import secrets

from database import Base, SessionLocal, engine
import models  # noqa: F401  registers every table
from models import ApiKey, Organization, OrganizationMembership, User
from auth import hash_api_key, hash_password

Base.metadata.create_all(bind=engine)
db = SessionLocal()

user = User(email="demo@sheshnag.local", password_hash=hash_password(secrets.token_hex(16)),
            full_name="Demo Customer", is_active=True)
db.add(user)
db.flush()
org = Organization(name="Demo Lab")
db.add(org)
db.flush()
db.add(OrganizationMembership(org_id=org.id, user_id=user.id, role="owner"))

personal, worker = f"gk-{secrets.token_hex(16)}", f"gk-{secrets.token_hex(16)}"
db.add(ApiKey(key_type="personal", created_by_user_id=user.id, name="demo customer",
              key_prefix=personal[:8], key_hash=hash_api_key(personal)))
db.add(ApiKey(key_type="worker", org_id=org.id, created_by_user_id=user.id, name="forge-runner",
              key_prefix=worker[:8], key_hash=hash_api_key(worker)))
db.commit()

print(f"PERSONAL_KEY={personal}")
print(f"WORKER_KEY={worker}")
