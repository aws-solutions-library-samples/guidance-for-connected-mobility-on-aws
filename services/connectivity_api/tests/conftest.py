"""
conftest.py — extend sys.path so test modules can import from the repo root.

Mirrors the pattern used in services/fleet_intelligence/tests/conftest.py.
The repo root is added so 'from services.connectivity_api.<module> import ...'
resolves when running pytest from the repository root.
"""
import sys
import os

# Add the repo root (four levels up from this conftest) to sys.path so that
# `from services.connectivity_api.subscriber import ...` resolves.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
