# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `products/handler.py` — spec T3.5 Accept (backend side)."""
import json
import os
import sys

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

from products import handler as h  # noqa: E402


def _event(*, groups=("subscriber",), sub="subscriber-alice"):
    return {
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": sub,
                    "cognito:groups": ",".join(groups) if groups else "",
                }
            }
        }
    }


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    h._load_catalog.cache_clear()
    yield
    h._load_catalog.cache_clear()


class TestAuthorization:
    def test_subscriber_admitted(self):
        resp = h.products_handler(_event(), None)
        assert resp["statusCode"] == 200

    def test_cors_header_present_on_success_and_denial(self):
        """AWS_PROXY adds no CORS — without this the browser can read neither response.

        Asserted on the 403 as well as the 200: a denial the browser cannot read
        surfaces as an opaque CORS error, which is precisely how this defect was
        first misdiagnosed as an authorization problem.
        See issues/2026-09-14-cs-subscriptions-responses-missing-cors-headers/.
        """
        for resp in (
            h.products_handler(_event(), None),
            h.products_handler(_event(groups=()), None),
        ):
            assert resp["headers"]["Access-Control-Allow-Origin"] == "*"

    @pytest.mark.parametrize("groups", [(), ("connected-services",), ("driver-self",)])
    def test_non_subscriber_denied(self, groups):
        resp = h.products_handler(_event(groups=groups), None)
        assert resp["statusCode"] == 403


class TestCatalogShape:
    def test_response_carries_products_and_version(self):
        resp = h.products_handler(_event(), None)
        body = json.loads(resp["body"])
        assert "products" in body
        assert "catalog_version" in body
        assert isinstance(body["products"], list)
        assert len(body["products"]) >= 1

    def test_telemetry_product_present(self):
        """Group 1's seed is the telemetry product only."""
        resp = h.products_handler(_event(), None)
        body = json.loads(resp["body"])
        pids = {p["product_id"] for p in body["products"]}
        assert "telemetry-hifi-v1" in pids

    def test_each_product_carries_required_fields(self):
        resp = h.products_handler(_event(), None)
        body = json.loads(resp["body"])
        for product in body["products"]:
            for field in ("product_id", "name", "schema_version",
                          "data_category", "delivery_profile", "source"):
                assert field in product, f"missing {field} on {product.get('product_id')}"
