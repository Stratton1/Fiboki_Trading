"""JWT authentication for FIBOKEI API."""

import os
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from fibokei.db.models import Base


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user")


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


def _get_jwt_secret() -> str:
    secret = os.environ.get("FIBOKEI_JWT_SECRET")
    if not secret:
        raise ValueError("FIBOKEI_JWT_SECRET environment variable is required")
    return secret


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: int, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": expire,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=ALGORITHM)


class TokenData(BaseModel):
    user_id: int
    username: str


def decode_token(token: str) -> TokenData:
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
        username = payload["username"]
        return TokenData(user_id=user_id, username=username)
    except (JWTError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenData:
    """FastAPI dependency to get current authenticated user."""
    return decode_token(token)
