"""Declarative base shared by every model.

The naming convention is what makes `alembic revision --autogenerate` work
reliably across environments: without it, every index or constraint gets a
Postgres-generated name that can differ between databases, and Alembic stops
recognizing "this already existed" when comparing schemas.

`type_annotation_map` makes every `Mapped[datetime]` use TIMESTAMPTZ, not the
timezone-naive `TIMESTAMP` SQLAlchemy defaults to — this is the convention
`plan/theclub-api-PLAN.md` already declared ("all timestamps in TIMESTAMPTZ
UTC"), set here once instead of repeating it field by field in every model.
"""

from datetime import datetime

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # SQLAlchemy reads this once when configuring the declarative mapping,
    # it's not a mutable attribute shared across instances — pattern
    # documented as such by SQLAlchemy itself.
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012
