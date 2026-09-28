# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`make regenerate-runtime-config` writes `dmsUiOrigin` (FG5 of spec
2026-09-24-cms-diagnostics-tab-ia-redesign).

Before FG5 the target never wrote the key, so CMS built a relative "View RO"
link that opened its own 404 page
(issues/2026-09-25-diagnostics-view-ro-link-404/).

The target overwrites the live `runtimeConfig.json` after every deploy. So
these tests run the WHOLE recipe: `make -n` prints it, and `sh` runs it with a
fake `aws` first on PATH. The fake answers the stack-output reads with stubs
and records what `aws s3 cp` would upload. The tests assert on that upload,
which covers the order of the recipe's steps as well as its content. The
harness strips AWS credentials so nothing can reach real AWS.

The origin is resolved in the recipe's shell, never by make: make recursively
expands an environment variable referenced as $(VAR), so a value containing
$(shell …) would run at parse time, even under `make -n` (FG5 security review
cycle 2, W2).
"""

import json
import os
import pathlib
import stat
import subprocess

import pytest

DEPLOYMENT_DIR = pathlib.Path(__file__).resolve().parents[2]
MAKEFILE = DEPLOYMENT_DIR / "Makefile"
STAGING_ENV = DEPLOYMENT_DIR / "config" / "staging.env"
NO_DMS_STAGE = "no-such-stage-fg5"

# The 15 keys the target wrote before FG5 (and the served staging file carried).
BASE_KEYS = {
    "awsRegion", "mapAuth", "locationServices", "isDemoMode", "apiEndpoint", "wsEndpoint",
    "userPreferencesApiEndpoint", "awsCredentials", "dataProcessingApiEndpoint",
    "simulationApiEndpoint", "commandsApiEndpoint", "vsaApiEndpoint", "cognitoDomain",
    "_resolved_at", "_unresolved_endpoints",
}

FAKE_AWS = """#!/bin/sh
# Test double for the aws CLI. Stack reads get stubs; `s3 cp` records the upload.
case "$*" in
  *"describe-stacks"*"StackStatus"*) echo CREATE_COMPLETE ;;
  *"describe-stacks"*) printf 'stub-%s' "$(printf '%s' "$*" | sed -n "s/.*OutputKey==[\\`']\\([A-Za-z]*\\)[\\`'].*/\\1/p")" ;;
  *"list-stack-resources"*"S3::Bucket"*) echo stub-bucket ;;
  *"list-stack-resources"*"CloudFront"*) echo STUBDIST ;;
  *"location list-maps"*) echo None ;;
  *"s3 cp"*) [ -n "$FAKE_S3_FAIL" ] && exit 1; cp "$3" "$FAKE_UPLOAD" ;;
  *"create-invalidation"*) echo INV123 ;;
