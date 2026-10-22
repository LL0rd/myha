"""Column types shared by the models and the migrations."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """Aware datetime stored as UTC ISO-8601 text such as ``2026-10-22T09:30:00.000000Z``.

    Fixed microsecond precision keeps string order equal to time order, so SQL comparisons
    on these columns stay correct. Naive datetimes are rejected, written or read.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError(f"naive datetime {value!r}: a timezone is required")
        return value.astimezone(UTC).isoformat(timespec="microseconds").removesuffix("+00:00") + "Z"

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError(f"naive datetime {value!r} in the database")
        return result.astimezone(UTC)
