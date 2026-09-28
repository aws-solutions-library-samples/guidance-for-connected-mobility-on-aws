"""Guard: the IoT lifecycle Lambda must ship its dependencies, built for Lambda.

Issue: issues/2026-09-23-iot-lifecycle-processor-dead-58m-sqs-backlog/

``Code.from_asset`` pointed at the handler's source directory shipped it as-is with
no ``pip install``, so ``cms-staging-iot-IoTLifecycleProcessor*`` died on
``Runtime.ImportModuleError: No module named 'aws_lambda_powertools'`` on every
invocation for its entire life — 37,267 errors in one hour, and a 5.8M-message SQS
backlog because a failing consumer never deletes.

Two things this guards that are easy to get wrong:

1. **The dependency must be staged at all.** That is the original defect, and the
   only one of the two that was actually causing the ImportModuleError.

2. **It should be built for the Lambda platform, not the build host.** The handler
   pins ``aws-lambda-powertools[all]==2.25.0``, which pulls
   ``pydantic>=1.8.2,<2.0.0``, and pydantic v1 ships compiled extension modules. A
   plain ``pip install -t`` — the pattern ``commands_stack.py`` uses, which only ever
   installed pure-Python ``protobuf`` and so never had to solve this — stages
   ``*-darwin.so`` when synthesised on a Mac.

   Scope of claim, measured rather than assumed: a darwin-staged tree *does* still
   import on linux/amd64 python3.11, because pydantic v1 and wrapt ship pure-Python
   fallbacks and CPython skips a mismatched extension tag. So this is defense in
   depth, not the fix — it keeps the compiled fast paths, and it matters the moment a
   dependency without a fallback is added (pydantic v2's ``pydantic-core``, numpy,
   cryptography all hard-fail on a platform mismatch).

Also guards the DynamoDB key schemas the handler writes to. The handler originally
wrote the attribute names of the SQLAlchemy models in ``iot_api/utils/models/``
(``name``, ``topic_name``) rather than the deployed key schemas (``topic_name``,
``topic_filter``), which raised ValidationException on every subscribe. Those names
are mirrored in the handler's own tests; this asserts the stack side has not drifted
away from them.

Run (from ``deployment/`` — see the ``template`` fixture for why):
  cd deployment && ../.venv/bin/python3 -m pytest stacks/tests/test_iot_lifecycle_bundling.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_STACKS = _HERE.parent
_DEPLOYMENT = _STACKS.parent
_REPO = _DEPLOYMENT.parent

for _p in (str(_DEPLOYMENT), str(_STACKS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

aws_cdk = pytest.importorskip(
    "aws_cdk", reason="needs a CDK-enabled interpreter (use .venv/bin/python3)"
)

from aws_cdk import App, assertions  # noqa: E402
from stacks.iot_stack import (  # noqa: E402
    IoTStack,
    _LIFECYCLE_PIP_PLATFORM,
    _LIFECYCLE_PIP_PYTHON_VERSION,
    _LIFECYCLE_LAMBDA_RUNTIME,
    _LIFECYCLE_LAMBDA_SRC_DIR,
    _bundle_lifecycle_lambda,
)

# Mirrors modules/.../iot_lifecycle_events/test_lifecycle_handler.py. If either side
# changes, one of these two suites fails.
#
# Note the doubled "-iot-iot-": the stack builds table names as
# f"{construct_id}-iot-connections" and construct_id already ends in "-iot"
# (cms-staging-iot). That is pre-existing and deployed; it is mirrored here rather
# than corrected, because renaming a table replaces it.
_STACK_ID = "cms-test-iot"
_EXPECTED_KEY_SCHEMAS = {
    f"{_STACK_ID}-iot-connections": [("client_id", "HASH")],
    f"{_STACK_ID}-iot-subscriptions": [("client_id", "HASH"), ("topic_filter", "RANGE")],
    f"{_STACK_ID}-iot-topics": [("topic_name", "HASH")],
}


@pytest.fixture(scope="module")
def staged_asset() -> Path:
    """Stage the asset once; this shells out to pip and takes a few seconds."""
    return Path(_bundle_lifecycle_lambda())


@pytest.fixture(scope="module")
def template():
    """Synthesize the stack.

    Must run with ``deployment/`` as the working directory — this stack's *other*
    Lambda (``IoTAPIFunction``) still uses a CWD-relative asset path
    (``../modules/...``), and jsii resolves it against the CWD of the Node
    subprocess, which is fixed at ``aws_cdk`` import time. That matches the repo
    convention (``cdk.json`` lives in ``deployment/``, and the sibling synth suites
    such as ``test_connected_services_waf.py`` also only pass from there).

    The lifecycle asset this suite is about uses an absolute path and does not care.
    """
    if Path.cwd() != _DEPLOYMENT:
        pytest.skip(
            f"run from {_DEPLOYMENT} (cwd is {Path.cwd()}): this stack has a "
            "CWD-relative asset path that jsii cannot resolve from elsewhere"
        )
    app = App()
    stack = IoTStack(app, _STACK_ID)
    return assertions.Template.from_stack(stack)


# ── 1. the dependency actually ships ────────────────────────────────────────

def test_powertools_is_staged(staged_asset: Path):
    """The module the function died on must be present in the shipped asset."""
    assert (staged_asset / "aws_lambda_powertools").is_dir(), (
        f"aws_lambda_powertools not staged in {staged_asset} — "
        "the lifecycle Lambda would fail at import again"
    )


def test_every_declared_requirement_is_staged(staged_asset: Path):
    """Each top-level distribution named in requirements.txt is present.

    Asserted by dist-info directory rather than by import, because these are Linux
    wheels that cannot be imported on the machine running this test.
    """
    reqs = (Path(_LIFECYCLE_LAMBDA_SRC_DIR) / "requirements.txt").read_text()
    names = [
        line.split("[")[0].split("=")[0].split(">")[0].split("<")[0].strip()
        for line in reqs.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert names, "requirements.txt parsed to nothing — parser or file is wrong"

    staged = {p.name.lower() for p in staged_asset.iterdir()}
    for name in names:
        normalized = name.replace("-", "_").lower()
        assert any(
            entry.startswith(normalized) for entry in staged
        ), f"requirement {name!r} not found in staged asset (looked for {normalized}*)"


def test_handler_module_is_staged(staged_asset: Path):
    assert (staged_asset / "lambda_function.py").is_file()


def test_stale_build_artifact_does_not_ship(staged_asset: Path):
    """The source dir holds a stale function.zip that must not be bundled."""
    assert not (staged_asset / "function.zip").exists(), (
        "function.zip from the source directory leaked into the staged asset"
    )


# ── 2. built for Lambda, not for the build host ─────────────────────────────

def test_no_foreign_platform_binaries_staged(staged_asset: Path):
    """No macOS/Windows compiled extensions may ship to a Linux Lambda.

    This is the assertion that would catch a plain `pip install -t` run on a
    developer's Mac. Note it guards artifact correctness, not importability: with the
    *current* pins a darwin-staged tree still imports on Linux via pure-Python
    fallbacks (verified in a linux/amd64 container). It matters for the compiled fast
    paths now, and for correctness the moment a dependency without a fallback lands.
    """
    foreign = [
        str(p.relative_to(staged_asset))
        for p in staged_asset.rglob("*")
        if p.suffix in {".so", ".pyd", ".dylib"}
        and ("darwin" in p.name or "macos" in p.name or p.suffix in {".pyd", ".dylib"})
    ]
    assert not foreign, (
        "non-Linux compiled extensions staged for a Linux Lambda: "
        f"{foreign[:10]}"
    )


def test_compiled_extensions_are_linux_x86_64(staged_asset: Path):
    """Positive control: the compiled modules present must be Linux x86_64.

    Without this, test_no_foreign_platform_binaries_staged would pass vacuously if
    pip staged no binaries at all (e.g. a future requirements change), and the
    platform pinning could silently stop mattering.
    """
    sos = list(staged_asset.rglob("*.so"))
    assert sos, (
        "no compiled extensions staged at all — if requirements.txt no longer pulls "
        "any, delete this test and the platform pinning together, deliberately"
    )
    wrong = [str(p.relative_to(staged_asset)) for p in sos if "x86_64-linux-gnu" not in p.name]
    assert not wrong, f"compiled extensions not built for Linux x86_64: {wrong[:10]}"


def test_pip_platform_matches_function_runtime():
    """The pip --python-version must track the Function's runtime, not drift from it."""
    runtime_version = _LIFECYCLE_LAMBDA_RUNTIME.name.replace("python", "")
    assert _LIFECYCLE_PIP_PYTHON_VERSION == runtime_version, (
        f"pip targets python {_LIFECYCLE_PIP_PYTHON_VERSION} but the Lambda runtime is "
        f"{_LIFECYCLE_LAMBDA_RUNTIME.name} — wheels would be built for the wrong version"
    )
    # The Function declares no `architecture`, so CDK defaults it to X86_64.
    assert "x86_64" in _LIFECYCLE_PIP_PLATFORM, (
        f"pip platform {_LIFECYCLE_PIP_PLATFORM} does not match the Function's "
        "default X86_64 architecture"
    )


