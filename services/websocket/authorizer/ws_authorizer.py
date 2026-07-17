"""
WebSocket Lambda REQUEST authorizer.

Reads ?token=<jwt> from the WebSocket upgrade query string, verifies it
against the Cognito User Pool JWKS endpoint, and returns an IAM Allow policy
with principalId + context fields for the handler to consume.

Raises ``Exception("Unauthorized")`` on any failure — API Gateway maps this
to HTTP 401. The exception MESSAGE is intentionally the bare string
"Unauthorized" (do not enrich it) because API Gateway matches the message
verbatim to select the 401 mapping template.

Diagnostic detail — the SPECIFIC rejection reason — is logged via
``logging.getLogger`` immediately BEFORE the raise. Every rejection path has
its own log line with a stable prefix (``[WS-AUTH][DENY]``) so CloudWatch
Insights / metric filters can distinguish JWKS-fetch failure from audience
mismatch from expiry from missing-claim, without changing the client-visible
401 shape.

Structured logging was added 2026-07-16 (issue
``2026-07-16-prod-ws-connect-unauthorized-live-user``) after a live prod
incident in which the bare-raise made it impossible to tell WHY a valid-looking
admin id-token was being rejected. The rejection turned out NOT to be in this
authorizer at all — it was in the downstream ``$connect`` handler — but the
absence of instrumentation here made investigation take 10x longer than it
should have.
"""
import json
import logging
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

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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


def _deny(reason: str, **extra) -> Exception:
    """
    Log the specific rejection reason with structured detail, then return a
    bare ``Exception("Unauthorized")`` for the caller to raise.

    Never returns a diagnostic-enriched exception — API Gateway keys off the
    string message verbatim to map to HTTP 401. Diagnostic detail goes ONLY
    to CloudWatch, never to the client.
    """
    # Emit a single INFO line — deny paths are not errors; a bad-token deny is
    # normal traffic. Log-level ERROR would create noise-on-normal.
    logger.info("[WS-AUTH][DENY] reason=%s extra=%s", reason, extra or "")
    return Exception("Unauthorized")


def handler(event: dict, context) -> dict:
    # Extract token from query string
    token = (event.get("queryStringParameters") or {}).get("token")
    if not token:
        # No token in ?token=; API Gateway routed us here because the
        # identity_source (route.request.querystring.token) matched empty.
        raise _deny("missing_token")

    method_arn = event.get("methodArn")

    # Verify JWT signature + standard claims
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
    except Exception as e:
        # PyJWKClient errors: cannot fetch JWKS, cannot find the kid in the
        # JWKS response, unrecognized kid, etc. Almost always a
        # (a) network/DNS failure (Lambda outbound denied), (b) token signed
        # by a different pool, (c) rotated-key gap.
        raise _deny(
            "jwks_signing_key_failure",
            exc_type=type(e).__name__,
            exc_msg=str(e)[:200],
        )

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=USER_POOL_CLIENT_ID,
            options={"require": ["exp", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise _deny("token_expired", exc_msg=str(e)[:200])
    except jwt.InvalidAudienceError as e:
        # Distinct from generic InvalidTokenError so CloudWatch can measure
        # "we deployed a bad USER_POOL_CLIENT_ID env var" separately from
        # "user's token is bogus".
        raise _deny(
            "audience_mismatch",
            expected_aud=USER_POOL_CLIENT_ID,
            exc_msg=str(e)[:200],
        )
    except jwt.InvalidSignatureError as e:
        raise _deny("signature_invalid", exc_msg=str(e)[:200])
    except jwt.MissingRequiredClaimError as e:
        raise _deny("missing_required_claim", exc_msg=str(e)[:200])
    except jwt.InvalidTokenError as e:
        # Catch-all for other PyJWT errors (issuer, format, etc.).
        raise _deny(
            "jwt_invalid",
            exc_type=type(e).__name__,
            exc_msg=str(e)[:200],
        )
    except Exception as e:
        # Non-jwt exception — unusual. Log with full type so we can extend
        # the taxonomy if it recurs.
        raise _deny(
            "decode_unexpected",
            exc_type=type(e).__name__,
            exc_msg=str(e)[:200],
        )

    # Verify issuer matches our pool. PyJWT does not check this by default —
    # audience-only. We enforce the exact issuer we expect.
    if claims.get("iss") != EXPECTED_ISS:
        raise _deny(
            "issuer_mismatch",
            expected_iss=EXPECTED_ISS,
            actual_iss=str(claims.get("iss"))[:200],
        )

    sub = claims["sub"]
    groups = claims.get("cognito:groups", [])
    fleet_ids = claims.get("custom:fleetIds", "")

    # Build resource ARN for the $connect route.
    # event["methodArn"] for a WS REQUEST authorizer is always the route ARN;
    # its absence is anomalous — fail closed rather than returning Allow on "*".
    if not method_arn:
        raise _deny("missing_method_arn")

    logger.info(
        "[WS-AUTH][ALLOW] sub=%s groups_count=%d has_fleet_ids=%s",
        sub,
        len(groups) if isinstance(groups, list) else 1,
        bool(fleet_ids),
    )

    auth_context = {
        "sub": sub,
        "cognito:groups": ",".join(groups) if isinstance(groups, list) else str(groups),
        "custom:fleetIds": str(fleet_ids),
    }

    return _build_policy(sub, "Allow", method_arn, auth_context)
