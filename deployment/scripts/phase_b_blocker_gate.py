#!/usr/bin/env python3
"""Phase B blocker gate — fail early if any precondition for external self-signup is unmet.

Runs every Phase B precondition in order and exits non-zero on the first failure.
Writes a Markdown gate report to the spec directory documenting the run.

BLOCKERS
--------
#1  Fail-open authz fix is DEPLOYED to prod (not just committed).
    Two halves, both required:
    (a) Source-tree grep: the fail-open OR patterns are absent from main_api/index.py.
    (b) Deployed Lambda: prod FleetAPIFunction LastModified >= fix commit authored time.
    Committed-but-not-deployed is exactly the failure mode this blocker exists to surface.

#2  Prod CloudFront distribution has a WAF WebACL attached.
    Queries live prod distribution config; passes if WebACLId is a non-empty WAFv2 ARN.

#3  Prod pool `cms.allow_self_signup` context flag is explicitly set to `true`.
    Reads deployment/cdk.context.json. Defaults false; must be set deliberately.

#4  Runtime brand/PII audit for prod is closed.
    Verifies issues/YYYY-MM-DD-cms-demo-external-exposure/summary.md exists with
    `Status: RESOLVED`. Reports "absent" (no issue directory) vs "present-but-unresolved"
    as distinct states — they need different remediation paths.

#5  UserPoolClient `WriteAttributes` must not expose authorization-bearing custom attributes.
    Queries the live UserPoolClient via cognito-idp:DescribeUserPoolClient. Passes
    when no `custom:*` is client-writable AND both `email` and `name` remain writable
    (the AmazonFederate-mapped attributes required for federated IdP sign-in — omitting
    them makes Cognito throw and locks federated users out). Automated 2026-09-02;
    previously text-only.

CREDENTIALS
-----------
    Requires the operator's AWS credentials with:
      - lambda:ListFunctions                (Blocker #1b)
      - cloudfront:GetDistributionConfig    (Blocker #2)
      - cognito-idp:ListUserPoolClients     (Blocker #5)
      - cognito-idp:DescribeUserPoolClient  (Blocker #5)
    Distribution ID and User Pool ID are read from env vars (PROD_DISTRIBUTION_ID,
    PROD_USER_POOL_ID) or from deployment/config/prod.env — never hardcoded, because this
    script ships in the public mirror. Optionally pin the pool's app client via
    PROD_USER_POOL_CLIENT_ID; if unset the check auto-discovers a single client.

USAGE
-----
    # From repo root:
    cd deployment && python3 scripts/phase_b_blocker_gate.py --stage prod

    # Override distribution ID and pool ID via env:
    PROD_DISTRIBUTION_ID=<id> PROD_USER_POOL_ID=<id> python3 scripts/phase_b_blocker_gate.py

SPEC
----
    Spec: .kiro/specs/2026-08-07-cms-account-provisioning-model/ (Group 6 + Fix Group)
    Decisions: decisions.md § 2026-08-07 "Phase B fail-open blocker checks deployed state"
    Decisions: decisions.md § 2026-08-10 "Phase B gains a fifth precondition"
    Decisions: decisions.md § 2026-09-02 "Fifth precondition automated in gate"
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
import io
import tokenize
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Paths (relative to the deployment/ directory, so scripts/ is one level down)
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent  # deployment/scripts/
_DEPLOYMENT_DIR = _HERE.parent           # deployment/
_REPO_ROOT = _DEPLOYMENT_DIR.parent      # repo root

# ---------------------------------------------------------------------------
# Import the Group-4 pre-deploy hook for Blocker #1b.
# Must paginate — prod has 91 Lambdas; the target sits at position 82,
# off page 1.  A bare list_functions() call reintroduces the bug that
# Fix Group 5a corrected.  Import, don't reimplement.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(_HERE))
from check_prod_fail_open_deployed import (  # noqa: E402
    get_latest_fleet_api_function,
    FIX_COMMIT_AUTHORED_TIME,
)

# ---------------------------------------------------------------------------
# Fail-open source-tree grep patterns (Blocker #1a).
# These should be absent from the source tree once commit d235fb31 is present.
# ---------------------------------------------------------------------------
_FAIL_OPEN_PATTERNS = [
    "or not user_groups",
    "or not user_fleet_ids",
]
_MAIN_API_PATH = Path("modules/cms_ui/source/handlers/main_api/index.py")

# ---------------------------------------------------------------------------
# Env-var names for resource IDs (never hardcoded — this file ships).
# ---------------------------------------------------------------------------
_ENV_DISTRIBUTION_ID = "PROD_DISTRIBUTION_ID"
_ENV_USER_POOL_ID = "PROD_USER_POOL_ID"
_ENV_USER_POOL_CLIENT_ID = "PROD_USER_POOL_CLIENT_ID"
_PROD_ENV_FILE = _DEPLOYMENT_DIR / "config" / "prod.env"

# ---------------------------------------------------------------------------
# Blocker #5 — UserPoolClient WriteAttributes rules.
# ---------------------------------------------------------------------------
# Minimum set that MUST remain client-writable so the AmazonFederate IdP can
# update its two mapped attributes (email -> EMAIL, name -> GIVEN_NAME) on
# federated sign-in. Cognito throws on IdP sign-in when a mapped attribute is
# not client-writable, which would lock every federated user out — over-restricting
# is as much a failure as under-restricting. Matches
# deployment/stacks/test_client_write_attributes.py _REQUIRED_WRITABLE.
_MIN_WRITABLE_ATTRS = frozenset({"email", "name"})

# Any attribute with this prefix is a custom attribute on the pool. None may be
# client-writable, because at least one (custom:driverId) is an authorization
# input consumed by main_api._classify_driver_self. See decisions.md 2026-08-10
# "Phase B gains a fifth precondition" for the full reasoning.
_CUSTOM_ATTR_PREFIX = "custom:"

# ---------------------------------------------------------------------------
# CDK context file location.
# ---------------------------------------------------------------------------
_CDK_CONTEXT_FILE = _DEPLOYMENT_DIR / "cdk.context.json"

# ---------------------------------------------------------------------------
# Issues directory glob pattern for brand/PII audit (Blocker #4).
# ---------------------------------------------------------------------------
_EXTERNAL_EXPOSURE_GLOB = "issues/*-cms-demo-external-exposure/summary.md"

# Accept BOTH shapes of a resolved status.
#
# The repo's documented convention (~/.kiro/steering/spec-workflow.md § "Issue Summary")
# is a heading followed by the verdict:
#
#     ## Status
#
#     RESOLVED
#
# The original regex here required a literal inline `Status: RESOLVED` line, which the
# convention does not produce — so this gate would have rejected every correctly
# formatted summary in the repo, including the two written on 2026-08-10. Found
# 2026-08-11 when the exposure audit's summary was written to the documented template
# and the gate reported "present-but-unresolved".
#
# A gate that demands a format the project does not use is indistinguishable from a
# gate that cannot be satisfied, so both forms are accepted. MITIGATED is deliberately
# NOT accepted: this blocker asks whether the exposure is closed, not managed.
_STATUS_RESOLVED_RE = re.compile(
    r"(?:^\s*Status:\s*RESOLVED\s*$)"          # inline form
    r"|(?:^\s*#{1,6}\s*Status\s*$\s*\n+\s*RESOLVED\b)",  # heading form (the convention)
    re.IGNORECASE | re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Env-var helpers
# ---------------------------------------------------------------------------

def _load_prod_env_file() -> dict[str, str]:
    """Parse deployment/config/prod.env into a dict.  Ignores comments and blanks."""
    result: dict[str, str] = {}
    if not _PROD_ENV_FILE.exists():
        return result
    for line in _PROD_ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _get_resource_id(env_key: str, prod_env: dict[str, str]) -> Optional[str]:
    """Return env var value from OS env, then from prod.env dict.  None if absent."""
    value = os.environ.get(env_key, "").strip()
    if not value:
        value = prod_env.get(env_key, "").strip()
    return value if value else None


# ---------------------------------------------------------------------------
# Blocker checks
# ---------------------------------------------------------------------------

def check_blocker_1_source_tree(repo_root: Path) -> tuple[str, str]:
    """Blocker #1a: check main_api/index.py for fail-open patterns in CODE.

    Returns (status, detail) where status is 'GREEN' or 'RED'.
    GREEN = all patterns absent from code (fix is in the tree).
    RED   = one or more patterns still present in code (fix not yet committed).

    STRING AND COMMENT TOKENS ARE SCRUBBED BEFORE MATCHING. The previous version
    excluded only lines whose first non-whitespace character was ``#``, which left
    docstrings matchable — and on 2026-09-01 that reported this blocker RED for a
    text reason rather than a security reason. ``index.py:1109`` contains the prose

        UNCONDITIONAL `or not user_groups` default that granted platform-admin

    inside a docstring *documenting that the fail-open had been removed*. The
    fail-open was genuinely gone; the check was matching its own documentation,
    and Phase B was blocked on it.

    Third instance of this trap in one day: the sim-api spec's Task 3.3 verify grep
    matched its own removal comments, and a comment in publish_product.py tripped a
    grep the same way. A check written in terms of the pattern it forbids will match
    the text explaining the ban. The check has to understand code structure.

    NOTE ON DUPLICATION — this logic also exists as
    ``deployment/aspects/provisioning_guards.py::_grep_fail_open_patched``. Having
    two implementations is what let this bug persist after one copy was fixed: the
    guards function returned GREEN while this one still reported RED. If you change
    the semantics here, change it there too, or better, extract a shared helper
    that carries no CDK import (this script must stay runnable without jsii).

    A tokenize failure fails CLOSED (RED), matching the missing-file case.
    """
    index_path = repo_root / _MAIN_API_PATH
    if not index_path.exists():
        return "RED", f"Source file not found: {_MAIN_API_PATH}"

    text = index_path.read_text()
    lines = text.splitlines()

    try:
        scrubbed = list(lines)
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                continue
            (srow, scol), (erow, ecol) = tok.start, tok.end
            for row in range(srow, erow + 1):
                idx = row - 1
                if idx >= len(scrubbed):
                    continue
                line = scrubbed[idx]
                lo = scol if row == srow else 0
                hi = ecol if row == erow else len(line)
                scrubbed[idx] = line[:lo] + (" " * max(0, hi - lo)) + line[hi:]
    except (tokenize.TokenError, IndentationError, SyntaxError) as e:
        return "RED", (
            f"Could not tokenize {_MAIN_API_PATH} ({type(e).__name__}: {e}); "
            f"failing closed rather than assuming the fix is present."
        )

    hits = []
    for lineno, scrubbed_line in enumerate(scrubbed, start=1):
        for pattern in _FAIL_OPEN_PATTERNS:
            if pattern in scrubbed_line:
                # Report the ORIGINAL line so the detail is readable.
                hits.append(f"{_MAIN_API_PATH}:{lineno}: {lines[lineno - 1].rstrip()}")

    if hits:
        detail = "Fail-open patterns still present in source tree:\n" + "\n".join(f"  {h}" for h in hits)
        return "RED", detail
    return "GREEN", "Fail-open patterns absent from source code (docstrings/comments excluded)"


def check_blocker_1_deployed(stage: str) -> tuple[str, str]:
    """Blocker #1b: prod FleetAPIFunction LastModified >= fix commit authored time.

    Calls check_prod_fail_open_deployed.get_latest_fleet_api_function() which
    paginates (prod has 91 Lambdas; target at position 82).
    Returns (status, detail).
    """
    if stage != "prod":
        return "SKIP", f"Deployed-Lambda check only applies to prod (got stage={stage!r})"

    result = get_latest_fleet_api_function()
    if result is None:
        return "RED", (
            "No cms-prod-ui-FleetAPIFunction* function found or Lambda API error. "
            "Check AWS credentials and that the prod stack is deployed."
        )

    func_name, last_modified = result
    # Ensure timezone-aware comparison.
    if last_modified.tzinfo is None:
        last_modified = last_modified.replace(tzinfo=datetime.timezone.utc)

    fix_time = FIX_COMMIT_AUTHORED_TIME
    if last_modified >= fix_time:
        detail = (
            f"Function {func_name!r} LastModified {last_modified.isoformat()} "
            f">= fix commit {fix_time.isoformat()} — fix is deployed"
        )
        return "GREEN", detail
    else:
        delta_seconds = int((fix_time - last_modified).total_seconds())
        detail = (
            f"Function {func_name!r} LastModified {last_modified.isoformat()} "
            f"is {delta_seconds}s BEFORE fix commit {fix_time.isoformat()}.\n"
            f"  The fix (commit d235fb31) is committed but NOT YET DEPLOYED to prod.\n"
            f"  Remediation: deploy cms-prod-ui (make phase1 DEPLOYMENT_STAGE=prod)."
        )
        return "RED", detail


def check_blocker_2_waf(distribution_id: Optional[str]) -> tuple[str, str]:
    """Blocker #2: prod CloudFront distribution has a WAF WebACL attached.

    Returns (status, detail).
    """
    if not distribution_id:
        return "RED", (
            f"Distribution ID not set. Set {_ENV_DISTRIBUTION_ID} env var or add it to "
            f"deployment/config/prod.env (publish-excluded)."
        )

    try:
        cf_client = boto3.client("cloudfront", region_name="us-east-1")
        resp = cf_client.get_distribution_config(Id=distribution_id)
        web_acl_id = resp["DistributionConfig"].get("WebACLId", "")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        msg = exc.response["Error"]["Message"]
        return "RED", f"CloudFront API error ({code}): {msg}"
    except Exception as exc:  # noqa: BLE001
        return "RED", f"Unexpected error querying CloudFront: {exc}"

    if web_acl_id and web_acl_id.startswith("arn:aws:wafv2:"):
        return "GREEN", f"WAF WebACL attached: {web_acl_id}"
    elif web_acl_id:
        return "RED", (
            f"WebACLId is set but does not look like a WAFv2 ARN: {web_acl_id!r}.\n"
            f"  Expected an ARN starting with 'arn:aws:wafv2:'. "
            f"Remediation: deploy Group 7's WAF stack (cms-<stage>-ui-waf)."
        )
    else:
        return "RED", (
            "Prod CloudFront distribution has no WAF WebACL (WebACLId is empty).\n"
            "  Remediation: deploy Group 7's WAF stack (cms-<stage>-ui-waf), "
            "then re-run this gate."
        )


def check_blocker_3_signup_flag(stage: str) -> tuple[str, str]:
    """Blocker #3: cms.allow_self_signup context flag is explicitly true.

    Reads deployment/cdk.context.json.  The flag defaults to false; the operator
    must set it deliberately after all other blockers are GREEN.
    Returns (status, detail).
    """
    import json  # noqa: PLC0415 — lazy import to keep top-level imports minimal

    if not _CDK_CONTEXT_FILE.exists():
        return "RED", f"cdk.context.json not found at {_CDK_CONTEXT_FILE}"

    try:
        ctx = json.loads(_CDK_CONTEXT_FILE.read_text())
    except json.JSONDecodeError as exc:
        return "RED", f"Failed to parse cdk.context.json: {exc}"

    value = ctx.get("cms.allow_self_signup")
    if value is True or str(value).lower() == "true":
        return "GREEN", "cms.allow_self_signup is explicitly set to true in cdk.context.json"
    elif value is None:
        return "RED", (
            "cms.allow_self_signup is NOT set in cdk.context.json (key absent — defaults false).\n"
            "  Remediation: set cms.allow_self_signup=true in cdk.context.json after all other "
            "blockers are GREEN and the operator deliberately enables external signup."
        )
    else:
        return "RED", (
            f"cms.allow_self_signup is set to {value!r} (not true).\n"
            "  Remediation: set the value to true (boolean, not string)."
        )


def check_blocker_4_brand_audit(repo_root: Path) -> tuple[str, str]:
    """Blocker #4: brand/PII audit for prod is closed.

    Looks for issues/YYYY-MM-DD-cms-demo-external-exposure/summary.md with
    `Status: RESOLVED`.  Reports 'absent' vs 'present-but-unresolved' distinctly.
    Returns (status, detail).
    """
    matches = sorted(repo_root.glob(_EXTERNAL_EXPOSURE_GLOB))

    if not matches:
        return "RED", (
            "No issues/*-cms-demo-external-exposure/summary.md found.\n"
            "  Status: ABSENT — the issue directory has not been created yet.\n"
            "  Remediation: complete the runtime brand/PII audit for prod "
            "(backlog row 'Demo external exposure'), create the issue directory, "
            "and write summary.md with 'Status: RESOLVED' once the audit is clean."
        )

    # Check each match for Status: RESOLVED
    resolved = []
    unresolved = []
    for summary_path in matches:
        text = summary_path.read_text()
        if _STATUS_RESOLVED_RE.search(text):
            resolved.append(summary_path)
        else:
            unresolved.append(summary_path)

    if resolved:
        paths = ", ".join(str(p.relative_to(repo_root)) for p in resolved)
        return "GREEN", f"Brand/PII audit closed (Status: RESOLVED): {paths}"

    # Present but not resolved
    paths = ", ".join(str(p.relative_to(repo_root)) for p in unresolved)
    return "RED", (
        f"Brand/PII audit issue exists but is NOT resolved: {paths}\n"
        "  Status: PRESENT-BUT-UNRESOLVED — the issue directory and summary.md exist, "
        "but summary.md does not contain 'Status: RESOLVED'.\n"
        "  Remediation: complete the audit, verify prod content, and update summary.md."
    )


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _find_single_client_id(user_pool_id: str) -> tuple[Optional[str], str]:
    """List app clients on the pool; return (client_id, note).

    Called only when PROD_USER_POOL_CLIENT_ID is unset — auto-discovery is a
    friendliness feature, not a security feature. Refuses to pick when there is
    ambiguity: 0 clients or >1 clients returns (None, <reason>) so the caller can
    surface a RED with a clear remediation.

    The CMS pool has exactly one app client by design (see ui_stack.py
    CMSUserPoolClient). A future multi-client pool must pin the client explicitly
    via env so the gate is checking the SignUp surface, not an admin client.
    """
    try:
        cognito = boto3.client("cognito-idp")
        # MaxResults=60 is the API cap; a CMS-shaped pool has 1-2 clients.
        resp = cognito.list_user_pool_clients(UserPoolId=user_pool_id, MaxResults=60)
        clients = resp.get("UserPoolClients", [])
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        msg = exc.response["Error"]["Message"]
        return None, f"Cognito API error listing clients ({code}): {msg}"
    except Exception as exc:  # noqa: BLE001
        return None, f"Unexpected error listing user pool clients: {exc}"

    if not clients:
        return None, f"No app clients found on pool {user_pool_id!r}."
    if len(clients) > 1:
        names = ", ".join(c.get("ClientName", "<unnamed>") for c in clients)
        return None, (
            f"Multiple app clients found on pool {user_pool_id!r} ({len(clients)}): "
            f"{names}. Cannot disambiguate — set {_ENV_USER_POOL_CLIENT_ID} env var "
            f"to the ClientId that hosts SignUp so the gate checks the right client."
        )
    return clients[0]["ClientId"], "ok"


def check_blocker_5_write_attributes(
    stage: str, user_pool_id: Optional[str]
) -> tuple[str, str]:
    """Blocker #5: UserPoolClient WriteAttributes must not expose authorization-bearing
    custom attributes.

    Cognito's default (WriteAttributes unset) lets a self-registered user set
    custom:driverId in their own SignUp call — verified empirically 2026-08-10 —
    and thereby obtain driver-self access via main_api._classify_driver_self
    without triggering any group-based control.

    GREEN when WriteAttributes contains no `custom:*` and both `email` and `name`
    remain writable. The two directions are asserted separately because getting
    this wrong either way is a real failure:
      * a custom attribute writable at signup is a privilege-escalation surface;
      * an unmapped Federate attribute (email or name) missing here makes Cognito
        throw on IdP sign-in and locks out every federated user.

    Mirrors the synth-based assertions in deployment/stacks/test_client_write_attributes.py.
    That test verifies the CDK template; this gate verifies the deployed pool. Both
    matter, because a deploy lag between merge and rollout is exactly the window this
    entire gate exists to close.

    Returns (status, detail).
    """
    if stage != "prod":
        return "SKIP", (
            f"Live-pool WriteAttributes check only applies to prod "
            f"(got stage={stage!r}). Staging is asserted at synth by "
            f"stacks/test_client_write_attributes.py."
        )

    if not user_pool_id:
        return "RED", (
            f"User Pool ID not set. Set {_ENV_USER_POOL_ID} env var or add it to "
            f"deployment/config/prod.env (publish-excluded)."
        )

    # Explicit client ID beats auto-discovery. See _find_single_client_id.
    client_id = os.environ.get(_ENV_USER_POOL_CLIENT_ID, "").strip()
    if not client_id:
        discovered, reason = _find_single_client_id(user_pool_id)
        if not discovered:
            return "RED", reason
        client_id = discovered

    try:
        cognito = boto3.client("cognito-idp")
        resp = cognito.describe_user_pool_client(
            UserPoolId=user_pool_id, ClientId=client_id
        )
        client_props = resp.get("UserPoolClient", {})
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        msg = exc.response["Error"]["Message"]
        return "RED", f"Cognito API error describing client {client_id!r} ({code}): {msg}"
    except Exception as exc:  # noqa: BLE001
        return "RED", f"Unexpected error describing user pool client: {exc}"

    # An UNSET WriteAttributes is the vulnerable state — the Cognito default lets
    # SignUp write the pool's writable custom attributes. Empirically confirmed
    # live 2026-08-10. See ui_stack.py:1457 for the CDK-side fix (commit 2dab2dca).
    if "WriteAttributes" not in client_props:
        return "RED", (
            f"WriteAttributes is UNSET on client {client_id!r} (pool {user_pool_id!r}).\n"
            f"  The Cognito default lets a self-registered user write custom attributes "
            f"in their own SignUp call — including custom:driverId, which grants "
            f"driver-self access via main_api._classify_driver_self.\n"
            f"  Remediation: deploy the CDK change that restricts WriteAttributes to "
            f"{{email, name}} — already present in ui_stack.py:1457 as of commit "
            f"2dab2dca (2026-08-10). If the fix is committed, deploy cms-prod-ui."
        )

    write_attrs = set(client_props.get("WriteAttributes", []))

    custom_writable = sorted(a for a in write_attrs if a.startswith(_CUSTOM_ATTR_PREFIX))
    if custom_writable:
        return "RED", (
            f"Custom attributes are client-writable on {client_id!r}: "
            f"{custom_writable}.\n"
            f"  Any of these can be set in a SignUp call. custom:driverId in "
            f"particular is an authorization input — a self-registered user setting "
            f"it obtains driver-self access with no group at all, bypassing every "
            f"group-based control this spec builds.\n"
            f"  Remediation: restrict WriteAttributes to standard attributes only "
            f"({sorted(_MIN_WRITABLE_ATTRS)})."
        )

    missing_required = _MIN_WRITABLE_ATTRS - write_attrs
    if missing_required:
        return "RED", (
            f"WriteAttributes is OVER-restricted on {client_id!r}: missing "
            f"{sorted(missing_required)}.\n"
            f"  The AmazonFederate IdP maps email -> EMAIL and name -> GIVEN_NAME. "
            f"Cognito throws on IdP sign-in when a mapped attribute is not "
            f"client-writable, which would lock every federated user out.\n"
            f"  Remediation: keep email and name in WriteAttributes. Current set: "
            f"{sorted(write_attrs)}."
        )

    return "GREEN", (
        f"Client {client_id!r} WriteAttributes={sorted(write_attrs)} — no "
        f"custom:* attribute is client-writable, and Federate-mapped attributes "
        f"({sorted(_MIN_WRITABLE_ATTRS)}) remain writable."
    )


def _write_report(
    spec_dir: Path,
    stage: str,
    run_timestamp: str,
    results: list[tuple[str, str, str, str]],
    additional_precondition: str,
) -> Path:
    """Write a Markdown gate report and return its path.

    results items: (blocker_label, half_label, status, detail)
    """
    date_slug = run_timestamp[:10]  # YYYY-MM-DD
    report_path = spec_dir / f"phase-b-gate-{date_slug}.md"

    lines = [
        f"# Phase B Blocker Gate Report — {run_timestamp}",
        "",
        f"Stage: `{stage}`  ",
        f"Run at: {run_timestamp} (UTC)  ",
        "",
        "## Summary",
        "",
    ]

    overall = "GREEN" if all(r[2] == "GREEN" or r[2] == "SKIP" for r in results) else "RED"
    lines.append(f"**Overall gate: {overall}**")
    lines.append("")
    lines.append("| # | Check | Status | Notes |")
    lines.append("|---|-------|--------|-------|")
    for blocker, half, status, detail in results:
        first_line = detail.splitlines()[0] if detail else ""
        status_md = f"**{status}**"
        lines.append(f"| {blocker} | {half} | {status_md} | {first_line} |")

    lines += [
        "",
        "## Blocker Details",
        "",
    ]

    for blocker, half, status, detail in results:
        lines.append(f"### {blocker} — {half}")
        lines.append(f"**Status: {status}**")
        lines.append("")
        for dline in detail.splitlines():
            lines.append(dline)
        lines.append("")

    # Optional un-automated-precondition section — omit entirely when the string is empty
    # rather than emitting a stale "not yet automated" heading with no content under it.
    # #5 was automated 2026-09-02; future preconditions can re-populate this by passing
    # a non-empty string.
    if additional_precondition.strip():
        lines += [
            "## Additional Precondition (not yet automated)",
            "",
            additional_precondition,
            "",
        ]

    lines += [
        "## Gate Conclusion",
        "",
    ]

    red_blockers = [r for r in results if r[2] == "RED"]
    if red_blockers:
        lines.append("**Gate is RED.** Phase B implementation cannot proceed until all blockers are GREEN.")
        lines.append("")
        lines.append("Failing blockers:")
        for blocker, half, _, _ in red_blockers:
            lines.append(f"- {blocker} — {half}")
    else:
        lines.append("**Gate is GREEN.** All Phase B preconditions verified. Phase B implementation may proceed.")

    lines.append("")
    report_path.write_text("\n".join(lines) + "\n")
    return report_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_gate(stage: str, repo_root: Path) -> int:
    """Execute all four blocker checks and write the report.

    Returns 0 if all checks are GREEN (or SKIP), non-zero if any are RED.
    """
    run_timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Load publish-excluded config for resource IDs.
    prod_env = _load_prod_env_file()
    distribution_id = _get_resource_id(_ENV_DISTRIBUTION_ID, prod_env)
    # user_pool_id is loaded for Blocker #3 context (not directly used in the check,
    # but captured here for future use and report transparency).
    user_pool_id = _get_resource_id(_ENV_USER_POOL_ID, prod_env)

    print(f"Phase B blocker gate — stage={stage!r}, run_at={run_timestamp}", flush=True)
    print(f"Distribution ID source: {'env' if os.environ.get(_ENV_DISTRIBUTION_ID) else 'prod.env' if distribution_id else 'NOT SET'}")
    print(f"User Pool ID source: {'env' if os.environ.get(_ENV_USER_POOL_ID) else 'prod.env' if user_pool_id else 'NOT SET'}")
    print()

    results: list[tuple[str, str, str, str]] = []

    # --- Blocker #1: fail-open fix ---
    print("Checking Blocker #1a — Source-tree grep ...", end=" ", flush=True)
    status_1a, detail_1a = check_blocker_1_source_tree(repo_root)
    print(status_1a)
    results.append(("#1", "Source-tree (committed)", status_1a, detail_1a))

    if stage == "prod":
        print("Checking Blocker #1b — Deployed Lambda LastModified ...", end=" ", flush=True)
        status_1b, detail_1b = check_blocker_1_deployed(stage)
        print(status_1b)
        results.append(("#1", "Deployed Lambda (runtime)", status_1b, detail_1b))

    # --- Blocker #2: WAF ---
    print("Checking Blocker #2 — Prod CloudFront WAF ...", end=" ", flush=True)
    status_2, detail_2 = check_blocker_2_waf(distribution_id)
    print(status_2)
    results.append(("#2", "Prod CloudFront WAF WebACL", status_2, detail_2))

    # --- Blocker #3: self-signup context flag ---
    print("Checking Blocker #3 — cdk.context.json self-signup flag ...", end=" ", flush=True)
    status_3, detail_3 = check_blocker_3_signup_flag(stage)
    print(status_3)
    results.append(("#3", "cdk.context.json cms.allow_self_signup", status_3, detail_3))

    # --- Blocker #4: brand/PII audit ---
    print("Checking Blocker #4 — Brand/PII audit summary ...", end=" ", flush=True)
    status_4, detail_4 = check_blocker_4_brand_audit(repo_root)
    print(status_4)
    results.append(("#4", "Brand/PII audit (issues dir)", status_4, detail_4))

    # --- Blocker #5: UserPoolClient WriteAttributes ---
    if stage == "prod":
        print("Checking Blocker #5 — UserPoolClient WriteAttributes ...", end=" ", flush=True)
        status_5, detail_5 = check_blocker_5_write_attributes(stage, user_pool_id)
        print(status_5)
        results.append(("#5", "UserPoolClient WriteAttributes", status_5, detail_5))

    print()

    # No un-automated preconditions remain: #5 was automated 2026-09-02. Kept as an
    # empty string so _write_report omits the section rather than emitting an empty
    # "not yet automated" heading. Future preconditions surface here again.
    additional = ""

    # --- Write report ---
    spec_dir = repo_root / ".kiro" / "specs" / "2026-08-07-cms-account-provisioning-model"
    spec_dir.mkdir(parents=True, exist_ok=True)
    report_path = _write_report(spec_dir, stage, run_timestamp, results, additional)
    print(f"Gate report written: {report_path.relative_to(repo_root)}")
    print()

    # --- Print per-blocker summary ---
    red_results = [r for r in results if r[2] == "RED"]
    if red_results:
        print("GATE RED — the following blockers must be resolved before Phase B proceeds:")
        for blocker, half, _, detail in red_results:
            first_line = detail.splitlines()[0]
            print(f"  {blocker} [{half}]: {first_line}")
        print()
        print("Phase B implementation is HALTED. Hand back to PO/user with blocker details above.")
        return 1

    print("GATE GREEN — all Phase B preconditions verified. Phase B implementation may proceed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase B blocker gate — verify all preconditions before Phase B implementation"
    )
    parser.add_argument(
        "--stage",
        default="prod",
        help="Deployment stage (default: prod). Only prod performs the deployed-Lambda check.",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repo root directory (default: two levels up from this script).",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _REPO_ROOT
    return run_gate(stage=args.stage, repo_root=repo_root)


if __name__ == "__main__":
    sys.exit(main())
