from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_auth import router as auth_router
from app.api.routes_connect import router as connect_router
from app.api.routes_slack import router as slack_router
from app.config import get_settings
from app.configs.database.db import Base, engine
from app.configs.database.migrate import ensure_schema
from app.middleware.security import AuthRateLimitMiddleware, SecurityHeadersMiddleware
from app.schema import auth_session, connections, password_reset_otp, pending_action, users  # noqa: F401

settings = get_settings()

app = FastAPI(title="LetsConnect AI Assistant")

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AuthRateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router)
app.include_router(connect_router)
app.include_router(slack_router)

try:
    conn = engine.connect()
    conn.close()
    ensure_schema()
except Exception as e:
    print(f"Error connecting to the database: {e}")
