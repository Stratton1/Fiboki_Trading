"""Auth routes: login, logout, me, and health check."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from fibokei.api.auth import (
    TokenData,
    UserModel,
    UserResponse,
    create_access_token,
    get_current_user,
    verify_password,
)
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
    response: Response,
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
    response.set_cookie(
        key="fibokei_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=24 * 3600,
    )
    return {"access_token": token, "token_type": "bearer"}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(key="fibokei_token")
    return {"detail": "Logged out"}


@router.get("/auth/me", response_model=UserResponse)
def get_me(
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.scalar(
        select(UserModel).where(UserModel.id == current_user.user_id)
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserResponse(
        user_id=user.id,
        username=user.username,
        role=user.role,
    )
