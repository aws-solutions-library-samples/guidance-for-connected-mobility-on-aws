"""Synth tests for the commands Lambda's driver-self grant.

Issue: issues/2026-09-26-ios-controls-403-driver-self-commands-api/

The Lambda reads one driver row per driver-self request to find the driver's
assigned vehicle. The grant must be exactly dynamodb:GetItem on the drivers
table: a wider grant would let this role write or enumerate driver records.

Run: cd deployment && .venv/bin/python -m pytest stacks/tests/test_commands_stack_driver_self.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
for _dir in (str(_HERE.parent.parent), str(_HERE.parent)):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

_STAGE = "staging"
_ACCOUNT = "123456789012"
_REGION = "us-west-2"
_DRIVERS_TABLE = f"cms-{_STAGE}-storage-drivers"
_DRIVERS_TABLE_ARN = f"arn:aws:dynamodb:{_REGION}:{_ACCOUNT}:table/{_DRIVERS_TABLE}"


@pytest.fixture(scope="module")
def template() -> dict:
    try:
        from aws_cdk import App, Environment
        from stacks.commands_stack import CommandsStack
    except ImportError as exc:
        pytest.skip(f"aws_cdk unavailable: {exc}")
    os.environ["DEPLOYMENT_STAGE"] = _STAGE
    app = App()
    CommandsStack(app, f"cms-{_STAGE}-commands", env=Environment(account=_ACCOUNT, region=_REGION))
    raw = app.synth().get_stack_by_name(f"cms-{_STAGE}-commands").template
    return json.loads(json.dumps(raw))


def _statements(t: dict) -> list[dict]:
    out = []
    for res in t["Resources"].values():
        if res["Type"] == "AWS::IAM::Policy":
            out.extend(res["Properties"]["PolicyDocument"]["Statement"])
    return out


def _as_list(v) -> list:
    return v if isinstance(v, list) else [v]


def _commands_api_env(t: dict) -> dict:
    fns = [r for r in t["Resources"].values()
           if r["Type"] == "AWS::Lambda::Function"
           and "commands-api" in r["Properties"].get("FunctionName", "")]
    assert len(fns) == 1, "expected exactly one commands-api Lambda"
    return fns[0]["Properties"]["Environment"]["Variables"]


def test_commands_lambda_env_names_the_drivers_table(template: dict) -> None:
    assert _commands_api_env(template).get("DRIVERS_TABLE") == _DRIVERS_TABLE


def test_drivers_table_grant_is_exactly_getitem_on_the_table(template: dict) -> None:
    matching = [s for s in _statements(template)
                if any("storage-drivers" in json.dumps(r) for r in _as_list(s.get("Resource", [])))]
    assert len(matching) == 1, f"expected one drivers-table statement, got {matching}"
    stmt = matching[0]
    assert stmt["Effect"] == "Allow"
    assert _as_list(stmt["Action"]) == ["dynamodb:GetItem"]
    assert _as_list(stmt["Resource"]) == [_DRIVERS_TABLE_ARN]


def test_no_dynamodb_statement_uses_a_wildcard_resource(template: dict) -> None:
    """A Resource '*' on any DynamoDB action would include the drivers table."""
    for stmt in _statements(template):
        actions = _as_list(stmt.get("Action", []))
        if any(a.startswith("dynamodb:") for a in actions):
            assert "*" not in _as_list(stmt.get("Resource", [])), stmt
