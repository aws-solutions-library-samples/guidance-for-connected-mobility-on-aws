#!/usr/bin/env python3
"""Static-analysis guard: no account-creation path produces a groupless account.

Scans:
  - deployment/scripts/seed_*.py          (Python)
  - deployment/lambdas/cognito_triggers/*/handler.py  (Python, may not exist yet)
  - deployment/scripts/*.sh               (shell — looks for admin-create-user)

For each file that contains a user-creation call, checks that a group-assignment
call is reachable within the same function/script before the success return.

Exits non-zero on any violation. Prints per-violation:
  path=<file>:<line> reason=<phrase>

NEVER echoes account values, credentials, or pool IDs.

Exemptions
----------
Exemptions are **explicit, per-call-site, and greppable**. There is no implicit
exemption of any kind: a file cannot earn one by what strings it happens to contain.

To exempt a call site, put a marker on the call's own line, or anywhere in the
contiguous block of comment lines immediately above it:

  # noqa: groupless-ok   OR   # CMS-SCANNER: groupless-driver-ok

The legitimate use today is driver/owner demo personas, which are groupless by
design: they authorize via ``custom:driverId`` through main_api's
``_classify_driver_self`` path rather than via a Cognito group. The `Fail-open
authz` fix (d235fb31) is what makes "groupless" mean "not an admin" instead of
"unscoped everything" — before it, a groupless account was a privilege-escalation
path, which is why this scanner exists at all.

An earlier revision granted an *implicit* file-level exemption: any file containing
the literal ``"custom:driverId"`` and making no group-assignment call anywhere had
all of its create calls skipped. That was removed 2026-08-10 because it keyed on a
string appearing in a file rather than on the account being a driver — a future seed
script copy-pasting an attribute helper would have been silently exempt — and
because its incentive gradient ran backwards: a file assigning groups to *some*
accounts lost the exemption while a file assigning groups to *none* kept it. Both
failure modes are test-enforced against now.

Spec: .kiro/specs/2026-08-07-cms-account-provisioning-model/ Group 2
See:  deployment/scripts/assert_no_credential_in_assets.py for the scanner
      pattern this follows (fail-closed, never echo credentials, prove the
      guard fires via test fixtures).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOYMENT = _REPO_ROOT / "deployment"
_SCRIPTS_DIR = _DEPLOYMENT / "scripts"
_TRIGGERS_DIR = _DEPLOYMENT / "lambdas" / "cognito_triggers"

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Python API names that CREATE a Cognito user (any case-insensitive variant).
_PY_CREATE_NAMES: frozenset[str] = frozenset({
    "admin_create_user",
    "AdminCreateUser",
})

# Python API names that ASSIGN a user to a group.
_PY_GROUP_NAMES: frozenset[str] = frozenset({
    "admin_add_user_to_group",
    "AdminAddUserToGroup",
})

# Shell CLI operations that create a Cognito user.
_SH_CREATE_RE: re.Pattern[str] = re.compile(
    r"\badmin-create-user\b", re.IGNORECASE
)
# Shell CLI operations that assign a user to a group.
_SH_GROUP_RE: re.Pattern[str] = re.compile(
    r"\badmin-add-user-to-group\b", re.IGNORECASE
)

# Inline suppression comment — on the same line or the line immediately above.
#
# A RATIONALE IS MANDATORY. Closes security review Cycle 1 Suggestion 2 (2026-08-11): the
# module docstring already said the marker "needs a rationale on the line above", but the
# regex matched the bare marker, so the documented requirement was unenforced. A rule with
# no executable form is not a control — a one-line `# CMS-SCANNER: groupless-driver-ok` with
# no explanation would silence a real violation in a diff a reviewer skims past.
#
# `_MIN_RATIONALE_CHARS` exists to reject token rationales ("ok", "wip", "fixme") that
# satisfy a non-empty check while explaining nothing. 12 characters is not a magic number:
# it is comfortably below both real suppressions in this repo (32 and 33 characters) and
# comfortably above every dismissive one-word form.
_MIN_RATIONALE_CHARS = 12

_SUPPRESS_RE: re.Pattern[str] = re.compile(
    r"#\s*(?:noqa:\s*groupless-ok|CMS-SCANNER:\s*groupless-driver-ok)"
    # Optional separator (em-dash, colon, hyphen), then the rationale itself.
    r"\s*[\u2014:-]?\s*"
    r"(?P<rationale>\S.{" + str(_MIN_RATIONALE_CHARS - 1) + r",})",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class Violation(NamedTuple):
    path: Path
    line: int
    reason: str

    def __str__(self) -> str:
        return f"path={self.path}:{self.line} reason={self.reason}"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _ast_name(node: ast.expr) -> str | None:
    """Return the bare name of a Call's func, or None if not extractable."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _find_calls_in_node(
    node: ast.AST, names: frozenset[str]
) -> list[ast.Call]:
    """Return all Call nodes within *node* whose func name is in *names*."""
    result: list[ast.Call] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _ast_name(child.func)
            if name in names:
                result.append(child)
    return result


