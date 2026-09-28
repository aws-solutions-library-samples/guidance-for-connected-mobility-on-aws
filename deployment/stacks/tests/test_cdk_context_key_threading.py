# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Repo-wide lint: every CDK context key a stack reads must be threaded by the
Makefile target that deploys that stack.

Generalises two single-module guards that were written three days apart, for the
same defect, on two different stacks:

* ``test_connected_services_ui_context_threading.py`` — prevention half of
  ``issues/2026-09-12-cs-portal-subscriptions-endpoint-never-threaded/``
* ``test_ui_stack_cs_consumer_context_threading.py`` — prevention half of
  ``issues/2026-09-13-cs-consumer-context-keys-never-threaded/``

Two independent instances in two days is a structural problem, not bad luck. A
CDK context key is a *declared* input with **no compiler, no default, and no
owner**: `try_get_context("k")` returns `None` for a key nobody passes, the stack
coerces it to `''`, the deploy succeeds, and the feature is silently off. No synth
test can see it, because the missing half lives in a Makefile and CDK never parses
one. Nothing fails. That is the whole failure mode.

WHY THIS IS A RATCHET AND NOT A CLEAN ASSERTION
-----------------------------------------------
The repo does not currently satisfy the contract, so a plain assertion would ship
red and be deleted or skipped within a day. Instead the known gaps are enumerated
in ``_KNOWN_GAPS`` below, each with a reason. The test fails only on a gap that is
**not** in that list.

That makes the existing debt legible instead of invisible, and it means adding an
unthreaded key to any stack fails immediately. Removing an entry from
``_KNOWN_GAPS`` is how a gap gets closed; adding one is a deliberate, reviewable
act that has to carry a justification.

**Read ``_KNOWN_GAPS`` as a findings list**, and keep it honest in both
directions. It opened at 23 entries on 2026-09-13 and is at 7 the same day: the
largest contributor, ``refresh-recalls-ui``, contributed 16 and was fixed by
deleting its `cdk deploy` outright rather than by threading sixteen flags —
refreshing recall data regenerates a frontend source file and never needed
CloudFormation. `test_known_gaps_are_still_real` enumerated those 16 as stale and
refused to pass until they were removed.

That is the useful property: the cheapest fix for an unthreaded context key is
sometimes to stop deploying the stack.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STACKS_DIR = _REPO_ROOT / "deployment" / "stacks"
_MAKEFILE = _REPO_ROOT / "deployment" / "Makefile"

# CDK stack-name suffix (`cms-{stage}-<suffix>`) -> stack module filename.
# Explicit rather than derived: `connected-services-ui` -> `connected_services_ui_stack.py`
# is guessable, but `ui-analytics` and `connector-` are not, and a wrong guess
# here silently narrows the lint instead of failing it.
_STACK_SUFFIX_TO_MODULE = {
    "ui": "ui_stack.py",
    "storage": "storage_stack.py",
    "connected-services-ui": "connected_services_ui_stack.py",
    "dms-service-events": "dms_service_events_stack.py",
}

# Targets that deploy a stack but whose recipe we do NOT hold to the contract.
# Keep this empty unless there is a structural reason; per-key exemptions belong
# in _KNOWN_GAPS, which is auditable per key.
_EXEMPT_TARGETS: set[str] = set()

# ── Known, accepted gaps: (target, stack_module, context_key) ───────────────
#
# Every entry is a key with NO delivery path today. Each line is a finding, not a
# style nit. Grouped by cause; remove an entry when the gap closes (a stale entry
# fails `test_known_gaps_are_still_real`).
#
# THE LINT REPRODUCED A KNOWN DEFECT MECHANICALLY. `dmsUiCallbackOrigin` below is
# the cause of an already-filed P2: federated sign-in is broken on the dealer
# portal's custom domain because `ui_stack.py` accepts that key and nothing feeds
# it. That took a human investigation to find; this lint finds it in 0.2s. It is
# the argument for the file existing.

