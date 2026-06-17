import os
from datetime import datetime, timedelta

import bcrypt as _bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select

from ..database.db import get_session
from ..database.models import User
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

_ALGORITHM = "HS256"
_EXPIRY_HOURS = 8


def _secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET is not set in environment / .env")
    return secret


def create_token(user_id: int, username: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=_EXPIRY_HOURS)
    return jwt.encode(
        {"sub": str(user_id), "username": username, "exp": expire},
        _secret(),
        algorithm=_ALGORITHM,
    )


def verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def login(username: str, password: str) -> dict:
    async with get_session() as session:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()

    if not user or not _bcrypt.checkpw(password.encode(), user.hashed_password.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    return {"access_token": create_token(user.id, user.username), "token_type": "bearer"}


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    return verify_token(token)
