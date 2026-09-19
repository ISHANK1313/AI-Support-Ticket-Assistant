"""Password hashing and JWT issue/verify."""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pydantic import BaseModel

from .config import Settings

_JWT_ALGORITHMS = ["HS256"]

# Password policy per TRD: 8-128 chars.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


def hash_password(password: str) -> str:
    from argon2 import PasswordHasher

    return PasswordHasher().hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

    try:
        return PasswordHasher().verify(password_hash, password)
    except (VerificationError, InvalidHashError, ValueError):
        return False


def create_access_token(user_id: int, settings: Settings) -> tuple[str, int]:
    expires_in = settings.jwt_expire_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=_JWT_ALGORITHMS[0])
    return token, expires_in


class AuthenticatedUser(BaseModel):
    id: int
    email: str


def make_auth_dependency(settings: Settings):
    """FastAPI dependency factory: validates the Bearer JWT and resolves the user.

    The explicit algorithms allowlist blocks alg=none / algorithm-confusion attacks.
    """
    bearer_scheme = HTTPBearer(auto_error=False)

    def _get_current_user(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> AuthenticatedUser:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "NOT_AUTHENTICATED", "message": "Missing bearer token"},
            )
        try:
            payload = jwt.decode(
                credentials.credentials,
                settings.jwt_secret,
                algorithms=_JWT_ALGORITHMS,
                options={"require": ["sub", "iat", "exp"]},
            )
            subject = payload["sub"]
            if not isinstance(subject, str) or not subject.isdecimal() or len(subject) > 18:
                raise ValueError("Invalid subject")
            user_id = int(subject)
        except (InvalidTokenError, KeyError, ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid or expired token"},
            )
        import sqlite3

        from . import database

        conn = database.connect(settings.database_path)
        try:
            row = database.get_user_by_id(conn, user_id)
        finally:
            conn.close()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid or expired token"},
            )
        return AuthenticatedUser(id=row["id"], email=row["email"])

    return _get_current_user
