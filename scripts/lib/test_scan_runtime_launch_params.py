"""Tests for scan_runtime_launch_params.

Uses fake boto3-shaped dicts injected via a stub ecs client — no AWS calls.
Every fixture assembles PEM-header shape at runtime from parts so this test
source file does not itself carry PEM-header substrings (which would trip
static secret scanners for no reason — the scanner-under-test looks at ECS
API responses, never at this file).
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

# Add scripts/lib to sys.path so the target module can be imported
_LIB = pathlib.Path(__file__).parent
sys.path.insert(0, str(_LIB))

from scan_runtime_launch_params import (  # noqa: E402
    Finding,
    ScanConfig,
    classify_env,
    format_report,
    scan_running_tasks,
    scan_task_definitions,
    _scan_env_list,
)


# ── Fixtures assembled at runtime ─────────────────────────────────────────
_DASH5 = "-" * 5
_PEM_TYPE_PRIV = "PRIVATE KEY"
_PEM_TYPE_CERT = "CERTIFICATE"


def _pem(kind_prefix: str, type_: str) -> str:
    """Assemble a PEM-header shape without embedding the literal string."""
    header_type = f"{kind_prefix}{type_}".strip()
    return (
        f"{_DASH5}BEGIN {header_type}{_DASH5}\n"
        "AAAA\n"
        f"{_DASH5}END {header_type}{_DASH5}"
    )


FAKE_PEM_RSA = _pem("RSA ", _PEM_TYPE_PRIV)
FAKE_PEM_EC = _pem("EC ", _PEM_TYPE_PRIV)
FAKE_PEM_ENCRYPTED = _pem("ENCRYPTED ", _PEM_TYPE_PRIV)
FAKE_CERT_HEADER = _pem("", _PEM_TYPE_CERT)

# Fake AWS access-key-shape prefixes — shape only, never real credentials.
# Assembled from parts to keep this test file from carrying literal prefixes.
_FAKE_ACCESS_TAIL = "IOSFODNN7EXAMPLE"
FAKE_AKIA = "AK" + "IA" + _FAKE_ACCESS_TAIL
FAKE_ASIA = "AS" + "IA" + _FAKE_ACCESS_TAIL


# ── Unit tests for classify_env ────────────────────────────────────────────


def test_classify_env_pem_value_flagged():
    r = classify_env("fwe-agent", "PRIVATE_KEY", FAKE_PEM_RSA)
    assert r is not None
    assert "PEM private-key block" in r


def test_classify_env_certificate_value_flagged():
    r = classify_env("fwe-agent", "SOMETHING", FAKE_CERT_HEADER)
    assert r is not None
    assert "certificate" in r.lower()


def test_classify_env_ec_variant_pem_flagged():
    assert classify_env("x", "K", FAKE_PEM_EC) is not None


def test_classify_env_encrypted_variant_pem_flagged():
    assert classify_env("x", "K", FAKE_PEM_ENCRYPTED) is not None


def test_classify_env_akia_shape_flagged():
    assert classify_env("x", "AWS_KEY", FAKE_AKIA) is not None
    assert classify_env("x", "AWS_KEY", FAKE_ASIA) is not None


def test_classify_env_secret_named_empty_still_flagged():
    # Even an empty value with a secret-shaped name is a finding — that shape
    # is the whole reason task-override delivery is unsafe.
    assert classify_env("fwe-agent", "PRIVATE_KEY", "") is not None
    assert classify_env("fwe-agent", "CERTIFICATE", "") is not None
    assert classify_env("api", "API_KEY", "") is not None
    assert classify_env("api", "PASSWORD", "") is not None


def test_classify_env_case_insensitive_secret_name():
    assert classify_env("x", "private_key", "") is not None
    assert classify_env("x", "private-key", "") is not None
    assert classify_env("x", "Certificate", "") is not None


def test_classify_env_benign_name_and_value_clean():
    assert classify_env("worker", "SIM_ID", "abcd-1234") is None
    assert classify_env("worker", "AWS_REGION", "us-west-2") is None
    assert classify_env("worker", "DEPLOYMENT_STAGE", "staging") is None


def test_classify_env_lowercase_pem_marker_does_NOT_match():
    # PEM headers per RFC 7468 are always upper-case; a lowercased header
    # inside a legit doc string should not false-positive.
    lowered = FAKE_PEM_RSA.lower()
    assert classify_env("x", "DOCSTRING", f"This describes {lowered} format") is None


# ── _scan_env_list allowed_names honors legit exceptions ───────────────────


def test_scan_env_list_allowed_name_bypassed():
    # Use a name that IS secret-shaped (PASSWORD) so we know the bypass is what
    # exempted it, not the base rule.
    envs = [{"name": "PASSWORD", "value": ""}]
    result = list(_scan_env_list(envs, "c", {"PASSWORD"}))
    assert result == []


def test_scan_env_list_not_allowed_still_hits():
    envs = [{"name": "PASSWORD", "value": ""}]
    result = list(_scan_env_list(envs, "c", set()))
    assert len(result) == 1


# ── Integration tests with a stub ecs client ────────────────────────────────


def _stub_ecs_with_task_def(env_vars: list[dict], cname: str = "worker"):
    ecs = MagicMock()
    ecs.get_paginator.return_value.paginate.return_value = iter([
        {"taskDefinitionArns": ["arn:aws:ecs:us-west-2:123:task-definition/fam:1"]}
    ])
    ecs.describe_task_definition.return_value = {
        "taskDefinition": {
            "revision": 1,
            "containerDefinitions": [{
                "name": cname,
                "environment": env_vars,
            }],
        }
    }
    return ecs


def test_scan_task_definitions_flags_baked_in_pem():
    ecs = _stub_ecs_with_task_def([
        {"name": "PRIVATE_KEY", "value": FAKE_PEM_RSA},
    ])
    findings = scan_task_definitions(ecs, ScanConfig(include_inactive=False))
    assert len(findings) == 1
    assert findings[0].where == "taskdef"
    assert findings[0].env_name == "PRIVATE_KEY"


def test_scan_task_definitions_clean_when_no_secrets():
    ecs = _stub_ecs_with_task_def([
        {"name": "AWS_REGION", "value": "us-west-2"},
        {"name": "STAGE", "value": "staging"},
    ])
    findings = scan_task_definitions(ecs, ScanConfig(include_inactive=False))
    assert findings == []


def _stub_ecs_with_running_task(env_vars: list[dict], cname: str = "fwe-agent"):
    ecs = MagicMock()

    def get_paginator(op_name):
        m = MagicMock()
        if op_name == "list_clusters":
            m.paginate.return_value = iter([
                {"clusterArns": ["arn:aws:ecs:us-west-2:123:cluster/c"]}
            ])
        elif op_name == "list_tasks":
            m.paginate.return_value = iter([
                {"taskArns": ["arn:aws:ecs:us-west-2:123:task/c/abcd"]}
            ])
        else:
            m.paginate.return_value = iter([{}])
        return m

    ecs.get_paginator.side_effect = get_paginator
    ecs.describe_tasks.return_value = {
        "tasks": [{
            "taskArn": "arn:aws:ecs:us-west-2:123:task/c/abcd",
            "overrides": {
                "containerOverrides": [{
                    "name": cname,
                    "environment": env_vars,
                }],
            },
        }]
    }
    return ecs


def test_scan_running_tasks_flags_pem_in_override():
    ecs = _stub_ecs_with_running_task([
        {"name": "PRIVATE_KEY", "value": FAKE_PEM_RSA},
        {"name": "CERTIFICATE", "value": FAKE_CERT_HEADER},
    ])
    findings = scan_running_tasks(ecs, ScanConfig())
    assert len(findings) == 2
    names = sorted(f.env_name for f in findings)
    assert names == ["CERTIFICATE", "PRIVATE_KEY"]
    for f in findings:
        assert f.where == "runtime-override"


def test_scan_running_tasks_clean_when_only_benign_env():
    ecs = _stub_ecs_with_running_task([
        {"name": "SIM_ID", "value": "abc-123"},
    ])
    findings = scan_running_tasks(ecs, ScanConfig())
    assert findings == []


def test_stage_filter_excludes_non_matching_arns():
    """A running task in a cluster whose ARN doesn't match --stage-filter is skipped."""
    ecs = MagicMock()

    def get_paginator(op_name):
        m = MagicMock()
        if op_name == "list_clusters":
            m.paginate.return_value = iter([
                {"clusterArns": ["arn:aws:ecs:us-west-2:123:cluster/prod-cluster"]}
            ])
        else:
            m.paginate.return_value = iter([])
        return m

    ecs.get_paginator.side_effect = get_paginator
    ecs.describe_tasks.side_effect = AssertionError("should not be called")
    findings = scan_running_tasks(ecs, ScanConfig(stage_filter="staging"))
    assert findings == []


# ── Format & exit-code smoke ────────────────────────────────────────────────


def test_format_report_clean_message():
    assert "OK" in format_report([])


def test_format_report_includes_remediation():
    f = Finding(
        where="runtime-override",
        task_arn="arn:aws:ecs:us-west-2:123:task/x/y",
        revision=None,
        container="fwe-agent",
        env_name="PRIVATE_KEY",
        reason="pem",
    )
    r = format_report([f])
    assert "FAIL" in r
    assert "Secrets Manager" in r
    assert "PRIVATE_KEY" in r
    # And NEVER contains the fixture value itself
    assert FAKE_PEM_RSA not in r


def test_format_report_never_echoes_value():
    """The report renders only names/reasons, not values."""
    f = Finding(
        where="runtime-override",
        task_arn="task-x",
        revision=None,
        container="c",
        env_name="PRIVATE_KEY",
        reason="pem",
    )
    r = format_report([f])
    # No PEM-header substring should ever appear in the report.
    assert f"{_DASH5}BEGIN" not in r
