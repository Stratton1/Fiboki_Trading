"""Auth routes: login and health check."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from fibokei.api.auth import UserModel, create_access_token, verify_password
from fibokei.api.deps import get_db

router = APIRouter(tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/health", response_model=HealthResponse)
def health_check():
    return {"status": "ok", "version": "1.0.0"}


@router.post("/auth/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = db.scalar(
        select(UserModel).where(UserModel.username == form_data.username)
    )
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(user.id, user.username)
    return {"access_token": token, "token_type": "bearer"}
