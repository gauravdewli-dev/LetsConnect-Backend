from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.configs.database.db import get_db
from app.schema.users import User
from app.security import create_access_token, get_current_user
from app.service import users as user_service
from app.types import (
    DeleteUserRequest,
    LoginRequest,
    MessageResponse,
    SignupRequest,
    TokenResponse,
    UpdateUserRequest,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _value_error_to_http(exc: ValueError) -> HTTPException:
    message = str(exc)
    if message == "Invalid email or password":
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message)
    if message in {"Current password is incorrect", "Invalid password"}:
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    try:
        user = user_service.create_user(db, payload.email, payload.password)
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    try:
        user = user_service.authenticate(db, payload.email, payload.password)
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserResponse)
def update_me(
    payload: UpdateUserRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.email is None and payload.password is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide email and/or password to update",
        )
    try:
        updated = user_service.update_user(
            db,
            user,
            email=payload.email,
            password=payload.password,
            current_password=payload.current_password,
        )
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    return updated


@router.delete("/me", response_model=MessageResponse)
def delete_me(
    payload: DeleteUserRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        user_service.delete_user(db, user, payload.password)
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    return MessageResponse(message="Account deleted successfully")
