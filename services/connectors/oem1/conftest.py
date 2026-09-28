"""
Pytest configuration for OEM1 connector tests.

Integration tests (marked @pytest.mark.integration) are skipped by default
in unit-only runs. Use -m integration to run them:
  python3 -m pytest tests/ -v -m integration
"""
import sys
from pathlib import Path

import pytest


def pytest_pycollect_makemodule(module_path, parent):
    """Before importing each Lambda test module, put its handler dir first on sys.path
    and evict any previously-cached 'handler' from sys.modules.

    With --import-mode=importlib, collection and import interleave (one makemodule
    fires then the module imports immediately), so the path is correct at import time.
    """
    handler_candidate = module_path.parent.parent / "handler.py"
    if handler_candidate.exists():
        lambda_dir = str(module_path.parent.parent)
        if lambda_dir in sys.path:
            sys.path.remove(lambda_dir)
        sys.path.insert(0, lambda_dir)
        sys.modules.pop("handler", None)
    return None  # use default collection


@pytest.fixture(autouse=True)
def _pin_handler_in_sys_modules(request):
    """Re-pin sys.modules['handler'] to the module that THIS test's file imported.

    When multiple Lambda test suites run in one session, the last-imported 'handler'
    wins in sys.modules.  patch('handler.X') looks up sys.modules['handler'] at
    enter-time, so it must point to the right Lambda's handler when each test runs.
    """
    test_module_handler = getattr(request.module, "handler", None)
    if test_module_handler is not None:
        sys.modules["handler"] = test_module_handler
    yield


def pytest_collection_modifyitems(config, items):
    """Auto-skip integration tests unless -m integration is explicitly passed."""
    # If the user passed -m integration (or -m "integration"), let them run.
    # Otherwise, skip all integration-marked tests so unit-only runs stay fast.
    if config.option.markexpr and "integration" in config.option.markexpr:
        return  # User explicitly requested integration tests

    skip_integration = pytest.mark.skip(
        reason="Integration test: pass -m integration to run. Requires mock gRPC server."
    )
    for item in items:
        if "integration" in item.keywords:
            # tests/integration/ are moto-based and run unconditionally — do not skip
            if "tests/integration" in str(item.fspath):
                continue
            item.add_marker(skip_integration)
