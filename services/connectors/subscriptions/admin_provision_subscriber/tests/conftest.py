# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Path shim + env-var defaults for `admin_provision_subscriber` tests.

Mirrors `admin_mark_available/tests/conftest.py` (same discipline: the
per-handler directory is deliberately NOT on `sys.path`; the test files
import handlers package-qualified because every Lambda dir has a `handler.py`
and a bare `import handler` would collide across the suite).
"""
import os
import sys

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("USER_POOL_ID", "eu-west-1_TESTFAKE0")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)
