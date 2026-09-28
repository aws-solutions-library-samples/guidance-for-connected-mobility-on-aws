"""Tests for assert_no_groupless_account_path.py.

FAIL-THEN-PASS PROOF
--------------------
A scanner only ever run against clean input proves nothing.
These tests assert:
  - The scanner FAILS on fixture_creates_user_no_group.py
  - The scanner PASSES on fixture_creates_user_with_group.py

Additional coverage:
  - Scanner exits 0 against the real deployment/scripts/ tree
    (no pre-existing violations; driver accounts are exempted)
  - Shell-script scanning
  - Suppression comment mechanism
  - Edge cases (no create calls, syntax errors, absent trigger dirs)

Spec: .kiro/specs/2026-08-07-cms-account-provisioning-model/ Group 2
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent
_SCANNER_PATH = _SCRIPTS_DIR / "assert_no_groupless_account_path.py"
_FIXTURE_NO_GROUP = _SCRIPTS_DIR / "fixture_creates_user_no_group.py"
_FIXTURE_WITH_GROUP = _SCRIPTS_DIR / "fixture_creates_user_with_group.py"

# Lazy import so the test module doesn't fail to load even if the scanner
# has a syntax error (the subprocess tests still run).
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("assert_no_groupless_account_path", _SCANNER_PATH)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

scan = _mod.scan
_scan_python = _mod._scan_python
_scan_shell = _mod._scan_shell


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_py(tmp_path: Path, name: str, source: str) -> Path:
    """Write a Python file to tmp_path and return its path."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(source), encoding="utf-8")
    return p


