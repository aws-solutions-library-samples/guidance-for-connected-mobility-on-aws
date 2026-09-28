"""Synth-based tests for the SOVD additions to CommandsStack.

Spec: .kiro/specs/2026-09-01-cms-remote-diagnostics-sovd/ (Group 2 Task 2.2)

These tests synthesise CommandsStack in isolation (no app.py guards) using the
CDK Python API, then assert structural invariants on the CloudFormation template.

Run with:
  cd deployment && .venv/bin/python -m pytest stacks/tests/test_commands_stack_sovd.py -v

Or from the project root:
  PYTHONPATH=deployment/.venv/lib/python3.13/site-packages \\
    python3 -m pytest deployment/stacks/tests/test_commands_stack_sovd.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure the deployment directory is on sys.path so `stacks.*` imports work.
_HERE = Path(__file__).resolve().parent          # deployment/stacks/tests/
_STACKS = _HERE.parent                           # deployment/stacks/
_DEPLOYMENT = _STACKS.parent                     # deployment/

for _dir in (str(_DEPLOYMENT), str(_STACKS)):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

# ---------------------------------------------------------------------------
# Lazy fixtures: synthesise the stack once per module. Skip if CDK unavailable.
# ---------------------------------------------------------------------------

_STAGE = "staging"
_ACCOUNT = "123456789012"   # synthetic placeholder
_REGION = "us-west-2"

_BUCKET_NAME = f"cms-{_STAGE}-storage-sovd-responses-{_REGION}-{_ACCOUNT}"
_SOVD_RULE_SQL = "SELECT *, topic() AS topic FROM 'cms/commands/things/+/executions/+/sovd/response'"
_VEHICLE_ID_INDEX_ARN_FRAGMENT = "storage-fleet-enrollment/index/vehicleId-index"
_DTC_HISTORY_TABLE_FRAGMENT = "storage-dtc-history"


@pytest.fixture(scope="module")
def template() -> dict:
    """Synthesise CommandsStack and return the CloudFormation template dict."""
    try:
        from aws_cdk import App, Environment
        from stacks.commands_stack import CommandsStack
    except ImportError as exc:
        pytest.skip(f"aws_cdk unavailable: {exc}")

    os.environ["DEPLOYMENT_STAGE"] = _STAGE

    app = App()
    stack = CommandsStack(
        app,
        f"cms-{_STAGE}-commands",
        env=Environment(account=_ACCOUNT, region=_REGION),
    )
    synth_result = app.synth()
    raw = synth_result.get_stack_by_name(f"cms-{_STAGE}-commands").template
    return json.loads(json.dumps(raw))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _of_type(t: dict, resource_type: str) -> dict[str, dict]:
    return {k: v for k, v in t["Resources"].items() if v["Type"] == resource_type}


def _buckets(t: dict) -> dict[str, dict]:
    return _of_type(t, "AWS::S3::Bucket")


def _iot_rules(t: dict) -> dict[str, dict]:
    return _of_type(t, "AWS::IoT::TopicRule")


def _iam_policies(t: dict) -> dict[str, dict]:
    return _of_type(t, "AWS::IAM::Policy")


def _all_iam_statements(t: dict) -> list[dict]:
    """Flatten all IAM statements from all policies in the template."""
    statements = []
    for policy in _iam_policies(t).values():
        doc = policy.get("Properties", {}).get("PolicyDocument", {})
        statements.extend(doc.get("Statement", []))
    return statements


def _statement_actions(stmt: dict) -> list[str]:
    actions = stmt.get("Action", [])
    if isinstance(actions, str):
        return [actions]
    return actions


def _statement_resources(stmt: dict) -> list[str]:
    resources = stmt.get("Resource", [])
    if isinstance(resources, str):
        return [resources]
    # Resources may be dicts (Fn::Sub, Fn::Join, etc.) — convert to string for grep
    result = []
    for r in resources:
        result.append(json.dumps(r))
    return result


# ---------------------------------------------------------------------------
# Test: SOVD S3 bucket
# ---------------------------------------------------------------------------

class TestSovdResponsesBucket:

    def test_bucket_exists_with_correct_name(self, template: dict) -> None:
        """The new SOVD responses bucket is present with the region+account-suffixed name."""
        buckets = _buckets(template)
        names = [
            v.get("Properties", {}).get("BucketName", "")
            for v in buckets.values()
        ]
        assert any(_BUCKET_NAME in name for name in names), (
            f"Expected bucket with name containing '{_BUCKET_NAME}'. "
            f"Found names: {names}"
        )

    def test_bucket_deletion_policy_is_retain(self, template: dict) -> None:
        """DeletionPolicy must be Retain — never rely on CDK defaults for named buckets."""
        buckets = _buckets(template)
        sovd_buckets = {
            k: v for k, v in buckets.items()
            if _BUCKET_NAME in v.get("Properties", {}).get("BucketName", "")
        }
        assert sovd_buckets, f"SOVD bucket not found in template resources"
        for logical_id, bucket in sovd_buckets.items():
            assert bucket.get("DeletionPolicy") == "Retain", (
                f"Bucket {logical_id} has DeletionPolicy={bucket.get('DeletionPolicy')!r}, "
                "expected Retain. See cross-region-namespace.md and spec § Constraints."
            )

    def test_bucket_has_30_day_lifecycle_rule(self, template: dict) -> None:
        """The SOVD bucket must have a lifecycle rule expiring objects after 30 days."""
        buckets = _buckets(template)
        sovd_buckets = {
            k: v for k, v in buckets.items()
            if _BUCKET_NAME in v.get("Properties", {}).get("BucketName", "")
        }
        assert sovd_buckets, "SOVD bucket not found in template resources"
        for logical_id, bucket in sovd_buckets.items():
            props = bucket.get("Properties", {})
            lifecycle = props.get("LifecycleConfiguration", {})
            rules = lifecycle.get("Rules", [])
            assert rules, f"Bucket {logical_id} has no lifecycle rules"
            expiration_days = [
                r.get("ExpirationInDays")
                for r in rules
                if r.get("Status") == "Enabled" and r.get("ExpirationInDays")
            ]
            assert expiration_days, (
                f"Bucket {logical_id} has no enabled lifecycle rule with ExpirationInDays. "
                f"Rules: {rules}"
            )
            assert any(d == 30 for d in expiration_days), (
                f"Expected a 30-day expiry rule. Found ExpirationInDays values: {expiration_days}"
            )

    def test_bucket_count_increased_by_exactly_one(self, template: dict) -> None:
        """Exactly 1 new S3 bucket was added to the stack (the SOVD responses bucket).

        The original CommandsStack had 0 buckets; this spec adds exactly 1.
        A count higher than 1 means an unintended bucket was added.
        """
        count = len(_buckets(template))
        assert count == 1, (
            f"Expected exactly 1 S3 bucket in CommandsStack (the SOVD responses bucket); "
            f"found {count}. The original stack had 0 buckets. "
            f"Logical IDs: {list(_buckets(template).keys())}"
        )

    def test_bucket_enforces_ssl(self, template: dict) -> None:
        """FG2.4: SOVD bucket policy must deny non-TLS requests (enforce_ssl=True).

        CDK enforce_ssl=True synthesizes a BucketPolicy statement:
          Effect=Deny, Principal=*, Action=s3:*, Condition.Bool['aws:SecureTransport']=false
        """
        bucket_policies = _of_type(template, "AWS::S3::BucketPolicy")
        assert bucket_policies, "No BucketPolicy resources found in template"

        found_deny_non_tls = False
        for logical_id, policy in bucket_policies.items():
            document = policy.get("Properties", {}).get("PolicyDocument", {})
            for stmt in document.get("Statement", []):
                effect = stmt.get("Effect", "")
                principal = stmt.get("Principal", "")
                actions = stmt.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                condition = stmt.get("Condition", {})

                # Match: Deny, *, s3:*, aws:SecureTransport == false
                principal_is_star = principal == "*" or principal == {"AWS": "*"}
                has_s3_star = "s3:*" in actions
                # CDK generates condition as {"Bool": {"aws:SecureTransport": "false"}}
                condition_bool = condition.get("Bool", {})
                secure_transport_val = condition_bool.get("aws:SecureTransport", "")
                is_secure_transport_false = str(secure_transport_val).lower() == "false"

                if (
                    effect == "Deny"
                    and principal_is_star
                    and has_s3_star
                    and is_secure_transport_false
                ):
                    found_deny_non_tls = True
                    break
            if found_deny_non_tls:
                break

        assert found_deny_non_tls, (
            "No BucketPolicy statement found with Effect=Deny, Principal=*, "
            "Action=s3:*, Condition.Bool['aws:SecureTransport']=false. "
            "The SOVD bucket must have enforce_ssl=True. "
            f"Policies found: {json.dumps({k: v.get('Properties', {}).get('PolicyDocument') for k, v in bucket_policies.items()}, indent=2)}"
        )

    def test_bucket_blocks_all_public_access(self, template: dict) -> None:
        """FG2.4: SOVD bucket must have all four PublicAccessBlock settings set to True."""
        buckets = _buckets(template)
        sovd_buckets = {
            k: v for k, v in buckets.items()
            if _BUCKET_NAME in v.get("Properties", {}).get("BucketName", "")
        }
        assert sovd_buckets, "SOVD bucket not found in template"
        for logical_id, bucket in sovd_buckets.items():
            props = bucket.get("Properties", {})
            bpa = props.get("PublicAccessBlockConfiguration", {})
            assert bpa, (
                f"Bucket {logical_id} has no PublicAccessBlockConfiguration. "
                "Add block_public_access=s3.BlockPublicAccess.BLOCK_ALL."
            )
            for key in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"):
                assert bpa.get(key) is True, (
                    f"Bucket {logical_id} PublicAccessBlockConfiguration.{key} is not True; "
                    f"got: {bpa.get(key)!r}. "
                    "block_public_access=s3.BlockPublicAccess.BLOCK_ALL must set all four keys."
                )


# ---------------------------------------------------------------------------
# Test: SOVD IoT rule
# ---------------------------------------------------------------------------

class TestSovdIoTRule:

    def test_sovd_rule_is_present(self, template: dict) -> None:
        """The new SOVD IoT rule is present in the template."""
        rules = _iot_rules(template)
        assert rules, "No IoT rules found in template"
        rule_names = [
            v.get("Properties", {}).get("RuleName", "")
            for v in rules.values()
        ]
        assert any("sovd" in name for name in rule_names), (
            f"No IoT rule with 'sovd' in its name. Found: {rule_names}"
        )

    def test_sovd_rule_has_correct_sql(self, template: dict) -> None:
        """The SOVD IoT rule must route the correct sub-path SQL."""
        rules = _iot_rules(template)
        sovd_rules = {
            k: v for k, v in rules.items()
            if "sovd" in v.get("Properties", {}).get("RuleName", "")
        }
        assert sovd_rules, "SOVD IoT rule not found"
        for logical_id, rule in sovd_rules.items():
            sql = rule["Properties"]["TopicRulePayload"]["Sql"]
            assert _SOVD_RULE_SQL == sql, (
                f"SOVD rule {logical_id} has unexpected SQL: {sql!r}\n"
                f"Expected: {_SOVD_RULE_SQL!r}"
            )

    def test_existing_actuator_rules_unchanged(self, template: dict) -> None:
        """The existing command_response_rule and fwe_command_response_rule must not be changed."""
        rules = _iot_rules(template)
        rule_names = {
            v.get("Properties", {}).get("RuleName", "")
            for v in rules.values()
        }
        assert any("command_response_rule" in name and "sovd" not in name
                   for name in rule_names), (
            f"Legacy command_response_rule is missing. Names: {rule_names}"
        )
        assert any("fwe_command_response_rule" in name for name in rule_names), (
            f"Legacy fwe_command_response_rule is missing. Names: {rule_names}"
        )


# ---------------------------------------------------------------------------
# Test: IAM grants
# ---------------------------------------------------------------------------

class TestIamGrants:

    def test_commands_lambda_has_dynamodb_query_on_vehicle_id_index(
        self, template: dict
    ) -> None:
        """commands Lambda role must have dynamodb:Query on the vehicleId-index (fleet_membership)."""
        statements = _all_iam_statements(template)
        matching = [
            stmt for stmt in statements
            if "dynamodb:Query" in _statement_actions(stmt)
            and any(_VEHICLE_ID_INDEX_ARN_FRAGMENT in r
                    for r in _statement_resources(stmt))
        ]
        assert matching, (
            f"No IAM statement with dynamodb:Query on '{_VEHICLE_ID_INDEX_ARN_FRAGMENT}'. "
            "The commands Lambda needs this to call fleet_membership.resolve_vins_to_fleets(). "
            "See spec § Design § 6 and § Interfaces."
        )

    def test_commands_lambda_has_s3_put_on_sovd_bucket(
        self, template: dict
    ) -> None:
        """commands Lambda role must have s3:PutObject on the SOVD responses bucket."""
        statements = _all_iam_statements(template)
        matching = [
            stmt for stmt in statements
            if "s3:PutObject" in _statement_actions(stmt)
            and any(_BUCKET_NAME in r for r in _statement_resources(stmt))
        ]
        assert matching, (
            f"No IAM statement with s3:PutObject on bucket '{_BUCKET_NAME}'. "
            "Both Lambda roles must be able to write oversized SOVD payloads. "
            "See spec § Interfaces."
        )

    def test_response_handler_has_s3_get_only_on_sovd_bucket(
        self, template: dict
    ) -> None:
        """FG2.5: response handler Lambda role has s3:GetObject (not s3:PutObject) on the SOVD bucket.

        The response handler does not upload to S3 — only the sidecar does.
        s3:PutObject was removed (SR Cycle 2 Suggestion #1) to tighten least-privilege.
        s3:GetObject is retained for generate_presigned_url on the history read route.
        """
        statements = _all_iam_statements(template)

        # Must have s3:GetObject on the SOVD bucket
        get_matching = [
            stmt for stmt in statements
            if "s3:GetObject" in _statement_actions(stmt)
            and any(_BUCKET_NAME in r for r in _statement_resources(stmt))
        ]
        assert get_matching, (
            f"No IAM statement with s3:GetObject on bucket '{_BUCKET_NAME}'. "
            "The response handler needs GetObject for pre-signed URL generation. "
            "See spec § Interfaces."
        )

        # Must NOT have a standalone s3:PutObject (without GetObject) on the SOVD bucket
        # from the response handler role. Note: the commands Lambda still has PutObject —
        # we verify that policy statements with ONLY PutObject (no GetObject) pointing at
        # the SOVD bucket are absent, which catches the old single-action s3:PutObject grant.
        put_only_matching = [
            stmt for stmt in statements
            if _statement_actions(stmt) == ["s3:PutObject"]
            and any(_BUCKET_NAME in r for r in _statement_resources(stmt))
        ]
        assert not put_only_matching, (
            f"Found an IAM statement with ONLY s3:PutObject on the SOVD bucket. "
            "The response handler must not have s3:PutObject — it was removed by FG2.5. "
            f"Matching statements: {put_only_matching}"
        )

    def test_s3_grants_cover_bucket_contents(self, template: dict) -> None:
        """S3 grants must include both the bucket ARN and the '/*' prefix for object access."""
        statements = _all_iam_statements(template)
        for stmt in statements:
            actions = _statement_actions(stmt)
            if not ("s3:PutObject" in actions or "s3:GetObject" in actions):
                continue
            resources = _statement_resources(stmt)
            sovd_resources = [r for r in resources if _BUCKET_NAME in r]
            if not sovd_resources:
                continue
            # At least one resource must end with /* for object-level access
            has_object_resource = any(r.endswith('/*"') or r.endswith("/*") for r in sovd_resources)
            assert has_object_resource, (
                f"S3 grant on SOVD bucket lacks the '/*' suffix for object access. "
                f"Resources: {sovd_resources}"
            )

    def test_commands_lambda_env_vars_include_sovd(self, template: dict) -> None:
        """commands Lambda must have SOVD_RESPONSES_BUCKET, FLEET_ENROLLMENT_TABLE_NAME,
        DTC_HISTORY_TABLE in its environment."""
        lambdas = _of_type(template, "AWS::Lambda::Function")
        # Find the commands API lambda by function name
        commands_lambdas = {
            k: v for k, v in lambdas.items()
            if "commands-api" in v.get("Properties", {}).get("FunctionName", "")
        }
        assert commands_lambdas, "commands-api Lambda not found in template"
        for logical_id, fn in commands_lambdas.items():
            env_vars_list = (
                fn.get("Properties", {})
                .get("Environment", {})
                .get("Variables", {})
            )
            env_keys = list(env_vars_list.keys()) if isinstance(env_vars_list, dict) else []
            for required_key in ("SOVD_RESPONSES_BUCKET", "FLEET_ENROLLMENT_TABLE_NAME", "DTC_HISTORY_TABLE"):
                assert required_key in env_keys, (
                    f"commands Lambda {logical_id} is missing env var {required_key!r}. "
                    f"Present: {env_keys}"
                )

    def test_response_handler_env_vars_include_sovd(self, template: dict) -> None:
        """response handler Lambda must have SOVD_RESPONSES_BUCKET and DTC_HISTORY_TABLE."""
        lambdas = _of_type(template, "AWS::Lambda::Function")
        response_lambdas = {
            k: v for k, v in lambdas.items()
            if "response-handler" in v.get("Properties", {}).get("FunctionName", "")
        }
        assert response_lambdas, "command-response-handler Lambda not found in template"
        for logical_id, fn in response_lambdas.items():
            env_vars_list = (
                fn.get("Properties", {})
                .get("Environment", {})
                .get("Variables", {})
            )
            env_keys = list(env_vars_list.keys()) if isinstance(env_vars_list, dict) else []
            for required_key in ("SOVD_RESPONSES_BUCKET", "DTC_HISTORY_TABLE"):
                assert required_key in env_keys, (
                    f"response handler Lambda {logical_id} is missing env var {required_key!r}. "
                    f"Present: {env_keys}"
                )


# ---------------------------------------------------------------------------
# Test: Task 5.2 Part A — bundling, response handler IAM, IoT invoke permission
# ---------------------------------------------------------------------------

class TestFleetMembershipBundled:
    """T5.2 Part A.1 — _lib/fleet_membership.py is bundled into the commands Lambda asset."""

    def test_fleet_membership_py_in_build_dir(self, template: dict) -> None:  # noqa: ARG002
        """_lib/fleet_membership.py must exist in the commands Lambda build directory.

        The stack calls _bundle_commands_lambda() at synth time which copies
        services/connectors/oem1/_lib/ into .build/commands_lambda/_lib/.
        We verify the build dir directly (the template fixture already called the
        bundler as a side-effect of instantiating CommandsStack).
        """
        import os
        # Import the build dir path from commands_stack without re-running bundling.
        from stacks.commands_stack import _COMMANDS_LAMBDA_BUILD_DIR
        fleet_membership_path = os.path.join(
            _COMMANDS_LAMBDA_BUILD_DIR, "_lib", "fleet_membership.py"
        )
        assert os.path.isfile(fleet_membership_path), (
            f"fleet_membership.py not found in the commands Lambda build dir at "
            f"{fleet_membership_path}. "
            "The commands_stack bundler must copy services/connectors/oem1/_lib/ "
            "into the build directory so the Lambda can import _lib.fleet_membership. "
            "See spec § Design § 6 and .kiro/specs/.../decisions.md § overlay path."
        )

    def test_fleet_membership_py_imports_correctly(self, template: dict) -> None:  # noqa: ARG002
        """_lib.fleet_membership must be importable from the bundled build directory.

        This checks that the overlay not only copies the file but that the file
        parses without syntax errors and exposes the required helpers.
        """
        import importlib
        import importlib.util
        import os
        import sys
        from stacks.commands_stack import _COMMANDS_LAMBDA_BUILD_DIR

        # Temporarily insert the build dir and its _lib sub-dir so fleet_membership
        # resolves its own relative imports (e.g. data_source).
        lib_dir = os.path.join(_COMMANDS_LAMBDA_BUILD_DIR, "_lib")
        build_dir = _COMMANDS_LAMBDA_BUILD_DIR

        prev_path = list(sys.path)
        sys.path.insert(0, lib_dir)
        sys.path.insert(0, build_dir)
        try:
            spec = importlib.util.spec_from_file_location(
                "fleet_membership",
                os.path.join(lib_dir, "fleet_membership.py"),
            )
            assert spec is not None, "Could not create module spec for fleet_membership.py"
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            for fn_name in ("classify_driver_self", "parse_fleet_ids"):
                assert hasattr(mod, fn_name), (
                    f"fleet_membership module is missing expected function '{fn_name}'. "
                    "See spec § Design § 6 and Task 1.4 overlay-note."
                )
        finally:
            sys.path[:] = prev_path


class TestSharedModuleBundled:
    """F2.1 — _shared/routine_catalog.py is bundled into the commands Lambda asset.

    An import that works locally proves nothing about what ships — this test
    asserts presence in the build directory at synth time, not in the process
    that runs the test. Same shape as TestFleetMembershipBundled above.
    """

    def test_shared_routine_catalog_py_in_build_dir(self, template: dict) -> None:  # noqa: ARG002
        """_shared/routine_catalog.py must exist in the commands Lambda build directory.

        The bundler overlays services/_shared/ into .build/commands_lambda/_shared/.
        Without this, the Lambda cannot import ``_shared.routine_catalog`` and
        reads safety_class from the request instead — the F28 fail-open defect.
        """
        import os
        from stacks.commands_stack import _COMMANDS_LAMBDA_BUILD_DIR
        routine_catalog_path = os.path.join(
            _COMMANDS_LAMBDA_BUILD_DIR, "_shared", "routine_catalog.py"
        )
        assert os.path.isfile(routine_catalog_path), (
            f"routine_catalog.py not found in the commands Lambda build dir at "
            f"{routine_catalog_path}. "
            "The commands_stack bundler must overlay services/_shared/ into _shared/ "
            "so the Lambda can import _shared.routine_catalog. "
            "F28 root cause: a safety authority that ships only in deployment/scripts/ "
            "is not reachable by the runtime. See Fix Group 2 and "
            "issues/2026-09-08-safety-class-taken-from-request-fail-open/."
        )

    def test_shared_init_py_in_build_dir(self, template: dict) -> None:  # noqa: ARG002
        """_shared/__init__.py must also be present so `_shared` is a package.

        Without __init__.py Python treats _shared/ as a namespace package rather
        than a regular package; under some import configurations this silently
        prevents `from _shared.routine_catalog import ...` from resolving.
        """
        import os
        from stacks.commands_stack import _COMMANDS_LAMBDA_BUILD_DIR
        init_path = os.path.join(_COMMANDS_LAMBDA_BUILD_DIR, "_shared", "__init__.py")
        assert os.path.isfile(init_path), (
            f"_shared/__init__.py not found in the commands Lambda build dir at "
            f"{init_path}. "
            "services/_shared/__init__.py must be present in the overlay for Python "
            "to treat _shared/ as a regular package."
        )

    def test_shared_routine_catalog_not_duplicated_in_service_dir(
        self, template: dict  # noqa: ARG002
    ) -> None:
        """routine_catalog.py must NOT appear in services/commands/ directly.

        A copy in services/commands/ would drift silently from services/_shared/ —
        the exact failure mode that produced F27 (two sources of truth for the
        safety-class table). One module, one source of truth, overlaid at bundle time.
        """
        import os
        commands_src = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "services", "commands",
        ))
        direct_copy = os.path.join(commands_src, "routine_catalog.py")
        assert not os.path.isfile(direct_copy), (
            f"routine_catalog.py found at {direct_copy}. "
            "Do NOT copy this module into services/commands/ — "
            "services/_shared/ is the single source of truth, overlaid at bundle time. "
            "A second copy drifts silently and is the F27 failure mode one layer down."
        )


class TestResponseHandlerIamStatements:
    """T5.2 Part A.2 — Response handler role has the required new IAM statements."""

    def test_response_handler_has_dynamodb_put_and_update_on_dtc_history(
        self, template: dict
    ) -> None:
        """Response handler must have dynamodb:PutItem + dynamodb:UpdateItem on dtc-history.

        The handler writes SOVD read results and clear-status updates. Without
        this the handler fails at runtime with AccessDenied.
        """
        statements = _all_iam_statements(template)
        matching = [
            stmt for stmt in statements
            if "dynamodb:PutItem" in _statement_actions(stmt)
            and "dynamodb:UpdateItem" in _statement_actions(stmt)
            and any(_DTC_HISTORY_TABLE_FRAGMENT in r for r in _statement_resources(stmt))
        ]
        assert matching, (
            f"No IAM statement with both dynamodb:PutItem and dynamodb:UpdateItem "
            f"on a resource containing '{_DTC_HISTORY_TABLE_FRAGMENT}'. "
            "The response handler must be able to write SOVD DTC history rows. "
            "See spec § Design § 7 and Task 2.2."
        )


class TestIoTInvokeSovdPermission:
    """T5.2 Part A.3 — Lambda invoke permission covers the SOVD rule ARN specifically."""

    def test_response_handler_has_iot_invoke_permission_for_sovd_rule(
        self, template: dict
    ) -> None:
        """Lambda::Permission with SourceArn pointing at the SOVD IoT rule must exist.

        Without this, IoT Core cannot invoke the response handler Lambda when a
        message arrives on the SOVD response topic — the call is silently dropped.
        The permission must reference the SOVD rule by its rule name, not the generic
        command_response_rule.
        """
        permissions = _of_type(template, "AWS::Lambda::Permission")
        _SOVD_RULE_NAME = f"cms_staging_sovd_response_rule"
        _SOVD_RULE_ARN_FRAGMENT = f"rule/{_SOVD_RULE_NAME}"

        sovd_permissions = [
            v for v in permissions.values()
            if v.get("Properties", {}).get("Principal") == "iot.amazonaws.com"
            and _SOVD_RULE_ARN_FRAGMENT in v.get("Properties", {}).get("SourceArn", "")
        ]
        assert sovd_permissions, (
            f"No Lambda::Permission found with Principal=iot.amazonaws.com and "
            f"SourceArn containing '{_SOVD_RULE_ARN_FRAGMENT}'. "
            "The response handler needs an explicit IoT permission for the SOVD rule. "
            "See commands_stack.py response_handler.add_permission('IoTInvokeSovd', ...)."
        )

    def test_legacy_iot_invoke_permissions_are_unchanged(
        self, template: dict
    ) -> None:
        """The legacy IoT permissions (command_response_rule, fwe_command_response_rule)
        must still be present. Adding SOVD must not remove them.
        """
        permissions = _of_type(template, "AWS::Lambda::Permission")
        iot_permissions = [
            v for v in permissions.values()
            if v.get("Properties", {}).get("Principal") == "iot.amazonaws.com"
        ]
        iot_source_arns = [
            v.get("Properties", {}).get("SourceArn", "")
            for v in iot_permissions
        ]

        assert any("command_response_rule" in arn and "sovd" not in arn and "fwe" not in arn
                   for arn in iot_source_arns), (
            f"Legacy command_response_rule IoT permission is missing. "
            f"SourceArns present: {iot_source_arns}"
        )
        assert any("fwe_command_response_rule" in arn
                   for arn in iot_source_arns), (
            f"Legacy fwe_command_response_rule IoT permission is missing. "
            f"SourceArns present: {iot_source_arns}"
        )




# ---------------------------------------------------------------------------
# F32: /api/commands/{vehicleId}/routines API Gateway route
# ---------------------------------------------------------------------------

class TestRoutinesRoute:
    """Fix Group 4, spec 2026-09-02-cms-diagnostics-platform.

    The Lambda handler for GET /api/commands/{vehicleId}/routines was authored
    during Wave 3 (T11.0a) but never wired into API Gateway. The deployed
    endpoint returned 403 "Missing Authentication Token" on every request,
    invisible on the UI as a CORS error, and the routines section was empty.

    These tests assert the route is present in the synthesised template. They
    are mutation-per-contract: removing the `.add_resource('routines')` line
    in `commands_stack.py` must make at least one of them fail — not the
    presence of a method on some sibling resource.
    """

    def _routines_resource(self, template: dict) -> dict | None:
        """Find the API Gateway resource with PathPart 'routines' whose parent
        is the /api/commands/{vehicleId} resource. Returns the resource dict
        (Properties + Type) or None if absent.
        """
        resources = _of_type(template, "AWS::ApiGateway::Resource")
        # The parent {vehicleId} resource id is not deterministic; identify it
        # by its own PathPart == '{vehicleId}' and parent chain up to /commands.
        vehicle_id_logical_ids = {
            lid for lid, r in resources.items()
            if r.get("Properties", {}).get("PathPart") == "{vehicleId}"
        }
        for lid, r in resources.items():
            props = r.get("Properties", {})
            if props.get("PathPart") != "routines":
                continue
            parent = props.get("ParentId", {})
            # ParentId is a Ref intrinsic pointing at the parent resource.
            if isinstance(parent, dict):
                ref = parent.get("Ref") or (parent.get("Fn::GetAtt", [None])[0])
                if ref in vehicle_id_logical_ids:
                    return r
        return None

    def test_routines_path_part_exists_under_vehicle_id(self, template: dict) -> None:
        """A 'routines' resource must exist whose parent is /api/commands/{vehicleId}.

        Mutation contract: deleting `vehicle_routines = vehicle_cmd.add_resource('routines')`
        from commands_stack.py fails this test — not because 'routines' is absent
        somewhere in the API, but because it isn't nested under {vehicleId}.
        """
        found = self._routines_resource(template)
        assert found is not None, (
            "API Gateway is missing the /api/commands/{vehicleId}/routines resource. "
            "See commands_stack.py: vehicle_routines = vehicle_cmd.add_resource('routines'). "
            "Without this line, the frontend's fetchRoutines() call receives 403 "
            "Missing Authentication Token from API Gateway and the routines section "
            "renders as invisible (F32)."
        )

    def test_routines_has_get_method_with_cognito_auth(self, template: dict) -> None:
        """A GET method on /routines must exist with Cognito user-pool auth.

        Mutation contract: replacing the auth on the routines method (e.g.
        AuthorizationType=NONE) or removing the GET method entirely fails this.
        Presence of the routes at all does not satisfy this — the method must
        be present with the correct auth.
        """
        routines_resource = self._routines_resource(template)
        assert routines_resource is not None, "routines resource missing (see prior test)"

        methods = _of_type(template, "AWS::ApiGateway::Method")
        get_on_routines: list[dict] = []
        for m in methods.values():
            props = m.get("Properties", {})
            if props.get("HttpMethod") != "GET":
                continue
            resource_id = props.get("ResourceId", {})
            # ResourceId is Ref{LogicalId of routines resource}
            if isinstance(resource_id, dict):
                ref = resource_id.get("Ref")
                # Find the routines resource's own logical id by re-lookup
                for lid, r in _of_type(template, "AWS::ApiGateway::Resource").items():
                    if r is routines_resource and lid == ref:
                        get_on_routines.append(props)
                        break

        assert get_on_routines, (
            "GET method on /api/commands/{vehicleId}/routines is missing. "
            "See commands_stack.py: vehicle_routines.add_method('GET', integration, **auth_kwargs)."
        )
        # Auth check
        for props in get_on_routines:
            auth_type = props.get("AuthorizationType", "")
            assert auth_type == "COGNITO_USER_POOLS", (
                f"GET /routines has AuthorizationType={auth_type!r}, expected COGNITO_USER_POOLS. "
                "The routine catalog is a read-only endpoint but must still require an authenticated "
                "caller; the Lambda's per-vin authz enforces fleet scope after that."
            )
            authorizer_id = props.get("AuthorizerId")
            assert authorizer_id is not None, (
                "GET /routines has no AuthorizerId — Cognito auth is required. "
                "See commands_stack.py auth_kwargs."
            )
