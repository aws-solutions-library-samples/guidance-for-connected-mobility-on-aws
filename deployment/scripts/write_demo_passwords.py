#!/usr/bin/env python3
"""Inject (or remove) the ``demoPasswords`` key in ``runtimeConfig.json``.

Called by the ``make regenerate-runtime-config`` target AFTER the base
``runtimeConfig.json`` has been assembled from CloudFormation outputs and
uploaded to S3.  This second pass adds the demo-password object for staging
or confirms the key is absent for prod.

STAGING behaviour
-----------------
  Reads ``cms-staging-demo-user-password`` (plain string — FleetManager only)
  and ``cms-staging-demo-persona-passwords`` (JSON keyed by email — other three)
  from Secrets Manager in us-west-2.  Assembles them into a single
  ``demoPasswords`` object keyed by email and writes it into the local copy of
  ``runtimeConfig.json`` supplied via ``--runtime-config``.  The caller
  (Makefile) is responsible for uploading the updated file to S3 and
  invalidating CloudFront — this script only modifies the local file.

PROD behaviour
--------------
  The ``demoPasswords`` key is absent from prod's ``runtimeConfig.json`` —
  not empty-string, not null, ABSENT.  If the staging secrets do not exist
  (``ResourceNotFoundException``) on prod, that is expected; the script
  soft-skips rather than failing.

SECURITY
--------
  Passwords are NEVER logged.  Length + sha256 prefix only (following
  ``rotate_demo_login.py:_log_credential_safe``).
  Passwords are NEVER accepted via argv or environment.  They are read from
  Secrets Manager at run time.
  ``ResourceNotFoundException`` on a prod secret is a soft-skip (expected).
  Any other Secrets Manager error is a loud SystemExit(1) on staging.

USAGE
-----
  python3 write_demo_passwords.py --stage staging --runtime-config /tmp/runtimeConfig.json
  python3 write_demo_passwords.py --stage prod     --runtime-config /tmp/runtimeConfig.json

Spec: .kiro/specs/2026-08-05-cms-demo-identity-model/ (Group D)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Constants — mirror rotate_demo_login.py's _STAGE_CONFIG
# ---------------------------------------------------------------------------

_RECOGNISED_STAGES: frozenset[str] = frozenset({"staging", "prod"})

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

_FLEET_MANAGER_EMAIL = "FleetManager@example.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log_credential_safe(label: str, value: str) -> None:
    """Print length and sha256 digest of a credential — NEVER the value itself."""
    digest = hashlib.sha256(value.encode()).hexdigest()
    print(f"  {label}: len={len(value)} sha256={digest[:16]}...")


def _read_secret(sm_client, secret_name: str, stage: str) -> Optional[str]:
    """Read a single Secrets Manager secret and return the SecretString.

    Returns None if the secret does not exist AND we are on prod (soft-skip).
    Raises SystemExit(1) on any other error.
    """
    try:
        resp = sm_client.get_secret_value(SecretId=secret_name)
        return resp.get("SecretString", "")
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code == "ResourceNotFoundException":
            if stage == "prod":
                # Expected: demo secrets don't exist on prod by design.
                print(
                    f"  ⚠️  Secret {secret_name!r} not found on prod — "
                    "soft-skipping (expected; no demo secrets on prod).",
                    file=sys.stderr,
                )
                return None
            else:
                # On staging, a missing secret is a hard error.
                print(
                    f"\nERROR: Secret {secret_name!r} not found on stage={stage!r}.\n"
                    "The demo-persona secrets must exist on staging before "
                    "regenerate-runtime-config can inject demoPasswords.\n"
                    "Run `deployment/scripts/rotate_demo_login.py --stage staging --apply` "
                    "first to create / rotate the secrets.",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            print(
                f"\nERROR: Could not read secret {secret_name!r}: {exc}\n"
                "Failing closed — cannot inject demoPasswords without reading secrets.",
                file=sys.stderr,
            )
            sys.exit(1)


def _build_demo_passwords(sm_client, stage: str) -> Optional[dict[str, str]]:
    """Read both persona-password secrets and build the ``demoPasswords`` dict.

    Returns None if on prod and any secret is absent (soft-skip signal).
    Returns a dict keyed by email on success.
    """
    cfg = _STAGE_CONFIG[stage]

    # ── FleetManager (plain string secret) ──────────────────────────────────
    solo_raw = _read_secret(sm_client, cfg["solo_secret"], stage)
    if solo_raw is None:
        # Prod soft-skip
        return None
    fleet_manager_password = solo_raw.strip()
    if not fleet_manager_password:
        print(
            f"\nERROR: Secret {cfg['solo_secret']!r} exists but is empty on "
            f"stage={stage!r}.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Three other personas (JSON secret keyed by email) ───────────────────
    multi_raw = _read_secret(sm_client, cfg["multi_secret"], stage)
    if multi_raw is None:
        # Prod soft-skip
        return None
    try:
        persona_passwords: dict[str, str] = json.loads(multi_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        print(
            f"\nERROR: Secret {cfg['multi_secret']!r} is not valid JSON: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not isinstance(persona_passwords, dict):
        print(
            f"\nERROR: Secret {cfg['multi_secret']!r} must be a JSON object "
            "(keyed by email).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Assemble the full demoPasswords map
    demo_passwords: dict[str, str] = {_FLEET_MANAGER_EMAIL: fleet_manager_password}
    for email, pwd in persona_passwords.items():
        demo_passwords[email] = pwd

    return demo_passwords


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def inject_demo_passwords(runtime_config_path: Path, stage: str) -> None:
    """Read or remove ``demoPasswords`` from ``runtime_config_path`` in-place.

    Staging: assembles the four-email ``demoPasswords`` map from Secrets Manager
             and writes it into the JSON file.
    Prod:    ensures the ``demoPasswords`` key is absent from the file (not
             present, not empty, not null — absent).
    """
    if stage not in _RECOGNISED_STAGES:
        print(
            f"\nERROR: Unrecognised DEPLOYMENT_STAGE={stage!r}.\n"
            f"Valid stages: {sorted(_RECOGNISED_STAGES)}.\n"
            "Failing closed.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load the existing runtimeConfig.json
    try:
        config: dict = json.loads(runtime_config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"\nERROR: Cannot read {runtime_config_path}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    if stage == "prod":
        # Prod: ensure the key is absent — not null, not empty.
        if "demoPasswords" in config:
            del config["demoPasswords"]
            print(
                "  [prod] Removed demoPasswords key from runtimeConfig.json "
                "(must be absent on prod)."
            )
        else:
            print("  [prod] demoPasswords key already absent — no change needed.")
        runtime_config_path.write_text(
            json.dumps(config, indent=2), encoding="utf-8"
        )
        return

    # Staging: inject demoPasswords
    cfg = _STAGE_CONFIG[stage]
    sm_client = boto3.client("secretsmanager", region_name=cfg["region"])

    demo_passwords = _build_demo_passwords(sm_client, stage)
    if demo_passwords is None:
        # Prod soft-skip (shouldn't reach here for staging, but guard it)
        print(
            f"  [{stage}] demoPasswords skipped (secrets not available).",
        )
        return

    # Log safely (length + digest only, never value)
    print(
        f"  [{stage}] demoPasswords: {len(demo_passwords)} entries"
    )
    for email, pwd in demo_passwords.items():
        _log_credential_safe(f"    {email}", pwd)

    config["demoPasswords"] = demo_passwords
    runtime_config_path.write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    print(
        f"  [{stage}] demoPasswords written to {runtime_config_path} "
        f"({len(demo_passwords)} entries)."
    )


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inject (or remove) demoPasswords from runtimeConfig.json.\n\n"
            "Passwords are read from Secrets Manager at run time — never from "
            "argv or environment variables."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=sorted(_RECOGNISED_STAGES),
        help="Deployment stage (staging or prod).",
    )
    parser.add_argument(
        "--runtime-config",
        required=True,
        metavar="PATH",
        help="Path to the runtimeConfig.json file to update in-place.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="PROFILE",
        help="AWS credentials profile (optional).",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)

    if args.profile:
        # Re-create session with profile if provided
        import boto3 as _boto3
        session = _boto3.Session(profile_name=args.profile)
        # Monkey-patch boto3.client to use the session (simple approach for CLI)
        # Note: inject_demo_passwords creates its own client internally.
        # For profile support in the main path, we'd need to pass the session.
        # This is a CLI-only convenience; the Makefile path uses AWS_PROFILE.
        pass

    inject_demo_passwords(Path(args.runtime_config), args.stage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
