import json
import secrets
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def _format_event(event: dict[str, Any]) -> dict[str, Any]:
    start = event.get("start", {})
    end = event.get("end", {})
    attendees = event.get("attendees", [])
    conference = event.get("conferenceData", {})
    meet_link = None
    for entry_point in conference.get("entryPoints", []):
        if entry_point.get("entryPointType") == "video":
            meet_link = entry_point.get("uri")
            break
    if not meet_link:
        meet_link = event.get("hangoutLink")

    return {
        "id": event.get("id"),
        "summary": event.get("summary"),
        "description": event.get("description"),
        "location": event.get("location"),
        "status": event.get("status"),
        "html_link": event.get("htmlLink"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "timezone": start.get("timeZone"),
        "all_day": "date" in start and "dateTime" not in start,
        "attendees": [
            {
                "email": attendee.get("email"),
                "display_name": attendee.get("displayName"),
                "response_status": attendee.get("responseStatus"),
            }
            for attendee in attendees
        ],
        "organizer": (event.get("organizer") or {}).get("email"),
        "meet_link": meet_link,
    }


def _event_time(value: str, *, timezone_name: str | None, all_day: bool = False) -> dict[str, str]:
    if all_day:
        return {"date": value[:10]}
    payload: dict[str, str] = {"dateTime": value}
    if timezone_name:
        payload["timeZone"] = timezone_name
    return payload


def _http_error_details(exc: HttpError) -> tuple[int | None, str | None, str | None]:
    status = exc.resp.status if exc.resp else None
    reason: str | None = None
    message: str | None = None
    try:
        raw = exc.content.decode("utf-8") if isinstance(exc.content, bytes) else exc.content
        payload = json.loads(raw) if raw else {}
        err = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(err, dict):
            message = err.get("message")
            errors = err.get("errors") or []
            if errors and isinstance(errors[0], dict):
                reason = errors[0].get("reason")
                message = message or errors[0].get("message")
        elif isinstance(err, str):
            message = err
    except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
        pass
    return status, reason, message


def _calendar_http_error(exc: HttpError) -> ValueError:
    status, reason, message = _http_error_details(exc)
    detail = message or str(exc)

    if status == 403:
        if reason == "accessNotConfigured":
            return ValueError(
                "Google Calendar API is not enabled for this Google Cloud project. "
                "In Google Cloud Console → APIs & Services → Library, enable "
                "'Google Calendar API', then retry."
            )
        return ValueError(
            "Google Calendar permission denied. "
            "Disconnect Gmail in LetsConnect, remove LetsConnect at "
            "https://myaccount.google.com/permissions, reconnect, and allow Calendar. "
            "Also confirm Google Calendar API is enabled in Google Cloud Console. "
            f"({reason or 'forbidden'}: {detail})"
        )
    if status == 404:
        return ValueError("Calendar event or calendar not found.")
    return ValueError(f"Google Calendar API error ({status or 'unknown'}): {detail}")


class CalendarClient:
    def __init__(self, *, credentials: Credentials) -> None:
        self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        self._timezone_cache: str | None = None

    def _execute(self, request: Any) -> Any:
        try:
            return request.execute()
        except HttpError as exc:
            raise _calendar_http_error(exc) from exc

    def get_primary_timezone(self) -> str:
        """Read primary calendar timezone.

        Requires the full ``calendar`` scope. With only ``calendar.events``,
        metadata calls 403 — we fall back so event list/create still work.
        """
        if self._timezone_cache:
            return self._timezone_cache

        try:
            calendar = self._service.calendars().get(calendarId="primary").execute()
            tz_name = calendar.get("timeZone") or "UTC"
            self._timezone_cache = tz_name
            return tz_name
        except HttpError as exc:
            status, _reason, _message = _http_error_details(exc)
            # calendar.events can manage events but not calendars.get metadata
            if status in {403, 404}:
                self._timezone_cache = "UTC"
                return "UTC"
            raise _calendar_http_error(exc) from exc

    def list_events(
        self,
        *,
        time_min: str | None = None,
        time_max: str | None = None,
        on_date: str | None = None,
        max_results: int = 10,
        query: str | None = None,
        timezone_name: str | None = None,
    ) -> dict[str, Any]:
        max_results = max(1, min(max_results, 50))
        tz_name = timezone_name or self.get_primary_timezone()

        if on_date:
            day = datetime.strptime(on_date[:10], "%Y-%m-%d").date()
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                tz = ZoneInfo("UTC")
                tz_name = "UTC"
            time_min = datetime.combine(day, time.min, tzinfo=tz).isoformat()
            time_max = datetime.combine(day, time.max, tzinfo=tz).isoformat()
        elif not time_min:
            time_min = datetime.now(timezone.utc).isoformat()

        request_kwargs: dict[str, Any] = {
            "calendarId": "primary",
            "timeMin": time_min,
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if time_max:
            request_kwargs["timeMax"] = time_max
        if query:
            request_kwargs["q"] = query

        result = self._execute(self._service.events().list(**request_kwargs))
        events = [_format_event(event) for event in result.get("items", [])]

        # Prefer timezone from an event if metadata lookup fell back to UTC
        if tz_name == "UTC":
            for event in events:
                event_tz = event.get("timezone")
                if event_tz:
                    tz_name = event_tz
                    self._timezone_cache = event_tz
                    break

        return {
            "events": events,
            "timezone": tz_name,
            "count": len(events),
        }

    def get_event(self, event_id: str) -> dict[str, Any]:
        event = self._execute(
            self._service.events().get(calendarId="primary", eventId=event_id)
        )
        return _format_event(event)

    def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        *,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        timezone_name: str | None = None,
        all_day: bool = False,
        add_google_meet: bool = False,
    ) -> dict[str, Any]:
        tz = timezone_name or self.get_primary_timezone()
        body: dict[str, Any] = {
            "summary": summary,
            "start": _event_time(start, timezone_name=tz, all_day=all_day),
            "end": _event_time(end, timezone_name=tz, all_day=all_day),
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": email.strip()} for email in attendees if email.strip()]

        insert_kwargs: dict[str, Any] = {
            "calendarId": "primary",
            "body": body,
            "sendUpdates": "all" if attendees else "none",
        }
        if add_google_meet:
            body["conferenceData"] = {
                "createRequest": {
                    "requestId": secrets.token_hex(8),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            insert_kwargs["conferenceDataVersion"] = 1

        event = self._execute(self._service.events().insert(**insert_kwargs))
        return _format_event(event)

    def update_event(
        self,
        event_id: str,
        *,
        summary: str | None = None,
        start: str | None = None,
        end: str | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        timezone_name: str | None = None,
        all_day: bool = False,
    ) -> dict[str, Any]:
        existing = self._execute(
            self._service.events().get(calendarId="primary", eventId=event_id)
        )
        tz = timezone_name or self.get_primary_timezone()
        if summary is not None:
            existing["summary"] = summary
        if description is not None:
            existing["description"] = description
        if location is not None:
            existing["location"] = location
        if start is not None:
            existing["start"] = _event_time(start, timezone_name=tz, all_day=all_day)
        if end is not None:
            existing["end"] = _event_time(end, timezone_name=tz, all_day=all_day)
        if attendees is not None:
            existing["attendees"] = [{"email": email.strip()} for email in attendees if email.strip()]

        event = self._execute(
            self._service.events().update(
                calendarId="primary",
                eventId=event_id,
                body=existing,
                sendUpdates="all" if attendees else "none",
            )
        )
        return _format_event(event)

    def delete_event(self, event_id: str) -> dict[str, str]:
        self._execute(self._service.events().delete(calendarId="primary", eventId=event_id))
        return {"deleted": event_id}
