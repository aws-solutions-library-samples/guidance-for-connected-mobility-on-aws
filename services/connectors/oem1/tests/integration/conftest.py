"""
Local conftest for tests/integration/.

Tests in this directory that use moto + requests_mock are self-contained
(no live AWS, no mock gRPC server required). The parent conftest.py auto-skips
anything with 'integration' in item.keywords (which includes these tests because
the directory name is a Python package). Override that skip here for tests that
are NOT explicitly decorated with @pytest.mark.integration.
"""
import pytest


def pytest_collection_modifyitems(config, items):
    """
    Remove the auto-skip marker added by the parent conftest for tests in this
    directory that are self-contained (moto + requests_mock, no external server).

    Tests decorated with @pytest.mark.integration keep the skip so they're still
    gated behind -m integration (they require a live/external server).
    """
    for item in items:
        # Only touch items collected from this directory
        if "tests/integration" not in str(item.fspath):
            continue
        # If the test has an explicit @pytest.mark.integration decoration, keep skip.
        if item.get_closest_marker("integration"):
            continue
        # Remove any skip markers added by the parent conftest for directory-keyword match.
        item.own_markers = [
            m for m in item.own_markers
            if not (m.name == "skip" and "Integration test" in str(m.kwargs.get("reason", "")))
        ]
