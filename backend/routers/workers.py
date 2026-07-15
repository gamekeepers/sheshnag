from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from database import get_db
from models import (
    Batch, BatchAssignment, File as FileModel, Worker,
    ProviderCapability, Organization, OrganizationMembership, ApiKey, unix_now, get_org_owner,
)
from schemas import HeartbeatRequest, WorkerRegisterRequest
from pydantic import BaseModel
from typing import Optional
from auth import get_current_user, require_role, generate_api_key, hash_api_key, get_api_key_prefix, require_worker_key
from provider_picker import picker
import shutil, os, json

router = APIRouter()

VALID_TRANSITIONS = {
    "validating":  ["validated", "failed"],
    "validated":   ["in_progress"],
    "in_progress": ["completed", "failed"],
    "completed":   [],
    "failed":      [],
}


def validate_transition(current: str, target: str):
    allowed = VALID_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition from '{current}' to '{target}'. "
                   f"Allowed: {allowed or 'none (terminal state)'}",
        )


class PollRequest(BaseModel):
    worker_id: str


class FailureReport(BaseModel):
    job_id: str
    worker_id: str
    error: Optional[str] = None


# ─── Worker Registration & Heartbeat ───────────────────────

@router.post("/register")
def register_worker(
    req: WorkerRegisterRequest,
    user=Depends(require_worker_key),
    db: Session = Depends(get_db),
):
    """Register a worker under the API key's organization."""
    # org_id comes directly from the worker key — no membership lookup needed
    org_id = getattr(user, "active_org_id", None)
    if not org_id:
        raise HTTPException(status_code=400, detail="Worker key has no associated organization")

    # Check if worker with same hostname already exists for this org
    existing = db.query(Worker).filter(
        Worker.org_id == org_id,
        Worker.hostname == req.hostname,
    ).first()

    gpus_json = json.dumps([g.model_dump() for g in req.gpus])
    runtimes_json = json.dumps([r.model_dump() for r in req.runtimes])
    cpu_cores = req.cpu.get("cores") if req.cpu else None
    ram_gb = req.ram.get("total_gb") if req.ram else None

    if existing:
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

    worker = Worker(
        org_id=org_id,
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


@router.post("/{worker_id}/heartbeat")
def worker_heartbeat(
    worker_id: str,
    user=Depends(require_worker_key),
    db: Session = Depends(get_db),
):
    """Worker heartbeat — updates status and timestamp."""
    worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    worker.status = "online"
    worker.last_heartbeat = unix_now()
    db.commit()
    return {"status": "ok", "worker_id": worker_id}


# ─── Legacy heartbeat (ProviderCapability compat) ──────────

@router.post("/heartbeat")
def heartbeat(
    req: HeartbeatRequest,
    user=Depends(require_worker_key),
    db: Session = Depends(get_db),
):
    """Legacy heartbeat (ProviderCapability compat)."""
    caps = db.query(ProviderCapability).filter(
        ProviderCapability.worker_id == req.worker_id,
    ).first()

    org_id = getattr(user, "active_org_id", None)
    if not org_id:
        raise HTTPException(status_code=400, detail="Worker key has no associated organization")

    if caps:
        caps.vram_total_gb = req.vram_total_gb
        caps.vram_available_gb = req.vram_available_gb
        caps.loaded_models = json.dumps(req.loaded_models)
        caps.status = "online"
        caps.last_heartbeat = unix_now()
    else:
        caps = ProviderCapability(
            worker_id=req.worker_id,
            provider_id=org_id,
            vram_total_gb=req.vram_total_gb,
            vram_available_gb=req.vram_available_gb,
            loaded_models=json.dumps(req.loaded_models),
            status="online",
            last_heartbeat=unix_now(),
        )
        db.add(caps)

    db.commit()
    return {"status": "ok"}


# ─── Job Polling & Results ─────────────────────────────────

@router.post("/poll")
def poll_job(
    req: PollRequest,
    user=Depends(require_worker_key),
    db: Session = Depends(get_db),
):
    caps = db.query(ProviderCapability).filter(
        ProviderCapability.worker_id == req.worker_id,
    ).first()

    available_batches = (
        db.query(Batch)
        .filter(Batch.status == "validated")
        .order_by(Batch.created_at)
        .all()
    )

    if not available_batches:
        return {"job": None}

    if caps:
        batch = picker.find_best_batch(caps, available_batches)
    else:
        batch = available_batches[0] if available_batches else None

    if not batch:
        return {"job": None}

    validate_transition(batch.status, "in_progress")
    batch.status = "in_progress"

    assignment = BatchAssignment(
        batch_id=batch.id,
        worker_id=req.worker_id,
        assigned_at=unix_now(),
    )
    db.add(assignment)
    db.commit()
    db.refresh(batch)

    return {
        "job": {
            "job_id":        batch.id,
            "input_file_id": batch.input_file_id,
            "input_path":    f"/v1/files/{batch.input_file_id}/content",
            "model":         batch.model,
        }
    }


@router.post("/upload-results")
def upload_results(
    job_id: str = Form(...),
    file: UploadFile = File(...),
    user=Depends(require_worker_key),
    db: Session = Depends(get_db),
):
    batch = db.query(Batch).filter(Batch.id == job_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    validate_transition(batch.status, "completed")

    output_file = FileModel(
        user_id=batch.user_id,
        filename=f"{batch.id}_output.jsonl",
        purpose="batch_output",
    )
    db.add(output_file)
    db.flush()

    FILES_DIR = "files"
    os.makedirs(FILES_DIR, exist_ok=True)
    filepath = f"{FILES_DIR}/{output_file.id}.jsonl"
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    output_file.filepath = filepath
    output_file.bytes = os.path.getsize(filepath)

    batch.output_file_id = output_file.id
    batch.status = "completed"
    batch.completed_at = unix_now()
    batch.request_counts_completed = batch.request_counts_total

    db.commit()
    db.refresh(batch)

    return {
        "status":         "completed",
        "batch_id":       batch.id,
        "output_file_id": output_file.id,
    }


@router.post("/report-failure")
def report_failure(
    req: FailureReport,
    user=Depends(require_worker_key),
    db: Session = Depends(get_db),
):
    batch = db.query(Batch).filter(Batch.id == req.job_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    validate_transition(batch.status, "failed")
    batch.status = "failed"
    batch.completed_at = unix_now()
    batch.request_counts_failed = batch.request_counts_total

    db.commit()
    db.refresh(batch)

    return {
        "status":   "failed",
        "batch_id": batch.id,
        "error":    req.error,
    }


# ─── Organization-Scoped Endpoints ─────────────────────────

@router.get("/v1/organizations")
def list_user_organizations(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List organizations the current user belongs to. Rejects worker keys."""
    if getattr(user, "_is_machine_identity", False):
        raise HTTPException(status_code=403, detail="Worker keys cannot access management endpoints")
    memberships = db.query(OrganizationMembership).filter(
        OrganizationMembership.user_id == user.id
    ).all()

    orgs = []
    for m in memberships:
        org = db.query(Organization).filter(Organization.id == m.org_id).first()
        if org:
            orgs.append({
                "id": org.id,
                "name": org.name,
                "role": m.role,
                "derived_owner_id": get_org_owner(db, org.id),
                "created_at": org.created_at,
            })

    return {"object": "list", "data": orgs}


@router.get("/v1/organizations/{org_id}/api-keys")
def list_org_api_keys(
    org_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List worker API keys for an organization. Must be a member."""
    if getattr(user, "_is_machine_identity", False):
        raise HTTPException(status_code=403, detail="Worker keys cannot access management endpoints")

    membership = db.query(OrganizationMembership).filter(
        OrganizationMembership.org_id == org_id,
        OrganizationMembership.user_id == user.id,
    ).first()

    if not membership and user.platform_role != "superadmin":
        raise HTTPException(status_code=403, detail="Not a member of this organization")

    keys = db.query(ApiKey).filter(
        ApiKey.org_id == org_id,
        ApiKey.key_type == "worker",
    ).order_by(ApiKey.created_at.desc()).all()
    return {
        "object": "list",
        "data": [
            {
                "id": k.id,
                "key_prefix": k.key_prefix,
                "name": k.name,
                "status": k.status,
                "last_used_at": k.last_used_at,
                "expires_at": k.expires_at,
                "created_at": k.created_at,
            }
            for k in keys
        ],
    }


@router.post("/v1/organizations/{org_id}/api-keys/regenerate")
def regenerate_org_api_key(
    org_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Regenerate a worker API key for an organization. Must be owner or admin."""
    if getattr(user, "_is_machine_identity", False):
        raise HTTPException(status_code=403, detail="Worker keys cannot access management endpoints")
    membership = db.query(OrganizationMembership).filter(
        OrganizationMembership.org_id == org_id,
        OrganizationMembership.user_id == user.id,
        OrganizationMembership.role.in_(["owner", "admin"]),
    ).first()

    if not membership and user.platform_role != "superadmin":
        raise HTTPException(status_code=403, detail="Only org owner/admin can regenerate keys")

    api_key_entry = db.query(ApiKey).filter(
        ApiKey.org_id == org_id,
        ApiKey.key_type == "worker",
    ).first()
    if not api_key_entry:
        raise HTTPException(status_code=404, detail="No worker API key found for this org")

    raw_key = generate_api_key()
    api_key_entry.key_prefix = get_api_key_prefix(raw_key)
    api_key_entry.key_hash = hash_api_key(raw_key)
    db.commit()
    db.refresh(api_key_entry)
    return {"api_key": raw_key}


@router.get("/v1/organizations/{org_id}/workers")
def list_org_workers(
    org_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List workers belonging to an organization. Must be a member."""
    if getattr(user, "_is_machine_identity", False):
        raise HTTPException(status_code=403, detail="Worker keys cannot access management endpoints")
    membership = db.query(OrganizationMembership).filter(
        OrganizationMembership.org_id == org_id,
        OrganizationMembership.user_id == user.id,
    ).first()

    if not membership and user.platform_role != "superadmin":
        raise HTTPException(status_code=403, detail="Not a member of this organization")

    workers = (
        db.query(Worker)
        .filter(Worker.org_id == org_id)
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