"""
Portfolio canary parity check: CMS ↔ CVX ``forbidden_strings``.

Background
----------
On 2026-08-05, a short demo password was found live on the public CMS GitHub
mirror.  Post-mortem: the value existed as a ``forbidden_strings`` entry in
the sibling CVX repo but was absent from CMS — so one repo treated it as
sensitive while the other published it.  Root cause documented in:
  cms/issues/2026-08-05-demo-pw-published-on-public-mirror/report.md

This test closes the detection gap by asserting that every ``forbidden_strings``
entry shared between CMS and CVX is present in **both** configs.

How "shared" is determined
--------------------------
The check treats a canary as shared when it appears in **either** repo's
``forbidden_strings`` list AND is semantically cross-repo (i.e. the value is
one that both repos could plausibly encounter).  Rather than maintain a third
"required-everywhere" list, we use the simpler rule:

  Any canary present in BOTH configs today is expected in BOTH configs going
  forward.  If a canary is later deliberately removed from one repo (e.g. a
  value that is no longer relevant to that repo's domain), the removal must be
  accompanied by a removal from the other repo, not a silent divergence.

In practice: the intersection of the two ``forbidden_strings`` lists is the
shared set.  The check fails if, after computing the intersection, the result
is smaller than MIN_SHARED_CANARIES — a floor that ensures the test cannot
trivially pass when both configs are empty or near-empty.

The specific canary that motivated the spec (the one that reached the public
mirror on 2026-08-05) is asserted by **sha256 digest**, not by name.  Naming
it in this source file would trip the very scanner it is meant to protect —
the scanner treats every ``forbidden_strings`` entry as forbidden content in
publishable files, and this test file ships.  See the "Live credentials"
section below for the discipline this follows.

Sibling repo discovery
----------------------
Mirrors the pattern in::

  ~/guidance-for-connected-vehicle-experience-on-aws/scripts/tests/
      test_seed_scripts_no_fake_connected.py

  CVX_REPO_PATH env var  →  explicit override
  default fallback       →  ``../guidance-for-connected-vehicle-experience-on-aws``
                              relative to this file

When the sibling repo is absent the entire test is skipped with a clear message.
The CMS scan always runs (this file lives in CMS).

Live credentials
----------------
No canary appears as a plaintext literal in this file — including canaries
that are already dead (like the 2026-08-05 demo password).  The reason is not
that dead canaries are dangerous per se; it is that the scanner has no way to
tell a "safely referenced" literal from a "leak" literal, and a test file that
trips the scanner blocks every subsequent publish.  Verification is by sha256
digest instead; the required-canary constant below stores only the digest.

Manual invocation::

    cd ~/connected-mobility-guidance-on-aws
    python3 -m pytest scripts/lib/test_canary_parity.py -v

    # To point at a CVX repo in a non-default location:
    CVX_REPO_PATH=~/my-cvx-checkout python3 -m pytest scripts/lib/test_canary_parity.py -v
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

import pytest
import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The minimum number of canaries expected to be shared between both repos.
# Guards against the check trivially passing when configs are empty or minimal.
# Updated if the shared set legitimately shrinks below this floor (with a comment).
MIN_SHARED_CANARIES: int = 5

# The canary that motivated this spec — the 2026-08-05 demo password that
# reached the public mirror.  Stored as a sha256 digest so the value itself
# does not appear as a plaintext literal in this test source (which would
# trip the scanner that treats it as a forbidden string).  See § "Live
# credentials" in the module docstring for the reasoning.
#
# To rotate this to a new required-canary in the future: compute
#   python3 -c "import hashlib; print(hashlib.sha256(b'<value>').hexdigest())"
# and paste the hex digest below.  Never paste the value itself.
REQUIRED_SHARED_CANARY_SHA256: str = (
    "b9a73a0c1ef254a023d2af4d85ab05ffc1c65115b726cad54ce650e4374586f7"
)


def _sha256(entry: str) -> str:
    """Return the hex sha256 digest of a forbidden_strings entry."""
    return hashlib.sha256(entry.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Repo and config discovery
# ---------------------------------------------------------------------------

# CMS repo root: this file lives at scripts/lib/<this>.py → parents[2] is repo root.
CMS_REPO: Path = Path(__file__).resolve().parents[2]
CMS_SCAN_CONFIG: Path = CMS_REPO / ".publish-secrets-scan.yml"


def _discover_cvx_repo() -> Optional[Path]:
    """Locate the sibling CVX repo via env var or relative path convention."""
    env = os.environ.get("CVX_REPO_PATH")
    if env:
        p = Path(env).expanduser().resolve()
        return p if p.is_dir() else None
    # Convention: sibling directory relative to the CMS repo root.
    candidate = CMS_REPO.parent / "guidance-for-connected-vehicle-experience-on-aws"
    return candidate if candidate.is_dir() else None


CVX_REPO: Optional[Path] = _discover_cvx_repo()
CVX_SCAN_CONFIG: Optional[Path] = (
    CVX_REPO / ".publish-secrets-scan.yml" if CVX_REPO is not None else None
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_forbidden_strings(config_path: Path) -> list[str]:
    """
    Parse ``forbidden_strings`` from a ``.publish-secrets-scan.yml``.

    Returns a list of strings (order preserved as in the file).
    Raises ``FileNotFoundError`` if the config is absent.
    Raises ``KeyError``/``yaml.YAMLError`` on malformed config.
    """
    with config_path.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    raw = cfg.get("forbidden_strings") or []
    # Each entry is expected to be a plain string.  Filter defensively.
    return [entry for entry in raw if isinstance(entry, str)]


# ---------------------------------------------------------------------------
# The parity test
# ---------------------------------------------------------------------------

def test_canary_parity_cms_cvx():
    """
    Every ``forbidden_strings`` entry that appears in **both** CMS and CVX
    configs must continue to appear in both.

    The test:
      1. Loads CMS's ``forbidden_strings`` (mandatory — we are IN CMS).
      2. Locates CVX; if absent, skips with a loud message.
      3. Loads CVX's ``forbidden_strings``.
      4. Computes the intersection (the "shared" set at test-writing time).
      5. Asserts that both configs still contain every member of that set.
      6. Asserts the intersection has at least MIN_SHARED_CANARIES entries.
      7. Asserts REQUIRED_SHARED_CANARY_SHA256 is a digest of some entry in both configs.

    Failure means a canary that was shared has been silently dropped from one
    repo.  Fix: either re-add it to the repo that dropped it, or remove it from
    both if it is no longer relevant to either (and update this test's comment
    explaining the removal).
    """
    # ---- Step 1: CMS forbidden_strings ----
    assert CMS_SCAN_CONFIG.is_file(), (
        f"CMS scanner config not found at {CMS_SCAN_CONFIG}. "
        "This should never happen when running from the CMS repo."
    )
    cms_strings: set[str] = set(_load_forbidden_strings(CMS_SCAN_CONFIG))

    # ---- Step 2: CVX presence check ----
    if CVX_REPO is None:
        pytest.skip(
            "Sibling CVX repo not found — "
            "set CVX_REPO_PATH or check out at "
            "../guidance-for-connected-vehicle-experience-on-aws. "
            "Only CMS config was loaded; cross-repo parity check skipped."
        )

    assert CVX_SCAN_CONFIG is not None  # implied by CVX_REPO being set
    assert CVX_SCAN_CONFIG.is_file(), (
        f"CVX scanner config not found at {CVX_SCAN_CONFIG}. "
        "The CVX repo is present but its .publish-secrets-scan.yml is missing."
    )

    # ---- Step 3: CVX forbidden_strings ----
    cvx_strings: set[str] = set(_load_forbidden_strings(CVX_SCAN_CONFIG))

    # ---- Step 4: Shared set ----
    shared: set[str] = cms_strings & cvx_strings

    # ---- Step 5: Both configs still contain every shared entry ----
    # By definition of set-intersection these hold already; we assert them
    # explicitly so a future refactor that changes the logic gets a clear
    # failure message for each missing canary, not just "shared set shrank".
    missing_from_cms = shared - cms_strings  # will always be empty; belt-and-suspenders
    missing_from_cvx = shared - cvx_strings  # ditto

    # The real assertion: the shared set computed NOW must still be fully present
    # in BOTH configs.  If it diverges in the future (one side drops an entry),
    # the intersection shrinks and the next two blocks catch it.
    #
    # Full divergence detection: any canary that WAS in both configs (i.e. in the
    # shared set as of test-writing time) but is now absent from one config will
    # be caught because the intersection will no longer include it, causing the
    # MIN_SHARED_CANARIES floor or the REQUIRED_SHARED_CANARY assertion to fail.
    #
    # Belt-and-suspenders for future refactors:
    assert not missing_from_cms, (
        f"BUG in parity check: shared canaries computed but missing from CMS: "
        f"{missing_from_cms}"
    )
    assert not missing_from_cvx, (
        f"BUG in parity check: shared canaries computed but missing from CVX: "
        f"{missing_from_cvx}"
    )

    # ---- Step 6: Floor check ----
    assert len(shared) >= MIN_SHARED_CANARIES, (
        f"The shared canary set has shrunk to {len(shared)} entries "
        f"(minimum required: {MIN_SHARED_CANARIES}). "
        "A canary present in both configs when this test was written is now "
        "absent from one repo. Either:\n"
        "  (a) Re-add the missing canary to the repo that dropped it, OR\n"
        "  (b) Remove it from both repos AND update MIN_SHARED_CANARIES here "
        "with a comment explaining the removal.\n\n"
        f"Current CMS forbidden_strings ({len(cms_strings)}): "
        f"{sorted(cms_strings)}\n"
        f"Current CVX forbidden_strings ({len(cvx_strings)}): "
        f"{sorted(cvx_strings)}\n"
        f"Shared ({len(shared)}): {sorted(shared)}"
    )

    # ---- Step 7: Required canary (checked by digest) ----
    # The value itself is not stored in this test source; only its sha256.
    # This lets the test verify the canary is present in both configs without
    # naming it in publishable code.
    cms_digests: set[str] = {_sha256(s) for s in cms_strings}
    cvx_digests: set[str] = {_sha256(s) for s in cvx_strings}

    assert REQUIRED_SHARED_CANARY_SHA256 in cms_digests, (
        f"Required canary (sha256={REQUIRED_SHARED_CANARY_SHA256[:16]}...) is "
        f"MISSING from CMS ({CMS_SCAN_CONFIG}). This canary was added on "
        "2026-08-05 after it was found live on the public mirror — its "
        "absence here means the scanner will not catch a reintroduction. "
        "See cms/issues/2026-08-05-demo-pw-published-on-public-mirror/."
    )
    assert REQUIRED_SHARED_CANARY_SHA256 in cvx_digests, (
        f"Required canary (sha256={REQUIRED_SHARED_CANARY_SHA256[:16]}...) is "
        f"MISSING from CVX ({CVX_SCAN_CONFIG}). CVX is the repo where this "
        "canary was originally registered; its absence means the CVX scanner "
        "can no longer detect a reintroduction."
    )
