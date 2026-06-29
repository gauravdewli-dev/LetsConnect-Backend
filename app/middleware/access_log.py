import logging
import re

# Paths whose query strings must never appear in access logs (OAuth codes, tokens, state).
_SENSITIVE_PATH_LABELS: dict[str, str] = {
    "/oauth/callback": "Gmail OAuth callback handled successfully",
    "/slack/oauth/callback": "Slack OAuth callback handled successfully",
    "/jira/oauth/callback": "Jira OAuth callback handled successfully",
    "/gmail/connect": "Gmail OAuth connect initiated",
    "/slack/install": "Slack OAuth connect initiated",
    "/jira/connect": "Jira OAuth connect initiated",
}

_PATH_PATTERN = "|".join(re.escape(path) for path in _SENSITIVE_PATH_LABELS)
_ACCESS_LOG_RE = re.compile(
    rf'"([A-Z]+)\s+({_PATH_PATTERN})(?:\?[^"]*)?\s+HTTP/[\d.]+"'
)


def _redact_access_log_message(message: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        method = match.group(1)
        path = match.group(2)
        label = _SENSITIVE_PATH_LABELS[path]
        return f'"{method} {path} — {label} HTTP/1.1"'

    return _ACCESS_LOG_RE.sub(_replace, message)


class SanitizeAccessLogFilter(logging.Filter):
    """Strip OAuth codes, state, and tokens from uvicorn access log lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True

        redacted = _redact_access_log_message(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_sanitized_access_logs() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if any(isinstance(item, SanitizeAccessLogFilter) for item in access_logger.filters):
        return
    access_logger.addFilter(SanitizeAccessLogFilter())
