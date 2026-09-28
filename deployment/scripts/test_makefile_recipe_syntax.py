"""Guard: deploy-target recipes must be valid shell, and must not split their own state.

Both halves of a real defect that was live at HEAD on 2026-08-11 and made `make phase1`
— the canonical deploy target for the UI stack — completely non-functional.

**Half one: invalid shell.** A `@#` comment line placed *inside* a backslash-continued
recipe is passed to `sh` verbatim, and `sh` fails on the first `(` in the comment text.
Two such blocks had been inserted into `phase1`, one of them by the very fix whose own
task notes recorded this gotcha ("the explanatory comment must live above the recipe
block"). `make -n | sh -n` catches it in under a second.

**Half two, worse and silent: the command splits.** Because a comment line does not end
with a backslash, it also *terminates* the logical command. `DEMO_CTX_FLAGS`,
`UI_DOMAIN_CTX_FLAGS` and `_dsg` were therefore set in shells that had already exited by
the time `cdk deploy` ran, so the deploy would have received EMPTY context flags — no
custom domain, no `cms.enable_internal_auto_provisioning`, no `wafWebAclArn`,
`DRIVER_SELF_GUARD_ENABLED=false`. `sh -n` passes on that happily; nothing but an
end-to-end deploy would notice.

Why this matters beyond one target: `~/.kiro/steering/deploy-validation.md` requires
deploying through the Make target precisely because bypassing it drops
`regenerate-runtime-config`'s `demoPasswords` pass. When the Make target itself is broken,
that rule cannot be followed — which is the most likely reason it was violated on
2026-08-10, with a green stack and broken staging auto-logon as the result.

`make -n` alone is NOT a sufficient check and never was: it prints recipes without
executing them, so it exits 0 on a recipe that cannot run.

Run with:
  cd deployment && .venv/bin/python -m pytest scripts/test_makefile_recipe_syntax.py -v
"""
from __future__ import annotations

import os
import re
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEPLOYMENT = os.path.abspath(os.path.join(_HERE, os.pardir))
_MAKEFILE = os.path.join(_DEPLOYMENT, "Makefile")

# Targets whose recipes provision or mutate deployed infrastructure. A syntax error here
# is discovered at the worst possible moment.
_DEPLOY_TARGETS = [
    ("phase1", "staging"),
    ("phase1", "prod"),
    ("phase3", "staging"),
    ("phase4", "staging"),
    ("regenerate-runtime-config", "staging"),
]


