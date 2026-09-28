#!/usr/bin/env python3
"""Rotate one or all demo-persona passwords without a full cdk deploy.

Passwords are CDK-generated into Secrets Manager; this script lets an operator
rotate them on demand (e.g. after a suspected compromise, or to cycle before a
live demo) without waiting for a CDK deploy cycle.

SECRET LAYOUT
-------------
  cms-<stage>-demo-user-password       plain string — FleetManager@example.com only
  cms-<stage>-demo-persona-passwords   JSON object keyed by email — other three

STAGES
------
  staging   us-west-2
  prod      us-east-1

  The Cognito User Pool ID is resolved at runtime from CloudFormation stack output
  ``UserPoolId`` on stack ``cms-<stage>-ui`` in the stage's region.  This keeps
  the script portable for any accelerator deployer and avoids baking deployment-
  specific identifiers into source code.

SECURITY
--------
The password is NEVER accepted as a command-line argument — argv is visible in `ps`
to any local user.  Supply a custom value via the environment variable
CMS_DEMO_NEW_PASSWORD.  If that variable is absent the script calls
secretsmanager:GetRandomPassword server-side.

`exclude_characters` for the generated password MUST include a comma.  A generated
value containing one broke:
    aws cognito-idp initiate-auth --auth-parameters USERNAME=...,PASSWORD=...
during 2026-08-05 verification.

ACCEPTANCE
----------
After applying the Cognito change the script asserts that `UserLastModifiedDate`
actually moved relative to the pre-rotation snapshot.  A run that did not rotate
exits non-zero even if all earlier steps reported success.

Usage:
    # Dry run (default) — prints what would change, makes no mutating call
    python3 rotate_demo_login.py --stage staging

    # Rotate all four personas on staging
    python3 rotate_demo_login.py --stage staging --apply

    # Rotate a single user
    python3 rotate_demo_login.py --stage staging --apply --user agent1@cms-fleet.io

    # Rotate prod (requires --confirm-prod in addition to --apply)
    python3 rotate_demo_login.py --stage prod --apply --confirm-prod

    # Use an operator-supplied password instead of a generated one
    CMS_DEMO_NEW_PASSWORD=<value> python3 rotate_demo_login.py --stage staging --apply

Spec: .kiro/specs/2026-08-05-cms-demo-identity-model/ (Group A2, task 1)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PERSONAS: list[str] = [
    "FleetManager@example.com",
    "agent1@cms-fleet.io",
    "engineer@example.com",
    "kevin.dispatch@example.com",
]

# FleetManager lives in its own plain-string secret; the rest share a JSON object.
_FLEET_MANAGER_EMAIL = "FleetManager@example.com"

_STAGE_CONFIG: dict[str, dict[str, str]] = {
    "staging": {
        "region": "us-west-2",
        "solo_secret": "cms-staging-demo-user-password",
        "multi_secret": "cms-staging-demo-persona-passwords",
    },
    "prod": {
        "region": "us-east-1",
        "solo_secret": "cms-prod-demo-user-password",
        "multi_secret": "cms-prod-demo-persona-passwords",
    },
}

# Characters that break comma-delimited AWS CLI --auth-parameters or that are
# shell-hazardous.  The comma constraint is the hard one; the others are
# belt-and-suspenders hygiene.
_EXCLUDE_CHARS = ",'\"$`\\"

# CloudFormation stack and output names used to resolve the Cognito User Pool ID.
_CFN_STACK_TEMPLATE = "cms-{stage}-ui"
_CFN_OUTPUT_USER_POOL_ID = "UserPoolId"


def _secret_name_for_user(stage: str, email: str) -> str:
    """Return the Secrets Manager secret name that stores *email*'s password."""
    cfg = _STAGE_CONFIG[stage]
    if email == _FLEET_MANAGER_EMAIL:
        return cfg["solo_secret"]
    return cfg["multi_secret"]


