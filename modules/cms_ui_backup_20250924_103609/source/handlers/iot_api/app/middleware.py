from http import HTTPStatus
from typing import Callable, Any
from aws_lambda_powertools.middleware_factory import lambda_handler_decorator
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.event_handler import Response, content_types
from sqlalchemy.exc import SQLAlchemyError
from utils import logger
from utils.resources import session
from .schema import GeneralResponse


@lambda_handler_decorator
def middleware_api_session_manager(
    handler: Callable[[dict, LambdaContext], Response],
    event: dict,
    context: LambdaContext,
) -> Response[Any]:
    try:
        response = handler(event, context)
    except SQLAlchemyError as e:
        logger.error(e)
        session.rollback()
        return Response(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content_type=content_types.APPLICATION_JSON,
            body=GeneralResponse(status="error", message="Database operate failure."),
        )
    except Exception as e:
        logger.error(e)
        return Response(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content_type=content_types.APPLICATION_JSON,
            body=GeneralResponse(status="error", message=str(e)),
        )
    return response