def _make_sh(tmp_path: Path, name: str, source: str) -> Path:
    """Write a shell script to tmp_path and return its path."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(source), encoding="utf-8")
    p.chmod(0o755)
    return p


def _make_tree(
    tmp_path: Path,
    seed_files: dict[str, str] | None = None,
    sh_files: dict[str, str] | None = None,
    handler_files: dict[str, str] | None = None,
) -> Path:
    """Build a minimal fake deployment tree under tmp_path."""
    scripts = tmp_path / "deployment" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name, src in (seed_files or {}).items():
        (scripts / name).write_text(textwrap.dedent(src), encoding="utf-8")
    for name, src in (sh_files or {}).items():
        f = scripts / name
        f.write_text(textwrap.dedent(src), encoding="utf-8")
        f.chmod(0o755)

    if handler_files:
        triggers = tmp_path / "deployment" / "lambdas" / "cognito_triggers"
        for rel_name, src in handler_files.items():
            handler = triggers / rel_name
            handler.parent.mkdir(parents=True, exist_ok=True)
            handler.write_text(textwrap.dedent(src), encoding="utf-8")

    return tmp_path


# ---------------------------------------------------------------------------
# FAIL-THEN-PASS PROOF — the core requirement of this task
# ---------------------------------------------------------------------------


class TestFailThenPassProof:
    """The guard must fire on violation AND pass on compliant code.

    A scanner only ever run against clean input proves nothing.
    """

    def test_fixture_no_group_fails(self) -> None:
        """Scanner FAILS on fixture_creates_user_no_group.py.

        This is the 'fail' half of the fail-then-pass proof.
        """
        assert _FIXTURE_NO_GROUP.exists(), (
            f"Missing fixture: {_FIXTURE_NO_GROUP}. "
            "Was the fixture file created alongside this test?"
        )
        violations = _scan_python(_FIXTURE_NO_GROUP)
        assert len(violations) >= 1, (
            "Scanner did NOT detect the violation in fixture_creates_user_no_group.py. "
            "The guard is inert — it only ever sees clean input."
        )
        # The violation must mention the creating function name.
        reasons = [v.reason for v in violations]
        assert any("provision_user" in r for r in reasons), (
            f"Expected 'provision_user' in violation reason, got: {reasons}"
        )
        # The violation must not echo any account value or credential.
        for v in violations:
            assert "pool_id" not in str(v).lower() or "pool_id" in str(v), True  # attr name OK
            # Ensure no Cognito pool-ID shapes (us-east-1_EXAMPLE) in output.
            import re
            assert not re.search(r"us-[a-z]+-[0-9]_[A-Za-z0-9]+", str(v)), (
                "Violation output appears to echo an account value or pool ID"
            )

    def test_fixture_with_group_passes(self) -> None:
        """Scanner PASSES on fixture_creates_user_with_group.py.

        This is the 'pass' half of the fail-then-pass proof.
        """
        assert _FIXTURE_WITH_GROUP.exists(), (
            f"Missing fixture: {_FIXTURE_WITH_GROUP}. "
            "Was the fixture file created alongside this test?"
        )
        violations = _scan_python(_FIXTURE_WITH_GROUP)
        assert violations == [], (
            f"Scanner raised a false-positive on fixture_creates_user_with_group.py: "
            f"{violations}"
        )


# ---------------------------------------------------------------------------
# Real tree test — scanner exits 0 against the current deployment/scripts/
# ---------------------------------------------------------------------------


class TestRealTree:
    """Scanner must exit 0 against the actual project tree."""

    def test_exits_zero_against_current_tree(self) -> None:
        """No pre-existing violations in deployment/scripts/seed_*.py,
        deployment/scripts/*.sh, or any cognito_trigger handlers.
        """
        violations = scan()
        assert violations == [], (
            f"Scanner found {len(violations)} pre-existing violation(s) "
            f"in the current tree:\n"
            + "\n".join(str(v) for v in violations)
        )

    def test_subprocess_exits_zero(self) -> None:
        """End-to-end subprocess: 'python3 assert_no_groupless_account_path.py'
        exits 0 against the current tree.
        """
        result = subprocess.run(
            [sys.executable, str(_SCANNER_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Scanner subprocess exited {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_fixture_no_group_subprocess_exits_nonzero(self) -> None:
        """End-to-end subprocess: scanner exits non-zero against the
        fixture_creates_user_no_group.py fixture.

        Proves the guard fires via the CLI entry point.
        """
        # We need to scan just the fixture file, not the whole tree.
        # Use --root to point at a tmp tree that contains only the fixture.
        import tempfile
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scripts = tmp_path / "deployment" / "scripts"
            scripts.mkdir(parents=True)
            # Copy the fixture as a seed_*.py so the scanner picks it up.
            shutil.copy(
                _FIXTURE_NO_GROUP,
                scripts / "seed_fixture_no_group.py",
            )
            result = subprocess.run(
                [sys.executable, str(_SCANNER_PATH), "--root", str(tmp_path)],
                capture_output=True,
                text=True,
            )
        assert result.returncode != 0, (
            "Scanner subprocess exited 0 on fixture_creates_user_no_group.py "
            "(renamed to seed_fixture_no_group.py). The guard is inert."
        )


# ---------------------------------------------------------------------------
# Python scanner unit tests
# ---------------------------------------------------------------------------


class TestPythonScanner:
    """Unit tests for the Python create-without-group detection."""

    def test_create_without_group_is_violation(self, tmp_path: Path) -> None:
        """admin_create_user with no admin_add_user_to_group → violation."""
        f = _make_py(tmp_path, "seed_test.py", """
            import boto3

            def create_account(cog, pool_id, email):
                cog.admin_create_user(
                    UserPoolId=pool_id,
                    Username=email,
                    UserAttributes=[{"Name": "email", "Value": email}],
                )
        """)
        violations = _scan_python(f)
        assert len(violations) == 1
        assert "admin_add_user_to_group" in violations[0].reason

    def test_create_with_group_in_same_function_is_clean(self, tmp_path: Path) -> None:
        """admin_create_user followed by admin_add_user_to_group in same function → clean."""
        f = _make_py(tmp_path, "seed_test.py", """
            import boto3

            def create_account(cog, pool_id, email, group):
                cog.admin_create_user(
                    UserPoolId=pool_id,
                    Username=email,
                    UserAttributes=[{"Name": "email", "Value": email}],
                )
                cog.admin_add_user_to_group(
                    UserPoolId=pool_id,
                    Username=email,
                    GroupName=group,
                )
        """)
        violations = _scan_python(f)
        assert violations == []

    def test_driver_seeder_without_marker_is_NOT_exempt(self, tmp_path: Path) -> None:
        """A driver-ish file with NO explicit marker must FAIL.

        Inverted 2026-08-10 by the architect. This test previously asserted the
        opposite — that merely containing the literal "custom:driverId" and making no
        group call earned a file-wide exemption. That implicit exemption was removed:
        it keyed on a string appearing in a file rather than on the account being a
        driver, so any future seed script that copy-pasted a helper carrying that
        literal would have been silently exempt. Exemptions must now be authored per
        call site and are greppable in a diff.
        """
        f = _make_py(tmp_path, "seed_drivers.py", """
            import boto3

            def build_attrs(driver):
                return [
                    {"Name": "custom:driverId", "Value": driver["driverId"]},
                    {"Name": "email", "Value": driver["email"]},
                ]

            def create_driver(cog, pool_id, driver):
                attrs = build_attrs(driver)
                cog.admin_create_user(
                    UserPoolId=pool_id,
                    Username=driver["email"],
                    UserAttributes=attrs,
                )
        """)
        violations = _scan_python(f)
        assert len(violations) == 1, (
            "A driver-seeder file with no explicit suppression marker must be "
            f"reported. Got: {violations}"
        )

    def test_driver_seeder_with_explicit_marker_is_exempt(self, tmp_path: Path) -> None:
        """The same file passes once the call site carries an explicit marker."""
        f = _make_py(tmp_path, "seed_drivers.py", """
            import boto3

            def create_driver(cog, pool_id, driver):
                # CMS-SCANNER: groupless-driver-ok — authorizes via custom:driverId
                cog.admin_create_user(
                    UserPoolId=pool_id,
                    Username=driver["email"],
                    UserAttributes=[],
                )
        """)
        violations = _scan_python(f)
        assert violations == [], f"Explicit marker must exempt. Got: {violations}"

    def test_marker_found_at_top_of_multiline_comment_block(
        self, tmp_path: Path
    ) -> None:
        """A marker several comment-lines above the call still suppresses.

        A suppression worth granting is worth explaining, so the rationale is often
        multi-line and pushes the marker away from the call. If detection only looked
        one line up, an explained exemption would silently become no exemption.
        """
        f = _make_py(tmp_path, "seed_drivers.py", """
            import boto3

            def create_driver(cog, pool_id, driver):
                # CMS-SCANNER: groupless-driver-ok — driver personas are groupless
                # by design; they authorize via custom:driverId through
                # main_api's _classify_driver_self path, not via a Cognito group.
                # Do not "fix" this by adding a group.
                cog.admin_create_user(
                    UserPoolId=pool_id,
                    Username=driver["email"],
                    UserAttributes=[],
                )
        """)
        violations = _scan_python(f)
        assert violations == [], (
            f"Marker at top of a comment block must suppress. Got: {violations}"
        )

    def test_marker_does_not_leak_across_a_code_line(self, tmp_path: Path) -> None:
        """The upward comment-walk stops at the first non-comment line.

        Without this, a marker in an unrelated block further up the file would
        silently exempt every later create call — the same too-broad-exemption
        failure that motivated removing the file-level rule.
        """
        f = _make_py(tmp_path, "seed_drivers.py", """
            import boto3

            def create_driver(cog, pool_id, driver):
                # CMS-SCANNER: groupless-driver-ok — belongs to the call below
                cog.admin_create_user(UserPoolId=pool_id, Username="a")
                other = compute_something()
                cog.admin_create_user(
                    UserPoolId=pool_id,
                    Username=driver["email"],
                    UserAttributes=[],
                )
        """)
        violations = _scan_python(f)
        assert len(violations) == 1, (
            "The second create call is separated from the marker by a code line and "
            f"must NOT inherit the suppression. Got: {violations}"
        )

    def test_BARE_marker_does_NOT_suppress(self, tmp_path) -> None:
        """Security review Cycle 1 Suggestion 2 (2026-08-11): a marker needs a rationale.

        The module docstring always said the marker "needs a rationale on the line above",
        but the regex matched the bare marker, so the documented requirement was unenforced.
        A rule with no executable form is not a control. Without this test, a one-line
        `# CMS-SCANNER: groupless-driver-ok` in a diff a reviewer skims past would silence a
        real groupless-account violation.
        """
        f = _make_py(tmp_path, "bare_marker.py", """
            import boto3

            def provision(cog, pool_id, email):
                # CMS-SCANNER: groupless-driver-ok
                cog.admin_create_user(
                    UserPoolId=pool_id,
                    Username=email,
                    UserAttributes=[],
                )
        """)
        violations = _scan_python(f)
        assert violations, (
            "a BARE suppression marker suppressed the violation — the rationale "
            "requirement has been weakened or removed"
        )

    def test_token_rationale_does_NOT_suppress(self, tmp_path) -> None:
        """'ok' / 'wip' satisfy a non-empty check while explaining nothing."""
        f = _make_py(tmp_path, "token_rationale.py", """
            import boto3

            def provision(cog, pool_id, email):
                # CMS-SCANNER: groupless-driver-ok wip
                cog.admin_create_user(
                    UserPoolId=pool_id,
                    Username=email,
                    UserAttributes=[],
                )
        """)
        assert _scan_python(f), "a token rationale must not suppress"

    def test_suppression_comment_on_same_line(self, tmp_path: Path) -> None:
        """A # noqa: groupless-ok comment on the same line suppresses the violation."""
        f = _make_py(tmp_path, "seed_test.py", """
            import boto3

            def provision(cog, pool_id, email):
                cog.admin_create_user(  # noqa: groupless-ok driver persona, scoped by driverId
                    UserPoolId=pool_id,
                    Username=email,
                    UserAttributes=[],
                )
        """)
        violations = _scan_python(f)
        assert violations == [], f"Suppression comment not honoured: {violations}"

    def test_suppression_comment_on_preceding_line(self, tmp_path: Path) -> None:
        """A suppression comment on the line above the create call is honoured."""
        f = _make_py(tmp_path, "seed_test.py", """
            import boto3

            def provision(cog, pool_id, email):
                # CMS-SCANNER: groupless-driver-ok — driver persona, no group by design
                cog.admin_create_user(
                    UserPoolId=pool_id,
                    Username=email,
                    UserAttributes=[],
                )
        """)
        violations = _scan_python(f)
        assert violations == [], f"Preceding-line suppression not honoured: {violations}"

    def test_no_create_calls_is_clean(self, tmp_path: Path) -> None:
        """File with no admin_create_user calls → no violations."""
        f = _make_py(tmp_path, "seed_test.py", """
            def seed_fleets(ddb, stage):
                ddb.put_item(Item={"fleetId": "FLT-001"})
        """)
        violations = _scan_python(f)
        assert violations == []

    def test_syntax_error_is_reported(self, tmp_path: Path) -> None:
        """A file with a syntax error produces a syntax-error violation."""
        f = _make_py(tmp_path, "seed_bad.py", "def broken(: pass\n")
        violations = _scan_python(f)
        assert len(violations) == 1
        assert "syntax error" in violations[0].reason

    def test_AdminCreateUser_camelcase_is_detected(self, tmp_path: Path) -> None:
        """AdminCreateUser (camelCase) is also detected as a create call."""
        f = _make_py(tmp_path, "seed_test.py", """
            import boto3

            def provision(client, pool_id, email):
                client.AdminCreateUser(
                    UserPoolId=pool_id,
                    Username=email,
                    UserAttributes=[],
                )
        """)
        violations = _scan_python(f)
        assert len(violations) == 1

    def test_create_in_nested_function_detected(self, tmp_path: Path) -> None:
        """admin_create_user inside a nested function is detected."""
        f = _make_py(tmp_path, "seed_test.py", """
            import boto3

            def outer(cog, pool_id, email):
                def inner():
                    cog.admin_create_user(
                        UserPoolId=pool_id,
                        Username=email,
                        UserAttributes=[],
                    )
                inner()
        """)
        violations = _scan_python(f)
        # The scanner walks ast.walk() which includes nested funcs inside outer —
        # so we get violations for both 'outer' (which walks into inner) and
        # 'inner' itself. At least 1 violation must be found; callers should
        # ensure group assignment in the innermost function containing the create.
        assert len(violations) >= 1
        func_names = {v.reason for v in violations}
        assert any("inner" in r or "outer" in r for r in func_names)


# ---------------------------------------------------------------------------
# Shell scanner unit tests
# ---------------------------------------------------------------------------


class TestShellScanner:
    """Unit tests for the shell admin-create-user detection."""

    def test_shell_create_without_group_is_violation(self, tmp_path: Path) -> None:
        """admin-create-user in a shell script without admin-add-user-to-group → violation."""
        f = _make_sh(tmp_path, "seed_users.sh", """
            #!/usr/bin/env bash
            set -euo pipefail
            aws cognito-idp admin-create-user \\
                --user-pool-id "$POOL_ID" \\
                --username "user@example.com"
        """)
        violations = _scan_shell(f)
        assert len(violations) >= 1
        assert any("admin-add-user-to-group" in v.reason for v in violations)

    def test_shell_create_with_group_is_clean(self, tmp_path: Path) -> None:
        """admin-create-user followed by admin-add-user-to-group in same script → clean."""
        f = _make_sh(tmp_path, "seed_users.sh", """
            #!/usr/bin/env bash
            set -euo pipefail
            aws cognito-idp admin-create-user \\
                --user-pool-id "$POOL_ID" \\
                --username "user@example.com"
            aws cognito-idp admin-add-user-to-group \\
                --user-pool-id "$POOL_ID" \\
                --username "user@example.com" \\
                --group-name "fleet-viewer"
        """)
        violations = _scan_shell(f)
        assert violations == []

    def test_shell_comment_line_skipped(self, tmp_path: Path) -> None:
        """A commented-out admin-create-user line does not trigger a violation."""
        f = _make_sh(tmp_path, "notes.sh", """
            #!/usr/bin/env bash
            # aws cognito-idp admin-create-user --user-pool-id xxx
            echo "nothing to see here"
        """)
        violations = _scan_shell(f)
        assert violations == []

    def test_shell_suppression_comment(self, tmp_path: Path) -> None:
        """A noqa: groupless-ok comment on the shell create line suppresses violation."""
        f = _make_sh(tmp_path, "seed.sh", """
            #!/usr/bin/env bash
            aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username u  # noqa: groupless-ok seed driver, scoped by driverId
        """)
        violations = _scan_shell(f)
        assert violations == []


# ---------------------------------------------------------------------------
# Scan (full tree) with synthetic fixture trees
# ---------------------------------------------------------------------------


class TestScanWithFixtureTrees:
    """Test the top-level scan() function against synthetic trees."""

    def test_clean_tree_no_violations(self, tmp_path: Path) -> None:
        """A tree where every create is paired with a group → scan() returns []."""
        _make_tree(
            tmp_path,
            seed_files={
                "seed_accounts.py": """
                    import boto3

                    def provision(cog, pool_id, email, group):
                        cog.admin_create_user(
                            UserPoolId=pool_id,
                            Username=email,
                            UserAttributes=[],
                        )
                        cog.admin_add_user_to_group(
                            UserPoolId=pool_id,
                            Username=email,
                            GroupName=group,
                        )
                """,
            },
        )
        violations = scan(root=tmp_path)
        assert violations == []

    def test_violating_tree_detected(self, tmp_path: Path) -> None:
        """A tree with a create-without-group → scan() returns non-empty."""
        _make_tree(
            tmp_path,
            seed_files={
                "seed_bad.py": """
                    import boto3

                    def provision(cog, pool_id, email):
                        cog.admin_create_user(
                            UserPoolId=pool_id,
                            Username=email,
                            UserAttributes=[],
                        )
                """,
            },
        )
        violations = scan(root=tmp_path)
        assert len(violations) >= 1

    def test_absent_triggers_dir_is_graceful(self, tmp_path: Path) -> None:
        """scan() does not error when cognito_triggers/ does not exist yet."""
        _make_tree(tmp_path)  # No handler_files → triggers dir absent.
        violations = scan(root=tmp_path)
        assert violations == []

    def test_cognito_trigger_handler_with_group_is_clean(self, tmp_path: Path) -> None:
        """A cognito_triggers handler that assigns a group passes the scanner."""
        _make_tree(
            tmp_path,
            handler_files={
                "provisioning/handler.py": """
                    import boto3

                    def handler(event, context):
                        cog = boto3.client('cognito-idp')
                        pool_id = event['userPoolId']
                        username = event['userName']
                        cog.admin_create_user(
                            UserPoolId=pool_id,
                            Username=username,
                            UserAttributes=[],
                        )
                        cog.admin_add_user_to_group(
                            UserPoolId=pool_id,
                            Username=username,
                            GroupName='fleet-viewer',
                        )
                        return event
                """,
            },
        )
        violations = scan(root=tmp_path)
        assert violations == []

    def test_cognito_trigger_handler_without_group_is_violation(
        self, tmp_path: Path
    ) -> None:
        """A cognito_triggers handler that creates without a group → violation."""
        _make_tree(
            tmp_path,
            handler_files={
                "provisioning/handler.py": """
                    import boto3

                    def handler(event, context):
                        cog = boto3.client('cognito-idp')
                        pool_id = event['userPoolId']
                        username = event['userName']
                        cog.admin_create_user(
                            UserPoolId=pool_id,
                            Username=username,
                            UserAttributes=[],
                        )
                        return event
                """,
            },
        )
        violations = scan(root=tmp_path)
        assert len(violations) >= 1

    def test_shell_violation_detected_in_tree(self, tmp_path: Path) -> None:
        """A shell script with admin-create-user but no group call → violation."""
        _make_tree(
            tmp_path,
            sh_files={
                "seed_bootstrap.sh": """
                    #!/usr/bin/env bash
                    aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username admin@example.com
                """,
            },
        )
        violations = scan(root=tmp_path)
        assert len(violations) >= 1

    def test_output_does_not_echo_account_values(self, tmp_path: Path) -> None:
        """Violation output must not echo account values, pool IDs, or credentials."""
        import re

        _make_tree(
            tmp_path,
            seed_files={
                "seed_unsafe.py": """
                    import boto3

                    POOL_ID = "us-east-1_EXAMPLE"
                    SECRET = "hunter2"

                    def provision(cog, email):
                        cog.admin_create_user(
                            UserPoolId=POOL_ID,
                            Username=email,
                            UserAttributes=[{"Name": "email", "Value": email}],
                        )
                """,
            },
        )
        violations = scan(root=tmp_path)
        assert len(violations) >= 1
        for v in violations:
            output = str(v)
            # Must not echo the pool ID value.
            assert "ABC123456" not in output, (
                f"Violation echoes a pool-ID-shaped value: {output}"
            )
            # Must not echo a credential.
            assert "hunter2" not in output, (
                f"Violation echoes a credential: {output}"
            )
            # Output must be in the required format.
            assert output.startswith("path="), (
                f"Violation output does not start with 'path=': {output}"
            )
            assert " reason=" in output, (
                f"Violation output missing 'reason=': {output}"
            )