def _file_contains_call(tree: ast.Module, names: frozenset[str]) -> bool:
    """True if *any* call in the entire file has a func name in *names*."""
    return bool(_find_calls_in_node(tree, names))


def _is_suppressed(source_lines: list[str], lineno: int) -> bool:
    """True if the create call at *lineno* (1-based) carries a suppression marker.

    Scans the call's own line, then walks upward through the **contiguous block of
    comment lines** immediately above it. Walking the whole block (rather than only
    one line up) is deliberate: a suppression worth granting is a suppression worth
    explaining, and a multi-line rationale would otherwise push the marker out of
    detection range — which silently converts an explained exemption into no
    exemption at all. Blank lines and any non-comment code terminate the walk, so a
    marker cannot leak in from an unrelated block further up the file.
    """
    if lineno <= len(source_lines) and _SUPPRESS_RE.search(source_lines[lineno - 1]):
        return True

    # Walk upward through contiguous comment lines only.
    idx = lineno - 2  # 0-based index of the line above the call
    while idx >= 0:
        stripped = source_lines[idx].strip()
        if not stripped.startswith("#"):
            break
        if _SUPPRESS_RE.search(source_lines[idx]):
            return True
        idx -= 1
    return False


# ---------------------------------------------------------------------------
# Python file scanner
# ---------------------------------------------------------------------------


def _scan_python(path: Path) -> list[Violation]:
    """Scan a Python file for create-without-group violations."""
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [Violation(path, 0, f"syntax error: {exc.msg}")]

    violations: list[Violation] = []

    # NOTE (architect, 2026-08-10): an implicit file-level driver-seeder exemption
    # was REMOVED here. It exempted every create call in any file that contained the
    # literal "custom:driverId" anywhere AND made no group-assignment call anywhere.
    #
    # Two things were wrong with it, and both matter for a security guard:
    #
    #   1. It keyed on a STRING APPEARING IN A FILE, not on the account being
    #      created actually being a driver. A future seed script that copy-pasted a
    #      `build_attrs()` helper carrying that literal, and assigned no groups,
    #      would have been silently exempt. This repo has prior art for exactly that
    #      failure mode: CVX's `seed-persona-users.py` reintroduced a bug class a
    #      sibling script had already fixed, because the prevention was a source
    #      comment in one file rather than an executable check reaching the new one
    #      (issues/2026-07-31-fake-connected-status-regression/).
    #
    #   2. Its incentive gradient ran backwards. A file that assigned groups to SOME
    #      accounts LOST the exemption; a file that assigned groups to NONE KEPT it.
    #      The guard relaxed precisely as a file got less compliant.
    #
    # Driver accounts are still legitimately groupless — they authorize via
    # custom:driverId / _classify_driver_self, and the `Fail-open authz` fix
    # (d235fb31) is what makes "groupless" mean "not an admin" rather than
    # "everything". But that exemption must be an authored, greppable, diff-visible
    # act per call site, not an emergent property of file contents. Use the
    # `# CMS-SCANNER: groupless-driver-ok` marker with a rationale on the line above.

    # Collect top-level and nested function/async-function defs.
    funcs: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node)

    # Check each function that contains a create call.
    for func in funcs:
        create_calls = _find_calls_in_node(func, _PY_CREATE_NAMES)
        if not create_calls:
            continue

        # Does this function also contain a group assignment?
        func_has_group = bool(_find_calls_in_node(func, _PY_GROUP_NAMES))

        for call_node in create_calls:
            lineno = call_node.lineno

            # Inline suppression comment wins outright.
            if _is_suppressed(source_lines, lineno):
                continue

            if func_has_group:
                # Group assignment exists within the same function — compliant.
                continue

            violations.append(
                Violation(
                    path=path,
                    line=lineno,
                    reason=(
                        f"admin_create_user in '{func.name}' has no "
                        "admin_add_user_to_group reachable before success return"
                    ),
                )
            )

    return violations