def _log_credential_safe(label: str, value: str) -> None:
    """Print length and sha256 digest of a secret value — never the value itself."""
    digest = hashlib.sha256(value.encode()).hexdigest()
    print(f"  {label}: len={len(value)} sha256={digest[:16]}...")


# ---------------------------------------------------------------------------
# AWS helpers
# ---------------------------------------------------------------------------


def _get_pool_id(cfn_client: Any, stage: str) -> str:
    """Resolve the Cognito User Pool ID from CloudFormation stack output.

    Reads the ``UserPoolId`` output from stack ``cms-<stage>-ui``.  Fails with
    a clear error message if the stack is absent or the output is missing so
    that callers get an actionable diagnostic rather than an obscure AWS error.

    Raises:
        SystemExit: If the stack or the expected output does not exist.
    """
    stack_name = _CFN_STACK_TEMPLATE.format(stage=stage)
    try:
        resp = cfn_client.describe_stacks(StackName=stack_name)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("ValidationError", "StackNotFoundException"):
            print(
                f"ERROR: CloudFormation stack {stack_name!r} not found.  "
                f"Deploy the stack first, or check the --stage value.",
                file=sys.stderr,
            )
        else:
            print(
                f"ERROR: Failed to describe stack {stack_name!r}: {exc}",
                file=sys.stderr,
            )
        sys.exit(1)

    stacks = resp.get("Stacks", [])
    if not stacks:
        print(
            f"ERROR: No stacks returned for {stack_name!r}.",
            file=sys.stderr,
        )
        sys.exit(1)

    outputs = stacks[0].get("Outputs", [])
    for output in outputs:
        if output.get("OutputKey") == _CFN_OUTPUT_USER_POOL_ID:
            return output["OutputValue"]

    print(
        f"ERROR: Stack {stack_name!r} exists but has no output named "
        f"{_CFN_OUTPUT_USER_POOL_ID!r}.  "
        f"Re-deploy the stack to expose the output, or check the stack name.",
        file=sys.stderr,
    )
    sys.exit(1)


def _get_random_password(sm_client: Any) -> str:
    """Generate a password server-side.  exclude_characters includes comma."""
    resp = sm_client.get_random_password(
        PasswordLength=20,
        ExcludeCharacters=_EXCLUDE_CHARS,
        ExcludeNumbers=False,
        ExcludeUppercase=False,
        ExcludeLowercase=False,
        RequireEachIncludedType=True,
    )
    return resp["RandomPassword"]


def _read_secret_string(sm_client: Any, secret_name: str) -> str:
    """Return the raw SecretString for *secret_name*, raising on error."""
    resp = sm_client.get_secret_value(SecretId=secret_name)
    return resp["SecretString"]


def _get_current_password(sm_client: Any, stage: str, email: str) -> str:
    """Read the current password for *email* from Secrets Manager."""
    secret_name = _secret_name_for_user(stage, email)
    raw = _read_secret_string(sm_client, secret_name)
    if email == _FLEET_MANAGER_EMAIL:
        # Plain string
        return raw.strip()
    # JSON object keyed by email
    data = json.loads(raw)
    return data[email]


def _write_secret(sm_client: Any, stage: str, email: str, new_password: str, dry_run: bool) -> None:
    """Update Secrets Manager to record the new password.

    For FleetManager: overwrites the plain string.
    For others: reads the JSON object, updates the relevant key, writes it back.
    Does NOT create a new secret — secrets are pre-existing (rotated 2026-08-05).
    """
    secret_name = _secret_name_for_user(stage, email)

    if dry_run:
        print(f"  [dry-run] would update secret {secret_name!r} for {email!r}")
        return

    if email == _FLEET_MANAGER_EMAIL:
        sm_client.put_secret_value(
            SecretId=secret_name,
            SecretString=new_password,
        )
    else:
        # Read-modify-write the JSON object
        raw = _read_secret_string(sm_client, secret_name)
        data = json.loads(raw)
        data[email] = new_password
        sm_client.put_secret_value(
            SecretId=secret_name,
            SecretString=json.dumps(data),
        )
    print(f"  ✓ Secrets Manager updated: {secret_name!r} for {email!r}")


