import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings
from pydantic import Field
from repository import CalendarRepository
from stdio_server import run_stdio

Settings.model_rebuild()

repository = CalendarRepository()
LOCAL_TIMEZONE_NAME = os.getenv("MCP_LOCAL_TIMEZONE", "America/Mexico_City")
try:
    LOCAL_TIMEZONE = ZoneInfo(LOCAL_TIMEZONE_NAME)
except ZoneInfoNotFoundError as exc:
    raise RuntimeError(f"Unknown MCP_LOCAL_TIMEZONE: {LOCAL_TIMEZONE_NAME}") from exc


def _parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TIMEZONE)
    return parsed


mcp = FastMCP(
    "Local Calendar",
    instructions=(
        "Manage calendars and appointments stored by this Open WebUI instance. Interpret natural "
        "phrasing broadly: infer useful titles, descriptions, locations, and date ranges from the "
        "user's words without requiring them to dictate field names. Use get_current_datetime to "
        "resolve relative expressions such as today, tomorrow, next Tuesday, or this week, then pass "
        "absolute local ISO datetimes to calendar tools. Omit genuinely unspecified optional fields "
        "and allow server defaults to apply. Search before changing an existing event and use only "
        "the exact event ID returned by that search. Never broaden a single-event request into a "
        "batch operation. Bulk deletion is disabled."
    ),
)


@mcp.tool()
def get_current_datetime() -> dict[str, Any]:
    """Return the authoritative current date, weekday, local time, timezone, and UTC time.

    Use this whenever a request depends on relative or contextual time, including “today”,
    “tomorrow”, “next week”, “in two hours”, or whether an event is in the past. The returned
    local timezone is the timezone expected by the other calendar tools.
    """
    local_now = datetime.now(LOCAL_TIMEZONE)
    return {
        "local_datetime": local_now.isoformat(timespec="seconds"),
        "utc_datetime": local_now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "date": local_now.date().isoformat(),
        "weekday": local_now.strftime("%A"),
        "timezone": LOCAL_TIMEZONE_NAME,
    }


@mcp.tool()
def search_calendar_events(
    query: Annotated[
        str | None,
        Field(
            description=(
                "Optional free-text filter matched against event titles, descriptions, and "
                "locations. Examples: 'dentist', 'Ana Leyva', 'office', or 'project review'. "
                "Omit it when the user is asking only about a date range."
            )
        ),
    ] = None,
    start: Annotated[
        str | None,
        Field(
            description=(
                "Optional inclusive beginning of the range as an absolute local ISO date or "
                "datetime, for example '2026-09-09', '2026-09-09 09:00', or "
                "'2026-09-09T09:00:00-06:00'. Resolve phrases such as 'this week' first."
            )
        ),
    ] = None,
    end: Annotated[
        str | None,
        Field(
            description=(
                "Optional end of the range as an absolute local ISO date or datetime, for "
                "example '2026-09-15 23:59'. Use the current datetime as the end when searching "
                "for events that have already happened."
            )
        ),
    ] = None,
    count: Annotated[
        int,
        Field(
            description=(
                "Maximum number of matching events to return, from 1 to 100. Use a larger value "
                "when the user asks for a complete list and a smaller value for a quick answer."
            ),
            ge=1,
            le=100,
        ),
    ] = 20,
) -> dict[str, Any]:
    """Find calendar events using text, a time range, or both.

    This supports broad requests such as “What is on my schedule tomorrow?”, “Find my appointment
    with Ana”, “How many meetings happened this week?”, and “What is next?”. Results include
    readable event details and exact IDs. Use those IDs for updates or single-event deletion; do
    not invent IDs or assume that similarly named events are interchangeable.
    """
    events = repository.search(
        query=query,
        start=_parse_datetime(start).isoformat() if start else None,
        end=_parse_datetime(end).isoformat() if end else None,
        count=count,
    )
    return {"events": events, "total": len(events)}


@mcp.tool()
def create_calendar_event(
    title: Annotated[
        str,
        Field(
            description=(
                "Concise human-readable event name inferred from the purpose or participants. "
                "Examples: 'Appointment with Dra Ana Leyva', 'Team planning meeting', "
                "'Pick up Maria', or 'Pay electricity bill'. Do not leave this blank merely "
                "because the user did not say the word title."
            ),
            min_length=1,
        ),
    ],
    start: Annotated[
        str,
        Field(
            description=(
                "Resolved start as an absolute local ISO date or datetime. Examples: "
                "'2026-09-09 12:00', '2026-09-09T12:00:00-06:00', or '2026-09-09' for an "
                "all-day event. Convert 'tomorrow at noon' or 'next Friday morning' before "
                "calling; never pass unresolved relative words."
            )
        ),
    ],
    end: Annotated[
        str | None,
        Field(
            description=(
                "Optional absolute local ISO end. Examples: '2026-09-09 12:30' or "
                "'2026-09-09T13:00:00-06:00'. Infer it when the user gives a duration or end "
                "time; otherwise omit it and the server uses a one-hour default for timed events."
            )
        ),
    ] = None,
    description: Annotated[
        str | None,
        Field(
            description=(
                "Optional useful notes or context that do not belong in the short title. Examples: "
                "'Bring recent test results', 'Discuss the Q4 budget', or a phone number supplied "
                "by the user. Omit when no additional context was provided."
            )
        ),
    ] = None,
    all_day: Annotated[
        bool,
        Field(
            description=(
                "Whether the event occupies a whole day rather than a specific time. Use true for "
                "phrases such as 'all day', 'birthday', or 'holiday' when appropriate; use false "
                "when the user supplies a time such as noon or 3:30 PM."
            )
        ),
    ] = False,
    location: Annotated[
        str | None,
        Field(
            description=(
                "Optional physical or virtual place mentioned by the user. Examples: 'Dra Ana "
                "Leyva's office', 'Conference Room B', 'Home', or a meeting URL. Natural phrases "
                "such as 'in her office' should be preserved or clarified from context."
            )
        ),
    ] = None,
    reminder_minutes: Annotated[
        int,
        Field(
            description=(
                "Minutes before the event for its reminder. Examples: 10 for the normal default, "
                "30 for 'remind me half an hour before', or 1440 for one day before."
            ),
            ge=0,
        ),
    ] = 10,
) -> dict[str, Any]:
    """Create one calendar event from a natural-language scheduling request.

    Infer reasonable structured values from what the user already supplied instead of asking them
    to repeat information as field names. A request like “appointment with Dra Ana Leyva tomorrow
    at noon in her office” provides a title, start, and location. Resolve relative time first,
    omit unspecified optional fields, and create exactly one event per requested appointment.
    """
    start_at = _parse_datetime(start)
    end_at = _parse_datetime(end) if end else (None if all_day else start_at + timedelta(hours=1))
    event = repository.create(
        title=title,
        description=description,
        start=start_at.isoformat(),
        end=end_at.isoformat() if end_at else None,
        all_day=all_day,
        location=location,
        reminder_minutes=reminder_minutes,
    )
    return {"status": "created", "event": event}


