import os
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select

from ..database.db import get_session
from ..database.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

_ALGORITHM = "HS256"
_EXPIRY_HOURS = 8


def _secret() -> str:
    return os.getenv("JWT_SECRET", "fallback-dev-secret-change-in-production")


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

    if not user or not pwd_context.verify(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    return {"access_token": create_token(user.id, user.username), "token_type": "bearer"}


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    return verify_token(token)
