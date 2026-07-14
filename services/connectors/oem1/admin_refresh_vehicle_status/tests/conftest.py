import sys
import os

_OEM1_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _OEM1_DIR not in sys.path:
    sys.path.insert(0, _OEM1_DIR)

_HANDLER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _HANDLER_DIR not in sys.path:
    sys.path.insert(0, _HANDLER_DIR)

# Repo root needed for services.connectors.oem1._lib namespace package resolution
_REPO_ROOT = os.path.abspath(os.path.join(_OEM1_DIR, "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