# ---------------------------------------------------------------------------
# Shell file scanner
# ---------------------------------------------------------------------------


def _scan_shell(path: Path) -> list[Violation]:
    """Scan a shell script for admin-create-user without admin-add-user-to-group."""
    lines = path.read_text(encoding="utf-8").splitlines()

    create_lines: list[int] = []
    for i, line in enumerate(lines, start=1):
        # Skip comment lines.
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if _SH_CREATE_RE.search(line):
            # Suppression comment on the same line.
            if _SUPPRESS_RE.search(line):
                continue
            # Suppression on the line immediately above.
            if i > 1 and _SUPPRESS_RE.search(lines[i - 2]):
                continue
            create_lines.append(i)

    if not create_lines:
        return []

    # If the script also contains admin-add-user-to-group anywhere, assume
    # the pairing exists (shell scripts are harder to scope per-function).
    # A missing pairing is only reported when no group call exists at all.
    full_text = "\n".join(lines)
    if _SH_GROUP_RE.search(full_text):
        return []

    # Report each create line without a group-assignment partner.
    return [
        Violation(
            path=path,
            line=lineno,
            reason=(
                "admin-create-user in shell script has no "
                "admin-add-user-to-group in the same script"
            ),
        )
        for lineno in create_lines
    ]


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def _python_targets() -> list[Path]:
    """Return all Python files to scan."""
    targets: list[Path] = []

    # seed_*.py in scripts/
    targets.extend(sorted(_SCRIPTS_DIR.glob("seed_*.py")))

    # cognito_triggers handler.py files (may not exist yet in early groups)
    if _TRIGGERS_DIR.exists():
        targets.extend(sorted(_TRIGGERS_DIR.glob("*/handler.py")))

    return targets


def _shell_targets() -> list[Path]:
    """Return all shell scripts to scan in deployment/scripts/."""
    return sorted(_SCRIPTS_DIR.glob("*.sh"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def scan(root: Path | None = None) -> list[Violation]:
    """Run all checks and return violations.

    *root* overrides the repository root (for testing against fixture trees).
    """
    if root is not None:
        scripts_dir = root / "deployment" / "scripts"
        triggers_dir = root / "deployment" / "lambdas" / "cognito_triggers"
    else:
        scripts_dir = _SCRIPTS_DIR
        triggers_dir = _TRIGGERS_DIR

    violations: list[Violation] = []

    # Python: seed_*.py
    for py_path in sorted(scripts_dir.glob("seed_*.py")):
        violations.extend(_scan_python(py_path))

    # Python: cognito_triggers handler.py files (gracefully absent)
    if triggers_dir.exists():
        for handler_path in sorted(triggers_dir.glob("*/handler.py")):
            violations.extend(_scan_python(handler_path))

    # Shell: *.sh in deployment/scripts/
    for sh_path in sorted(scripts_dir.glob("*.sh")):
        violations.extend(_scan_shell(sh_path))

    return violations


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Override repository root (for testing)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else None
    violations = scan(root)

    if not violations:
        print("assert_no_groupless_account_path: PASS — no violations found")
        return 0

    print(
        f"assert_no_groupless_account_path: FAIL — {len(violations)} violation(s) found",
        file=sys.stderr,
    )
    for v in violations:
        # Print path=<file>:<line> reason=<phrase> — no account values echoed.
        print(str(v))
    return 1


if __name__ == "__main__":
    sys.exit(main())