# ── 3. runtime configuration ────────────────────────────────────────────────

def test_tracer_is_disabled_in_env(template):
    """Powertools Tracer must be disabled or it logs an error every invocation.

    Powertools 2.25.0 only self-disables *outside* Lambda. This Function does not
    enable X-Ray tracing, so in Lambda the Tracer finds no active segment and
    aws-xray-sdk (context_missing='LOG_ERROR' by default) logs an error per call.
    Either this env var is set, or the Function enables ACTIVE tracing — not neither.
    """
    fns = template.find_resources("AWS::Lambda::Function")
    lifecycle = [
        body for name, body in fns.items()
        if name.startswith("IoTLifecycleProcessor")
    ]
    assert len(lifecycle) == 1, f"expected exactly one lifecycle function, got {len(lifecycle)}"

    props = lifecycle[0]["Properties"]
    env = props.get("Environment", {}).get("Variables", {})
    tracing = props.get("TracingConfig", {}).get("Mode")
    assert env.get("POWERTOOLS_TRACE_DISABLED") == "true" or tracing == "Active", (
        "lifecycle Lambda has Tracer decorators but neither disables tracing nor "
        f"enables X-Ray (env={env.get('POWERTOOLS_TRACE_DISABLED')!r}, tracing={tracing!r})"
    )