_DELIBERATE_SECURITY = (
    "deliberately NOT passed — reintroducing it opens unauthenticated SignUp on "
    "an internet-facing pool. Removed 2026-09-08; see the Makefile block comment "
    "on phase1 and issues/2026-09-08-public-prod-pool-permits-external-self-signup/"
)
_UNFED_INTEGRATION = (
    "read by ui_stack.py, fed by no Makefile target or config file — same class as "
    "issues/2026-09-13-cs-consumer-context-keys-never-threaded/, not yet fixed"
)

_KNOWN_GAPS: dict[tuple[str, str, str], str] = {
    # phase1 — the main deploy path. Seven keys with no delivery.
    ("phase1", "ui_stack.py", "cms.allow_self_signup"): _DELIBERATE_SECURITY,
    ("phase1", "ui_stack.py", "cms.allow_unauth_websocket"): (
        "defaults closed; no stage currently opts in. Fails safe, unlike the keys "
        "whose empty value silently disables a feature."
    ),
    ("phase1", "ui_stack.py", "bedrockAgentsStackName"): (
        "threaded by refresh-recalls-ui but not phase1 — the VFO stack it names "
        "was RETIRED 2026-09-05 (spec 2026-09-05-cms-vfo-teardown), so this key "
        "is probably dead and should be deleted rather than threaded"
    ),
    ("phase1", "ui_stack.py", "vsaUserPoolId"): _UNFED_INTEGRATION,
    ("phase1", "ui_stack.py", "dmsUiUrl"): _UNFED_INTEGRATION,
    ("phase1", "ui_stack.py", "dmsUiCallbackOrigin"): (
        "THE CAUSE OF an already-filed P2 — federated sign-in is broken on the "
        "dealer portal's custom domain because this key is accepted and never "
        "fed. " + _UNFED_INTEGRATION
    ),
    ("phase1", "ui_stack.py", "connectedServicesUiCallbackOrigin"): (
        "ui_stack.py reads a connectedServicesUi* key that only the CS-UI targets "
        "thread; either phase1 should thread it or ui_stack should not read it"
    ),
}

# RESOLVED 2026-09-13 — `refresh-recalls-ui` no longer appears above.
#
# It used to contribute SIXTEEN of this baseline's entries: it ran
# `cdk deploy cms-{stage}-ui` with one of ui_stack.py's nineteen context keys and
# no Federate creds. Verified by synth to remove federated sign-in, delete the
# IdP, drop the custom domain and cert, detach the WAF and drop the edge auth
# gate — the 2026-08-11 outage mechanism, on the public production surface.
#
# Fixed by deleting the CDK deploy rather than by adding sixteen flags: refreshing
# recalls regenerates a frontend SOURCE file, so it needs a build and an S3 sync,
# never CloudFormation. The target now delegates to `ui-quick-deploy`, which
# touches no CloudFormation at all — so there is no context left to lose, and
# this lint has nothing to say about it.
#
# The lesson for the remaining `phase1` entries: the cheapest fix for an
# unthreaded context key is sometimes to stop deploying the stack, not to thread
# the key. See issues/2026-09-13-refresh-recalls-ui-deploys-ui-stack-context-less/.


def _module_source(module: str) -> str:
    path = _STACKS_DIR / module
    assert path.is_file(), f"expected stack module to exist: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"unexpectedly empty: {path}"
    return text


