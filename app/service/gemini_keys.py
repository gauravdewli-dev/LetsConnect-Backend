import logging

from google import genai
from google.genai import types
from google.genai.errors import ClientError

logger = logging.getLogger(__name__)


class GeminiKeyPool:
    """Round-robin Gemini API keys; rotate to the next key on rate-limit errors."""

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("At least one Gemini API key is required")
        self._keys = keys
        self._index = 0

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def client(self) -> genai.Client:
        return genai.Client(api_key=self._keys[self._index])

    def rotate(self) -> bool:
        if self._index + 1 >= len(self._keys):
            return False
        self._index += 1
        logger.warning(
            "Gemini rate limit (429) — switching to API key %s/%s",
            self._index + 1,
            len(self._keys),
        )
        return True


def is_rate_limit_error(exc: ClientError) -> bool:
    if exc.code == 429:
        return True
    message = str(exc).lower()
    return "rate limit" in message or "resource_exhausted" in message or "quota" in message


def generate_content(
    pool: GeminiKeyPool,
    *,
    model: str,
    contents: list[types.Content],
    config: types.GenerateContentConfig,
) -> types.GenerateContentResponse:
    last_exc: ClientError | None = None

    while True:
        try:
            return pool.client().models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except ClientError as exc:
            last_exc = exc
            if is_rate_limit_error(exc) and pool.rotate():
                continue
            break

    if last_exc and is_rate_limit_error(last_exc):
        if pool.key_count > 1:
            raise ValueError(
                "Gemini rate limit reached on all configured API keys — wait a minute and try again."
            ) from last_exc
        raise ValueError(
            "Gemini rate limit reached — wait a minute and try again, "
            "or add GEMINI_API_KEY_ONE/TWO/THREE in .env for automatic failover."
        ) from last_exc

    assert last_exc is not None
    raise ValueError(f"Gemini API error: {last_exc}") from last_exc
