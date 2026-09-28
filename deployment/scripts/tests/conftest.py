# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pytest configuration for deployment/scripts/tests/.

Registers custom marks used across this test directory.  Scoped here
(not in a root pytest.ini or pyproject.toml) so other suites are unaffected.
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require a live AWS resource (DynamoDB, S3, …). "
        "Skip with -m 'not integration'.",
    )