def _literal_str_list(node: ast.AST) -> list[str] | None:
    """Return the string literals if *node* is a list/tuple of all-strings."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    if not node.elts:
        return None
    if not all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
        return None
    return [e.value for e in node.elts]  # type: ignore[attr-defined]


def context_keys_for(module: str) -> set[str]:
    """Every context key *module* reads, across BOTH idioms in this repo.

    1. ``self.node.try_get_context("literalKey")``
    2. a list of string literals iterated into ``try_get_context(loop_var)`` —
       `connected_services_ui_stack.py` reads five REQUIRED keys this way.

    Idiom 2 matters and was a live blind spot: the CS-UI guard's regex only
    matches idiom 1, so it never saw those five, and its anti-vacuity floor of
    ">= 5 keys" passes on the six it *does* find. All five happen to be threaded
    today, so there is no live defect — but a sixth added to that list would have
    been invisible to the guard written specifically to prevent this.

    Idiom 2 is resolved PRECISELY: find `for v in <name>` whose body calls
    `try_get_context(v)`, then resolve `<name>`'s literal assignment in the same
    module. An earlier draft instead harvested every list-of-strings whose
    elements merely *looked* like context keys, on the reasoning that
    over-collecting "fails safe" because it can only make the lint stricter.
    That reasoning was wrong: it collected `index.html` and
    `route.request.querystring.token`, which then had to be recorded as
    permanent "known gaps" that will never close. A lint whose baseline contains
    entries that are not real is a lint nobody trusts — over-collection fails
    LOUD, not safe.

    Parsed with `ast`, not regex, so a reformat cannot silently shrink the set.
    """
    tree = ast.parse(_module_source(module))
    keys: set[str] = set()

    # Idiom 1 — literal argument.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "try_get_context"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                keys.add(value)

    # Every `name = [<all string literals>]` in the module, for idiom-2 lookup.
    literal_lists: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            values = _literal_str_list(node.value)
            if values is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    literal_lists[target.id] = values
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            values = _literal_str_list(node.value)
            if values is not None and isinstance(node.target, ast.Name):
                literal_lists[node.target.id] = values

    # Idiom 2 — `for v in <name>:` whose body calls try_get_context(v).
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        loop_var = node.target.id

        iterated: list[str] | None = None
        if isinstance(node.iter, ast.Name):
            iterated = literal_lists.get(node.iter.id)
        else:
            iterated = _literal_str_list(node.iter)
        if iterated is None:
            continue

        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if not (isinstance(func, ast.Attribute) and func.attr == "try_get_context"):
                continue
            if inner.args and isinstance(inner.args[0], ast.Name) and inner.args[0].id == loop_var:
                keys.update(iterated)
                break

    return keys


def _target_bodies() -> dict[str, str]:
    """Split the Makefile into ``target -> recipe body``."""
    text = _MAKEFILE.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    bodies: dict[str, list[str]] = {}
    current: str | None = None
    target_re = re.compile(r"^([A-Za-z0-9_.\-]+)\s*:(?!=)")
    for line in lines:
        m = target_re.match(line)
        if m:
            current = m.group(1)
            bodies.setdefault(current, [])
            continue
        if line.startswith("\t") and current is not None:
            bodies[current].append(line)
        elif line.strip() and not line.startswith("\t"):
            # A non-recipe, non-target line ends the current recipe.
            current = None
    return {k: "".join(v) for k, v in bodies.items() if v}


def deploying_targets() -> dict[str, set[str]]:
    """Map Makefile target -> set of stack modules it deploys or synths.

    Only stacks NAMED explicitly on a `cdk deploy` / `cdk synth` line are
    counted. A bare `cdk deploy` (no stack named) is skipped, as is any stack
    suffix not in `_STACK_SUFFIX_TO_MODULE`.

    **Known limitation, stated rather than papered over**: without
    `--exclusively`, `cdk deploy X` also deploys X's DEPENDENCY stacks, and that
    is exactly how the 2026-08-11 Federate outage happened — a `data-processing`
    target touched `cms-staging-ui` as a dependency, not as a named stack, so
    this lint would not have flagged it. Modelling the dependency graph needs a
    synth of the whole app; out of scope here. This lint covers named stacks only.
    """
    suffix_re = re.compile(r"cms-\$\(DEPLOYMENT_STAGE\)-([a-z0-9-]+)")
    out: dict[str, set[str]] = {}
    for target, body in _target_bodies().items():
        if target in _EXEMPT_TARGETS:
            continue
        for line in body.splitlines():
            if "cdk deploy" not in line and "cdk synth" not in line:
                continue
            for suffix in suffix_re.findall(line):
                module = _STACK_SUFFIX_TO_MODULE.get(suffix)
                if module:
                    out.setdefault(target, set()).add(module)
    return out


def _threaded(body: str, key: str) -> bool:
    """Is *key* passed as `-c key=<something non-empty>` in *body*?

    A single-`$` value is treated as NOT threaded. In a Makefile recipe `$VAR` is
    a *make* expansion — almost always empty — while `$$VAR` is what the shell
    sees. The single-dollar form ships a flag with no value and still exits 0,
    which is indistinguishable from success. This repo has hit it: a
    string-replace edit to this Makefile collapsed `$$` to `$` and emptied a
    variable while synth and deploy both passed.
    """
    for m in re.finditer(rf"-c\s+{re.escape(key)}=(\S*)", body):
        value = m.group(1)
        if not value:
            continue
        if re.match(r"^\$[A-Za-z_(]", value):  # single-$ -> make expansion
            continue
        return True
    return False


def _env_fallbacks_for(module: str) -> dict[str, str]:
    """Map context key -> env var name, where the stack accepts EITHER.

    Several keys are read as ``try_get_context("k") or os.environ.get("K", "")``.
    For those, `-c k=` is one of two valid delivery paths and its absence is not
    a gap — `phase1` exports a lot of env. Reporting them as gaps would pad the
    baseline with non-findings, which is the trust problem that made the
    over-collecting draft of `context_keys_for` unusable.

    Matched on an `or` chain containing both calls, in either order, which is how
    all current instances are written (`ui_stack.py` csSubscriptionId,
    `storage_stack.py` / `dms_service_events_stack.py` dmsEventBusName).
    """
    tree = ast.parse(_module_source(module))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        ctx_key: str | None = None
        env_name: str | None = None
        for operand in node.values:
            for inner in ast.walk(operand):
                if not isinstance(inner, ast.Call):
                    continue
                func = inner.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr == "try_get_context" and inner.args:
                    arg = inner.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        ctx_key = arg.value
                elif func.attr == "get" and inner.args:
                    # os.environ.get("NAME", ...) — check the receiver is environ.
                    recv = func.value
                    is_environ = (
                        isinstance(recv, ast.Attribute) and recv.attr == "environ"
                    ) or (isinstance(recv, ast.Name) and recv.id == "environ")
                    arg = inner.args[0]
                    if is_environ and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        env_name = arg.value
        if ctx_key and env_name:
            out[ctx_key] = env_name
    return out


def _env_exported(body: str, env_name: str) -> bool:
    """Does *env_name* reach the cdk invocation for this target?

    Two delivery paths, both real:

    1. the target's own recipe sets it (`NAME=value \\` prefix, or `export NAME=`)
    2. it is set in ``config/staging.env``, which ``staging-deploy`` sources with
       ``set -a`` before invoking ``$(MAKE) deploy-all`` -> ``phase1``. The value
       is therefore in `phase1`'s environment at run time and **invisible in
       `phase1`'s recipe text**. Checking only (1) reported nine keys as gaps that
       are in fact delivered — the same baseline-padding problem as the
       over-collecting draft, from the opposite direction.

    Both paths reject an empty right-hand side, and reject the single-`$`
    make-expansion form for the reason given on `_threaded`.
    """
    for m in re.finditer(rf"(?:^|\s|export\s+){re.escape(env_name)}=(\S*)", body, re.M):
        value = m.group(1)
        if not value or value == "\\":
            continue
        if re.match(r"^\$[A-Za-z_(]", value):
            continue
        return True
    return _staging_env_defines(env_name)


def _staging_env_defines(env_name: str) -> bool:
    """Is *env_name* set to a non-empty value in ``config/staging.env``?"""
    path = _REPO_ROOT / "deployment" / "config" / "staging.env"
    if not path.is_file():
        return False
    m = re.search(rf"^{re.escape(env_name)}=(.*)$", path.read_text(encoding="utf-8"), re.M)
    return bool(m and m.group(1).strip())


def _gaps() -> list[tuple[str, str, str]]:
    """Every (target, stack_module, key) with NO delivery path in the Makefile.

    A key is delivered if the target passes `-c key=<shell-expanded value>`, or —
    for keys the stack reads with an `os.environ` fallback — if the target exports
    that env var.
    """
    bodies = _target_bodies()
    found: list[tuple[str, str, str]] = []
    for target, modules in sorted(deploying_targets().items()):
        body = bodies.get(target, "")
        for module in sorted(modules):
            fallbacks = _env_fallbacks_for(module)
            for key in sorted(context_keys_for(module)):
                if _threaded(body, key):
                    continue
                env_name = fallbacks.get(key)
                if env_name and _env_exported(body, env_name):
                    continue
                found.append((target, module, key))
    return found


# ── Anti-vacuity: this lint's own premises ─────────────────────────────────
#
# Every assertion below reduces to "the gap set matches the baseline". An
# extractor that returns nothing makes that trivially true, which is the exact
# defect class this file exists to catch — so each input is asserted non-trivial
# on its own.

def test_premise_stack_modules_declare_context_keys() -> None:
    assert len(context_keys_for("ui_stack.py")) >= 15, (
        "ui_stack.py should read 15+ context keys; the AST extractor found "
        f"{sorted(context_keys_for('ui_stack.py'))}"
    )


def test_premise_loop_idiom_keys_are_recovered() -> None:
    """The five keys the CS-UI guard's regex cannot see must be found here."""
    keys = context_keys_for("connected_services_ui_stack.py")
    for key in (
        "connectedServicesUiCognitoUserPoolId",
        "connectedServicesUiCognitoClientId",
        "connectedServicesUiCognitoDomain",
        "connectedServicesUiApiEndpoint",
        "connectedServicesUiCallbackOrigin",
    ):
        assert key in keys, (
            f"{key} is read via the _required_context_keys loop and was not "
            "recovered. Without idiom-2 support this lint is blind to five "
            "REQUIRED keys."
        )