def _get_user_last_modified(cognito_client: Any, pool_id: str, email: str) -> Any:
    """Return the UserLastModifiedDate datetime for *email* in the Cognito pool."""
    resp = cognito_client.admin_get_user(
        UserPoolId=pool_id,
        Username=email,
    )
    return resp["UserLastModifiedDate"]


def _set_cognito_password(
    cognito_client: Any,
    pool_id: str,
    email: str,
    new_password: str,
    dry_run: bool,
) -> None:
    """Call AdminSetUserPassword (permanent=True) for *email*."""
    if dry_run:
        print(f"  [dry-run] would call AdminSetUserPassword for {email!r}")
        return
    cognito_client.admin_set_user_password(
        UserPoolId=pool_id,
        Username=email,
        Password=new_password,
        Permanent=True,
    )
    print(f"  ✓ Cognito password set for {email!r}")


def _assert_timestamp_moved(
    cognito_client: Any,
    pool_id: str,
    email: str,
    before: Any,
) -> None:
    """Assert UserLastModifiedDate for *email* is strictly after *before*.

    This is the acceptance test for a successful rotation.  A Cognito password
    change always updates UserLastModifiedDate; if it did not move the rotate
    did not take effect and we must fail loudly.
    """
    after = _get_user_last_modified(cognito_client, pool_id, email)
    if after <= before:
        raise RuntimeError(
            f"ROTATION FAILED for {email!r}: UserLastModifiedDate did not move "
            f"(before={before!r}, after={after!r}).  The password was NOT changed."
        )
    print(f"  ✓ UserLastModifiedDate moved for {email!r}: {before!r} → {after!r}")


# ---------------------------------------------------------------------------
# Core rotation logic
# ---------------------------------------------------------------------------


