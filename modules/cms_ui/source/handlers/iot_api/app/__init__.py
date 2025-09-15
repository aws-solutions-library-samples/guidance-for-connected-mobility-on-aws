from http import HTTPStatus
from aws_lambda_powertools.event_handler import Response, content_types

from .resources import app, tracer
from .router.alarms import *
from .router.connections import *
from .router.retained_messages import *
from .router.rules import *
from .router.statistics import *
from .router.topics import *
from .router.tracing import *
from .router.subscriptions import *
from .router.policies import *
from .router.users import *
from .middleware import *
from .schema import AwsInfrastructure


__all__ = ["app", "tracer", "middleware_session_manager"]


@app.get(
    "/infrastructure",
    summary="Get AWS Infrastructure Information",
    description="Get AWS Infrastructure Information",
    response_description="Information of AWS Infrastructure",
)
@tracer.capture_method
def get_aws_infrastructure() -> Response[AwsInfrastructure]:
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=AwsInfrastructure(
            aws_region=os.environ["AWS_REGION"],
            aws_partition=os.environ["AWS_PARTITION"],
            aws_account_id=os.environ["AWS_ACCOUNT_ID"],
        ),
    )