@mcp.tool()
def update_calendar_event(
    event_id: Annotated[
        str,
        Field(
            description=(
                "Exact event ID returned by search_calendar_events, for example "
                "'b5af15df-f358-4061-ac6b-365c81d76f78'. Never substitute a title, calendar ID, "
                "placeholder, or guessed value."
            )
        ),
    ],
    title: Annotated[
        str | None,
        Field(description="Optional replacement title. Omit it to preserve the current title."),
    ] = None,
    start: Annotated[
        str | None,
        Field(
            description=(
                "Optional replacement start as an absolute local ISO datetime, such as "
                "'2026-09-10 14:30'. Resolve relative requests like 'move it to tomorrow' first."
            )
        ),
    ] = None,
    end: Annotated[
        str | None,
        Field(
            description=(
                "Optional replacement end as an absolute local ISO datetime. Omit it to preserve "
                "the existing end; include it when changing the duration or explicit end time."
            )
        ),
    ] = None,
    description: Annotated[
        str | None,
        Field(description="Optional replacement notes. Omit to preserve existing notes."),
    ] = None,
    all_day: Annotated[
        bool | None,
        Field(description="Optional replacement all-day status. Omit to preserve its current value."),
    ] = None,
    location: Annotated[
        str | None,
        Field(
            description=(
                "Optional replacement physical or virtual location, such as 'Main office', "
                "'Room 4', or a meeting URL. Omit to preserve the existing location."
            )
        ),
    ] = None,
    reminder_minutes: Annotated[
        int | None,
        Field(
            description=(
                "Optional replacement reminder lead time in minutes; for example 15, 60, or 1440. "
                "Omit to preserve the existing reminder."
            ),
            ge=0,
        ),
    ] = None,
) -> dict[str, Any]:
    """Change selected fields on exactly one existing calendar event.

    Search first when the user identifies an event by person, title, date, or conversational
    reference. Use the returned exact event ID, change only the fields requested, and leave every
    other field omitted so it is preserved. If several events could match, ask the user to choose.
    """
    changes = {
        "title": title,
        "start": _parse_datetime(start).isoformat() if start else None,
        "end": _parse_datetime(end).isoformat() if end else None,
        "description": description,
        "all_day": all_day,
        "location": location,
        "reminder_minutes": reminder_minutes,
    }
    event = repository.update(event_id, **changes)
    return {"status": "updated", "event": event}


@mcp.tool()
def delete_calendar_event(
    event_id: Annotated[
        str,
        Field(
            description=(
                "Exact ID of the single event to delete, obtained from search_calendar_events. "
                "Example: 'b5af15df-f358-4061-ac6b-365c81d76f78'. Never pass a calendar ID, "
                "event title, placeholder, multiple IDs, or an ID inferred from an unrelated result."
            )
        ),
    ],
) -> dict[str, Any]:
    """Permanently delete exactly one identified calendar event.

    Use only when the user clearly requested deletion of that specific event. Search first unless
    the user supplied its exact event ID. Never interpret “that one” as every search result, never
    broaden this operation, and never use it to implement bulk deletion.
    """
    event = repository.delete(event_id)
    return {"status": "deleted", "event": event}


@mcp.tool()
def delete_calendar_events(
    event_ids: Annotated[
        list[str],
        Field(
            description=(
                "Requested event IDs. This compatibility parameter is accepted only so the tool "
                "can return a controlled rejection; none of the IDs will be deleted."
            )
        ),
    ],
) -> dict[str, Any]:
    """Reject every batch calendar-deletion request without changing the calendar.

    Bulk deletion is deliberately disabled as a safety policy. This tool always returns a rejection.
    To delete an event, the user must select one exact ID and use delete_calendar_event.
    """
    return {
        "status": "rejected",
        "error": "Bulk calendar deletion is disabled. Delete one event at a time by exact ID.",
        "requested_total": len(set(event_id for event_id in event_ids if event_id)),
    }


if __name__ == "__main__":
    print(f"Shared calendar database: {repository.path}; timezone: {LOCAL_TIMEZONE_NAME}", file=sys.stderr)
    if os.getenv("CALENDAR_API_ENABLED", "true").lower() == "true":
        from api import run as run_api

        threading.Thread(target=run_api, name="shared-calendar-api", daemon=True).start()
    run_stdio(mcp)