def _rotate_one(
    sm_client: Any,
    cognito_client: Any,
    stage: str,
    pool_id: str,
    email: str,
    new_password: str | None,
    dry_run: bool,
) -> None:
    """Rotate the password for a single *email* on *stage*.

    Steps:
      1. Snapshot UserLastModifiedDate (acceptance anchor)
      2. Resolve the new password (env var > generated)
      3. Update Secrets Manager
      4. Call AdminSetUserPassword --permanent
      5. Assert UserLastModifiedDate moved (exits non-zero on failure)
    """
    print(f"\n{'[DRY-RUN] ' if dry_run else ''}Rotating {email!r} on {stage!r}…")

    # Step 1 — snapshot before timestamp (read-only, always executed)
    if not dry_run:
        before_ts = _get_user_last_modified(cognito_client, pool_id, email)
        print(f"  pre-rotation UserLastModifiedDate: {before_ts!r}")
    else:
        before_ts = None  # not needed for dry-run path

    # Step 2 — resolve the new password
    if new_password is not None:
        resolved = new_password
        print("  using operator-supplied password via CMS_DEMO_NEW_PASSWORD")
    else:
        if dry_run:
            print("  [dry-run] would call GetRandomPassword (exclude_chars includes comma)")
            resolved = "<generated>"
        else:
            resolved = _get_random_password(sm_client)
            _log_credential_safe("generated password", resolved)

    # Step 3 — update Secrets Manager
    _write_secret(sm_client, stage, email, resolved, dry_run)

    # Step 4 — set Cognito password
    _set_cognito_password(cognito_client, pool_id, email, resolved, dry_run)

    # Step 5 — assert timestamp moved (only when actually applying)
    if not dry_run:
        _assert_timestamp_moved(cognito_client, pool_id, email, before_ts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rotate demo persona passwords in Secrets Manager + Cognito "
            "without a full cdk deploy.\n\n"
            "PASSWORD SOURCE: set CMS_DEMO_NEW_PASSWORD env var for a custom value, "
            "or omit it to generate server-side via secretsmanager:GetRandomPassword."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=sorted(_STAGE_CONFIG.keys()),
        help="Deployment stage (maps to region + secret names + Cognito pool).",
    )
    parser.add_argument(
        "--user",
        metavar="EMAIL",
        default=None,
        help=(
            "Rotate a single persona by email address.  "
            "Omit to rotate all four personas."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help=(
            "Actually rotate. Without this flag the script runs in dry-run mode "
            "and prints a plan with no mutating calls."
        ),
    )
    parser.add_argument(
        "--confirm-prod",
        action="store_true",
        default=False,
        help=(
            "Required when --stage prod is combined with --apply.  "
            "Forces explicit opt-in before touching a live Cognito pool."
        ),
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="PROFILE",
        help="AWS credentials profile (optional).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    args = _parse_args(argv)

    # ── Guard: NEVER accept password from argv ──────────────────────────────
    # (The parser is intentionally missing a --password argument.  This comment
    # is here so reviewers can confirm the omission is deliberate.)

    dry_run = not args.apply

    # ── Guard: prod requires --confirm-prod ────────────────────────────────
    if args.stage == "prod" and args.apply and not args.confirm_prod:
        print(
            "ERROR: --stage prod with --apply requires --confirm-prod.\n"
            "This prevents accidental production rotations.  Re-run with both flags.",
            file=sys.stderr,
        )
        return 1

    # ── Resolve user list ──────────────────────────────────────────────────
    if args.user is not None:
        if args.user not in _PERSONAS:
            print(
                f"ERROR: {args.user!r} is not a known demo persona.\n"
                f"Valid emails: {', '.join(_PERSONAS)}",
                file=sys.stderr,
            )
            return 1
        users = [args.user]
    else:
        users = list(_PERSONAS)

    # ── Read operator-supplied password from env (never argv) ─────────────
    operator_password: str | None = os.environ.get("CMS_DEMO_NEW_PASSWORD") or None
    if operator_password is not None and not dry_run:
        _log_credential_safe("operator-supplied CMS_DEMO_NEW_PASSWORD", operator_password)

    # ── Build clients ──────────────────────────────────────────────────────
    cfg = _STAGE_CONFIG[args.stage]
    session = boto3.Session(profile_name=args.profile, region_name=cfg["region"])
    sm_client = session.client("secretsmanager")
    cognito_client = session.client("cognito-idp")

    # ── Resolve Cognito User Pool ID from CloudFormation ──────────────────
    # In dry-run mode we still look up the pool ID so the plan output is useful
    # and any misconfiguration is caught early.
    cfn_client = session.client("cloudformation")
    pool_id = _get_pool_id(cfn_client, args.stage)

    # ── Print plan header ──────────────────────────────────────────────────
    print(
        f"\n{'DRY-RUN PLAN' if dry_run else 'ROTATING'} — stage={args.stage!r}  "
        f"region={cfg['region']!r}  pool=<resolved from cfn>"
    )
    print(f"  Users : {', '.join(users)}")
    print(f"  Source: {'operator env var CMS_DEMO_NEW_PASSWORD' if operator_password else 'server-side GetRandomPassword'}")
    if dry_run:
        print("  Mode  : DRY-RUN (pass --apply to mutate)\n")

    # ── Rotate ────────────────────────────────────────────────────────────
    errors: list[str] = []
    for email in users:
        try:
            _rotate_one(
                sm_client=sm_client,
                cognito_client=cognito_client,
                stage=args.stage,
                pool_id=pool_id,
                email=email,
                new_password=operator_password,
                dry_run=dry_run,
            )
        except (ClientError, RuntimeError) as exc:
            msg = f"FAILED for {email!r}: {exc}"
            print(f"\n  ✗ {msg}", file=sys.stderr)
            errors.append(msg)

    # ── Summary ───────────────────────────────────────────────────────────
    n = len(users)
    n_ok = n - len(errors)
    print(
        f"\n{'Dry-run complete' if dry_run else 'Rotation complete'}: "
        f"{n_ok}/{n} {'planned' if dry_run else 'succeeded'}."
    )
    if errors:
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
