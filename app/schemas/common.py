from datetime import UTC, datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """JSON uses camelCase (matching the mobile app); Python uses snake_case."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


def iso_with_offset(dt: datetime, offset_min: int) -> str:
    """Render a UTC timestamp in its original local offset, e.g. 2026-10-25T10:03:00+05:30."""
    tz = timezone(timedelta(minutes=offset_min))
    return dt.astimezone(tz).isoformat(timespec="seconds")


def offset_minutes(dt: datetime) -> int:
    off = dt.utcoffset()
    if off is None:
        raise ValueError("Timestamp must include a UTC offset")
    return int(off.total_seconds() // 60)


def to_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)
