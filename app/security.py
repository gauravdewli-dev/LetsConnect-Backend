import base64
import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.configs.database.db import get_db
from app.constants import ACCESS_TOKEN_EXPIRE_MINUTES, JWT_ALGORITHM, OAUTH_STATE_EXPIRE_MINUTES
from app.schema.users import User

_bearer = HTTPBearer(auto_error=False)


def _fernet_key() -> bytes:
    settings = get_settings()
    raw_key = settings.encryption_key.strip()
    if raw_key:
        key_bytes = raw_key.encode()
        try:
            Fernet(key_bytes)
            return key_bytes
        except ValueError:
            pass

    derived = hashlib.sha256(settings.jwt_secret.encode()).digest()
    return base64.urlsafe_b64encode(derived)


def _fernet() -> Fernet:
    return Fernet(_fernet_key())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_refresh_token(plain: str, hashed: str) -> bool:
    return hash_refresh_token(plain) == hashed


def create_access_token(user_id: int, email: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "email": email, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def create_oauth_state_token(
    user_id: int,
    provider: str,
    code_verifier: str | None = None,
) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=OAUTH_STATE_EXPIRE_MINUTES)
    payload: dict[str, str | int] = {
        "sub": str(user_id),
        "provider": provider,
        "exp": expire,
        "type": "oauth_state",
    }
    if code_verifier:
        payload["cv"] = code_verifier
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc


def encrypt_token(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_token(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def _user_from_payload(payload: dict, db: Session) -> User:
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
    except OperationalError:
        db.rollback()
        user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    if payload.get("type") == "oauth_state":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    if payload.get("type") not in (None, "access"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    return _user_from_payload(payload, db)


def get_user_from_query_token(token: str | None, db: Session) -> User:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    payload = decode_token(token)
    return _user_from_payload(payload, db)
