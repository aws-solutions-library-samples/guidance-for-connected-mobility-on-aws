import sys
import os

_OEM1_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _OEM1_DIR not in sys.path:
    sys.path.insert(0, _OEM1_DIR)

_HANDLER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _HANDLER_DIR not in sys.path:
    sys.path.insert(0, _HANDLER_DIR)
