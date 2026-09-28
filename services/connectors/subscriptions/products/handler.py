# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""GET /products — the seeded data-product catalog.

Spec `2026-09-10-cms-connected-services-subscriptions`, T3.5 (Group 3).

The Lambda-bundled `products.json` file is the source of truth (spec D3
seeds the telemetry product only in Group 1; T4.1 / T4.2 add diagnostics
and charging). This route reads that file and returns the catalog so the
frontend can flip its `DataProductsView` off the fixture path.

## Cognito group

`subscriber` group. Not `connected-services`: operators do not need this
route (they see the catalog in another surface, if at all), and gating
per the same group as the CRUD routes keeps the "subscriber-facing"
scope-check pattern consistent.

## No caching in the handler

The Lambda's execution environment caches the file in
`_load_catalog`'s `functools.lru_cache` (same shape as
`subscription_crud`'s implementation) — the catalog cannot change without a
redeploy anyway, so an in-Lambda cache is correct and an external cache
(ElastiCache, DAX) is not warranted at this scale.

## Env vars

    DEPLOYMENT_STAGE, AWS_DEFAULT_REGION

## IAM

    logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents

(No DynamoDB, no S3 — the catalog is bundled with the Lambda.)
"""
from __future__ import annotations

import functools
import json
import logging
import os

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_SUBSCRIBER_GROUP = "subscriber"


def _api_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            # AWS_PROXY returns this verbatim — API Gateway adds no CORS for proxy
            # integrations, so the browser blocks every read while curl sees 200.
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def _claims(event: dict) -> dict:
    return (
        (event.get("requestContext") or {})
        .get("authorizer", {})
        .get("claims", {})
    ) or {}


def _parse_groups(claims: dict) -> list[str]:
    groups_raw = claims.get("cognito:groups", "")
    if isinstance(groups_raw, list):
        return [str(g).strip() for g in groups_raw if str(g).strip()]
    groups_str = str(groups_raw).strip()
    if groups_str.startswith("[") and groups_str.endswith("]"):
        groups_str = groups_str[1:-1]
    return [g.strip() for g in groups_str.split(",") if g.strip()] if groups_str else []


class _Unauthorized(Exception):
    """Caller is not a subscriber."""


@functools.lru_cache(maxsize=1)
def _load_catalog() -> dict:
    """Load the bundled product catalog.

    The catalog is a Lambda-bundled JSON, so the cache never becomes stale
    inside one execution environment. Same shape as
    `subscription_crud.handler._load_catalog`, which reads the same file.

    Fixed 2026-09-12: supports both source-tree layout (products.json is one
    dir UP from handler.py — services/connectors/subscriptions/products.json)
    AND Lambda flat-bundle layout (products.json co-located with handler.py
    in /var/task/). The prior `dirname(dirname(__file__))` resolves to
    `/var/products.json` on Lambda, which doesn't exist. This handler had
    diverged from `subscription_crud.handler._load_catalog`'s 2026-09-11 fix
    for the identical defect — see
    issues/2026-09-12-products-catalog-flat-bundle-path-500/ for why the
    stack-side compensating copy didn't cover this handler either.
    """
    _here = os.path.dirname(os.path.abspath(__file__))
    for path in (
        os.path.join(os.path.dirname(_here), "products.json"),
        os.path.join(_here, "products.json"),
    ):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    raise FileNotFoundError(
        "products.json not found in source or Lambda-bundle locations"
    )


def products_handler(event: dict, context) -> dict:  # noqa: ANN001
    """Return the seeded product catalog."""
    try:
        claims = _claims(event)
        if _SUBSCRIBER_GROUP not in _parse_groups(claims):
            raise _Unauthorized(f"'{_SUBSCRIBER_GROUP}' group required")

        catalog = _load_catalog()
        return _api_response(200, {
            "catalog_version": catalog.get("catalog_version", "unknown"),
            "products": catalog.get("products", []),
        })

    except _Unauthorized as exc:
        logger.warning("products denied: %s", exc)
        return _api_response(403, {"error": "Forbidden"})
    except Exception:  # noqa: BLE001
        logger.exception("Internal error in products_handler")
        return _api_response(500, {"error": "Internal server error"})