def test_premise_makefile_targets_are_parsed() -> None:
    bodies = _target_bodies()
    assert len(bodies) >= 40, f"only parsed {len(bodies)} Makefile targets"
    assert "phase1" in bodies and "cdk deploy" in bodies["phase1"]


def test_premise_stack_to_target_mapping_is_non_empty() -> None:
    mapping = deploying_targets()
    assert mapping, "no target was found to deploy any known stack"
    assert "ui_stack.py" in mapping.get("phase1", set()), (
        "phase1 must be detected as deploying ui_stack.py; if the recipe moved, "
        "fix the mapping rather than the assertion"
    )


# ── The contract ───────────────────────────────────────────────────────────

def test_no_new_unthreaded_context_key() -> None:
    """THE ratchet. A gap not in `_KNOWN_GAPS` fails here."""
    new = [g for g in _gaps() if g not in _KNOWN_GAPS]
    assert not new, (
        "These CDK context keys are read by a stack but NOT threaded by the "
        "Makefile target that deploys it:\n"
        + "\n".join(f"  target={t}  stack={m}  key={k}" for t, m, k in new)
        + "\n\nAn unthreaded key resolves to None, the stack coerces it to '', "
        "the deploy SUCCEEDS, and the feature is silently off — no synth test "
        "can see this because the missing half is in a Makefile.\n"
        "Fix: add `-c <key>=$$VAR \\` to the target and give VAR a value in "
        "deployment/config/<stage>.env. If the gap is deliberate, add it to "
        "_KNOWN_GAPS with a reason."
    )


def test_known_gaps_are_still_real() -> None:
    """A closed gap must be removed from the baseline, not left to rot.

    Without this the ratchet only tightens on paper: entries accumulate, stop
    corresponding to anything, and the next reader cannot tell which are real.
    """
    actual = set(_gaps())
    stale = sorted(g for g in _KNOWN_GAPS if g not in actual)
    assert not stale, (
        "These _KNOWN_GAPS entries no longer correspond to a real gap — the key "
        "is now threaded (or the stack/target changed). Delete them:\n"
        + "\n".join(f"  {g}" for g in stale)
    )


@pytest.mark.parametrize(
    "key",
    ["csProducerApiEndpoint", "csSubscriptionId"],
)
def test_connected_services_consumer_keys_are_threaded_by_phase1(key: str) -> None:
    """Pins the specific fix that motivated this lint.

    Redundant with the ratchet by construction, and kept anyway: the ratchet
    would also stay green if someone added these to `_KNOWN_GAPS`, and these two
    keys going unthreaded is the defect
    `issues/2026-09-13-cs-consumer-context-keys-never-threaded/` was filed for.
    """
    body = _target_bodies()["phase1"]
    assert _threaded(body, key), f"phase1 no longer threads {key}"
