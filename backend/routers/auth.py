from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import User
from schemas import (
    SignupRequest, LoginRequest, ChangePasswordRequest,
    UserOut, TokenOut,
)
from auth import (
    hash_password, verify_password, create_access_token,
    generate_api_key, get_current_user,
)

router = APIRouter()

ALLOWED_SIGNUP_ROLES = ["user", "provider"]


@router.post("/auth/signup", response_model=UserOut)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    if req.role not in ALLOWED_SIGNUP_ROLES:
        raise HTTPException(status_code=400, detail="Role must be 'user' or 'provider'")

    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    api_key = generate_api_key() if req.role == "user" else None

    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        role=req.role,
        api_key=api_key,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/auth/login", response_model=TokenOut)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    token = create_access_token(user.id, user.role)
    return TokenOut(
        access_token=token,
        must_change_password=user.must_change_password,
    )


@router.get("/auth/me", response_model=UserOut)
def get_profile(user=Depends(get_current_user)):
    return user


@router.post("/auth/change-password")
def change_password(
    req: ChangePasswordRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(req.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Old password is incorrect")

    user.password_hash = hash_password(req.new_password)
    user.must_change_password = False
    db.commit()
    return {"detail": "Password changed successfully"}


@router.post("/auth/api-keys/regenerate")
def regenerate_api_key(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role != "user":
        raise HTTPException(status_code=403, detail="Only users can have API keys")

    user.api_key = generate_api_key()
    db.commit()
    db.refresh(user)
    return {"api_key": user.api_key}
