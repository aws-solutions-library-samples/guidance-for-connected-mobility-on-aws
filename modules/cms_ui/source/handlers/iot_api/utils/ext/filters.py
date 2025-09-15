from enum import Enum
from typing import Optional, TypeVar, Type, Any
from inspect import signature
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import func
from sqlalchemy.sql import Select
from .base import (
    find_field_from_models,
)

__all__ = [
    "Operator",
    "FilterSpec",
    "apply_filters",
    "filter_spec_validate",
]


class Operator(str, Enum):
    IS_NULL = ("is_null", lambda f: f.is_(None))
    IS_NOT_NULL = ("is_not_null", lambda f: f.isnot(None))
    EQUAL = ("eq", lambda f, a: f == a)
    NOT_EQUAL = ("ne", lambda f, a: f != a)
    GREATER_THAN = ("gt", lambda f, a: f > a)
    GREATER_THAN_EQUAL = ("ge", lambda f, a: f >= a)
    LESS_THAN = ("lt", lambda f, a: f < a)
    LESS_THAN_EQUAL = ("le", lambda f, a: f <= a)
    LIKE = ("like", lambda f, a: f.like(a))
    ILIKE = ("ilike", lambda f, a: f.ilike(a))
    IN = ("in", lambda f, a: f.in_(a))
    NOT_IN = ("not_in", lambda f, a: ~f.in_(a))
    ANY = ("any", lambda f, a: f.any(a))
    NOT_ANY = ("not_any", lambda f, a: func.not_(f.any(a)))

    def __new__(cls, operator, function):
        member = str.__new__(cls)
        member._value_ = operator
        member._function = function  # type: ignore
        return member

    @property
    def function(self):
        return self._function  # type: ignore


class FilterSpec(BaseModel):
    model: str = ""
    attribute: str
    operator: Operator
    value: Optional[Any] = None


T = TypeVar("T", bound=FilterSpec)


def filter_spec_validate(
    filter_spec: FilterSpec,
    model: Type[BaseModel],
) -> FilterSpec:
    if filter_spec.attribute not in model.model_fields.keys():
        return filter_spec

    _type = model.model_fields[filter_spec.attribute].annotation

    if filter_spec.operator.value in (
        Operator.EQUAL.value,
        Operator.NOT_EQUAL.value,
        Operator.GREATER_THAN.value,
        Operator.GREATER_THAN_EQUAL.value,
        Operator.LESS_THAN.value,
        Operator.LESS_THAN_EQUAL.value,
        Operator.LIKE.value,
        Operator.ILIKE.value,
    ):
        filter_spec.value = TypeAdapter(_type).validate_python(filter_spec.value)
    elif filter_spec.operator.value in (
        Operator.IN.value,
        Operator.NOT_IN.value,
    ):
        filter_spec.value = TypeAdapter(list[_type]).validate_python(filter_spec.value)

    return filter_spec


def apply_filters(
    statement: Select,
    filter_specs: Optional[list[T]] = None,
) -> Select:
    """Apply filters to a SQLAlchemy query.

    :param query:
        A :class:`sqlalchemy.sql.expression.Select` instance.

    :param filter_spec:
        A dict or an iterable of dicts, where each one includes
        the necessary information to create a filter to be applied to the
        query.

        Example::

            filter_spec = [
                {'model': 'Foo', 'attribute': 'name', 'op': 'eq', 'value': 'foo'},
            ]

        If the query being modified refers to a single model, the `model` key
        may be omitted from the filter spec.

    :returns:
        The :class:`sqlalchemy.sql.expression.Select` instance after all the filters
        have been applied.
    """

    if not filter_specs:
        return statement

    filter_models = {x.name: x for x in statement.get_final_froms()}  # type: ignore

    if not filter_models:
        return statement

    for filter_spec in filter_specs:
        field = find_field_from_models(
            models=filter_models,
            field_name=filter_spec.attribute,
            model_name=filter_spec.model,
        )
        if field is None:
            continue

        _func = Operator(filter_spec.operator).function
        arity = len(signature(_func).parameters)

        if arity == 1:
            expression = _func(field)
        elif arity == 2:
            expression = _func(field, filter_spec.value)

        statement = statement.where(expression)
    return statement
