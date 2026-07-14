"""Synth-time dependency bundler for the WebSocket authorizer Lambda.

The authorizer needs ``PyJWT[crypto]`` + ``cryptography`` (a NATIVE extension).
A plain ``lambda_.Code.from_asset("../services/websocket/authorizer")`` ships
only the source — the deps would be ABSENT in the deployed package and the
authorizer would ``ModuleNotFoundError: jwt`` at cold start, rejecting every
``$connect``.

This bundles the deps + source into ``deployment/build/ws_authorizer`` and
returns that path for ``from_asset``. Design notes:

- **No Docker** (keeps the CMS UI-stack deploy daemonless, consistent with the
  sim-images direction) and **no Makefile edit** (that file is owned by the
  concurrent sim-images spec). Bundling runs at synth via ``pip``.
- ``cryptography`` is native → we fetch **linux** wheels for the Lambda
  architecture (x86_64) with ``--platform manylinux2014_x86_64
  --only-binary=:all:`` so a macOS/arm64 synth host still produces a
  Lambda-correct package. The Lambda is pinned to ``Architecture.X86_64`` to
  match.
- Cached by a content hash of ``requirements.txt`` + ``ws_authorizer.py`` so
  repeat synths (and the synth-walk tests) don't re-run pip.
- Hard-fails on pip error — never produces a silently-incomplete package.

Spec: ``.kiro/specs/2026-06-15-cms-websocket-api-auth-gap/`` (Fix Group 1).
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_DEPLOYMENT_DIR = _THIS.parent.parent          # deployment/
_REPO_ROOT = _DEPLOYMENT_DIR.parent            # repo root
_SRC_DIR = _REPO_ROOT / "services" / "websocket" / "authorizer"
_BUILD_DIR = _DEPLOYMENT_DIR / "build" / "ws_authorizer"

# Must match the Lambda runtime + architecture wired in ui_stack.py.
_LAMBDA_PYTHON_VERSION = "3.12"
_LAMBDA_PLATFORM = "manylinux2014_x86_64"

_MARKER = ".bundle-hash"


def _content_hash(requirements: Path, source: Path) -> str:
    h = hashlib.sha256()
    h.update(requirements.read_bytes())
    h.update(source.read_bytes())
    h.update(f"{_LAMBDA_PYTHON_VERSION}:{_LAMBDA_PLATFORM}".encode())
    return h.hexdigest()


def bundle_ws_authorizer() -> str:
    """Build (or reuse cached) deps+source bundle; return the asset dir path."""
    requirements = _SRC_DIR / "requirements.txt"
    source = _SRC_DIR / "ws_authorizer.py"
    if not requirements.exists() or not source.exists():
        raise FileNotFoundError(
            f"WebSocket authorizer source incomplete under {_SRC_DIR} "
            f"(need requirements.txt + ws_authorizer.py)"
        )

    want = _content_hash(requirements, source)
    marker = _BUILD_DIR / _MARKER
    if _BUILD_DIR.exists() and marker.exists() and marker.read_text().strip() == want:
        return str(_BUILD_DIR)

    if _BUILD_DIR.exists():
        shutil.rmtree(_BUILD_DIR)
    _BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # Fetch linux wheels for the Lambda arch (cryptography is native).
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--platform", _LAMBDA_PLATFORM,
            "--only-binary=:all:",
            "--python-version", _LAMBDA_PYTHON_VERSION,
            "--implementation", "cp",
            "-r", str(requirements),
            "-t", str(_BUILD_DIR),
        ],
        check=True,
    )
    shutil.copy2(source, _BUILD_DIR / "ws_authorizer.py")
    marker.write_text(want)
    return str(_BUILD_DIR)
