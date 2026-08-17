"""Base declarativa compartida por todos los modelos.

La convención de nombres es lo que hace que `alembic revision --autogenerate`
funcione de forma estable entre entornos: sin ella, cada índice o constraint
recibe un nombre generado por Postgres que puede diferir entre bases de
datos, y Alembic deja de reconocer "esto ya existía" al comparar esquemas.
"""

from sqlalchemy import MetaData
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
