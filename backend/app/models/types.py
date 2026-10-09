"""Portable column types: native Postgres types in production, JSON/TEXT fallbacks on SQLite."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import TypeDecorator

# JSONB on Postgres, JSON elsewhere.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class StringList(TypeDecorator):
    """A list of strings: TEXT[] on Postgres, JSON array on SQLite."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(ARRAY(Text()))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return [str(v) for v in value]

    def process_result_value(self, value, dialect):
        if value is None:
            return []
        return list(value)


class UTCDateTime(TypeDecorator):
    """Timezone-aware datetime that always round-trips as UTC (SQLite stores naive)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)
