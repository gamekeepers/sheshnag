from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import User, Organization, Worker, unix_now
from schemas import ProviderSignupRequest, WorkerRegisterRequest
from auth import hash_password, generate_api_key, get_current_user, require_role
import json

router = APIRouter()


# ─── Provider endpoints (/provider/*) ──────────────────────

@router.post("/provider/signup")
def provider_signup(req: ProviderSignupRequest, db: Session = Depends(get_db)):
    """Provider signs up → creates account + org + gets API key."""
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Create provider user with API key
    api_key = generate_api_key()
    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        role="provider",
        api_key=api_key,
    )
    db.add(user)
    db.flush()  # get user.id

    # Create organization
    org = Organization(
        name=req.org_name,
        owner_id=user.id,
    )
    db.add(org)
    db.flush()

    # Link user to org
    user.org_id = org.id
    db.commit()
    db.refresh(user)
    db.refresh(org)

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "api_key": user.api_key,
        "organization": {
            "id": org.id,
            "name": org.name,
        },
    }


@router.get("/provider/workers")
def list_provider_workers(
    user=Depends(require_role("provider")),
    db: Session = Depends(get_db),
):
    """Provider: list their own registered workers."""
    workers = (
        db.query(Worker)
        .filter(Worker.provider_id == user.id)
        .order_by(Worker.created_at.desc())
        .all()
    )
    return {
        "object": "list",
        "data": [
            {
                "id": w.id,
                "hostname": w.hostname,
                "os": w.os,
                "cpu_cores": w.cpu_cores,
                "ram_total_gb": w.ram_total_gb,
                "gpus": json.loads(w.gpus) if w.gpus else [],
                "runtimes": json.loads(w.runtimes) if w.runtimes else [],
                "status": w.status,
                "last_heartbeat": w.last_heartbeat,
                "created_at": w.created_at,
            }
            for w in workers
        ],
    }


# ─── Worker endpoints (/worker/*) ──────────────────────────

@router.post("/worker/register")
def register_worker(
    req: WorkerRegisterRequest,
    user=Depends(require_role("provider")),
    db: Session = Depends(get_db),
):
    """Daemon: self-register a machine with full hardware specs."""
    if not user.org_id:
        raise HTTPException(status_code=400, detail="Provider has no organization")

    # Check if worker with same hostname already exists for this provider
    existing = db.query(Worker).filter(
        Worker.provider_id == user.id,
        Worker.hostname == req.hostname,
    ).first()

    gpus_json = json.dumps([g.model_dump() for g in req.gpus])
    runtimes_json = json.dumps([r.model_dump() for r in req.runtimes])
    cpu_cores = req.cpu.get("cores") if req.cpu else None
    ram_gb = req.ram.get("total_gb") if req.ram else None

    if existing:
        # Update existing worker
        existing.os = req.os
        existing.cpu_cores = cpu_cores
        existing.ram_total_gb = ram_gb
        existing.gpus = gpus_json
        existing.runtimes = runtimes_json
        existing.status = "online"
        existing.last_heartbeat = unix_now()
        db.commit()
        db.refresh(existing)
        return {
            "worker_id": existing.id,
            "status": "updated",
            "message": f"Worker '{req.hostname}' re-registered",
        }

    # Create new worker
    worker = Worker(
        provider_id=user.id,
        org_id=user.org_id,
        hostname=req.hostname,
        os=req.os,
        cpu_cores=cpu_cores,
        ram_total_gb=ram_gb,
        gpus=gpus_json,
        runtimes=runtimes_json,
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)

    return {
        "worker_id": worker.id,
        "status": "registered",
        "message": f"Worker '{req.hostname}' registered successfully",
    }


@router.post("/worker/heartbeat")
def worker_heartbeat(
    worker_id: str,
    user=Depends(require_role("provider")),
    db: Session = Depends(get_db),
):
    """Daemon: periodic heartbeat to keep worker status online."""
    worker = db.query(Worker).filter(
        Worker.id == worker_id,
        Worker.provider_id == user.id,
    ).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    worker.status = "online"
    worker.last_heartbeat = unix_now()
    db.commit()
    return {"status": "ok", "worker_id": worker_id}
