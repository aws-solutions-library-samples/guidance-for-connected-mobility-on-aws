# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Path shim so `handler` and `_lib.*` resolve as they do in the Lambda bundle.

Mirrors `services/connectors/oem1/admin_enroll_quota/tests/conftest.py`.
"""
import os
import sys

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

# Handler modules are imported package-qualified by the test files (every
# Lambda dir has a `handler.py`, so a bare import would collide), so the
# per-handler directory is deliberately not on sys.path here.
