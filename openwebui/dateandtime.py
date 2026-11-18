"""
title: Datetime
description: Return the current date and time in ISO 8601 format and convert timestamps between timezones.
version: 0.1.0
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_OFFSET_RE = re.compile(
    r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$",
    re.IGNORECASE,
)


class Tools:
    def __init__(self):
        pass

    def _parse_timezone(self, tz_name: str) -> tzinfo:
        """
        Resolve an IANA timezone name or a fixed UTC/GMT offset string into a tzinfo object.

        :param tz_name: IANA timezone name (e.g. 'America/New_York') or offset (e.g. 'UTC-5', '+05:30', 'Z').
        :return: Corresponding tzinfo object.
        """
        cleaned = tz_name.strip()
        if not cleaned:
            raise ValueError("Timezone must not be empty")

        if cleaned.upper() in {"Z", "UTC", "GMT"}:
            return timezone.utc

        match = _OFFSET_RE.match(cleaned)
        if match:
            sign_str, hours_str, minutes_str = match.groups()
            hours = int(hours_str)
            minutes = int(minutes_str) if minutes_str is not None else 0
            if hours > 23 or minutes > 59:
                raise ValueError(f"Invalid timezone offset: {tz_name}")
            delta = timedelta(hours=hours, minutes=minutes)
            if sign_str == "-":
                delta = -delta
            return timezone(delta)

        try:
            return ZoneInfo(cleaned)
        except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
            raise ValueError(f"Unknown or invalid timezone: {tz_name}") from exc

    def get_current_time(self) -> str:
        """
        Return the current UTC date and time in ISO 8601 format.
        Use this to get the current date as well as the current time.

        :return: The current UTC date and time as an ISO 8601 string.
        """
        return datetime.now(timezone.utc).isoformat()

    def convert_timezone(
        self, datetime_str: str, source_tz: str, target_tz: str
    ) -> str:
        """
        Convert a timestamp from a source timezone to a target timezone.

        :param datetime_str: Timestamp in ISO 8601 format (e.g. '2026-03-08T01:30:00').
        :param source_tz: Source timezone as an IANA name (e.g. 'America/New_York') or fixed offset (e.g. 'UTC-5', '+02:00').
        :param target_tz: Target timezone as an IANA name (e.g. 'Europe/London') or fixed offset (e.g. 'UTC', '+09:00').
        :return: The timestamp re-rendered in the target timezone in ISO 8601 format.
        """
        src_tz = self._parse_timezone(source_tz)
        dst_tz = self._parse_timezone(target_tz)

        dt = datetime.fromisoformat(datetime_str.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=src_tz)

        return dt.astimezone(dst_tz).isoformat()

