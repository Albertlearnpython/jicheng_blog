import uuid

from django.conf import settings
from django.core.cache import cache

from .feishu_client import FeishuConfigError, FeishuRequestError, feishu_api_request


def _dig(payload, *path):
    current = payload
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or len(current) <= part:
                return None
            current = current[part]
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _default_calendar_cache_key():
    return "feishu:calendar:default_id"


def resolve_calendar_id():
    configured = (settings.FEISHU_CALENDAR_ID or "").strip()
    if configured:
        return configured

    cached = cache.get(_default_calendar_cache_key())
    if cached:
        return cached

    response_data = feishu_api_request("POST", "/open-apis/calendar/v4/calendars/primary")
    calendar_id = (
        _dig(response_data, "data", "primary_calendar", "calendar_id")
        or _dig(response_data, "data", "calendar", "calendar_id")
        or _dig(response_data, "data", "calendar_id")
        or _dig(response_data, "data", "calendars", 0, "calendar", "calendar_id")
        or _dig(response_data, "data", "calendars", 0, "calendar_id")
        or _dig(response_data, "data", "items", 0, "calendar_id")
    )
    if not calendar_id:
        raise FeishuRequestError("Failed to resolve default Feishu calendar ID.")
    cache.set(_default_calendar_cache_key(), calendar_id, 3600)
    return calendar_id


def create_calendar_event(summary, description, start_timestamp, end_timestamp, timezone, attendee_open_id=None):
    if not summary:
        raise FeishuConfigError("Calendar summary is required.")

    calendar_id = resolve_calendar_id()
    response_data = feishu_api_request(
        "POST",
        f"/open-apis/calendar/v4/calendars/{calendar_id}/events",
        payload={
            "summary": summary,
            "description": description or "",
            "need_notification": True,
            "start_time": {"timestamp": str(start_timestamp), "timezone": timezone},
            "end_time": {"timestamp": str(end_timestamp), "timezone": timezone},
            "vchat": {"vc_type": "no_meeting"},
        },
        params={"idempotency_key": uuid.uuid4().hex},
    )
    event = _dig(response_data, "data", "event") or _dig(response_data, "data") or {}
    event_id = event.get("event_id") or _dig(response_data, "data", "event_id")
    if not event_id:
        raise FeishuRequestError("Feishu calendar create API returned no event ID.")

    if attendee_open_id and settings.FEISHU_CALENDAR_AUTO_INVITE_SENDER:
        try:
            add_event_attendees(calendar_id, event_id, [attendee_open_id])
        except FeishuRequestError:
            try:
                delete_calendar_event(event_id, calendar_id=calendar_id, need_notification=False)
            except FeishuRequestError:
                pass
            raise

    event["event_id"] = event_id
    event["calendar_id"] = calendar_id
    return event


def add_event_attendees(calendar_id, event_id, attendee_open_ids):
    attendees = [
        {"type": "user", "user_id": open_id}
        for open_id in attendee_open_ids
        if open_id
    ]
    if not attendees:
        return {}

    return feishu_api_request(
        "POST",
        f"/open-apis/calendar/v4/calendars/{calendar_id}/events/{event_id}/attendees",
        payload={
            "attendees": attendees,
            "need_notification": True,
        },
        params={"user_id_type": "open_id"},
    )


def list_calendar_events(start_timestamp, end_timestamp, page_size=None):
    calendar_id = resolve_calendar_id()
    response_data = feishu_api_request(
        "GET",
        f"/open-apis/calendar/v4/calendars/{calendar_id}/events",
        params={
            "start_time": str(start_timestamp),
            "end_time": str(end_timestamp),
            "page_size": page_size or settings.FEISHU_CALENDAR_LIST_PAGE_SIZE,
        },
    )
    items = (
        _dig(response_data, "data", "items")
        or _dig(response_data, "data", "events")
        or _dig(response_data, "data", "event_list")
        or []
    )
    return {
        "calendar_id": calendar_id,
        "items": items if isinstance(items, list) else [],
    }


def delete_calendar_event(event_id, calendar_id=None, need_notification=True):
    target_calendar_id = calendar_id or resolve_calendar_id()
    feishu_api_request(
        "DELETE",
        f"/open-apis/calendar/v4/calendars/{target_calendar_id}/events/{event_id}",
        params={"need_notification": "true" if need_notification else "false"},
    )
    return {"calendar_id": target_calendar_id, "event_id": event_id}
