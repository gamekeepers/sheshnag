from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import File as FileModel, Batch, BatchAssignment, Worker, OrganizationMembership
from schemas import FileOut
from auth import get_human_context
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


@router.get("/files/{file_id}/content")
def download_file_content(
    file_id: str,
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    user, _api_key = ctx

    db_file = db.query(FileModel).filter(FileModel.id == file_id).first()
    if not db_file:
        raise HTTPException(status_code=404, detail="File not found")

    # Superadmin can access all files
    if user.platform_role == "superadmin":
        pass
    # Owner can access their own files
    elif db_file.user_id == user.id:
        pass
    else:
        # Check if user is member of an org whose worker is assigned to this file's batch
        batch = db.query(Batch).filter(Batch.input_file_id == file_id).first()
        if batch:
            assignment = db.query(BatchAssignment).filter(
                BatchAssignment.batch_id == batch.id
            ).first()
            if assignment:
                worker = db.query(Worker).filter(Worker.id == assignment.worker_id).first()
                if worker:
                    membership = db.query(OrganizationMembership).filter(
                        OrganizationMembership.org_id == worker.org_id,
                        OrganizationMembership.user_id == user.id,
                    ).first()
                    if membership:
                        pass  # Allow access
                    else:
                        raise HTTPException(status_code=403, detail="Access denied")
                else:
                    raise HTTPException(status_code=403, detail="Access denied")
            else:
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            raise HTTPException(status_code=403, detail="Access denied")

    if not os.path.exists(db_file.filepath):
        raise HTTPException(status_code=404, detail="File data missing")
    return FileResponse(db_file.filepath, filename=db_file.filename)
