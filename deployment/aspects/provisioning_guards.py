"""Synth-time gate for Phase B external self-signup provisioning preconditions.

Raised at CDK synth time when ``enable_external_self_signup`` is True and the
deployment targets ``prod`` but the four mandatory pre-conditions have not been
verified.  Unrecognised stages also fail closed: an unknown stage is treated as
prod because guessing wrong on a production variant is a worse failure mode than
blocking a correctly-named dev stage.

Design follows ``_require_driver_self_guard()`` in ``deployment/stacks/ui_stack.py``
exactly:

- Denylist of known non-prod stages; everything else fails closed.
- Each un-met precondition raises ``ValueError`` with a message that names:
  (a) which precondition failed, (b) the concrete consequence of proceeding without
  it, and (c) the action required to close the blocker.
- Helper callables that read *repo state only* (no boto3/runtime calls) so the
  guard runs during offline synth with no AWS credentials required.

The caller (``ui_stack.py``, Group 8 of the provisioning-model spec) is responsible
for passing the right values in.  This module is *pure functions + constants* — no
CDK constructs, no imports from aws_cdk.

Lesson from the 2026-08-05 ``_DIGEST_TOKEN_RE`` silent-failure post-mortem: a guard
tested only against clean input is indistinguishable from no guard.  The test file
therefore includes a dedicated test for every failing precondition, asserting both
that ``ValueError`` is raised **and** that the message identifies which blocker
failed.
"""

from __future__ import annotations

import glob
import os
import tokenize
import io
import re

# ---------------------------------------------------------------------------
# Stage denylist — mirrors _GUARD_OPTIONAL_STAGES in ui_stack.py.
# An unrecognised or misspelled stage is NOT in this set and therefore fails
# closed.  The failure mode of guessing wrong is a production deployment that
# bypasses all Phase B preconditions.
# ---------------------------------------------------------------------------
_GUARD_OPTIONAL_STAGES: frozenset[str] = frozenset(
    {"", "dev", "development", "local", "test"}
)

# The two grep targets that must be absent from non-comment lines in
# main_api/index.py for the fail-open authz to be considered patched.
_FAIL_OPEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?!#).*\bor not user_groups\b"),
    re.compile(r"^\s*(?!#).*\bor not user_fleet_ids\b"),
)

# Relative (from repo root) path to the handler under inspection.
_MAIN_API_RELPATH = os.path.join(
    "modules", "cms_ui", "source", "handlers", "main_api", "index.py"
)

# Glob pattern for the brand/PII audit summary that must exist and be resolved.
_BRAND_AUDIT_GLOB = os.path.join(
    "issues", "2*-cms-demo-external-exposure", "summary.md"
)
_BRAND_AUDIT_RESOLVED_MARKER = "Status: RESOLVED"


# ---------------------------------------------------------------------------
# Repository-state helpers (no network / no boto3)
# ---------------------------------------------------------------------------

# Matches the non-formatting IoT SQL idiom timestamp("yyyy") / timestamp('MM') etc.
# IoT SQL's timestamp() takes NO argument; parse_time("<fmt>", timestamp()) formats.
_IOT_TIMESTAMP_FMT_RE = re.compile(r'timestamp\(\s*[\\\'"]+\s*[yMdHmsS]{1,4}\s*[\\\'"]+\s*\)')


def _repo_root() -> str:
    """Return the repository root by walking up from this file.

    Looks for the first ancestor directory that contains ``deployment/`` as a
    sub-directory.  Falls back to the current working directory so tests that
    ``monkeypatch`` ``os.getcwd`` still work.
    """
    candidate = os.path.dirname(os.path.abspath(__file__))
    # Walk upward at most 5 levels.
    for _ in range(5):
        if os.path.isdir(os.path.join(candidate, "deployment")):
            return candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return os.getcwd()


