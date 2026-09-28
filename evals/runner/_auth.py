"""Cognito JWT acquisition for CMS eval runner.

Reads credentials from environment variables — never hardcoded.
"""

from __future__ import annotations

import os

import boto3


def _require_env(name: str, hint: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(
            f"Required env var {name!r} is not set. {hint}"
        )
    return value


def get_jwt(stage_endpoint: str, username: str, password: str) -> str:
    """Acquire a Cognito IdToken via USER_PASSWORD_AUTH flow.

    Uses the non-admin ``initiate_auth`` endpoint so the eval runner does
    NOT need AWS credentials — only the public app client ID. The CMS UI
    user pool client allows ``ALLOW_USER_PASSWORD_AUTH``.

    Args:
        stage_endpoint: The staging API endpoint URL (unused in auth call,
            kept for interface symmetry with CVX pattern).
        username: Cognito username (the email, since the user pool is
            configured with ``UsernameAttributes=[email]``).
        password: Cognito password.

    Returns:
        The IdToken string.

    Raises:
        EnvironmentError: If required env vars are missing.
        botocore.exceptions.ClientError: On Cognito auth failure.
    """
    client_id = _require_env(
        "COGNITO_CLIENT_ID",
        "Set COGNITO_CLIENT_ID to your Cognito App Client ID.",
    )
    region = os.environ.get("AWS_REGION", "us-west-2")

    cognito = boto3.client("cognito-idp", region_name=region)
    response = cognito.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    return response["AuthenticationResult"]["IdToken"]


def get_jwt_from_env(stage_endpoint: str = "") -> str:
    """Convenience wrapper that reads credentials from env vars.

    Args:
        stage_endpoint: Optional staging endpoint (passed through to get_jwt).

    Returns:
        The IdToken string.

    Raises:
        EnvironmentError: If CMS_EVAL_USERNAME or CMS_EVAL_PASSWORD are unset.
    """
    username = _require_env(
        "CMS_EVAL_USERNAME",
        "Set CMS_EVAL_USERNAME to the eval Cognito username.",
    )
    password = _require_env(
        "CMS_EVAL_PASSWORD",
        "Set CMS_EVAL_PASSWORD to the eval Cognito password (sourced from Secrets Manager in CI).",
    )
    return get_jwt(stage_endpoint, username, password)
