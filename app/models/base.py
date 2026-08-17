"""Base declarativa compartida por todos los modelos.

La convención de nombres es lo que hace que `alembic revision --autogenerate`
funcione de forma estable entre entornos: sin ella, cada índice o constraint
recibe un nombre generado por Postgres que puede diferir entre bases de
datos, y Alembic deja de reconocer "esto ya existía" al comparar esquemas.

`type_annotation_map` hace que todo `Mapped[datetime]` use TIMESTAMPTZ, no el
`TIMESTAMP` sin zona horaria que SQLAlchemy usa por defecto — es la convención
que ya declaraba `plan/theclub-api-PLAN.md` ("todos los timestamps en
TIMESTAMPTZ UTC"), fijada aquí una única vez en vez de repetirla campo a
campo en cada modelo.
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
    # SQLAlchemy lo lee una sola vez al configurar el mapeo declarativo, no es
    # un atributo mutable compartido entre instancias — patrón documentado así
    # por SQLAlchemy mismo.
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012
