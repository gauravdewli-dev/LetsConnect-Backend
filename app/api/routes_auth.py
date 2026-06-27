from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.configs.database.db import get_db
from app.constants import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    LOGIN_RATE_LIMIT_REQUESTS,
    LOGIN_RATE_LIMIT_WINDOW_SECONDS,
)
from app.schema.users import User
from app.security import get_current_user
from app.middleware.rate_limit import auth_rate_limiter
from app.service import users as user_service
from app.service.auth_sessions import create_session, refresh_session, revoke_session
from app.service.email_verification import send_signup_verification, verify_signup_email
from app.service.password_reset import request_password_reset, reset_password_with_otp
from app.types import (
    DeleteUserRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshTokenRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SignupRequest,
    SignupResponse,
    TokenResponse,
    UpdateUserRequest,
    UserResponse,
    VerifyEmailRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _enforce_login_rate_limit(request: Request, email: str) -> None:
    key = f"login:{_client_ip(request)}:{email.strip().lower()}"
    if not auth_rate_limiter.is_allowed(
        key,
        max_requests=LOGIN_RATE_LIMIT_REQUESTS,
        window_seconds=LOGIN_RATE_LIMIT_WINDOW_SECONDS,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
        )


def _value_error_to_http(exc: ValueError) -> HTTPException:
    message = str(exc)
    if message in {
        "Invalid email or password",
        "Invalid or expired session",
        "Invalid or expired code",
        "Email not verified — check your inbox for the verification code",
    }:
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message)
    if message in {"Current password is incorrect", "Invalid password"}:
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _token_response(access_token: str, refresh_token: str) -> TokenResponse:
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    user = None
    try:
        user = user_service.create_user(db, str(payload.email), payload.password)
        send_signup_verification(db, user.email)
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    except RuntimeError as exc:
        if user is not None:
            db.delete(user)
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not send verification email. Try again later.",
        ) from exc
    return SignupResponse(
        message="Verification code sent to your email. Enter it below to activate your account."
    )


@router.post("/verify-email", response_model=TokenResponse)
def verify_email(payload: VerifyEmailRequest, db: Session = Depends(get_db)):
    try:
        verify_signup_email(db, str(payload.email), payload.otp)
        user = user_service.get_user_by_email(db, str(payload.email))
        if not user:
            raise ValueError("Invalid email or code")
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    access_token, refresh_token = create_session(db, user)
    return _token_response(access_token, refresh_token)


@router.post("/resend-verification", response_model=MessageResponse)
def resend_verification(payload: ResendVerificationRequest, db: Session = Depends(get_db)):
    user = user_service.get_user_by_email(db, str(payload.email))
    if not user or user.email_verified:
        return MessageResponse(message="If your account needs verification, a new code has been sent.")
    try:
        send_signup_verification(db, user.email)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not send verification email. Try again later.",
        ) from exc
    return MessageResponse(message="Verification code sent. Check your email.")


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    _enforce_login_rate_limit(request, str(payload.email))
    try:
        user = user_service.authenticate(db, str(payload.email), payload.password)
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    access_token, refresh_token = create_session(db, user)
    return _token_response(access_token, refresh_token)


@router.post("/refresh", response_model=TokenResponse)
def refresh_tokens(payload: RefreshTokenRequest, db: Session = Depends(get_db)):
    try:
        access_token, refresh_token, _user = refresh_session(db, payload.refresh_token)
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    return _token_response(access_token, refresh_token)


@router.post("/logout", response_model=MessageResponse)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)):
    revoke_session(db, payload.refresh_token)
    return MessageResponse(message="Logged out")


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    try:
        request_password_reset(db, str(payload.email))
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not send reset email. Try again later.",
        ) from exc
    return MessageResponse(
        message="If an account exists for that email, a reset code has been sent."
    )


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    try:
        reset_password_with_otp(db, str(payload.email), payload.otp, payload.new_password)
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    return MessageResponse(message="Password updated. You can sign in now.")


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
            email=str(payload.email) if payload.email else None,
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
