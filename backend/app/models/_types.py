from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import mapped_column

# Timestamps are always stored as UTC timestamptz.
TimestampTZ = DateTime(timezone=True)
NOW = text("now()")


def text_array():
    """Non-null text[] column defaulting to the empty array."""
    return mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))


def enum_check(column: str, enum: type[StrEnum], name: str) -> CheckConstraint:
    values = ", ".join(f"'{m.value}'" for m in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name)


__all__ = ["NOW", "TimestampTZ", "enum_check", "text_array"]
