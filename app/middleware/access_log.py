import logging

# Paths whose query strings must never appear in access logs (OAuth codes, tokens, state).
_SENSITIVE_PATH_LABELS: dict[str, str] = {
    "/oauth/callback": "Gmail OAuth callback handled successfully",
    "/slack/oauth/callback": "Slack OAuth callback handled successfully",
    "/jira/oauth/callback": "Jira OAuth callback handled successfully",
    "/gmail/connect": "Gmail OAuth connect initiated",
    "/slack/install": "Slack OAuth connect initiated",
    "/jira/connect": "Jira OAuth connect initiated",
}


class SanitizeAccessLogFilter(logging.Filter):
    """Strip OAuth query strings from uvicorn access logs without breaking its formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True

        full_path = args[2]
        if not isinstance(full_path, str):
            return True

        path = full_path.split("?", 1)[0]
        label = _SENSITIVE_PATH_LABELS.get(path)
        if not label:
            return True

        # Uvicorn AccessFormatter expects exactly 5 args — only replace the path segment.
        record.args = (
            args[0],
            args[1],
            f"{path} — {label}",
            args[3],
            args[4],
        )
        return True


def configure_sanitized_access_logs() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if any(isinstance(item, SanitizeAccessLogFilter) for item in access_logger.filters):
        return
    access_logger.addFilter(SanitizeAccessLogFilter())