def _grep_fail_open_patched(repo_root: str | None = None) -> bool:
    """Return True iff the fail-open authz defaults have been removed from
    ``main_api/index.py``.

    Reads the source tree only — no runtime / boto3 calls.  The check succeeds
    (returns True) when **neither** of the two fail-open expressions
    (``or not user_groups`` / ``or not user_fleet_ids``) appears on a non-comment
    code line in the file.

    A comment such as::

        # `is_admin = ... or not user_groups` default DID treat as platform-admin

    does NOT count as a match — the pattern anchors exclude lines whose first
    non-whitespace character is ``#``.

    Returns False when:
    - the handler file does not exist (file missing → treat as un-patched), OR
    - any forbidden pattern matches a non-comment line.
    """
    root = repo_root or _repo_root()
    handler_path = os.path.join(root, _MAIN_API_RELPATH)
    if not os.path.isfile(handler_path):
        return False
    with open(handler_path, encoding="utf-8") as fh:
        source = fh.read()

    # Scrub STRING and COMMENT tokens before matching.
    #
    # The regexes anchor on `^\s*(?!#)`, which excludes `#` comments but NOT
    # docstrings. On 2026-09-01 that made this blocker RED for a text reason
    # rather than a security reason: `index.py:1109` contains the prose
    #
    #     UNCONDITIONAL `or not user_groups` default that granted platform-admin
    #
    # inside a docstring *documenting that the fail-open was removed*. The
    # fail-open was genuinely gone; the check was matching its own documentation.
    #
    # That is the third instance of this trap in one day — Task 3.3's verify grep
    # in the sim-api spec matched its own removal comments, and a comment I wrote
    # in publish_product.py tripped a grep the same way. A check written in terms
    # of the pattern it forbids will match the text explaining the ban, so the
    # check has to understand code structure rather than lines of text.
    #
    # Tokenizing is exact where a regex cannot be: it knows what is a string.
    # A tokenize failure is treated as un-patched (fail closed), consistent with
    # the missing-file case above.
    try:
        scrubbed_lines: list[str] = source.splitlines()
        readline = io.StringIO(source).readline
        for tok in tokenize.generate_tokens(readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                continue
            (srow, scol), (erow, ecol) = tok.start, tok.end
            for row in range(srow, erow + 1):
                idx = row - 1
                if idx >= len(scrubbed_lines):
                    continue
                line = scrubbed_lines[idx]
                lo = scol if row == srow else 0
                hi = ecol if row == erow else len(line)
                scrubbed_lines[idx] = line[:lo] + (" " * max(0, hi - lo)) + line[hi:]
    except (tokenize.TokenError, IndentationError, SyntaxError) as e:
        print(f"⚠ _grep_fail_open_patched: could not tokenize {handler_path}: "
              f"{type(e).__name__}: {e} — failing closed")
        return False

    for line in scrubbed_lines:
        for pattern in _FAIL_OPEN_PATTERNS:
            if pattern.search(line):
                return False
    return True


def _check_brand_audit_summary(repo_root: str | None = None) -> bool:
    """Return True iff a resolved brand/PII audit summary exists in ``issues/``.

    Looks for any file matching::

        issues/2*-cms-demo-external-exposure/summary.md

    and returns True only if that file contains the line ``Status: RESOLVED``.

    Returns False when:
    - no matching file exists (the audit issue has not been filed yet), OR
    - the matching file exists but does not contain ``Status: RESOLVED``.

    Note: as of spec creation (2026-08-07) the brand-audit issue does **not**
    exist yet.  This function therefore returns False today, and the corresponding
    test asserts that False path explicitly.
    """
    root = repo_root or _repo_root()
    pattern = os.path.join(root, _BRAND_AUDIT_GLOB)
    matches = glob.glob(pattern)
    for path in matches:
        try:
            with open(path, encoding="utf-8") as fh:
                contents = fh.read()
            if _BRAND_AUDIT_RESOLVED_MARKER in contents:
                return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------------
# Main guard
# ---------------------------------------------------------------------------

def _require_external_signup_config(
    *,
    stage_name: str,
    enable_external_signup: bool,
    external_group: str,
    external_fleet_ids: str,
) -> None:
    """Fail synth when Phase B is enabled but its guest scoping is incomplete.

    Two distinct misconfigurations, and they fail in opposite directions:

    1. **Group is `fleet-viewer`.** Despite the name, `fleet-viewer` is an UNSCOPED
       global-read role in ``main_api`` (``has_unscoped_access = is_admin or
       is_viewer``). Assigning it to self-registered external users — which the spec
       originally specified — grants every internet registrant read access to every
       fleet. This guard refuses it outright rather than trusting the config to be
       right, because the failure is silent and the blast radius is the whole fleet
       estate. See ``decisions.md`` 2026-08-10.

    2. **Fleet scope is empty.** ``fleet-guest`` is scoped, so with no
       ``custom:fleetIds`` the account is fail-closed but can see nothing — a
       registration flow that produces unusable accounts. Safe, but pointless, and
       far cheaper to catch here than from a confused external user.

    Dev stages are exempt; an unrecognised stage fails closed.
    """
    if not enable_external_signup:
        return

    stage = (stage_name or "").strip().lower()
    if stage in _GUARD_OPTIONAL_STAGES:
        return

    group = (external_group or "").strip()
    fleets = (external_fleet_ids or "").strip()

    if group in ("fleet-viewer", "fleet-operator", "platform-admin"):
        raise ValueError(
            f"EXTERNAL_SELF_SIGNUP_GROUP is {group!r} for DEPLOYMENT_STAGE={stage!r}.\n"
            "Self-registered external users must NOT receive an unscoped or writing "
            "role. `fleet-viewer` in particular reads as least-privileged but grants "
            "UNSCOPED cross-fleet read in main_api, so this would give every internet "
            "registrant access to all fleet data "
            "(issues/2026-08-10-cms-demo-external-exposure/).\n"
            "Fix: set EXTERNAL_SELF_SIGNUP_GROUP=fleet-guest."
        )

    if not group:
        raise ValueError(
            f"cms.enable_external_self_signup is true for DEPLOYMENT_STAGE={stage!r} "
            "but EXTERNAL_SELF_SIGNUP_GROUP is empty.\n"
            "A self-registered user would be left GROUPLESS, which every main_api "
            "route denies once the fail-open fix is deployed — registration would "
            "produce dead accounts.\n"
            "Fix: set EXTERNAL_SELF_SIGNUP_GROUP=fleet-guest."
        )

    if not fleets:
        raise ValueError(
            f"EXTERNAL_SELF_SIGNUP_FLEET_IDS is empty for DEPLOYMENT_STAGE={stage!r} "
            f"while external self-signup is enabled with group {group!r}.\n"
            "fleet-guest is a SCOPED role — it reads only the fleets named in "
            "custom:fleetIds — so with no scope every self-registered user sees "
            "nothing at all.\n"
            "Fix: set EXTERNAL_SELF_SIGNUP_FLEET_IDS to the purpose-built public demo "
            "fleet (e.g. FLEET-DEMO-PUBLIC), and make sure that fleet exists."
        )


def _require_internal_provisioning_config(
    *,
    stage_name: str,
    enable_internal_auto: bool,
    idp_provider_name: str,
    auto_assign_group: str,
    allow_unscoped_read_group: bool = False,
) -> None:
    """Fail synth when Phase A is enabled but its identity config is incomplete.

    Closes security review Cycle 1 Suggestion 2 (2026-08-10). Without this, an operator who
    turns the gate on but does not export ``INTERNAL_IDP_PROVIDER_NAME`` deploys a trigger
    whose internal-IdP predicate can never match: every real federated sign-in silently
    takes the handler's no-op path. The failure is invisible — the Lambda succeeds, emits
    ``outcome=noop``, and returns the event \u2014 while every new employee lands **groupless**.

    Which way that fails depends on state we do not control, and both outcomes are bad:
    with the ``Fail-open authz`` fix deployed a groupless admin is locked out of every
    ``main_api`` route; without it, groupless is treated as ``platform-admin``, which is the
    P0 this whole initiative exists to eliminate.

    An empty provider name is never a legitimate configuration, so raising at synth is
    strictly better than discovering it from a sign-in. Dev stages are exempt via
    ``_GUARD_OPTIONAL_STAGES`` so local synth without stage config still works; an
    unrecognised stage fails closed, same as ``_require_provisioning_guards``.
    """
    if not enable_internal_auto:
        return

    stage = (stage_name or "").strip().lower()
    if stage in _GUARD_OPTIONAL_STAGES:
        return

    missing = [
        name
        for name, value in (
            ("INTERNAL_IDP_PROVIDER_NAME", idp_provider_name),
            ("INTERNAL_AUTO_ASSIGN_GROUP", auto_assign_group),
        )
        if not (value or "").strip()
    ]
    if missing:
        raise ValueError(
            f"cms.enable_internal_auto_provisioning is true for "
            f"DEPLOYMENT_STAGE={stage!r} but {' and '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} empty or unset.\n"
            "The provisioning trigger would deploy and then silently no-op on every "
            "federated sign-in, leaving each new user GROUPLESS — which is either a "
            "lockout (with the fail-open authz fix deployed) or an unintended "
            "platform-admin (without it). Neither is an acceptable default.\n"
            "Fix: deploy via the canonical Make target, which exports these from "
            "config/<stage>.env — e.g. `make phase1 DEPLOYMENT_STAGE=<stage>`. For a bare "
            "`cdk` invocation, export them yourself.\n"
            "To deploy without internal auto-provisioning, leave "
            "CMS_ENABLE_INTERNAL_AUTO_PROVISIONING unset instead of enabling the gate "
            "with no identity config."
        )

    # ------------------------------------------------------------------ S5 ----
    # Closes security review Cycle 1 Suggestion 5 (2026-08-11). The check above proves the
    # group is SET; this proves it is not a catastrophic choice.
    #
    # `INTERNAL_AUTO_ASSIGN_GROUP=fleet-viewer` would grant UNSCOPED cross-fleet read
    # (`has_unscoped_access = is_admin or is_viewer` in main_api) to every identity the
    # configured IdP authenticates. For this deployment that is every Amazon employee; for a
    # customer forking this template it is typically every employee of the company. A typo
    # would silently hand global read to ~1.5M people, and nothing downstream would complain
    # because the config is internally consistent.
    #
    # `_require_external_signup_config` already refuses exactly this for the external group.
    # The asymmetry was the defect: the internal path is the one with the LARGER eligible
    # population, so it needed the check more, not less.
    #
    # `platform-admin` is deliberately NOT refused here — it is the intended value for this
    # deployment (9 named administrators via AmazonFederate) and refusing it would break the
    # working prod configuration. The risk it carries is a scope decision for the operator,
    # already called out in spec.md's OQ1 discussion, not a misconfiguration.
    group = (auto_assign_group or "").strip()
    if group == "fleet-viewer" and not allow_unscoped_read_group:
        raise ValueError(
            f"INTERNAL_AUTO_ASSIGN_GROUP is {group!r} for DEPLOYMENT_STAGE={stage!r}.\n"
            "`fleet-viewer` reads as a least-privileged name but grants UNSCOPED "
            "cross-fleet read in main_api (has_unscoped_access = is_admin or is_viewer), "
            "so every identity your IdP authenticates would get global read over every "
            "fleet's data. On this deployment the eligible population is every Amazon "
            "employee; on a customer fork it is typically every employee.\n"
            "Fix: use a SCOPED group (`fleet-guest` for read-only scoped access, or "
            "`fleet-operator` for per-fleet write) and set custom:fleetIds, or use "
            "`platform-admin` if the eligible population really is your administrators.\n"
            "If global read for every authenticated employee is genuinely intended, set "
            "INTERNAL_ALLOW_UNSCOPED_READ_GROUP=true to acknowledge it explicitly."
        )


def _require_signup_flag_coupled(
    *,
    stage_name: str,
    signup_flag: bool,
    enable_external_signup: bool,
) -> None:
    """Refuse a pool that accepts ``SignUp`` with none of the provisioning wiring behind it.

    Closes security review Cycle 1 Suggestion 4 (2026-08-11).

    Two context flags control external registration and they are independently settable:

      ``cms.allow_self_signup``            -> pool ``self_sign_up_enabled`` (Cognito ACCEPTS
                                              ``SignUp`` API calls)
      ``cms.enable_external_self_signup``  -> the pre-sign-up denylist trigger, the
                                              PostConfirmation external branch that assigns
                                              the scoped guest group, and the Phase B
                                              precondition guards

    Setting only the first opens public registration with **no denylist, no group assignment,
    and no scope**. Every resulting account is groupless.

    That is not exploitable today — the ``Fail-open authz`` fix (``d235fb31``) means a
    groupless caller is denied everywhere rather than treated as ``platform-admin``. The
    reason to fail closed anyway is that it is only safe *because of a patch elsewhere in the
    codebase*: if the fail-open default ever regresses on a later change, every account that
    self-registered during the window becomes retroactively exploitable. A configuration whose
    safety depends on a distant invariant holding forever is one an operator should not be
    able to reach by setting a single flag.

    The narrower alternative — deriving ``self_sign_up_enabled`` from
    ``cms.enable_external_self_signup`` alone, so one flag cannot disagree with the other —
    is the better end state and is a filed follow-on. It is not done here because the two
    flags are read in different places and collapsing them is a behaviour change to the
    Phase B enablement path, which is currently mid-flight.

    Scope: enforced on **prod and unrecognised stages**, exempt on the dev-like stages in
    ``_GUARD_OPTIONAL_STAGES`` and on ``staging``.

    The staging exemption is not a convenience — staging genuinely runs this configuration on
    purpose. ``deployment/Makefile`` passes ``-c cms.allow_self_signup=true`` for
    ``DEPLOYMENT_STAGE=staging`` with a rationale recorded in the same file ("keeps the seed
    flow working"), and the staging pool really does have
    ``AllowAdminCreateUserOnly=False``. What makes that acceptable there and not on prod is
    reachability, verified rather than assumed: the staging CloudFront distribution has
    ``TrustedKeyGroups {Enabled: true, Quantity: 1}`` — it sits behind an internal edge-auth
    gate, so only already-authenticated internal callers can reach the signup surface — while
    prod has ``{Enabled: false, Quantity: 0}`` and is reachable from the open internet.

    An earlier draft of this guard enforced on every non-dev stage and broke the staging
    synth. That was the guard being wrong, not the config: a guard that refuses a deliberate,
    documented, currently-deployed configuration is a false positive, and shipping it would
    have blocked staging deploys.

    Unrecognised stages are enforced, consistent with the other guards in this module —
    mistyping ``prod`` must not silently bypass the check.
    """
    if not signup_flag or enable_external_signup:
        return

    stage = (stage_name or "").strip().lower()
    if stage in _GUARD_OPTIONAL_STAGES or stage == "staging":
        return

    raise ValueError(
        f"cms.allow_self_signup is true but cms.enable_external_self_signup is false "
        f"for DEPLOYMENT_STAGE={stage!r}.\n"
        "That combination enables public registration on the user pool with NONE of the "
        "controls behind it: no disposable-domain denylist, no group assignment, no fleet "
        "scope. Every self-registered account lands GROUPLESS.\n"
        "Groupless is currently denied everywhere by the fail-open authz fix, so this is "
        "not exploitable today — but it is only safe because of that patch, and any "
        "regression on it would make every account registered during this window "
        "retroactively exploitable.\n"
        "Fix: set BOTH flags to open external signup properly (the Phase B preconditions "
        "are then checked by _require_provisioning_guards), or leave BOTH unset to keep "
        "registration closed. Do not set only cms.allow_self_signup."
    )


def _require_provisioning_guards(
    *,
    stage_name: str,
    enable_internal_auto: bool,
    enable_external_signup: bool,
    waf_acl_arn: str | None,
    fail_open_authz_patched: bool,
    signup_flag: bool,
    brand_audit_closed: bool,
) -> None:
    """Raise ``ValueError`` at synth time when Phase B preconditions are unmet.

    This guard MUST be called from the UI stack's synth path whenever
    ``enable_external_signup`` could be True for any stage — so that a prod
    deploy that bypasses the Make target still fails closed before any
    infrastructure is provisioned.

    Parameters
    ----------
    stage_name:
        The deployment stage as a lower-case string, e.g. ``"staging"``,
        ``"prod"``.  Unrecognised stages (anything not in
        ``_GUARD_OPTIONAL_STAGES``) fail closed regardless of other flags.
    enable_internal_auto:
        True when Phase A (Federate auto-provisioning) is enabled for this
        stack.  Does not affect Phase B precondition checks on its own.
    enable_external_signup:
        True when Phase B (external self-signup) is requested.  Phase B gates
        are only enforced when this is True **and** ``stage_name`` is not in
        ``_GUARD_OPTIONAL_STAGES``.  Staging is SSO-gated and exempt.
    waf_acl_arn:
        The CloudFront WAF WebACL ARN.  Must be a non-empty string on prod with
        external signup enabled.  ``None`` or empty string → raises.
    fail_open_authz_patched:
        Caller-supplied result of ``_grep_fail_open_patched()``.  Must be True
        on prod before Phase B can be enabled.  Shipping external signup without
        this patch means a self-registered (groupless) user inherits
        platform-admin — unauthenticated privilege escalation.
    signup_flag:
        Explicit opt-in that the operator has reviewed and set
        ``cms.allow_self_signup=true`` in the stack context.  Must be True to
        enable Phase B on prod.
    brand_audit_closed:
        Caller-supplied result of ``_check_brand_audit_summary()``.  The
        brand/PII audit must be resolved before Phase B ships to prod to avoid
        exposing demo-internal data through the self-signup surface.

    Raises
    ------
    ValueError
        For any unmet precondition when the stage is prod-equivalent, or when
        the stage is unrecognised (fail closed on purpose).
    """
    stage = (stage_name or "").strip().lower()

    # ── Unrecognised stage → always fail closed ────────────────────────────
    # Known dev stages are the only safe opt-out.  A typo like "staging2" or
    # "prd" must not silently allow Phase B deployment.
    if stage not in _GUARD_OPTIONAL_STAGES and stage != "staging" and stage != "prod":
        raise ValueError(
            f"Unrecognised DEPLOYMENT_STAGE={stage_name!r} for provisioning guard. "
            "Phase B precondition checks cannot be skipped on an unknown stage: "
            "mistyping 'prod' as an unknown value would silently bypass all guards. "
            f"Known non-production stages that may bypass the guard: "
            f"{sorted(_GUARD_OPTIONAL_STAGES)!r}. "
            "If this is a real deployment stage, add it to the project's stage map "
            "and re-run synth."
        )

    # ── Exempt stages (known dev/test + staging) ───────────────────────────
    # Staging is SSO-gated (TrustedKeyGroups enabled on its CloudFront
    # distribution) so Phase B external signup is safe to enable there without
    # the prod-level preconditions.
    if stage in _GUARD_OPTIONAL_STAGES or stage == "staging":
        return  # no precondition checks needed

    # ── Phase B prod preconditions ─────────────────────────────────────────
    # From here: stage == "prod".  Check gates only when external signup is
    # being enabled — an internal-auto-only deploy does not need them.
    if not enable_external_signup:
        return  # Phase B not requested; nothing to gate

    # Gate 1 — WAF WebACL must be attached.
    # Without WAF, the prod CloudFront distribution has no WAF WebACL
    # (WebACLId: "" as of 2026-08-07) and signup endpoints are unprotected.
    # Enabling external signup on prod without WAF exposes the Cognito Hosted UI
    # to enumeration and brute-force from the public internet.
    if not waf_acl_arn:
        raise ValueError(
            "waf_acl_arn is empty or None for DEPLOYMENT_STAGE='prod' with "
            "enable_external_signup=True. Enabling external self-signup on prod "
            "without a WAF WebACL leaves the Cognito Hosted UI signup endpoint "
            "unprotected against enumeration and rate-based attacks "
            "(observed on this deployment's prod distribution, which carried an empty "
            "WebACLId as of 2026-08-07). "
            "Fix: deploy the WAF stack (cms-prod-ui-waf), confirm the WebACL ARN "
            "is in /cms/prod/ui-waf/web-acl-arn, and re-run synth with the ARN "
            "passed as waf_acl_arn."
        )

    # Gate 2 — Fail-open authz must be patched.
    # A self-registered user is initially groupless.  Until commit d235fb31
    # (2026-08-05T22:12:17Z) a groupless caller was treated as platform-admin by
    # main_api/index.py.  Enabling external signup before that fix is deployed
    # converts authenticated privilege escalation into unauthenticated admin
    # (any self-registered stranger → platform-admin).
    if not fail_open_authz_patched:
        raise ValueError(
            "fail_open_authz_patched is False for DEPLOYMENT_STAGE='prod' with "
            "enable_external_signup=True. The fail-open authz defaults in "
            "main_api/index.py have not been removed: a groupless self-registered "
            "user would inherit platform-admin on the public prod surface. "
            "Enabling Phase B before this fix is deployed converts an authenticated "
            "privilege-escalation into unauthenticated admin access. "
            "Fix: ensure commit d235fb31 (2026-08-05T22:12:17Z) is deployed to "
            "the prod FleetAPIFunction Lambda, then re-run synth."
        )

    # Gate 3 — Operator signup-flag opt-in must be set.
    # The operator must explicitly set cms.allow_self_signup=true in the stack
    # context after reviewing the Phase B gate report.  A default-false flag with
    # no operator confirmation means Phase B was enabled without review.
    if not signup_flag:
        raise ValueError(
            "signup_flag is False for DEPLOYMENT_STAGE='prod' with "
            "enable_external_signup=True. The operator must explicitly set "
            "cms.allow_self_signup=true in the CDK context after reviewing the "
            "Phase B blocker gate report (deployment/scripts/phase_b_blocker_gate.py). "
            "This flag is the operator's attestation that all four Phase B blockers "
            "have been verified GREEN. "
            "Fix: run phase_b_blocker_gate.py --stage prod, verify all four blockers "
            "are GREEN, then re-deploy with -c cms.allow_self_signup=true."
        )

    # Gate 4 — Brand/PII audit must be closed.
    # The brand/PII audit (issues/YYYY-MM-DD-cms-demo-external-exposure/) must be
    # resolved before external signup ships to prod, to prevent demo-internal data
    # (hardcoded demo persona names, Fleet Manager handles, etc.) from being
    # visible to self-registered external users on the prod surface.
    if not brand_audit_closed:
        raise ValueError(
            "brand_audit_closed is False for DEPLOYMENT_STAGE='prod' with "
            "enable_external_signup=True. The brand/PII audit for demo-external "
            "exposure has not been resolved: issues/YYYY-MM-DD-cms-demo-external-"
            "exposure/summary.md either does not exist or does not contain "
            "'Status: RESOLVED'. Enabling external self-signup before this audit "
            "is closed risks exposing demo-internal data (persona names, fleet "
            "handles, etc.) to self-registered external users on the public prod "
            "surface. "
            "Fix: complete and resolve the brand/PII audit (backlog row "
            "'Demo external exposure'), then re-run synth."
        )


def _require_iot_rule_action_hygiene(
    *,
    rule_name: str,
    actions: list,
    required_action_kinds: set,
) -> None:
    """Raise at synth when an IoT topic rule's declared actions are unsafe or incomplete.

    Added after ``issues/2026-09-01-fwe-telemetry-s3-action-iac-drift/``, where the deployed
    ``fw_staging_iot_msk_rule`` carried an S3 backup action that existed **nowhere in source**.
    Because CloudFormation converges to the declared template, the next deploy of
    ``cms-staging-fleetwise`` would have deleted that backup silently — no error, because from
    CloudFormation's point of view it was doing exactly what it was told. That is the same
    mechanism as the 2026-08-11 outage handled by ``_require_client_idp_config`` above:
    **the template omits what is deployed.**

    **What this guard can and cannot do.** It CANNOT detect drift. Drift is a property of
    deployed state, and synth must not depend on a live API call — the same reasoning
    ``_require_client_idp_config`` gives for not verifying that a named IdP exists on the pool.
    Detecting drift belongs to ``cdk diff`` in CI or CloudFormation drift detection, which is
    recorded as the residual gap on that issue.

    What it CAN do is enforce two invariants on our own template, which together close the
    reachable half of the problem:

    1. **Required action kinds are present.** Once an action has been brought into IaC,
       silently dropping it again is a code change this guard refuses. A rule that is supposed
       to both stream to Kafka and back up to S3 cannot quietly become Kafka-only.
    2. **No ``timestamp("<fmt>")`` in any action string.** AWS IoT SQL's ``timestamp()`` takes
       **no** format argument — it returns epoch milliseconds. The drifted S3 key used
       ``${timestamp("yyyy")}`` for all four Hive partition values, so every message created
       its own partition: ~145,000 single-object partitions, none queryable by date, and the
       bug was invisible until a date-partitioned query returned zero while objects were
       landing every 30 seconds. The function that actually formats is
       ``parse_time("<fmt>", timestamp())``. This idiom is copied in at least two other places
       in the repo, so refusing it at synth stops the pattern spreading.

    Args:
        rule_name: the rule's name, for the error message.
        actions: the list of ``CfnTopicRule.ActionProperty`` objects being declared.
        required_action_kinds: action kinds that MUST be present, e.g. ``{"kafka", "s3"}``.

    Raises:
        ValueError: at synth, before anything is deployed.
    """
    present = set()
    for action in actions or []:
        for kind in ("kafka", "s3", "firehose", "lambda_", "republish", "dynamo_db", "sqs",
                     "sns", "kinesis", "http", "open_search", "timestream"):
            if getattr(action, kind, None) is not None:
                present.add(kind.rstrip("_"))

    missing = {k.rstrip("_") for k in required_action_kinds} - present
    if missing:
        raise ValueError(
            f"IoT rule '{rule_name}' is missing required action kind(s): "
            f"{sorted(missing)}. Declared: {sorted(present) or 'none'}.\n"
            f"These actions are declared required because they were previously configured "
            f"OUT OF BAND and a deploy would have deleted them silently "
            f"(issues/2026-09-01-fwe-telemetry-s3-action-iac-drift/). Removing one from the "
            f"template deletes it from the deployed rule with no error. If the action is "
            f"genuinely no longer wanted, drop it from required_action_kinds in the same "
            f"commit so the intent is explicit and reviewable."
        )

    # Invariant 2 — the non-formatting timestamp() idiom, anywhere in any action string.
    #
    # Walks the action objects structurally. An earlier version of this guard used
    # repr(actions), which silently NEVER FIRED: CDK/jsii property objects do not
    # include their contents in repr(), so the scan saw
    # "<ActionProperty object at 0x...>" and matched nothing. A guard that cannot
    # fail is worse than no guard, and it was caught only because the test fed a
    # known-bad key and expected a refusal.
    def _strings(obj, depth=0):
        if depth > 6:
            return
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from _strings(v, depth + 1)
        elif isinstance(obj, (list, tuple, set)):
            for v in obj:
                yield from _strings(v, depth + 1)
        else:
            for attr in dir(obj):
                if attr.startswith("__"):
                    continue
                try:
                    val = getattr(obj, attr)
                except Exception:
                    continue
                if callable(val):
                    continue
                if isinstance(val, (str, dict, list, tuple, set)):
                    yield from _strings(val, depth + 1)

    bad = set()
    for text in _strings(actions or []):
        bad.update(_IOT_TIMESTAMP_FMT_RE.findall(text))
    if bad:
        raise ValueError(
            f"IoT rule '{rule_name}' uses timestamp(\"<fmt>\") in an action: {sorted(set(bad))}.\n"
            f"AWS IoT SQL's timestamp() accepts NO format argument — it returns epoch "
            f"milliseconds. Every value formatted this way becomes the same integer, which on "
            f"an S3 key means every message creates its own partition "
            f"(~145,000 of them before this was caught — see "
            f"issues/2026-09-01-fwe-telemetry-s3-action-iac-drift/).\n"
            f"Use parse_time(\"<fmt>\", timestamp()) instead."
        )


def _require_client_idp_config(
    *,
    stage_name: str,
    ui_custom_domain: str,
    extra_idps: str,
    federate_creds_present: bool,
) -> None:
    """Raise at synth when a domain-bearing stage would ship an unusable app client.

    Added after the 2026-08-11 prod outage
    (``issues/2026-08-11-prod-federate-client-config-reset/``). A ``cms-prod-ui`` deploy
    reset ``SupportedIdentityProviders`` to ``["COGNITO"]`` and ``CallbackURLs`` to
    aws-cdk-lib's ``["https://example.com"]`` placeholder, and all 9 platform-admins lost
    Federate sign-in. Nothing failed at synth, nothing failed at deploy, and the stack went
    ``UPDATE_COMPLETE`` — the only signal was a human clicking the button.

    Two conditions are refused, because either alone breaks federated sign-in:

    1. **A stage has a custom domain but declares no federated IdP.** A stage that serves a
       real domain and offers only ``COGNITO`` is either misconfigured or has just silently
       dropped its IdP list — which is precisely what happened. ``CLIENT_EXTRA_IDPS``
       (permitting an existing provider, needs only its name) or the Federate credentials
       (creating one) satisfies it.
    2. **No custom domain while an IdP is declared.** The callback URL is derived from the
       domain, so without one the client would fall back to the placeholder callback and
       the ``redirect_uri`` the SPA sends could never match.

    Dev stages are exempt; an unrecognised stage fails closed, matching the other guards in
    this module.

    Deliberately NOT checked here: whether the named provider actually exists on the pool.
    That needs a live API call, which synth must not depend on. It is covered instead by
    the post-deploy verification in ``docs/DEPLOYMENT.md``, because the honest place for a
    live-state check is after the deploy that could invalidate it.
    """
    stage = (stage_name or "").strip().lower()
    if stage in _GUARD_OPTIONAL_STAGES:
        return
    if stage not in ("staging", "prod"):
        raise ValueError(
            f"Unrecognised DEPLOYMENT_STAGE={stage_name!r} for the app-client IdP guard. "
            "Mistyping a stage name must not silently skip it. Known non-production "
            f"stages that may bypass: {sorted(_GUARD_OPTIONAL_STAGES)!r}."
        )

    declared = [p.strip() for p in (extra_idps or "").split(",") if p.strip()]
    # `cognito-only` is a distinct sentinel, not an empty value, so "this stage has no
    # federated sign-in" has to be SAID rather than left as an absence. A generic empty
    # opt-out is what let the prod client silently lose its provider list; the same lesson
    # the CVX stage-defaults spec recorded when a `none` opt-out silently dropped a pool
    # trust that a source comment forbade dropping.
    if [d.lower() for d in declared] == ["cognito-only"]:
        return
    declared = [p for p in declared if p.upper() != "COGNITO"]
    has_federated = bool(declared) or federate_creds_present

    # 2026-08-30 outage class: CLIENT_EXTRA_IDPS=AmazonFederate SATISFIED the
    # first check below (has_federated=True from the declared list), but
    # ui_stack.py's AmazonFederateIdP CFN resource is only synthesised when
    # `_federate_creds_present` is True — so without creds, the CDK template
    # drops the IdP resource, CFN deletes it, and the client's SupportedIdentity-
    # Providers list points at a provider that no longer exists.
    #
    # This exactly reproduced 2026-08-11: staging deploy at 10:06 drove the
    # AmazonFederateIdP to DELETE_COMPLETE at 10:08:30 during a `data-processing`
    # target that touches `cms-staging-ui` as a CDK dependency stack without
    # re-loading the Federate creds. Recovery required a targeted
    # `make phase1` with creds in env.
    #
    # The docstring's "CLIENT_EXTRA_IDPS satisfies it (permitting an existing
    # provider, needs only its name)" pattern assumed an out-of-band IdP
    # lifecycle that no stage in this repo actually uses — every stage has the
    # IdP CDK-managed, driven by FEDERATE_OIDC_SECRET_ID in config/<stage>.env.
    # The safe rule is: if AmazonFederate is trusted, its creds must be present
    # so CDK keeps managing the resource. Out-of-band lifecycle is available for
    # non-`AmazonFederate` providers, or via the `cognito-only` sentinel.
    federate_in_extra = any(d == "AmazonFederate" for d in declared)
    if federate_in_extra and not federate_creds_present:
        raise ValueError(
            f"DEPLOYMENT_STAGE={stage!r} declares CLIENT_EXTRA_IDPS contains "
            "'AmazonFederate' but FEDERATE_CLIENT_ID / FEDERATE_CLIENT_SECRET "
            "are absent from the environment.\n"
            "ui_stack.py only synthesises the AmazonFederateIdP CFN resource "
            "when those creds are set. Deploying WITHOUT them drops the resource "
            "from the template, CloudFormation deletes the IdP, and every user "
            "who signs in via Federate loses access — exactly the 2026-08-11 "
            "outage, reproduced on staging 2026-08-30 during a `data-processing` "
            "target that touched cms-staging-ui as a dependency stack.\n"
            "Fix: source config/<stage>.env with `set -a` before running make, so "
            "FEDERATE_OIDC_SECRET_ID is exported and the phase1 recipe's "
            "Secrets-Manager fetch populates FEDERATE_CLIENT_ID / "
            "FEDERATE_CLIENT_SECRET. Or, if this stage genuinely has no Federate, "
            "set CLIENT_EXTRA_IDPS=cognito-only."
        )

    if ui_custom_domain and not has_federated:
        raise ValueError(
            f"DEPLOYMENT_STAGE={stage!r} serves custom domain {ui_custom_domain!r} but the "
            "app client would permit only COGNITO — no federated identity provider.\n"
            "Deploying this REMOVES federated sign-in from the client, because "
            "CloudFormation enforces template state over any provider list configured out "
            "of band. That is the 2026-08-11 prod outage: 9 platform-admins locked out, "
            "stack UPDATE_COMPLETE, no error anywhere.\n"
            "Fix: set CLIENT_EXTRA_IDPS in config/<stage>.env to the provider name(s) "
            "already on the pool (e.g. CLIENT_EXTRA_IDPS=AmazonFederate). Permitting an "
            "existing provider needs only its NAME — the client id/secret are required "
            "only to CREATE one.\n"
            "If this stage genuinely has no federated sign-in, set "
            "CLIENT_EXTRA_IDPS=cognito-only to say so deliberately."
        )

    if declared and not ui_custom_domain:
        raise ValueError(
            f"DEPLOYMENT_STAGE={stage!r} declares CLIENT_EXTRA_IDPS={declared!r} but no "
            "uiCustomDomain context was supplied.\n"
            "The OAuth callback URL is derived from that domain, so without it the client "
            "keeps aws-cdk-lib's placeholder callback (https://example.com) and the "
            "redirect_uri the SPA sends (<origin>/auth/callback) can never match — "
            "federated sign-in fails at the Hosted UI with a generic error page.\n"
            "Fix: deploy via the Make target, which supplies uiCustomDomain from "
            "config/<stage>.env."
        )
