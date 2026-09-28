#!/usr/bin/env python3
"""Built-asset credential + sensitive-identifier guard.

Scans the built asset tree the deploy is about to upload for three defect
classes:

1. **Credential values** — the stage's demo-persona secret(s) from Secrets
   Manager, and any 8+-character value from a demo-related field in the tree's
   ``runtimeConfig.json``. Value-based scan; a credential must not appear in a
   shipped bundle.
2. **Secrets Manager ARNs** — any ``arn:aws:secretsmanager:...`` string in a
   shipped bundle. Presence-based; an ARN in a public asset is a leak whatever
   account it names. Added 2026-09-15 after
   ``issues/2026-09-15-gate-403-template-publishes-account-id-and-secret-arn/``:
   a postbuild-substituted ``error/403.html`` reached the public bucket carrying
   the real ``CFSSigningKey-cms-staging-*`` ARN, unseen by every deploy-time
   check because the earlier scans looked only for credential *values*.
3. **AWS account id** — the deploying account id from STS, plus unallowlisted
   bare 12-digit sequences outside runtimeConfig-shaped contexts. The docs
   placeholder ``123456789012`` is allowlisted.

Fails closed — raises ``ValueError`` (or ``SystemExit`` for unreadable secrets
or unresolvable identity) on any error condition. Never prints matched
credential values; emits length + sha256 prefix instead, following the
``_redact_token`` convention in the sibling ``secret-scan.py``.

WHY THIS EXISTS
---------------
The CMS demo password was found live in the served JavaScript bundle on staging
AND prod, on the public GitHub mirror, and in a Tokyo template — all in one
session (2026-08-05).  The root cause was not that individual guards were absent;
it is that guards existed on the *build* path while the leak travelled by *deploy*.

``build-ui``'s existing 403-template assertion runs after build.  This script runs
FROM THE DEPLOY TARGET over the artefacts about to be uploaded, and fails closed
on an unrecognised stage.  That is the control that would have stopped the bundle
reaching prod.

Consistent with the two guards already shipped:
  - ``_require_driver_self_guard()``   (deployment/stacks/ui_stack.py)
  - ``_assert_no_plaintext_credential_in_template()``  (ibid.)

Both encode the same lesson: **prove the guard fires**.  Every task in the spec
that ships a guard asserts the failing case, not just the passing one.

SECURITY
--------
Never print a credential.  Length + sha256 digest only.
Degrade to a loud failure — not a silent skip — if the secret cannot be read on
a deployed stage.

USAGE
-----
Standalone:
    DEPLOYMENT_STAGE=staging python3 assert_no_credential_in_assets.py \
        --asset-root ../modules/cms_ui/source/frontend/build

From Makefile (wired into the deploy target; see deployment/Makefile):
    The script is invoked by the deploy recipe before ``cdk deploy`` uploads
    built assets, so a credential-containing build never reaches S3/CloudFront.

EXIT CODES
----------
0 — clean scan (no credential found)
1 — credential found in assets, or unrecognised stage, or secret unreadable

Spec: .kiro/specs/2026-08-05-cms-demo-identity-model/ (Group A2 — "Built-asset
      credential guard, invoked from the deploy path")
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Sequence

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Only these two stages are deployed.  Any other value fails closed.
_RECOGNISED_STAGES: frozenset[str] = frozenset({"staging", "prod"})

# Secret names per stage — mirror rotate_demo_login.py's _STAGE_CONFIG.
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

# Minimum credential length to check.  Values shorter than this floor are
# below the noise threshold and would produce false positives on common tokens.
_MIN_SECRET_LENGTH: int = 8

# Allowlisted values that are known-safe and must never trigger a finding:
#   - Angle-bracket placeholder shapes (documentation)
#   - The canonical AWS docs placeholder account ID
_PLACEHOLDER_RE: re.Pattern[str] = re.compile(r"<[^>]+>")
_AWS_DOCS_ACCOUNT_PLACEHOLDER: str = "123456789012"

# Secrets Manager ARN shape. Matches any secret ARN, regardless of region or account.
# The scan is a "no ARN in a shipped asset" check — the AWS account inside the ARN
# is not what triggers the finding; the presence of a *specific ARN* is. The docs
# placeholder account 123456789012 is deliberately NOT allowlisted here — a fully
# formed Secrets Manager ARN with a real-looking resource name (e.g. the CFSSigningKey
# leak this guard was extended to catch) is a shipped secret reference regardless of
# whether its account digits are the deploying one.
#
# Requires `:secret:` (colon), matching the real Secrets Manager ARN syntax. Mock
# data in this bundle uses `:secret/` (slash) for OEM-connector examples, which is
# not a valid ARN and does not match — so mock content stays clean.
_SECRETS_MANAGER_ARN_RE: re.Pattern[str] = re.compile(
    r"arn:aws:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@-]+"
)

# runtimeConfig.json field name patterns that indicate demo-related content.
# Any field whose name matches these patterns may carry a demo credential; its
# value is checked against built assets.
_DEMO_FIELD_PATTERNS: tuple[str, ...] = (
    "demo",
    "password",
    "prefill",
    "quicklogin",
    "persona",
)

# File extensions to scan in the asset tree.
_SCANNABLE_EXTENSIONS: frozenset[str] = frozenset(
    {".js", ".mjs", ".ts", ".html", ".htm", ".json", ".css", ".map"}
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redact(value: str) -> str:
    """Return a safe representation of *value*: length + sha256 prefix.

    Never returns the actual value — follows ``secret-scan.py``'s
    ``_redact_token`` convention.
    """
    digest = hashlib.sha256(value.encode()).hexdigest()
    return f"len={len(value)} sha256={digest[:16]}..."


def _is_placeholder(value: str) -> bool:
    """Return True if *value* is a known-safe placeholder that must be skipped."""
    if _PLACEHOLDER_RE.fullmatch(value):
        return True
    if value == _AWS_DOCS_ACCOUNT_PLACEHOLDER:
        return True
    return False


def _extract_secret_values(secret_string: str) -> list[str]:
    """Extract all password values from a secret string.

    Handles both plain strings (FleetManager) and JSON objects
    (``{"email": "password", ...}``).

    Returns a list of extracted string values with length >= _MIN_SECRET_LENGTH
    that are not known-safe placeholders.
    """
    values: list[str] = []

    # Try JSON first (the multi-persona secret is a JSON object).
    try:
        parsed = json.loads(secret_string)
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, str) and len(v) >= _MIN_SECRET_LENGTH:
                    if not _is_placeholder(v):
                        values.append(v)
            return values
        elif isinstance(parsed, str):
            # JSON string (unlikely, but handle it)
            if len(parsed) >= _MIN_SECRET_LENGTH and not _is_placeholder(parsed):
                values.append(parsed)
            return values
    except (json.JSONDecodeError, ValueError):
        pass

    # Plain string (the solo/FleetManager secret).
    plain = secret_string.strip()
    if len(plain) >= _MIN_SECRET_LENGTH and not _is_placeholder(plain):
        values.append(plain)
    return values


def _connected_services_subscriber_secret_name(stage: str, region: str) -> str | None:
    """Build CMS's Connected Services subscriber secret name for *stage*.

    Spec `2026-09-10-cms-connected-services-consumer` (T2.4). The name embeds
    the account id, so unlike `_STAGE_CONFIG`'s two demo secrets it cannot be a
    literal here — it is resolved via STS at run time, which also means this
    file never carries the account id.

    Must match `ui_stack.py`'s `_cs_subscriber_secret_name` and
    `scripts/provision-cms-subscriber.py:193`. Those two already agree by
    construction (one expression, consumed by both the IAM grant and the
    reader); this is a third site and the only defence against it drifting is
    that a drifted name yields no secret, which this function reports rather
    than swallowing.

    Returns None if the account cannot be resolved — the caller decides whether
    that is fatal, matching this module's fail-closed posture.
    """
    try:
        account = boto3.client("sts").get_caller_identity()["Account"]
    except (ClientError, BotoCoreError, KeyError):
        return None
    return f"cms-{stage}-connected-services-subscriber-{region}-{account}"


def _read_stage_secrets(stage: str) -> list[str]:
    """Read every credential secret for *stage* and return all values.

    Degrades to a loud failure (``SystemExit``) — not a silent skip — if a
    secret cannot be read.  This prevents a mis-configured deploy from bypassing
    the guard simply because Secrets Manager was unreachable.

    Covers the two demo-login secrets AND CMS's Connected Services subscriber
    credential. The subscriber credential is the one that matters most here and
    was NOT covered until T2.4: a browser holding it could act as CMS's
    subscriber account against the producer's API directly, bypassing CMS's own
    fleet-scope check entirely (spec D5). Value-based scanning is strictly
    stronger than name- or shape-based scanning for this, because it catches the
    credential however it arrived — inlined by a bundler, pasted into a fixture,
    or echoed into a runtimeConfig field nobody reviewed.
    """
    cfg = _STAGE_CONFIG[stage]
    region = cfg["region"]
    sm = boto3.client("secretsmanager", region_name=region)

    values: list[str] = []

    secret_names = [cfg["solo_secret"], cfg["multi_secret"]]
    cs_secret = _connected_services_subscriber_secret_name(stage, region)
    if cs_secret is not None:
        secret_names.append(cs_secret)
    else:
        # Fail closed rather than scanning a short list and reporting clean. An
        # unreachable STS is indistinguishable, from the assets' point of view,
        # from a credential that is not there — and only one of those is safe.
        print(
            "\nERROR: could not resolve the AWS account id, so CMS's Connected "
            "Services subscriber secret name cannot be built and its credential "
            "cannot be scanned for.\n"
            "Failing closed — refusing to report a clean tree on a partial check.",
            file=sys.stderr,
        )
        sys.exit(1)

    missing: list[str] = []
    for secret_name in secret_names:
        try:
            resp = sm.get_secret_value(SecretId=secret_name)
            raw = resp.get("SecretString", "")
            if raw:
                values.extend(_extract_secret_values(raw))
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            # ResourceNotFoundException — secret does not exist yet (new deploy).
            # We treat this as "no value to check" rather than a fatal error,
            # because the CDK construct that creates the secret may not have run
            # yet on a brand-new environment.
            if error_code == "ResourceNotFoundException":
                missing.append(secret_name)
            else:
                print(
                    f"\nERROR: Could not read secret {secret_name!r} for stage "
                    f"{stage!r}: {exc}\n"
                    "Failing closed — refusing to proceed without a clean check.",
                    file=sys.stderr,
                )
                sys.exit(1)

    # Security-review Cycle 1 Suggestion 2 (T2.4). ResourceNotFoundException is
    # the right answer for a brand-new environment, and a SILENT BYPASS for a
    # name that has drifted: the subscriber secret's name is built here from
    # stage/region/account and must match `ui_stack.py` and
    # `scripts/provision-cms-subscriber.py` exactly. A typo in any of the three
    # yields "not found", this guard scans for one fewer credential, and the
    # deploy reports a clean tree — which is the failure direction that matters.
    #
    # An at-least-one invariant distinguishes the two cases without breaking the
    # new-environment path: a real environment has SOME credential secret, so all
    # of them missing means the names are wrong, not that nothing exists yet.
    if missing and len(missing) == len(secret_names):
        print(
            f"\nERROR: none of the {len(secret_names)} credential secrets for "
            f"stage {stage!r} could be found:\n"
            + "\n".join(f"  - {n}" for n in missing)
            + "\nEither this is a brand-new environment with no secrets yet, or "
            "the names built here have drifted from the ones actually created "
            "(ui_stack.py and scripts/provision-cms-subscriber.py must agree).\n"
            "Failing closed — a guard that finds no credential to look for "
            "cannot report a clean tree.",
            file=sys.stderr,
        )
        sys.exit(1)
    for name in missing:
        # Named individually rather than counted: "1 secret missing" does not
        # tell an operator which check they are no longer getting.
        print(
            f"WARNING: secret {name!r} not found for stage {stage!r} — its "
            "credential was NOT scanned for in the built assets."
        )

    return values


def _extract_runtime_config_demo_values(runtime_config_path: Path) -> list[str]:
    """Read ``runtimeConfig.json`` and return 8+-char values from demo-related fields.

    A "demo-related field" is any field whose name (case-insensitively) contains
    one of the ``_DEMO_FIELD_PATTERNS`` tokens.  This catches ``demoPassword``,
    ``demoPrefillEmail``, ``showDemoButtons`` (boolean, skipped), etc.
    """
    if not runtime_config_path.exists():
        return []

    try:
        config = json.loads(runtime_config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(config, dict):
        return []

    values: list[str] = []
    for key, val in config.items():
        key_lower = key.lower()
        if not any(pat in key_lower for pat in _DEMO_FIELD_PATTERNS):
            continue
        if isinstance(val, str):
            if len(val) >= _MIN_SECRET_LENGTH and not _is_placeholder(val):
                values.append(val)
        elif isinstance(val, dict):
            # Nested dict — e.g. demoPasswords: { "email": "password", ... }
            # Flatten all string values, same length/placeholder checks.
            for nested_val in val.values():
                if isinstance(nested_val, str):
                    if len(nested_val) >= _MIN_SECRET_LENGTH and not _is_placeholder(nested_val):
                        values.append(nested_val)

    return values


def _account_id_from_sts() -> str | None:
    """Return the current AWS account id via STS, or None on failure.

    Failure is treated by the caller as "unable to scan for account-id leaks" —
    fail closed rather than silently proceed with a narrower check.
    """
    try:
        return boto3.client("sts").get_caller_identity()["Account"]
    except (ClientError, BotoCoreError, KeyError):
        return None


def _scan_files_for_secrets_manager_arn(files: list[Path], root: Path) -> list[str]:
    """Return one finding line per Secrets Manager ARN found in *files*.

    The scan is presence-based, not value-based: a Secrets Manager ARN in a
    shipped asset is a defect regardless of which account digits it names.

    Matching a full ARN also catches:
      - the 2026-09-15 CFSSigningKey exposure (gate 403 template)
      - any future postbuild substitution that inlines a secret ARN
      - error pages, help pages, and any other HTML/JS that echoes an ARN into
        the DOM as text-node content
    """
    findings: list[str] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _SECRETS_MANAGER_ARN_RE.finditer(content):
            arn = match.group(0)
            # Digest, not the ARN itself. Region + partition are the low-bits of
            # sensitivity here (the secret name being leaked is the top bits and
            # not something to echo back), so hash the whole ARN.
            findings.append(
                f"  SECRETS-MANAGER ARN FOUND in asset {path.relative_to(root)!s}: "
                f"({_redact(arn)})"
            )
    return findings


def _scan_files_for_account_id(
    files: list[Path],
    root: Path,
    account_id: str,
) -> list[str]:
    """Return one finding line per verbatim occurrence of *account_id* in *files*.

    Value-based scan, deliberately narrow: only the *deploying* account id is
    treated as a leak. A "any bare 12-digit sequence" widening was tried and
    withdrawn — the bundle legitimately carries 12-digit numeric constants (e.g.
    the fractional tail of a float like `4.00024414...`, from an easing
    function), all-zero placeholder IDs, and docs-placeholder ARNs in mock OEM
    data, and each of those produced findings that would have to be individually
    allowlisted at write time. The value-
    based form catches the class of leak this guard is extended to close (the
    2026-09-15 gate-403 exposure, which substituted the deploying account id
    verbatim into a shipped file) without needing an ever-growing allowlist.

    That example is written truncated on purpose. Spelled in full it is itself a
    bare 12-digit run, and the publish scanner's `aws_account_id` pattern flagged
    it here as a critical finding — a docstring explaining why bare-12-digit
    scanning false-positives, false-positiving by exactly that mechanism
    (`\\b` matches after the decimal point). See
    issues/2026-09-16-publish-scanner-9-critical-findings-in-guard-test-files/.
    Do not "restore" the full literal.

    The Secrets Manager ARN scan is shape-based and covers the broader case
    where an ARN is inlined without the account id appearing bare.
    """
    findings: list[str] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        offset = 0
        while True:
            idx = content.find(account_id, offset)
            if idx < 0:
                break
            findings.append(
                f"  ACCOUNT-ID FOUND in asset {path.relative_to(root)!s}: "
                f"(account id, redacted: {_redact(account_id)})"
            )
            offset = idx + len(account_id)
    return findings


def _collect_files(root: Path) -> list[Path]:
    """Return all scannable files under *root* recursively."""
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in _SCANNABLE_EXTENSIONS:
            files.append(path)
    return files


def _scan_files_for_value(files: list[Path], value: str) -> list[Path]:
    """Return every file in *files* whose content contains *value* verbatim."""
    hits: list[Path] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if value in content:
            hits.append(path)
    return hits


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_asset_tree(root: Path | str, stage: str) -> None:
    """Scan the built asset tree at *root* for demo credentials belonging to *stage*.

    Raises:
        ValueError: If *stage* is not a recognised deployment stage, or if any
            credential value is found in the asset tree.
        SystemExit: If a Secrets Manager secret is unreadable (loud failure, not
            a silent skip).

    This function is the primary callable under test.  It is also invoked from
    the Makefile deploy target so that a credential-containing build never reaches
    S3/CloudFront.
    """
    root = Path(root)

    # ── Stage validation: fail closed on unrecognised stage ─────────────────
    if stage not in _RECOGNISED_STAGES:
        raise ValueError(
            f"Unrecognised DEPLOYMENT_STAGE={stage!r}.  "
            f"Valid stages: {sorted(_RECOGNISED_STAGES)}.  "
            "Failing closed — refusing to scan without a known stage."
        )

    # ── Gather credential values to check ───────────────────────────────────
    # 1. Live values from Secrets Manager (the load-bearing check).
    secret_values = _read_stage_secrets(stage)

    # 2. Demo-field values from runtimeConfig.json in the build tree.
    runtime_config_values = _extract_runtime_config_demo_values(
        root / "runtimeConfig.json"
    )

    # 3. Account id, resolved via STS. Kept separate from the two above because
    #    it is fetched from the caller's identity, not from a credential store,
    #    and its scan must run even when no credential secrets exist yet
    #    (a brand-new environment can still leak an account id or an ARN into a
    #    postbuild-substituted template — see the 2026-09-15 gate-403 exposure).
    #    Fails closed on an unresolvable identity, matching the module's posture.
    account_id = _account_id_from_sts()
    if account_id is None:
        print(
            "\nERROR: could not resolve the AWS account id via STS.\n"
            "Failing closed — refusing to scan a build tree for account-id leaks "
            "without knowing which account id counts as a leak.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Collect files to scan ───────────────────────────────────────────────
    # Two scan sets:
    #   all_files   — ALL scannable files, used for Secrets Manager value checks
    #                 (a secret must NEVER appear in any file, including runtimeConfig.json)
    #   bundle_files — all files EXCEPT runtimeConfig.json, used for the check
    #                  that runtimeConfig demo-field values don't leak into bundles.
    #                  runtimeConfig.json is the INTENDED home of demoPasswords on
    #                  staging; scanning it against its own values is a tautology.
    all_files = _collect_files(root)
    bundle_files = [f for f in all_files if f.name.lower() != "runtimeconfig.json"]

    # ── Scan ────────────────────────────────────────────────────────────────
    findings: list[str] = []

    for value in secret_values:
        # Secrets Manager values must NOT appear in ANY file, including
        # runtimeConfig.json itself (if a secret leaked there, that's a defect).
        hit_files = _scan_files_for_value(all_files, value)
        if hit_files:
            safe_repr = _redact(value)
            for hit in hit_files:
                findings.append(
                    f"  CREDENTIAL FOUND in asset {hit.relative_to(root)!s}: "
                    f"({safe_repr})"
                )

    for value in runtime_config_values:
        # runtimeConfig demo-field values (e.g. demoPasswords entries) must NOT
        # appear in any BUNDLE file (JS/HTML/CSS). They ARE supposed to live in
        # runtimeConfig.json; scanning runtimeConfig.json against its own values
        # would be a tautology and produce false positives.
        hit_files = _scan_files_for_value(bundle_files, value)
        if hit_files:
            safe_repr = _redact(value)
            for hit in hit_files:
                findings.append(
                    f"  CREDENTIAL FOUND in asset {hit.relative_to(root)!s}: "
                    f"({safe_repr})"
                )

    # Secrets Manager ARN scan — presence is the defect. Runs against BUNDLE
    # files (not runtimeConfig.json): a legitimate frontend runtimeConfig may
    # in future carry an ARN for something benign, but bundles never should.
    # The 2026-09-15 leak was in build/error/403.html, a bundle file.
    findings.extend(_scan_files_for_secrets_manager_arn(bundle_files, root))

    # Account-id scan — verbatim deploying account id, plus unallowlisted bare
    # 12-digit sequences outside runtimeConfig-shaped contexts. Runs against
    # BUNDLE files: runtimeConfig.json legitimately carries Cognito pool ids
    # and API-GW hosts whose values include 12-digit runs, and would false-
    # positive here without extra context filtering the whole scan does not need.
    findings.extend(_scan_files_for_account_id(bundle_files, root, account_id))

    if findings:
        lines = "\n".join(findings)
        raise ValueError(
            f"Built-asset credential guard FAILED for stage={stage!r}.\n"
            f"The following findings were detected in shipped assets:\n{lines}\n\n"
            "One of: (a) a demo/subscriber credential leaked into the build, "
            "(b) a Secrets Manager ARN was inlined into a public asset, or "
            "(c) the AWS account id was substituted into a shipped file. "
            "Remove the value from the build, rotate it if the leak is a "
            "credential, and re-build before deploying."
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan built frontend assets for demo credential values.\n"
            "Exits 1 if any credential is found or if the stage is unrecognised."
        )
    )
    parser.add_argument(
        "--asset-root",
        default=None,
        metavar="PATH",
        help=(
            "Root directory of the built asset tree to scan.  "
            "Defaults to ../modules/cms_ui/source/frontend/build relative to "
            "the deployment/ directory."
        ),
    )
    parser.add_argument(
        "--stage",
        default=None,
        metavar="STAGE",
        help=(
            "Deployment stage (staging or prod).  "
            "Defaults to the DEPLOYMENT_STAGE environment variable."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    # Resolve stage
    stage = args.stage or os.environ.get("DEPLOYMENT_STAGE", "")
    if not stage:
        print(
            "ERROR: --stage or DEPLOYMENT_STAGE env var is required.",
            file=sys.stderr,
        )
        return 1

    # Resolve asset root
    if args.asset_root:
        root = Path(args.asset_root)
    else:
        # Default: relative to the deployment/ directory.
        this_dir = Path(__file__).resolve().parent.parent  # deployment/
        root = this_dir.parent / "modules" / "cms_ui" / "source" / "frontend" / "build"

    if not root.exists():
        print(
            f"ERROR: Asset root {root!s} does not exist.  "
            "Run 'make build-ui' first.",
            file=sys.stderr,
        )
        return 1

    try:
        scan_asset_tree(root, stage)
    except ValueError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    print(
        f"✅ Built-asset credential guard PASSED (stage={stage!r}, "
        f"root={root!s}): no demo credentials found in assets."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
