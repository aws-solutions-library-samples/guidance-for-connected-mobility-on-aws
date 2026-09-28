# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Make `_lib.*` importable when pytest is invoked from the repo root.

Mirrors `services/connectors/oem1/admin_enroll_quota/tests/conftest.py`, which
inserts the module root on `sys.path` for the same reason: the Lambda handlers
are packaged with `_lib/` as a sibling, so tests must resolve imports the same
way the deployed bundle does.
"""
import os
import sys

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)
