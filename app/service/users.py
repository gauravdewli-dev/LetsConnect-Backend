from sqlalchemy.orm import Session

from app.schema.connections import GmailConnection, SlackConnection
from app.schema.pending_action import PendingAction
from app.schema.users import User
from app.security import hash_password, verify_password


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == normalize_email(email)).first()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def create_user(db: Session, email: str, password: str) -> User:
    normalized = normalize_email(email)
    if get_user_by_email(db, normalized):
        raise ValueError("Email already registered")

    user = User(email=normalized, hashed_password=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    user = get_user_by_email(db, email)
    if not user or not verify_password(password, user.hashed_password):
        raise ValueError("Invalid email or password")
    return user


def update_user(
    db: Session,
    user: User,
    *,
    email: str | None = None,
    password: str | None = None,
    current_password: str | None = None,
) -> User:
    if email is not None or password is not None:
        if not current_password:
            raise ValueError("Current password is required")
        if not verify_password(current_password, user.hashed_password):
            raise ValueError("Current password is incorrect")

    if email is not None:
        normalized = normalize_email(email)
        if normalized != user.email:
            existing = get_user_by_email(db, normalized)
            if existing and existing.id != user.id:
                raise ValueError("Email already registered")
            user.email = normalized

    if password is not None:
        user.hashed_password = hash_password(password)

    db.commit()
    db.refresh(user)
    return user


def delete_user(db: Session, user: User, password: str) -> None:
    if not verify_password(password, user.hashed_password):
        raise ValueError("Invalid password")

    db.query(PendingAction).filter(PendingAction.user_id == user.id).delete()
    db.query(GmailConnection).filter(GmailConnection.user_id == user.id).delete()
    db.query(SlackConnection).filter(SlackConnection.user_id == user.id).delete()
    db.delete(user)
    db.commit()