def _make_n(target: str, stage: str) -> str:
    proc = subprocess.run(
        ["make", "-n", target, f"DEPLOYMENT_STAGE={stage}"],
        cwd=_DEPLOYMENT, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, (
        f"`make -n {target} DEPLOYMENT_STAGE={stage}` failed:\n{proc.stderr[-1200:]}"
    )
    return proc.stdout


@pytest.mark.parametrize("target,stage", _DEPLOY_TARGETS)
def test_recipe_is_valid_shell(target: str, stage: str) -> None:
    """`make -n <target> | sh -n` — the check `make -n` alone cannot perform."""
    rendered = _make_n(target, stage)
    proc = subprocess.run(
        ["sh", "-n"], input=rendered, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, (
        f"recipe for `{target}` (stage {stage}) is not valid shell:\n"
        f"{proc.stderr[-1200:]}\n"
        f"Most likely cause: a `@#` comment line inside a backslash-continued block. "
        f"Move it above the block."
    )


def _logical_commands(target_name: str) -> list[str]:
    """Split the target's recipe into logical shell commands by backslash continuation."""
    with open(_MAKEFILE) as fh:
        lines = fh.read().split("\n")

    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"{target_name}:"):
            start = i + 1
            break
    assert start is not None, f"target {target_name} not found in Makefile"

    commands: list[list[str]] = []
    current: list[str] = []
    for line in lines[start:]:
        # A recipe line starts with a tab; anything else ends the recipe.
        if not line.startswith("\t"):
            if line.strip() == "":
                continue
            break
        current.append(line)
        if not line.endswith("\\"):
            commands.append(current)
            current = []
    if current:
        commands.append(current)
    return ["\n".join(c) for c in commands]


def test_phase1_sets_its_context_flags_in_the_same_shell_as_cdk_deploy() -> None:
    """The silent half. `sh -n` cannot see this; only a deploy would.

    `DEMO_CTX_FLAGS`, `UI_DOMAIN_CTX_FLAGS` and `_dsg` are shell variables, so they exist
    only within the logical command that assigns them. If a comment (or any non-continued
    line) splits the block, `cdk deploy` runs in a fresh shell and every one of them
    expands to the empty string — deploying the UI stack with no custom domain, no
    provisioning gate, no WAF ARN and the driver-self guard off.
    """
    assignments = ('DEMO_CTX_FLAGS=""', 'UI_DOMAIN_CTX_FLAGS=""', "_dsg=")
    deploy_cmds = [
        c for c in _logical_commands("phase1")
        if "cdk deploy" in c and "$$DEMO_CTX_FLAGS" in c
    ]
    assert len(deploy_cmds) == 1, (
        f"expected exactly one logical command containing the parameterised "
        f"`cdk deploy`, found {len(deploy_cmds)}"
    )
    cmd = deploy_cmds[0]
    for assignment in assignments:
        assert assignment in cmd, (
            f"{assignment!r} is not assigned in the same logical shell command as "
            f"`cdk deploy`, so it expands to empty at deploy time. A non-continued line "
            f"(usually a `@#` comment) has split the block."
        )


def test_phase1_deploy_block_carries_at_only_on_its_first_line() -> None:
    """`make` strips a leading `@` once per recipe line; a mid-command `@` reaches sh.

    Joining a previously-separate `@echo`/`@VAR=` line into a continuation without
    dropping its `@` produces `sh: @echo: command not found` — a failure that appears
    only at run time.
    """
    deploy_cmds = [
        c for c in _logical_commands("phase1")
        if "cdk deploy" in c and "$$DEMO_CTX_FLAGS" in c
    ]
    assert deploy_cmds
    body = deploy_cmds[0].split("\n")
    for line in body[1:]:
        assert not line.lstrip("\t").startswith("@"), (
            f"mid-command line still carries `@`, which will be passed to sh: {line!r}"
        )


# ---------------------------------------------------------------------------
# Env-var drift between ui_stack.py and the deploy target
# ---------------------------------------------------------------------------

# `ui_stack.py` reads these from os.environ. They are NOT CDK context, so no `-c` flag
# supplies them: the deploy command's environment is the only channel. Names outside this
# set are supplied by the operator's shell or another target and are not this test's
# business.
_PROVISIONING_ENV_VARS = (
    "INTERNAL_IDP_PROVIDER_NAME",
    "INTERNAL_AUTO_ASSIGN_GROUP",
    "INTERNAL_ALLOWED_EMAIL_DOMAINS",
    "EXTERNAL_SELF_SIGNUP_GROUP",
    "EXTERNAL_SELF_SIGNUP_FLEET_IDS",
)


@pytest.mark.parametrize("var", _PROVISIONING_ENV_VARS)
def test_phase1_exports_the_provisioning_env_vars(var: str) -> None:
    """Third layer of the 2026-08-11 defect, and the one with the cruellest error message.

    `phase1` translated `CMS_ENABLE_INTERNAL_AUTO_PROVISIONING` into a CDK context flag but
    never exported the identity values the stack reads from `os.environ`. So enabling the
    gate made `make phase1` abort at synth with
    `_require_internal_provisioning_config`'s ValueError — whose remediation text reads
    "deploy via the canonical Make target, which exports these from config/<stage>.env".
    The Make target did not export them. The only path that worked was a bare
    `cdk deploy` with the env file sourced, which is precisely the path that skips
    `regenerate-runtime-config` and broke staging persona auto-logon on 2026-08-10.

    A guard whose error message names a remedy that does not exist is worse than no
    message: it sends the operator confidently down the wrong path.
    """
    deploy_cmds = [
        c for c in _logical_commands("phase1")
        if "cdk deploy" in c and "$$DEMO_CTX_FLAGS" in c
    ]
    assert deploy_cmds
    assert f"{var}=" in deploy_cmds[0], (
        f"{var} is read by ui_stack.py via os.environ but is not exported on phase1's "
        f"cdk deploy line, so it will be empty at synth. Add both the `_`-prefixed sed "
        f"extraction and the export, next to DRIVER_SELF_GUARD_ENABLED."
    )


@pytest.mark.parametrize("var", _PROVISIONING_ENV_VARS)
def test_provisioning_env_vars_are_still_read_by_ui_stack(var: str) -> None:
    """The other direction: don't export names nothing reads.

    Keeps this list honest, so a removed feature does not leave a permanent export and a
    permanently-passing test.

    The match is a regex tolerating a line break between `os.environ.get(` and the name,
    because black wraps long reads across lines — a naive substring check reported
    INTERNAL_ALLOWED_EMAIL_DOMAINS as absent when it is read three lines further down.
    """
    ui_stack = os.path.join(_DEPLOYMENT, "stacks", "ui_stack.py")
    with open(ui_stack) as fh:
        source = fh.read()
    pattern = re.compile(r"os\.environ\.get\(\s*[\"']" + re.escape(var) + r"[\"']")
    assert pattern.search(source), (
        f"{var} is exported by phase1 but no longer read by ui_stack.py — drop it from "
        f"both, or from _PROVISIONING_ENV_VARS if it moved elsewhere."
    )
