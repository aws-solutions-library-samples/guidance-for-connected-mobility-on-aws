"""Synth-based tests for ConnectorStack's OEM1 admin Lambda roles' vin-index grant.

Regression guard for issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/
and its follow-on live-verification finding: `_resolve_vins_to_vehicle_ids()` in
admin_refresh_vehicle_status/handler.py and admin_bulk_unenroll/handler.py queries
cms-{stage}-storage-vehicles' vin-index GSI to translate a caller's real VIN to its
vehicleId before the fleet-membership check runs. Neither Lambda's IAM role had a
`dynamodb:Query` grant on that GSI when the fix first shipped (commit `aab7247b`) --
confirmed via a live AccessDeniedException in CloudWatch on 2026-09-18 when the fix
was exercised against a real non-admin fleet-operator token for the first time.

These tests synthesise ConnectorStack in isolation (no app.py guards) using the CDK
Python API, then assert the IAM grant exists on both roles with the correct table
AND index -- a substring match against just "vin-index" would pass identically
whether the resource ARN pointed at the correct GSI or a nonexistent one, so the
assertion pins the full table name too.

Run with:
  cd deployment && .venv/bin/python -m pytest stacks/tests/test_connector_stack_oem1_vin_index.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent          # deployment/stacks/tests/
_STACKS = _HERE.parent                           # deployment/stacks/
_DEPLOYMENT = _STACKS.parent                     # deployment/

for _dir in (str(_DEPLOYMENT), str(_STACKS)):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

_STAGE = "staging"
_ACCOUNT = "123456789012"   # synthetic placeholder
_REGION = "us-west-2"

# The full, correct resource ARN fragment -- includes "storage-vehicles" so a
# regression that points at the base table name without "storage-", or at a
# different table entirely, is caught rather than passing on a bare "vin-index"
# substring match.
_VIN_INDEX_ARN_FRAGMENT = f"table/cms-{_STAGE}-storage-vehicles/index/vin-index"


@pytest.fixture(scope="module")
def template() -> dict:
    """Synthesise ConnectorStack and return the CloudFormation template dict."""
    try:
        from aws_cdk import App, Environment
        from stacks.connector_stack import ConnectorStack
    except ImportError as exc:
        pytest.skip(f"aws_cdk unavailable: {exc}")

    os.environ["DEPLOYMENT_STAGE"] = _STAGE
    os.environ["CONNECTOR_NAME"] = "oem1"
    os.environ["CONNECTOR_TYPE"] = "grpc_streaming"

    app = App()
    stack = ConnectorStack(
        app,
        "ConnectorStack",
        env=Environment(account=_ACCOUNT, region=_REGION),
    )
    synth_result = app.synth()
    raw = synth_result.get_stack_by_name("ConnectorStack").template
    return json.loads(json.dumps(raw))


# ---------------------------------------------------------------------------
# Helpers (mirrors test_commands_stack_sovd.py's convention)
# ---------------------------------------------------------------------------

def _of_type(t: dict, resource_type: str) -> dict[str, dict]:
    return {k: v for k, v in t["Resources"].items() if v["Type"] == resource_type}


def _iam_roles(t: dict) -> dict[str, dict]:
    return _of_type(t, "AWS::IAM::Role")


def _iam_policies(t: dict) -> dict[str, dict]:
    return _of_type(t, "AWS::IAM::Policy")


def _statement_actions(stmt: dict) -> list[str]:
    actions = stmt.get("Action", [])
    if isinstance(actions, str):
        return [actions]
    return actions


def _statement_resources(stmt: dict) -> list[str]:
    resources = stmt.get("Resource", [])
    if isinstance(resources, str):
        return [resources]
    result = []
    for r in resources:
        result.append(json.dumps(r) if isinstance(r, dict) else str(r))
    return result


def _statements_for_role(t: dict, role_name_fragment: str) -> list[dict]:
    """Return every IAM statement attached to a role whose RoleName contains
    the given fragment.

    CDK's `add_to_principal_policy` on an `iam.Role` attaches a SEPARATE
    `AWS::IAM::Policy` resource (named `<RoleLogicalId>DefaultPolicy...`) whose
    `Properties.Roles` references the role by logical ID -- it does NOT inline
    the statements into the Role resource's own `Properties.Policies`. Resolve
    role name -> logical ID -> matching policy resources -> statements.
    """
    target_role_logical_ids = {
        logical_id
        for logical_id, role in _iam_roles(t).items()
        if role_name_fragment in str(role.get("Properties", {}).get("RoleName", ""))
    }
    statements = []
    for policy in _iam_policies(t).values():
        roles_ref = policy.get("Properties", {}).get("Roles", [])
        referenced_ids = {
            r.get("Ref") for r in roles_ref if isinstance(r, dict) and "Ref" in r
        }
        if referenced_ids & target_role_logical_ids:
            doc = policy.get("Properties", {}).get("PolicyDocument", {})
            statements.extend(doc.get("Statement", []))
    return statements


class TestOEM1AdminRefreshStatusVinIndexGrant:
    """admin_refresh_vehicle_status's role must have dynamodb:Query on vin-index."""

    def test_refresh_status_role_has_vin_index_query(self, template: dict) -> None:
        statements = _statements_for_role(template, "oem1-refresh-status-role")
        assert statements, (
            "No IAM statements found on a role matching 'oem1-refresh-status-role'. "
            "The role-name matching in this test may be stale -- check "
            "connector_stack.py's OEM1AdminRefreshStatusRole role_name."
        )
        matching = [
            stmt for stmt in statements
            if "dynamodb:Query" in _statement_actions(stmt)
            and any(_VIN_INDEX_ARN_FRAGMENT in r for r in _statement_resources(stmt))
        ]
        assert matching, (
            f"No IAM statement with dynamodb:Query on '{_VIN_INDEX_ARN_FRAGMENT}' for "
            "OEM1AdminRefreshStatusRole. Without this grant, "
            "_resolve_vins_to_vehicle_ids() fails closed with AccessDeniedException "
            "for every non-admin caller. See "
            "issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/."
        )


class TestOEM1AdminBulkUnenrollVinIndexGrant:
    """admin_bulk_unenroll's role must have dynamodb:Query on vin-index."""

    def test_bulk_unenroll_role_has_vin_index_query(self, template: dict) -> None:
        statements = _statements_for_role(template, "oem1-bulk-unenroll-role")
        assert statements, (
            "No IAM statements found on a role matching 'oem1-bulk-unenroll-role'. "
            "The role-name matching in this test may be stale -- check "
            "connector_stack.py's OEM1AdminBulkUnenrollRole role_name."
        )
        matching = [
            stmt for stmt in statements
            if "dynamodb:Query" in _statement_actions(stmt)
            and any(_VIN_INDEX_ARN_FRAGMENT in r for r in _statement_resources(stmt))
        ]
        assert matching, (
            f"No IAM statement with dynamodb:Query on '{_VIN_INDEX_ARN_FRAGMENT}' for "
            "OEM1AdminBulkUnenrollRole. Without this grant, "
            "_resolve_vins_to_vehicle_ids() fails closed with AccessDeniedException "
            "for every non-admin caller. See "
            "issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/."
        )
