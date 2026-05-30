from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import File as FileModel
from schemas import FileOut
import shutil, os

router = APIRouter()
FILES_DIR = "files"
os.makedirs(FILES_DIR, exist_ok=True)


@router.post("/files", response_model=FileOut)
def upload_file(
    file: UploadFile = File(...),
    purpose: str = Form("batch"),
    db: Session = Depends(get_db),
):
    db_file = FileModel(
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
def download_file_content(file_id: str, db: Session = Depends(get_db)):
    db_file = db.query(FileModel).filter(FileModel.id == file_id).first()
    if not db_file:
        raise HTTPException(status_code=404, detail="File not found")
    if not os.path.exists(db_file.filepath):
        raise HTTPException(status_code=404, detail="File data missing")
    return FileResponse(db_file.filepath, filename=db_file.filename)
