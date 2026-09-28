# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pytest config for the ``main_api`` handler tests.

Spec ``.kiro/specs/2026-09-10-cms-connected-services-consumer/`` T2.1.

Registers the ``integration`` marker locally rather than in a repo-root
``pytest.ini``. The repo has no pytest config file at all, and introducing one
would change discovery and configuration for every other spec's suite in a
repository with several concurrent sessions — a wide blast radius to silence one
warning. This directory currently holds only this spec's tests, so the scope is
exactly right.
"""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: hits real deployed AWS resources; requires credentials "
        "and CS_LIVE_INTEGRATION=1. Deselect with -m 'not integration'.",
    )
