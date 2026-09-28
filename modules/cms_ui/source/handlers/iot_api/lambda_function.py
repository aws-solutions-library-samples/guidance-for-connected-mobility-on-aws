# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext
from app import app, tracer, middleware_api_session_manager
from utils import logger


app.enable_swagger(
    path="/_swagger",
    title="AIoT Management Console API",
)


@app.get(
    "/openapi.json",
    summary="Get OpenAPI documentation",
    description="Returns the OpenAPI documentation for the AIoT Management Console API.",
    response_description="OpenAPI JSON schema",
)
@tracer.capture_method
def get_openapi_documentation() -> str:
    return app.get_openapi_json_schema(
        title="AIoT Management Console API",
        version="v0.1.0",
        summary="Rest APIs for AIoT Management Console",
        description="This API implements all the CRUD operations related to AIoT Management Console.",
    )


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@middleware_api_session_manager
@tracer.capture_lambda_handler
def lambda_handler(event: dict, context: LambdaContext):
    logger.info(json.dumps(event, indent=2))
    return app.resolve(event, context)


if __name__ == "__main__":
    print(
        app.get_openapi_json_schema(
            title="AIoT Management Console API",
            version="v0.1.0",
            summary="Rest APIs for AIoT Management Console",
            description="This API implements all the CRUD operations related to AIoT Management Console.",
        ),
    )
