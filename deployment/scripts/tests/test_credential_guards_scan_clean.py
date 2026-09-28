"""The credential-guard scripts must themselves survive the publish scanner.

Regression guard for
``issues/2026-09-16-publish-scanner-9-critical-findings-in-guard-test-files/``:
the two scripts that exist to keep credentials out of shipped assets were
between them contributing **9 critical findings** to the pre-publish scan, and
blocked the public mirror. Five were the real deploying account id, embedded in
a test file as a fixture for the very defect its guard exists to catch.

Both files ship — neither is in ``.publish-exclude`` nor in the scan config's
``scan_exclude``, and that is the correct posture. Per
``~/.kiro/steering/public-mirror-publish.md``, a file that is ``scan_exclude``d
but not ``.publish-exclude``d ships *unexamined*, which is the worse of the two
outcomes and the mechanism behind four separate exposures. So the fix for a
finding in these files is to change the content, never to hide it from the
scanner.

This test asserts the scanner's real verdict on the real files rather than
grepping for particular strings. A grep-based version would only catch the
values that have leaked before; this catches any new pattern the config learns
to detect, including ones added after this test was written.

Scope, stated so it is not over-read: this covers the two files the issue named.
It is NOT a whole-tree publish gate. Verifying the full publishable archive
means staging ``git archive HEAD``, applying ``.publish-exclude``, and scanning
what survives — see ``scripts/publish-to-github.sh --dry-run``, which is the
real gate and belongs in CI. A broader structural guard over
``deployment/scripts/*.py`` is recommendation 2 of the issue and is not done
here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCANNER = _REPO_ROOT / "scripts" / "lib" / "secret-scan.py"
_CONFIG = _REPO_ROOT / ".publish-secrets-scan.yml"

# The two files the 2026-09-16 issue found findings in. Relative to the repo
# root, because the scanner reports paths relative to its --root.
_GUARD_FILES = (
    "deployment/scripts/test_no_credential_in_built_assets.py",
    "deployment/scripts/assert_no_credential_in_assets.py",
)


def _scan(paths: tuple[str, ...]) -> dict:
    """Run the real publish scanner over *paths* in an isolated tree.

    Copies each path into a temp root preserving its relative location, so the
    scanner sees the same paths it would see in a publish staging tree.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel in paths:
            src = _REPO_ROOT / rel
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        proc = subprocess.run(
            [sys.executable, str(_SCANNER),
             "--config", str(_CONFIG), "--root", str(root)],
            capture_output=True, text=True, check=False,
        )
    assert proc.stdout.strip(), (
        f"scanner produced no stdout; stderr was:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


@pytest.mark.parametrize("rel", _GUARD_FILES)
def test_guard_file_still_ships(rel: str) -> None:
    """Neither file may be hidden instead of fixed.

    If a future change adds one of these to `.publish-exclude` or to
    `scan_exclude`, the clean-scan test below would pass vacuously — it would be
    scanning a file that no longer ships, or not scanning it at all. This is the
    anti-vacuity floor for that test, so the pair cannot both go green by the
    file disappearing from the publish path.
    """
    exclude_file = _REPO_ROOT / ".publish-exclude"
    scan_config = _CONFIG.read_text()

    excluded = [
        ln.strip() for ln in exclude_file.read_text().splitlines()
        if ln.strip() == rel
    ]
    assert not excluded, (
        f"{rel} has been added to .publish-exclude. If that is deliberate, this "
        "test and the clean-scan test below must be retired together — "
        "otherwise the clean-scan test silently stops protecting anything."
    )
    assert f'"{rel}"' not in scan_config, (
        f"{rel} has been added to scan_exclude in .publish-secrets-scan.yml "
        "while still shipping. Per public-mirror-publish.md that means it ships "
        "UNEXAMINED, which is worse than either excluding it or fixing it."
    )


def test_credential_guard_files_scan_clean() -> None:
    """The real scanner, the real config, the real files: zero findings."""
    result = _scan(_GUARD_FILES)
    findings = result.get("findings", [])
    assert result.get("clean") is True and not findings, (
        "The credential-guard scripts are contributing findings to the "
        "pre-publish scan, which blocks the public mirror.\n"
        f"Findings: {json.dumps(findings, indent=2)}\n\n"
        "Fix the CONTENT. Do NOT add a scan_exclude entry — see "
        "~/.kiro/steering/public-mirror-publish.md, and note that an allowlist "
        "entry in .publish-secrets-scan.yml publishes the value it names.\n"
        "If the finding is a false positive (a 12-digit run inside a float, "
        "say), reword the prose so the run does not appear — that is how the "
        "2026-09-16 issue's last finding was closed."
    )


def test_scanner_detects_a_planted_account_id() -> None:
    """Positive control: the scan above must be capable of failing.

    Without this, `test_credential_guard_files_scan_clean` would pass just as
    happily against a broken scanner, a mis-specified config path, or a config
    whose `aws_account_id` pattern had been removed. Plants a non-allowlisted
    12-digit id in a throwaway file and asserts the scanner reports it.

    The planted value is **assembled from two shorter literals on purpose**. It
    has to be a non-allowlisted 12-digit id or the control is vacuous — an
    allowlisted value would not be flagged and this test would fail — but
    spelling it as a single literal puts a bare 12-digit run in a file that
    *ships*, which the publish scanner then flags as 3 critical findings. The
    first version of this test did exactly that, and the full-archive scan
    caught it; a scan of only the two files above cannot, because this file is
    not one of them.

    So: 8 digits + 4 digits in source, 12 digits at runtime. Same lesson as
    ~/.kiro/steering/public-mirror-publish.md's "a denylist written in terms of
    the secret embeds the secret" — a guard must not spell out the value it
    guards. Do NOT "simplify" this back into one literal.
    """
    planted = "31415926" + "5358"  # pi's digits; not an account, not allowlisted
    assert len(planted) == 12 and planted.isdigit(), "planted value must be 12 digits"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "deployment" / "scripts" / "_planted_for_test.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"ACCOUNT = '{planted}'\n")
        proc = subprocess.run(
            [sys.executable, str(_SCANNER),
             "--config", str(_CONFIG), "--root", str(root)],
            capture_output=True, text=True, check=False,
        )
        result = json.loads(proc.stdout)

    matched = [f.get("matched") for f in result.get("findings", [])]
    assert planted in matched, (
        "The scanner did not flag a planted non-allowlisted 12-digit account id. "
        "The clean-scan test in this file is therefore vacuous — it cannot "
        f"distinguish 'no findings' from 'not scanning'. Scanner said: {result}"
    )


def test_this_file_would_itself_survive_the_publish_scan() -> None:
    """This file ships, so it must not be a finding either.

    Guards the split-literal above. If someone rejoins it into one 12-digit
    literal the positive control keeps passing — it only cares about the runtime
    value — while the repo silently regains 3 critical publish findings. Nothing
    else catches that except a full-archive scan, which is not run in CI.
    """
    result = _scan((str(Path(__file__).resolve().relative_to(_REPO_ROOT)),))
    assert result.get("clean") is True, (
        "This test file is itself contributing publish-scanner findings:\n"
        f"{json.dumps(result.get('findings', []), indent=2)}\n\n"
        "Most likely cause: a 12-digit literal was spelled out in full. Assemble "
        "it from shorter parts instead — see test_scanner_detects_a_planted_account_id."
    )
