"""
conftest.py — extend sys.path so admin_bulk_enroll/handler.py can import
token_supplier from the parent services/connectors/oem1 directory.
"""
import sys
import os

# Point at services/connectors/oem1 so `from token_supplier import TokenSupplier` resolves.
_OEM1_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _OEM1_DIR not in sys.path:
    sys.path.insert(0, _OEM1_DIR)

# Also add the admin_bulk_enroll dir itself so handler is importable directly.
_HANDLER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _HANDLER_DIR not in sys.path:
    sys.path.insert(0, _HANDLER_DIR)
