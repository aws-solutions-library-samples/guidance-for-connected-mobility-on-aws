"""
WebSocket Lambda REQUEST authorizer.

Reads ?token=<jwt> from the WebSocket upgrade query string, verifies it
against the Cognito User Pool JWKS endpoint, and returns an IAM Allow policy
with principalId + context fields for the handler to consume.

Raises Exception("Unauthorized") on any failure — API Gateway maps this to HTTP 401.
"""
import json
import os

import jwt
from jwt import PyJWKClient

USER_POOL_ID = os.environ["USER_POOL_ID"]
USER_POOL_CLIENT_ID = os.environ["USER_POOL_CLIENT_ID"]
# AWS_REGION is injected automatically by the Lambda runtime; use it directly.
REGION = os.environ["AWS_REGION"]

JWKS_URL = (
    f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"
    f"/.well-known/jwks.json"
)
EXPECTED_ISS = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"

# Module-scope client — caches signing keys by kid across warm Lambda invocations.
_jwk_client = PyJWKClient(JWKS_URL)


def _build_policy(principal_id: str, effect: str, method_arn: str, context: dict) -> dict:
    return {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": method_arn,
                }
            ],
        },
        "context": context,
    }


def handler(event: dict, context) -> dict:
    # Extract token from query string
    token = (event.get("queryStringParameters") or {}).get("token")
    if not token:
        raise Exception("Unauthorized")

    # Verify JWT signature + standard claims
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=USER_POOL_CLIENT_ID,
            options={"require": ["exp", "iss", "sub"]},
        )
    except Exception:
        raise Exception("Unauthorized")

    # Verify issuer matches our pool
    if claims.get("iss") != EXPECTED_ISS:
        raise Exception("Unauthorized")

    sub = claims["sub"]
    groups = claims.get("cognito:groups", [])
    fleet_ids = claims.get("custom:fleetIds", "")

    # Build resource ARN for the $connect route.
    # event["methodArn"] for a WS REQUEST authorizer is always the route ARN;
    # its absence is anomalous — fail closed rather than returning Allow on "*".
    method_arn = event.get("methodArn")
    if not method_arn:
        raise Exception("Unauthorized")

    auth_context = {
        "sub": sub,
        "cognito:groups": ",".join(groups) if isinstance(groups, list) else str(groups),
        "custom:fleetIds": str(fleet_ids),
    }

    return _build_policy(sub, "Allow", method_arn, auth_context)