def test_lifecycle_tables_present_in_env(template):
    fns = template.find_resources("AWS::Lambda::Function")
    lifecycle = [b for n, b in fns.items() if n.startswith("IoTLifecycleProcessor")][0]
    env = lifecycle["Properties"]["Environment"]["Variables"]
    for var in ("CONNECTIONS_TABLE", "SUBSCRIPTIONS_TABLE", "TOPICS_TABLE"):
        assert var in env, f"{var} missing from lifecycle Lambda environment"


# ── 4. the key schemas the handler writes to ────────────────────────────────

@pytest.mark.parametrize("table_name,expected", sorted(_EXPECTED_KEY_SCHEMAS.items()))
def test_table_key_schema_matches_handler_expectation(template, table_name, expected):
    """The handler writes these exact attribute names; drift here breaks it.

    Renaming the subscriptions sort key back to ``topic_name``, or the topics
    partition key to ``name``, reintroduces the ValidationException that made every
    subscribe event fail.
    """
    tables = template.find_resources("AWS::DynamoDB::Table")
    matching = [
        body for body in tables.values()
        if body["Properties"].get("TableName") == table_name
    ]
    assert matching, f"table {table_name} not found in synthesized template"

    actual = [
        (k["AttributeName"], k["KeyType"])
        for k in matching[0]["Properties"]["KeySchema"]
    ]
    assert actual == expected, (
        f"{table_name} key schema is {actual}, handler expects {expected}"
    )
