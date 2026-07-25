from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import File as FileModel, Batch, BatchAssignment, Worker, OrganizationMembership
from schemas import FileOut
from auth import get_human_context, get_file_read_context
import shutil, os

router = APIRouter()
FILES_DIR = "files"
os.makedirs(FILES_DIR, exist_ok=True)


@router.post("/files", response_model=FileOut)
def upload_file(
    file: UploadFile = File(...),
    purpose: str = Form("batch"),
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    user, _api_key = ctx

    db_file = FileModel(
        user_id=user.id,
        filename=file.filename or "upload.jsonl",
        purpose=purpose,
    )
    db.add(db_file)
    db.flush()

    filepath = f"{FILES_DIR}/{db_file.id}.jsonl"
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    db_file.filepath = filepath
    db_file.bytes = os.path.getsize(filepath)
    db.commit()
    db.refresh(db_file)
    return db_file


def _file_assigned_worker_org(db, file_id):
    """org_id of the worker currently assigned the batch that uses this file
    as input, or None."""
    batch = db.query(Batch).filter(Batch.input_file_id == file_id).first()
    if not batch:
        return None
    assignment = db.query(BatchAssignment).filter(
        BatchAssignment.batch_id == batch.id
    ).first()
    if not assignment:
        return None
    worker = db.query(Worker).filter(Worker.id == assignment.worker_id).first()
    return worker.org_id if worker else None


@router.get("/files")
def list_files(
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    """List the caller's uploaded files (OpenAI files-list shape)."""
    user, _api_key = ctx

    query = db.query(FileModel)
    if user.platform_role != "superadmin":
        query = query.filter(FileModel.user_id == user.id)

    files = query.order_by(FileModel.created_at.desc()).all()
    return {"object": "list", "data": [FileOut.model_validate(f) for f in files]}


@router.delete("/files/{file_id}")
def delete_file(
    file_id: str,
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    user, _api_key = ctx

    db_file = db.query(FileModel).filter(FileModel.id == file_id).first()
    if not db_file:
        raise HTTPException(status_code=404, detail="File not found")

    if user.platform_role != "superadmin" and db_file.user_id != user.id:
        raise HTTPException(status_code=403, detail="You don't own this file")

    active = db.query(Batch).filter(
        Batch.input_file_id == file_id,
        Batch.status.in_(("validating", "validated", "in_progress")),
    ).first()
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"File is the input of batch '{active.id}' ({active.status}); "
                   "wait for it to finish before deleting",
        )

    if db_file.filepath and os.path.exists(db_file.filepath):
        try:
            os.remove(db_file.filepath)
        except OSError:
            pass  # orphaned disk file is harmless; the DB row is authoritative

    db.delete(db_file)
    db.commit()
    return {"id": file_id, "object": "file", "deleted": True}


@router.get("/files/{file_id}/content")
def download_file_content(
    file_id: str,
    ctx=Depends(get_file_read_context),
    db: Session = Depends(get_db),
):
    kind, principal = ctx

    db_file = db.query(FileModel).filter(FileModel.id == file_id).first()
    if not db_file:
        raise HTTPException(status_code=404, detail="File not found")

    if kind == "worker":
        # A daemon downloading job input: the file must be the input of a
        # batch assigned to a worker in this key's org.
        org = principal
        if _file_assigned_worker_org(db, file_id) != org.id:
            raise HTTPException(status_code=403, detail="Access denied")
    else:
        user = principal
        # Superadmin: all. Owner: own files. Otherwise: a member of the org
        # whose worker is assigned this file's batch.
        if user.platform_role == "superadmin" or db_file.user_id == user.id:
            pass
        else:
            worker_org_id = _file_assigned_worker_org(db, file_id)
            member = worker_org_id and db.query(OrganizationMembership).filter(
                OrganizationMembership.org_id == worker_org_id,
                OrganizationMembership.user_id == user.id,
            ).first()
            if not member:
                raise HTTPException(status_code=403, detail="Access denied")

    if not os.path.exists(db_file.filepath):
        raise HTTPException(status_code=404, detail="File data missing")
    return FileResponse(db_file.filepath, filename=db_file.filename)
