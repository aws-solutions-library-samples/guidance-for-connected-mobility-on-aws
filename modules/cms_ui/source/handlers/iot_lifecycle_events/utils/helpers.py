from typing import Callable
from aws_lambda_powertools.middleware_factory import lambda_handler_decorator
from aws_lambda_powertools.utilities.typing import LambdaContext
from sqlalchemy.exc import SQLAlchemyError
from .resources import aws_iot, session


def check_thing_exists(thing_name: str) -> bool:
    try:
        aws_iot.describe_thing(thingName=thing_name)
        return True
    except Exception:
        return False


@lambda_handler_decorator
def middleware_session_manager(
    handler: Callable[[dict, LambdaContext], dict],
    event: dict,
    context: LambdaContext,
):
    try:
        return handler(event, context)
    except SQLAlchemyError as e:
        session.rollback()
        raise e
    except Exception as e:
        raise e
