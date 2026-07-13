from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Lightweight liveness probe — used by keep-alive cron so Render stays awake."""
    return {"status": "ok"}
