from enum import Enum
from typing import Optional, Union
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.sql import Select
from .base import find_field_from_select

__all__ = [
    "OrderDirection",
    "SortSpec",
    "apply_sort",
]


class OrderDirection(str, Enum):
    ASC = ("asc", lambda f: f.asc())
    DESC = ("desc", lambda f: f.desc())

    def __new__(cls, direction, function):
        member = str.__new__(cls)
        member._value_ = direction
        member._function = function  # type: ignore
        return member

    @property
    def function(self):
        return self._function  # type: ignore


class SortSpec(BaseModel):
    model: str = ""
    attribute: str
    direction: OrderDirection
    nulls_first: Optional[bool] = None
    nulls_last: Optional[bool] = None


def apply_sort(
    statement: Select,
    sort_specs: Optional[list[SortSpec]] = None,
):
    """Apply sorting to a :class:`sqlalchemy.sql.expression.Select` instance.

    :param sort_spec:
        A list of dictionaries, where each one of them includes
        the necessary information to order the elements of the query.

        Example::

            sort_specs = [
                {'model': 'Foo', 'attribute': 'name', 'direction': 'asc'},
                {'model': 'Bar', 'attribute': 'id', 'direction': 'desc'},
                {
                    'model': 'Qux',
                    'attribute': 'surname',
                    'direction': 'desc',
                    'nulls_last': True,
                },
                {
                    'model': 'Baz',
                    'attribute': 'count',
                    'direction': 'asc',
                    'nulls_first': True,
                },
            ]

        If the query being modified refers to a single model, the `model` key
        may be omitted from the sort spec.

    :returns:
        The :class:`sqlalchemy.sql.expression.Select` instance after the provided
        sorting has been applied.
    """
    if not sort_specs:
        return statement

    for sort_spec in sort_specs:
        field = find_field_from_select(
            statement=statement,
            field_name=sort_spec.attribute,
        )
        if field is None:
            continue

        _func = OrderDirection(sort_spec.direction).function

        if sort_spec.nulls_first is True:
            statement = statement.order_by(_func(field).nullsfirst())
        elif sort_spec.nulls_last is True:
            statement = statement.order_by(_func(field).nullslast())
        else:
            statement = statement.order_by(_func(field))
    return statement
