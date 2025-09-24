from typing import Optional, Any
from sqlalchemy.sql import FromClause, Select
from sqlalchemy.sql.elements import ColumnElement


def find_field_from_models(
    models: dict[str, FromClause],
    field_name: str,
    model_name: Optional[str] = None,
) -> Optional[ColumnElement[Any]]:
    if model_name:
        if model_name in models.keys():
            models = {model_name: models[model_name]}
        else:
            return None
    for model in models.values():
        if field_name in model.columns.keys():
            return model.columns[field_name]
    return None


def find_field_from_select(
    statement: Select,
    field_name: str,
    model_name: Optional[str] = None,
) -> Optional[ColumnElement[Any]]:
    for column in statement.selected_columns:
        if model_name and (
            getattr(column, "table", None) is None or column.table.name != model_name
        ):
            continue
        if column.name == field_name:
            return column
    return None
