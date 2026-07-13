from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_auth import router as auth_router
from app.api.routes_connect import router as connect_router
from app.api.routes_health import router as health_router
from app.api.routes_slack import router as slack_router
from app.config import get_settings
from app.middleware.access_log import configure_sanitized_access_logs
from app.middleware.security import AuthRateLimitMiddleware, SecurityHeadersMiddleware
from app.schema import auth_session, connections, conversations, password_reset_otp, pending_action, users  # noqa: F401

settings = get_settings()

configure_sanitized_access_logs()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not settings.skip_startup_migrations:
        from app.configs.database.migrate import ensure_schema
        from app.configs.mongodb.client import ensure_mongo_indexes

        await asyncio.gather(
            asyncio.to_thread(ensure_schema),
            asyncio.to_thread(ensure_mongo_indexes),
        )
    yield


app = FastAPI(title="LetsConnect AI Assistant", lifespan=lifespan)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AuthRateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(connect_router)
app.include_router(slack_router)