esac
exit 0
"""

HOSTILE = [
    "https://good.test'; touch {canary}; #",                              # closes a single-quoted value
    "https://good.test`touch {canary}`",                                  # shell command substitution
    "https://good.test$(touch {canary})",                                 # shell command substitution
    "https://good.test$(shell touch {canary})",                           # make function, suffix
    "$(shell touch {canary})https://good.test",                           # make function, prefix
    "$(shell touch {canary})",                                            # make function, whole value
    "https://good.test' + __import__('os').system('touch {canary}') + '",  # Python source
    "https://good.test\\'; touch {canary}; #",                            # backslash before the quote
]


class Result:
    def __init__(self, recipe, proc, uploaded, canary_after_make):
        self.recipe, self.proc, self.uploaded, self.canary_after_make = recipe, proc, uploaded, canary_after_make


def _run(tmp_path, stage=NO_DMS_STAGE, cwd=None, origin=None, s3_fail=False, mutate=None, canary=None):
    """`make -n` the target, then run the printed recipe under sh with the fake aws.

    Both steps get the same environment and working directory, so the recipe
    sees the same DMS_UI_CALLBACK_ORIGIN and the same `config/<stage>.env`.
    """
    cwd = cwd or DEPLOYMENT_DIR
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "aws"
    fake.write_text(FAKE_AWS)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    upload = tmp_path / "uploaded.json"
    env = {k: v for k, v in os.environ.items()
           if k not in ("DMS_UI_CALLBACK_ORIGIN", "DMS_API_ENDPOINT", "AWS_PROFILE",
                        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")}
    # If the fake ever fails to execute, sh would fall through to the real CLI:
    # give that CLI no credentials so the fall-through fails instead of calling AWS.
    env.update(PATH=f"{bindir}:{os.environ['PATH']}", FAKE_UPLOAD=str(upload),
               AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull,
               AWS_EC2_METADATA_DISABLED="true")
    if origin is not None:
        env["DMS_UI_CALLBACK_ORIGIN"] = origin
    if s3_fail:
        env["FAKE_S3_FAIL"] = "1"
    recipe = subprocess.run(
        ["make", "-n", "-f", str(MAKEFILE), "regenerate-runtime-config",
         f"DEPLOYMENT_STAGE={stage}", "COGNITO_DOMAIN=x.auth.example.test"],
        cwd=cwd, env=env, capture_output=True, text=True, check=True,
    ).stdout
    canary_after_make = canary.exists() if canary else None
    if mutate:
        recipe = mutate(recipe)
    proc = subprocess.run(["sh", "-c", recipe], cwd=cwd, env=env, capture_output=True, text=True)
    uploaded = json.loads(upload.read_text()) if upload.exists() else None
    return Result(recipe, proc, uploaded, canary_after_make)


def _stage_file(tmp_path, value):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "fg5test.env").write_text(f"DMS_UI_CALLBACK_ORIGIN={value}\n")
    return "fg5test"


# ── Resolution ────────────────────────────────────────────────────────────────

def test_falls_back_to_the_stage_config_file(tmp_path):
    r = _run(tmp_path, stage=_stage_file(tmp_path, "https://file.example.test"), cwd=tmp_path)
    assert r.proc.returncode == 0, r.proc.stderr
    assert r.uploaded["dmsUiOrigin"] == "https://file.example.test"


def test_environment_value_wins_over_the_config_file(tmp_path):
    r = _run(tmp_path, stage=_stage_file(tmp_path, "https://file.example.test"), cwd=tmp_path,
             origin="https://env.example.test")
    assert r.proc.returncode == 0, r.proc.stderr
    assert r.uploaded["dmsUiOrigin"] == "https://env.example.test"


def test_the_real_staging_config_resolves(tmp_path):
    # staging.env is excluded from the public mirror (.publish-exclude); skip there.
    if not STAGING_ENV.exists():
        pytest.skip("deployment/config/staging.env not present")
    lines = [l for l in STAGING_ENV.read_text().splitlines() if l.startswith("DMS_UI_CALLBACK_ORIGIN=")]
    if not lines:
        pytest.skip("staging.env carries no DMS_UI_CALLBACK_ORIGIN")
    r = _run(tmp_path, stage="staging")
    assert r.proc.returncode == 0, r.proc.stderr
    assert r.uploaded["dmsUiOrigin"] == lines[0].split("=", 1)[1].strip().rstrip("/")


# ── What the whole recipe uploads ─────────────────────────────────────────────

def test_uploads_the_base_keys_plus_dmsUiOrigin(tmp_path):
    r = _run(tmp_path, origin="https://dms.example.test")
    assert r.proc.returncode == 0, r.proc.stderr
    assert set(r.uploaded) == BASE_KEYS | {"dmsUiOrigin"}
    assert r.uploaded["dmsUiOrigin"] == "https://dms.example.test"


def test_uploads_only_the_base_keys_when_unset(tmp_path):
    r = _run(tmp_path)
    assert r.proc.returncode == 0, r.proc.stderr
    assert set(r.uploaded) == BASE_KEYS


def test_the_fake_aws_is_the_one_that_runs(tmp_path):
    r = _run(tmp_path)
    assert r.uploaded["awsCredentials"]["userPoolId"] == "stub-UserPoolId"


def test_a_trailing_slash_is_dropped(tmp_path):
    r = _run(tmp_path, origin="https://dms.example.test/")
    assert r.proc.returncode == 0, r.proc.stderr
    assert r.uploaded["dmsUiOrigin"] == "https://dms.example.test"


@pytest.mark.parametrize("value", [
    "http://dms.example.test",
    "https://dms.example.test/path",
    "https://dms.example.test; echo injected",
    "javascript:alert(1)",
    "https://.", "https://a..b", "https://-a.test", "https://a.test:123456",
    "https://good.test\nhttps://good.test",
    "https://good.test\r",
])
def test_a_refused_origin_uploads_nothing(tmp_path, value):
    r = _run(tmp_path, origin=value)
    assert r.proc.returncode != 0
    assert r.uploaded is None, f"{value!r} was refused but a file was still uploaded"
    assert "injected" not in r.proc.stdout


@pytest.mark.parametrize("template", HOSTILE)
def test_a_hostile_config_file_value_runs_nothing(tmp_path, template):
    # FG5 security review cycle 1, W1: the value was interpolated inside single
    # quotes, so a ' in `config/<stage>.env` ran the rest as shell with the
    # operator's AWS credentials.
    canary = tmp_path / "OWNED"
    r = _run(tmp_path, stage=_stage_file(tmp_path, template.format(canary=canary)), cwd=tmp_path, canary=canary)
    assert not canary.exists(), "the origin value was executed"
    assert r.proc.returncode != 0
    assert r.uploaded is None


@pytest.mark.parametrize("template", HOSTILE)
def test_a_hostile_environment_value_runs_nothing(tmp_path, template):
    # FG5 security review cycle 2, W2: through the environment, a $(shell …) in
    # the value ran when make expanded it, even under `make -n`.
    canary = tmp_path / "OWNED"
    r = _run(tmp_path, origin=template.format(canary=canary), canary=canary)
    assert r.canary_after_make is False, "the value was executed while make printed the recipe"
    assert not canary.exists(), "the origin value was executed"
    assert r.proc.returncode != 0
    assert r.uploaded is None


def test_a_broken_generator_uploads_nothing(tmp_path):
    # A generator failure (here, a syntax error) must not reach `aws s3 cp`:
    # the upload would replace the live runtimeConfig.json with an empty file.
    r = _run(tmp_path, mutate=lambda recipe: recipe.replace("print(json.dumps({", "print(json.dumps({{", 1))
    assert r.proc.returncode != 0
    assert r.uploaded is None


def test_a_failed_upload_fails_the_target(tmp_path):
    r = _run(tmp_path, s3_fail=True)
    assert r.proc.returncode != 0
    assert "uploaded to s3://" not in r.proc.stdout
