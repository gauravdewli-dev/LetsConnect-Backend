from typing import Any

import httpx

from app.constants import SLACK_API


class SlackTools:
    def __init__(
        self,
        bot_token: str,
        *,
        user_token: str | None = None,
        team_id: str | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._user_token = user_token
        self._team_id = team_id
        self._user_cache: dict[str, str] | None = None

    def _token_for_read(self) -> str:
        return self._user_token or self._bot_token

    def _require_user_token(self) -> str:
        if not self._user_token:
            raise ValueError(
                "Slack user permissions missing — disconnect and reconnect Slack in the dashboard "
                "so messages can be sent as you."
            )
        return self._user_token

    def _get(self, method: str, params: dict[str, Any] | None = None, *, as_user: bool = False) -> dict[str, Any]:
        token = self._require_user_token() if as_user else self._token_for_read()
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                f"{SLACK_API}/{method}",
                headers={"Authorization": f"Bearer {token}"},
                params=params or {},
            )
            data = response.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("error", f"Slack API error: {method}"))
        return data

    def _post(self, method: str, payload: dict[str, Any], *, as_user: bool = False) -> dict[str, Any]:
        token = self._require_user_token() if as_user else self._bot_token
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                f"{SLACK_API}/{method}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            data = response.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("error", f"Slack API error: {method}"))
        return data

    def _user_name_map(self) -> dict[str, str]:
        if self._user_cache is not None:
            return self._user_cache
        mapping: dict[str, str] = {}
        for member in self.list_users(limit=200):
            mapping[member["id"]] = (
                member.get("real_name") or member.get("display_name") or member.get("name") or member["id"]
            )
        self._user_cache = mapping
        return mapping

    def get_workspace(self) -> dict[str, str]:
        if self._team_id:
            data = self._get("team.info", {"team": self._team_id})
            team = data.get("team", {})
            return {
                "team_id": team.get("id", self._team_id),
                "name": team.get("name", ""),
                "domain": team.get("domain", ""),
            }
        data = self._get("auth.test")
        return {
            "team_id": data.get("team_id", ""),
            "name": data.get("team", ""),
            "domain": data.get("url", "").replace("https://", "").replace(".slack.com/", ""),
            "bot_user": data.get("user", ""),
        }

    def list_users(self, limit: int = 50) -> list[dict[str, str]]:
        users: list[dict[str, str]] = []
        cursor: str | None = None
        while len(users) < limit:
            params: dict[str, Any] = {"limit": min(200, limit - len(users))}
            if cursor:
                params["cursor"] = cursor
            data = self._get("users.list", params)
            for member in data.get("members", []):
                if member.get("deleted") or member.get("is_bot"):
                    continue
                profile = member.get("profile", {})
                users.append(
                    {
                        "id": member["id"],
                        "name": member.get("name", ""),
                        "real_name": profile.get("real_name") or member.get("real_name", ""),
                        "display_name": profile.get("display_name", ""),
                    }
                )
                if len(users) >= limit:
                    break
            cursor = data.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                break
        return users[:limit]

    def list_channels(self, limit: int = 50) -> list[dict[str, str]]:
        channels: list[dict[str, str]] = []
        cursor: str | None = None
        use_user = bool(self._user_token)
        while len(channels) < limit:
            params: dict[str, Any] = {
                "types": "public_channel,private_channel",
                "exclude_archived": "true",
                "limit": min(200, limit - len(channels)),
            }
            if cursor:
                params["cursor"] = cursor
            data = self._get("conversations.list", params, as_user=use_user)
            for channel in data.get("channels", []):
                if not use_user and not channel.get("is_member") and channel.get("is_private"):
                    continue
                channels.append(
                    {
                        "id": channel["id"],
                        "name": channel.get("name", ""),
                        "is_private": str(channel.get("is_private", False)),
                        "topic": channel.get("topic", {}).get("value", ""),
                    }
                )
                if len(channels) >= limit:
                    break
            cursor = data.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                break
        return channels[:limit]

    def _resolve_user_id(self, user: str) -> str:
        if user.startswith("U") and len(user) >= 9:
            return user

        query = user.strip().lower()
        exact: list[dict[str, str]] = []
        partial: list[dict[str, str]] = []
        for member in self.list_users(limit=200):
            candidates = [
                member.get("name", ""),
                member.get("real_name", ""),
                member.get("display_name", ""),
            ]
            if any(c.lower() == query for c in candidates if c):
                exact.append(member)
            elif any(query in c.lower() for c in candidates if c):
                partial.append(member)

        matches = exact or partial
        if not matches:
            raise ValueError(f"No Slack user found matching '{user}'. Use slack_list_users first.")
        if len(matches) > 1:
            names = ", ".join(
                m.get("real_name") or m.get("name") or m["id"] for m in matches[:5]
            )
            raise ValueError(
                f"Multiple Slack users match '{user}': {names}. Be more specific or use a user ID."
            )
        return matches[0]["id"]

    def _resolve_channel_id(self, channel: str) -> str:
        if channel.startswith(("C", "G", "D")) and len(channel) >= 9:
            return channel
        name = channel.lstrip("#").lower()
        for ch in self.list_channels(limit=200):
            if ch["name"].lower() == name:
                return ch["id"]
        raise ValueError(
            f"No Slack channel '{channel}' found. "
            "Use slack_list_channels to see channels you can access."
        )

    def open_dm(self, user_id: str) -> str:
        data = self._post("conversations.open", {"users": user_id}, as_user=True)
        channel = data.get("channel", {})
        channel_id = channel.get("id")
        if not channel_id:
            raise RuntimeError("Failed to open Slack DM channel")
        return channel_id

    def send_dm(self, user: str, text: str) -> dict[str, Any]:
        user_id = self._resolve_user_id(user)
        channel_id = self.open_dm(user_id)
        data = self._post(
            "chat.postMessage",
            {"channel": channel_id, "text": text},
            as_user=True,
        )
        names = self._user_name_map()
        recipient = names.get(user_id, user)
        return {
            "ok": True,
            "user_id": user_id,
            "user_name": recipient,
            "channel_id": channel_id,
            "ts": data.get("ts", ""),
            "text": text,
            "sent_as": "user",
            "delivery_note": (
                f"Message sent as you in your normal DM with {recipient}. "
                "Check your Slack DM with them — it appears like you typed it."
            ),
        }

    def send_channel_message(self, channel: str, text: str) -> dict[str, Any]:
        channel_id = self._resolve_channel_id(channel)
        data = self._post(
            "chat.postMessage",
            {"channel": channel_id, "text": text},
            as_user=True,
        )
        return {
            "ok": True,
            "channel": channel.lstrip("#"),
            "channel_id": channel_id,
            "ts": data.get("ts", ""),
            "text": text,
            "sent_as": "user",
        }

    def read_channel(self, channel: str, limit: int = 10) -> dict[str, Any]:
        channel_id = self._resolve_channel_id(channel)
        use_user = bool(self._user_token)
        data = self._get(
            "conversations.history",
            {"channel": channel_id, "limit": min(max(limit, 1), 50)},
            as_user=use_user,
        )
        names = self._user_name_map()
        messages: list[dict[str, str]] = []
        for msg in data.get("messages", []):
            if msg.get("subtype") in {"channel_join", "channel_leave"}:
                continue
            user_id = msg.get("user", "")
            messages.append(
                {
                    "user": names.get(user_id, user_id or "unknown"),
                    "text": msg.get("text", ""),
                    "ts": msg.get("ts", ""),
                }
            )
        return {
            "channel": channel.lstrip("#"),
            "channel_id": channel_id,
            "message_count": len(messages),
            "messages": messages,
        }
