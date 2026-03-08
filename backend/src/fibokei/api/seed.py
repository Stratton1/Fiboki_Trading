"""Seed initial users for the Fiboki platform."""

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from fibokei.api.auth import UserModel, hash_password


def seed_users(session: Session) -> None:
    """Create or update Joe and Tom users from environment variables."""
    users = [
        ("joe", os.environ.get("FIBOKEI_USER_JOE_PASSWORD", "changeme")),
        ("tom", os.environ.get("FIBOKEI_USER_TOM_PASSWORD", "changeme")),
    ]
    for username, password in users:
        existing = session.scalar(
            select(UserModel).where(UserModel.username == username)
        )
        if existing is None:
            user = UserModel(
                username=username,
                password_hash=hash_password(password),
                role="admin",
            )
            session.add(user)
        else:
            existing.password_hash = hash_password(password)
    session.commit()
