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
        self._clients: dict[int, genai.Client] = {}

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def client(self) -> genai.Client:
        if self._index not in self._clients:
            self._clients[self._index] = genai.Client(api_key=self._keys[self._index])
        return self._clients[self._index]

    def invalidate_current_client(self) -> None:
        self._clients.pop(self._index, None)

    def rotate(self) -> bool:
        self.invalidate_current_client()
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


def _is_closed_client_error(exc: RuntimeError) -> bool:
    return "client has been closed" in str(exc).lower()


def generate_content(
    pool: GeminiKeyPool,
    *,
    model: str,
    contents: list[types.Content],
    config: types.GenerateContentConfig,
) -> types.GenerateContentResponse:
    last_exc: Exception | None = None
    retried_closed_on_key = False

    while True:
        client = pool.client()
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except ClientError as exc:
            last_exc = exc
            pool.invalidate_current_client()
            if is_rate_limit_error(exc) and pool.rotate():
                retried_closed_on_key = False
                continue
            break
        except RuntimeError as exc:
            if not _is_closed_client_error(exc):
                raise ValueError(f"Gemini API error: {exc}") from exc
            pool.invalidate_current_client()
            last_exc = exc
            if not retried_closed_on_key:
                retried_closed_on_key = True
                continue
            if pool.rotate():
                retried_closed_on_key = False
                continue
            break

    if last_exc and isinstance(last_exc, ClientError) and is_rate_limit_error(last_exc):
        if pool.key_count > 1:
            raise ValueError(
                "Gemini rate limit reached on all configured API keys — wait a minute and try again."
            ) from last_exc
        raise ValueError(
            "Gemini rate limit reached — wait a minute and try again, "
            "or add GEMINI_API_KEY_ONE/TWO/THREE in .env for automatic failover."
        ) from last_exc

    if last_exc and isinstance(last_exc, RuntimeError) and _is_closed_client_error(last_exc):
        raise ValueError("Gemini connection error — please try again.") from last_exc

    if last_exc and isinstance(last_exc, ClientError):
        raise ValueError(f"Gemini API error: {last_exc}") from last_exc

    raise RuntimeError("Gemini generate_content failed unexpectedly")
