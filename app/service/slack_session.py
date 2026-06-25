from collections import deque
from threading import Lock

MAX_MESSAGES = 20

_lock = Lock()
_sessions: dict[str, deque[dict[str, str]]] = {}


def session_key(slack_user_id: str, channel: str) -> str:
    return f"{slack_user_id}:{channel}"


def get_history(key: str) -> list[dict[str, str]]:
    with _lock:
        session = _sessions.get(key)
        return list(session) if session else []


def append_exchange(key: str, user_text: str, assistant_text: str) -> None:
    with _lock:
        if key not in _sessions:
            _sessions[key] = deque(maxlen=MAX_MESSAGES)
        session = _sessions[key]
        session.append({"role": "user", "content": user_text})
        session.append({"role": "assistant", "content": assistant_text})
