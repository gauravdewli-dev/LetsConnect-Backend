CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar",
]


def scope_grants_calendar_access(scopes: list[str]) -> bool:
    for scope in scopes:
        if scope in CALENDAR_SCOPES:
            return True
        if scope.startswith("https://www.googleapis.com/auth/calendar"):
            return True
    return False
