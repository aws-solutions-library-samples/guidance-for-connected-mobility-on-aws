"""Synth guards for the Fleet Intelligence ADP consumer's CDK surface (G4).

Spec: `.kiro/specs/2026-09-10-cms-fleet-intelligence-adp-consumer/` § D4, § D8.
Task: G4.T1 (red phase) / G4.T3 (green phase).

What this file pins
-------------------
Two stacks, because the resources live in two regions and CloudFormation cannot
span regions in one stack:

* ``cms-{stage}-ui`` (primary region, us-west-2 on staging) — the IAM policy on
  ``FleetIntelligenceRole``, the ``FleetIntelligenceFunction`` env block, and the
  § D8 one-line ``DEPLOYMENT_STAGE`` fix on the main_api Lambda.
* ``cms-{stage}-ui-analytics`` (pinned us-east-1) — the Athena workgroup and the
  query-results bucket. Athena must execute in the region holding ADP's Glue
  catalog and lake, and its results bucket must be in that same region.

Why the expected values are written out longhand here
-----------------------------------------------------
This module deliberately does **not** import the production naming helpers
(``stacks._fleet_intelligence_naming``). If it did, a mutation to a name or to
the product list would change both the expectation and the implementation
together and every assertion would still pass — the assertion would be a
tautology rather than a contract. Every literal below is an independent
restatement of § D4.

Assertions are against the synthesized CloudFormation template, never against
source text: a `grep` proves a line exists, not that CloudFormation receives it.

Every policy assertion is **exact-set or exact-value**, never `in` / `contains`.
This spec's recurring defect is a test named for a contract that asserts
something adjacent to it and passes regardless (six instances across two specs —
see `2026-09-01-dms-customer-master-adp`). Adding a sixth ADP product to the
prefix set, or widening a Glue ARN to ``database/*``, must FAIL here.

All identifiers are synthetic. The account is the AWS-documentation placeholder;
the real account id must never appear in this file, which is not
publish-excluded (the G3 `C3` correction).

Run with (from deployment/):
    .venv/bin/python -m pytest stacks/tests/test_ui_stack_fleet_intelligence_adp_grants.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — mirrors test_connected_services_waf.py exactly
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent          # deployment/stacks/tests/
_STACKS = _HERE.parent                           # deployment/stacks/
_DEPLOYMENT = _STACKS.parent                     # deployment/

for _dir in (str(_DEPLOYMENT), str(_STACKS)):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

_STAGE = "staging"
_ACCOUNT = "123456789012"   # AWS-documentation placeholder — never a real account
_PRIMARY_REGION = "us-west-2"
_ADP_REGION = "us-east-1"

_UI_STACK = f"cms-{_STAGE}-ui"
_ANALYTICS_STACK = f"cms-{_STAGE}-ui-analytics"

# ── § D4 expectations, restated independently of the implementation ──────────
_WORKGROUP = f"cms-{_STAGE}-analytics"
_RESULTS_BUCKET = f"cms-{_STAGE}-athena-results-{_ACCOUNT}-{_ADP_REGION}"
_RESULTS_PREFIX = "fleet-intelligence/"
_OUTPUT_LOCATION = f"s3://{_RESULTS_BUCKET}/{_RESULTS_PREFIX}"
_ADP_LAKE_BUCKET = f"adp-{_STAGE}-foundation-lake-{_ACCOUNT}-{_ADP_REGION}"

# The five products in ADP's CMS-facing Lake Formation share
# (`platform-foundation/stacks/governance_stack.py::_CMS_SHARE_DATABASES`).
# `tire_health` is granted with NO consumer, deliberately — § D11 defers the
# tire-prediction surface. Keeping it here keeps the IAM grant and the LF grant
# describing the same five databases.
_ADP_PRODUCTS = (
    "service_records",
    "charging_sessions",
    "vehicle_identity",
    "energy_usage",
    "tire_health",
)

# A product that must NOT appear anywhere — § D11 scopes it out explicitly.
_EXCLUDED_PRODUCT = "vehicle_telemetry_aggregated"

# The ADP rollup joins `adp_{stage}_dimensions.vins` for model and model year
# (spec 2026-09-25-cms-fi-adp-wide-lifecycle, Q3/Q4). Granted per table only:
# the same database holds `customers`, which is PII, so a database-wide table
# wildcard or the bare `dimensions/` S3 prefix would expose it.
_DIMENSIONS_DB = f"adp_{_STAGE}_dimensions"
_DIMENSION_TABLES = ("vins",)
_PII_DIMENSION_TABLE = "customers"

_EXPECTED_SIDS = {
    "AthenaWorkgroupAccess",
    "AthenaResultsBucketAccess",
    "AdpLakeS3Read",
    "AdpLakeS3List",
    "AdpLakeBucketLocation",
    "AdpLakeKmsDecrypt",
    "AdpGlueMetadataRead",
    "LakeFormationGetDataAccess",
}

# The eight env vars `services/fleet_intelligence/` actually reads. Measured,
# not copied from § D4: § D4 additionally prescribes ADP_LAKE_BUCKET and
# ADP_ACCOUNT, which no code reads (RESUME delta § 2). G4 drops both rather
# than shipping dead config.
_EXPECTED_FI_ENV_KEYS = {
    "ADP_DATA_PROVENANCE",
    "ADP_REGION",
    "ADP_STAGE",
    "ATHENA_OUTPUT_LOC",
    "ATHENA_WORKGROUP",
    "FI_LIFECYCLE_WINDOW_MONTHS",
    "FI_WINDOW_MONTHS",
    "PM_SCHEDULES_TABLE_NAME",
    "VEHICLES_TABLE_NAME",
}

_FORBIDDEN_FI_ENV_KEYS = {
    # The DDB cost stub is dead after this spec — § D4 removes it.
    "VEHICLE_COSTS_TABLE_NAME",
    # Prescribed by § D4 but read by nothing. Shipping them would repeat the
    # `Dead VITE vars` shape: config that reads as load-bearing from the test
    # side while no consumer exists.
    "ADP_LAKE_BUCKET",
    "ADP_ACCOUNT",
}


# ---------------------------------------------------------------------------
# Synth helpers
# ---------------------------------------------------------------------------


def _resolve(node):
    """Collapse a synthesized ARN node to a plain string where possible.

    CDK emits `Stack.format_arn`-built ARNs as ``Fn::Join`` with a
    ``{"Ref": "AWS::Partition"}`` element. Resolving the partition to ``aws``
    lets the assertions below compare against readable literals instead of
    against template plumbing.
    """
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if set(node) == {"Ref"} and node["Ref"] == "AWS::Partition":
            return "aws"
        if "Fn::Join" in node:
            sep, parts = node["Fn::Join"]
            return sep.join(_resolve(p) for p in parts)
        return node
    if isinstance(node, list):
        return [_resolve(p) for p in node]
    return node


def _as_set(node) -> set:
    """Normalise a template Action/Resource field to a set of strings."""
    resolved = _resolve(node)
    if isinstance(resolved, str):
        return {resolved}
    return set(resolved)


@pytest.fixture(scope="module")
def ui_template() -> dict:
    from aws_cdk import App, Environment

    from stacks.ui_stack import UIStack  # noqa: PLC0415

    os.environ["DEPLOYMENT_STAGE"] = _STAGE
    os.environ.setdefault("DRIVER_SELF_GUARD_ENABLED", "true")
    app = App(context={})
    UIStack(
        app,
        _UI_STACK,
        env=Environment(account=_ACCOUNT, region=_PRIMARY_REGION),
    )
    raw = app.synth().get_stack_by_name(_UI_STACK).template
    return json.loads(json.dumps(raw))


@pytest.fixture(scope="module")
def analytics_template() -> dict:
    from aws_cdk import App, Environment

    from stacks.fleet_intelligence_analytics_stack import (  # noqa: PLC0415
        FleetIntelligenceAnalyticsStack,
    )

    os.environ["DEPLOYMENT_STAGE"] = _STAGE
    app = App(context={})
    FleetIntelligenceAnalyticsStack(
        app,
        _ANALYTICS_STACK,
        stage=_STAGE,
        env=Environment(account=_ACCOUNT, region=_PRIMARY_REGION),
    )
    raw = app.synth().get_stack_by_name(_ANALYTICS_STACK).template
    return json.loads(json.dumps(raw))


def _resources_of_type(template: dict, cfn_type: str) -> dict:
    return {
        k: v for k, v in template["Resources"].items() if v["Type"] == cfn_type
    }


def _fleet_intelligence_policy_document(template: dict) -> dict:
    """Return the single IAM::Policy document attached to FleetIntelligenceRole."""
    role_ids = [
        k
        for k, v in template["Resources"].items()
        if v["Type"] == "AWS::IAM::Role" and k.startswith("FleetIntelligenceRole")
    ]
    assert len(role_ids) == 1, f"expected one FleetIntelligenceRole, got {role_ids}"

    docs = [
        v["Properties"]["PolicyDocument"]
        for v in template["Resources"].values()
        if v["Type"] == "AWS::IAM::Policy"
        and any(rid in json.dumps(v["Properties"].get("Roles", [])) for rid in role_ids)
    ]
    assert len(docs) == 1, (
        f"expected exactly one IAM::Policy attached to FleetIntelligenceRole, got {len(docs)}"
    )
    return docs[0]


def _statement_by_sid(template: dict, sid: str) -> dict:
    doc = _fleet_intelligence_policy_document(template)
    matches = [s for s in doc["Statement"] if s.get("Sid") == sid]
    assert len(matches) == 1, (
        f"expected exactly one statement with Sid={sid!r}, found {len(matches)}. "
        f"Sids present: {sorted(s.get('Sid') for s in doc['Statement'])}"
    )
    return matches[0]


def _lambda_env(template: dict, logical_id_prefix: str) -> dict:
    fns = {
        k: v
        for k, v in _resources_of_type(template, "AWS::Lambda::Function").items()
        if k.startswith(logical_id_prefix)
    }
    assert len(fns) == 1, (
        f"expected one Lambda with logical id prefixed {logical_id_prefix!r}, got {sorted(fns)}"
    )
    props = next(iter(fns.values()))["Properties"]
    return props.get("Environment", {}).get("Variables", {})


# ---------------------------------------------------------------------------
# (a) Analytics stack — Athena workgroup
# ---------------------------------------------------------------------------


class TestAthenaWorkgroup:
    def test_exactly_one_workgroup_with_expected_name(self, analytics_template) -> None:
        wgs = _resources_of_type(analytics_template, "AWS::Athena::WorkGroup")
        assert len(wgs) == 1, f"expected exactly one Athena WorkGroup, got {sorted(wgs)}"
        assert next(iter(wgs.values()))["Properties"]["Name"] == _WORKGROUP

    def test_workgroup_is_in_the_adp_region(self, analytics_template) -> None:
        """Athena can only query the Glue catalog in its own Region.

        ADP's lake and catalog are us-east-1. A workgroup synthesized into the
        primary region would pass a `Name` assertion and then fail every query
        with TABLE_NOT_FOUND.
        """
        env = analytics_template.get("Parameters"), analytics_template
        del env  # not used; the region is asserted via the stack's own env below
        from stacks.fleet_intelligence_analytics_stack import (  # noqa: PLC0415
            FleetIntelligenceAnalyticsStack,
        )
        from aws_cdk import App, Environment

        app = App(context={})
        stack = FleetIntelligenceAnalyticsStack(
            app,
            _ANALYTICS_STACK,
            stage=_STAGE,
            env=Environment(account=_ACCOUNT, region=_PRIMARY_REGION),
        )
        assert stack.region == _ADP_REGION, (
            f"analytics stack synthesized into {stack.region}, not {_ADP_REGION}; "
            "the workgroup must execute where ADP's Glue catalog lives even when "
            "the caller passes the primary region"
        )

    def test_workgroup_enforces_its_own_configuration(self, analytics_template) -> None:
        wg = next(iter(_resources_of_type(analytics_template, "AWS::Athena::WorkGroup").values()))
        cfg = wg["Properties"]["WorkGroupConfiguration"]
        assert cfg["EnforceWorkGroupConfiguration"] is True
        assert cfg["PublishCloudWatchMetricsEnabled"] is True

    def test_workgroup_output_location_is_the_results_bucket_prefix(
        self, analytics_template
    ) -> None:
        wg = next(iter(_resources_of_type(analytics_template, "AWS::Athena::WorkGroup").values()))
        loc = _resolve(
            wg["Properties"]["WorkGroupConfiguration"]["ResultConfiguration"]["OutputLocation"]
        )
        assert loc == _OUTPUT_LOCATION

    def test_workgroup_encrypts_query_results(self, analytics_template) -> None:
        """Results are SSE-S3, matching the bucket's own default.

        The bucket's encryption is asserted separately; this pins the workgroup's
        side, which is what actually applies to objects Athena writes. A flip to
        SSE_KMS here would need a CMK grant the role does not have, and would
        fail at query time rather than at synth.
        """
        wg = next(iter(_resources_of_type(analytics_template, "AWS::Athena::WorkGroup").values()))
        enc = wg["Properties"]["WorkGroupConfiguration"]["ResultConfiguration"][
            "EncryptionConfiguration"
        ]
        assert enc == {"EncryptionOption": "SSE_S3"}

    def test_workgroup_depends_on_the_results_bucket(self, analytics_template) -> None:
        """Explicit edge, because CDK cannot infer this one.

        `output_location` is a plain string built from
        `_fleet_intelligence_naming` — not a reference to the bucket construct —
        so CloudFormation sees no dependency and may create the workgroup first.
        The string is deliberate: ui_stack rebuilds the same name and cannot
        reference the construct across Regions.
        """
        wg_id, wg = next(
            iter(_resources_of_type(analytics_template, "AWS::Athena::WorkGroup").items())
        )
        bucket_ids = set(_resources_of_type(analytics_template, "AWS::S3::Bucket"))
        depends = wg.get("DependsOn", [])
        if isinstance(depends, str):
            depends = [depends]
        assert bucket_ids & set(depends), (
            f"{wg_id} has DependsOn={depends}; expected one of the bucket logical "
            f"ids {sorted(bucket_ids)}. Without the edge the workgroup can be "
            "created before its results bucket exists."
        )


# ---------------------------------------------------------------------------
# (b) Analytics stack — results bucket
# ---------------------------------------------------------------------------


class TestAthenaResultsBucket:
    def test_exactly_one_bucket_with_region_suffixed_name(self, analytics_template) -> None:
        buckets = _resources_of_type(analytics_template, "AWS::S3::Bucket")
        assert len(buckets) == 1, f"expected exactly one bucket, got {sorted(buckets)}"
        assert next(iter(buckets.values()))["Properties"]["BucketName"] == _RESULTS_BUCKET

    def test_bucket_blocks_all_public_access(self, analytics_template) -> None:
        bucket = next(iter(_resources_of_type(analytics_template, "AWS::S3::Bucket").values()))
        pab = bucket["Properties"]["PublicAccessBlockConfiguration"]
        assert pab == {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }

    def test_bucket_is_sse_s3_encrypted(self, analytics_template) -> None:
        bucket = next(iter(_resources_of_type(analytics_template, "AWS::S3::Bucket").values()))
        rules = bucket["Properties"]["BucketEncryption"]["ServerSideEncryptionConfiguration"]
        assert len(rules) == 1
        assert rules[0]["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"

    def test_bucket_is_retained(self, analytics_template) -> None:
        """Globally-namespaced bucket — BucketRetainAspect's invariant."""
        bucket = next(iter(_resources_of_type(analytics_template, "AWS::S3::Bucket").values()))
        assert bucket.get("DeletionPolicy") == "Retain"

    def test_results_expire_after_seven_days_under_the_fi_prefix(
        self, analytics_template
    ) -> None:
        """The real query-results lifecycle rule (7-day expiry, RESULTS_PREFIX)
        remains the only lifecycle rule on this bucket. The keep-warm
        mechanism (2026-09-19) does NOT get its own sibling prefix + rule —
        that was the original design and it failed on first live deploy:
        this workgroup's `EnforceWorkGroupConfiguration=True` silently
        redirects any `ResultConfiguration.OutputLocation` a caller passes
        back to the workgroup's own configured RESULTS_PREFIX regardless, so
        warm-up query results land in the SAME real prefix as everything
        else and are correctly covered by this one rule already."""
        bucket = next(iter(_resources_of_type(analytics_template, "AWS::S3::Bucket").values()))
        rules = bucket["Properties"]["LifecycleConfiguration"]["Rules"]
        assert len(rules) == 1, f"expected exactly one lifecycle rule, got {rules}"
        rule = rules[0]
        assert rule["ExpirationInDays"] == 7
        assert rule["Prefix"] == _RESULTS_PREFIX
        assert rule["Status"] == "Enabled"

    def test_warmer_iam_grant_targets_the_real_results_prefix_not_a_sibling(
        self, analytics_template
    ) -> None:
        """D1 (found live, 2026-09-19): the warmer's own IAM grant for
        `s3:PutObject` MUST target `_RESULTS_PREFIX` — the workgroup's real,
        enforced output prefix — not an independently-invented prefix. A
        grant scoped to any other prefix passes synth cleanly and fails at
        real invocation with "Access denied when writing to location",
        because Athena writes to the workgroup's configured location
        regardless of what the client's own request asks for. This is the
        actual defect the first live deploy hit; this test pins the fix so
        a future edit can't reintroduce a mismatched prefix undetected."""
        policies = _resources_of_type(analytics_template, "AWS::IAM::Policy")
        warmer_policy = next(
            v for k, v in policies.items() if "Warmer" in k
        )
        statements = warmer_policy["Properties"]["PolicyDocument"]["Statement"]
        s3_write = next(
            s for s in statements
            if "s3:PutObject" in (
                s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
            )
        )
        resources = (
            s3_write["Resource"]
            if isinstance(s3_write["Resource"], list)
            else [s3_write["Resource"]]
        )
        # Resource entries are Fn::Join intrinsics (bucket ARN + prefix
        # string), not plain strings — assert the prefix string is present
        # verbatim in the join parts rather than string-matching the whole
        # (unresolved) intrinsic.
        joined_parts = json.dumps(resources)
        assert _RESULTS_PREFIX in joined_parts, (
            f"expected the warmer's s3:PutObject grant to reference "
            f"{_RESULTS_PREFIX!r} (the real, enforced results prefix); "
            f"resource entries were: {joined_parts}"
        )

    def test_bucket_denies_insecure_transport(self, analytics_template) -> None:
        """Athena writes results here; the channel must be TLS-only."""
        policies = _resources_of_type(analytics_template, "AWS::S3::BucketPolicy")
        assert policies, "results bucket has no BucketPolicy — no TLS-only guard"
        doc = json.dumps(
            [p["Properties"]["PolicyDocument"] for p in policies.values()]
        )
        assert "aws:SecureTransport" in doc


# ---------------------------------------------------------------------------
# (c) UI stack — the eight IAM statements, each exact
# ---------------------------------------------------------------------------


class TestFleetIntelligenceIamStatements:
    def test_all_expected_sids_present_and_no_others_added_by_this_spec(
        self, ui_template
    ) -> None:
        doc = _fleet_intelligence_policy_document(ui_template)
        sids = {s.get("Sid") for s in doc["Statement"] if s.get("Sid")}
        missing = _EXPECTED_SIDS - sids
        assert not missing, f"missing Sids: {sorted(missing)}"
        unexpected = sids - _EXPECTED_SIDS
        assert not unexpected, (
            f"unexpected Sid(s) on FleetIntelligenceRole: {sorted(unexpected)}. "
            "Every named statement on this role belongs to § D4; a new one means "
            "the grant widened without the spec saying so."
        )

    def test_athena_workgroup_access_is_exact(self, ui_template) -> None:
        st = _statement_by_sid(ui_template, "AthenaWorkgroupAccess")
        assert _as_set(st["Action"]) == {
            "athena:StartQueryExecution",
            "athena:GetQueryExecution",
            "athena:GetQueryResults",
        }
        assert _as_set(st["Resource"]) == {
            f"arn:aws:athena:{_ADP_REGION}:{_ACCOUNT}:workgroup/{_WORKGROUP}"
        }
        assert st["Effect"] == "Allow"

    def test_athena_results_bucket_access_covers_bucket_and_objects(
        self, ui_template
    ) -> None:
        st = _statement_by_sid(ui_template, "AthenaResultsBucketAccess")
        assert _as_set(st["Action"]) == {
            "s3:PutObject",
            "s3:GetObject",
            "s3:GetBucketLocation",
            "s3:ListBucket",
        }
        assert _as_set(st["Resource"]) == {
            f"arn:aws:s3:::{_RESULTS_BUCKET}",
            f"arn:aws:s3:::{_RESULTS_BUCKET}/*",
        }

    def test_adp_lake_read_is_scoped_to_exactly_the_five_products_and_dimensions_vins(
        self, ui_template
    ) -> None:
        """Exact-set, not `contains`.

        A sixth product prefix — `vehicle_telemetry_aggregated/*` is the live
        temptation, § D11 scopes it out — or a wider dimensions prefix must fail
        here rather than widen the grant silently.
        """
        st = _statement_by_sid(ui_template, "AdpLakeS3Read")
        assert _as_set(st["Resource"]) == (
            {f"arn:aws:s3:::{_ADP_LAKE_BUCKET}/curated/{p}/*" for p in _ADP_PRODUCTS}
            | {
                f"arn:aws:s3:::{_ADP_LAKE_BUCKET}/dimensions/{t}/*"
                for t in _DIMENSION_TABLES
            }
        )

    def test_adp_lake_read_grants_only_object_reads(self, ui_template) -> None:
        """Object-only ARNs, therefore object-only actions.

        `s3:GetBucketLocation` on a `.../*` ARN authorizes nothing — it is a
        bucket-level action. § D4 lists it here; granting it on an object ARN is
        the defect `2026-09-01-dms-customer-master-adp` shipped and caught by
        reading the template. It lives in `AdpLakeBucketLocation` instead.
        """
        st = _statement_by_sid(ui_template, "AdpLakeS3Read")
        assert _as_set(st["Action"]) == {"s3:GetObject"}

    def test_adp_lake_list_is_prefix_conditioned_with_exactly_twelve_entries(
        self, ui_template
    ) -> None:
        st = _statement_by_sid(ui_template, "AdpLakeS3List")
        assert _as_set(st["Action"]) == {"s3:ListBucket"}
        assert _as_set(st["Resource"]) == {f"arn:aws:s3:::{_ADP_LAKE_BUCKET}"}
        prefixes = st["Condition"]["StringLike"]["s3:prefix"]
        expected = (
            {f"curated/{p}" for p in _ADP_PRODUCTS}
            | {f"curated/{p}/*" for p in _ADP_PRODUCTS}
            | {f"dimensions/{t}" for t in _DIMENSION_TABLES}
            | {f"dimensions/{t}/*" for t in _DIMENSION_TABLES}
        )
        assert set(prefixes) == expected
        assert len(prefixes) == 12, f"expected 12 prefix entries, got {len(prefixes)}"

    def test_bucket_location_is_granted_on_the_bucket_without_a_prefix_condition(
        self, ui_template
    ) -> None:
        """Athena calls GetBucketLocation before reading any object.

        It must land on the bucket ARN, and it must NOT carry an `s3:prefix`
        condition: `s3:prefix` is only populated for List operations, so a
        conditioned GetBucketLocation never matches and the query fails.
        """
        st = _statement_by_sid(ui_template, "AdpLakeBucketLocation")
        assert _as_set(st["Action"]) == {"s3:GetBucketLocation"}
        assert _as_set(st["Resource"]) == {f"arn:aws:s3:::{_ADP_LAKE_BUCKET}"}
        assert "Condition" not in st, (
            "GetBucketLocation must be unconditioned; an s3:prefix condition here "
            "silently never matches"
        )

    def test_kms_decrypt_is_via_service_constrained(self, ui_template) -> None:
        """ADP's lake is SSE-KMS; without this the read fails after every test passes."""
        st = _statement_by_sid(ui_template, "AdpLakeKmsDecrypt")
        assert _as_set(st["Action"]) == {"kms:Decrypt", "kms:DescribeKey"}
        assert _as_set(st["Resource"]) == {f"arn:aws:kms:{_ADP_REGION}:{_ACCOUNT}:key/*"}
        assert st["Condition"] == {
            "StringEquals": {"kms:ViaService": f"s3.{_ADP_REGION}.amazonaws.com"}
        }

    def test_glue_metadata_read_is_exactly_thirteen_arns(self, ui_template) -> None:
        """One catalog + five databases + five table wildcards + dimensions + dimensions.vins.

        A widening to `database/*` would pass any "at least one in-scope
        resource" check while granting metadata read across every ADP domain —
        the exact mutation that survived 14 tests in
        `2026-09-01-dms-customer-master-adp`.
        """
        st = _statement_by_sid(ui_template, "AdpGlueMetadataRead")
        assert _as_set(st["Action"]) == {
            "glue:GetDatabase",
            "glue:GetTable",
            "glue:GetTables",
            "glue:GetPartitions",
        }
        expected = (
            {f"arn:aws:glue:{_ADP_REGION}:{_ACCOUNT}:catalog"}
            | {
                f"arn:aws:glue:{_ADP_REGION}:{_ACCOUNT}:database/adp_{_STAGE}_{p}"
                for p in _ADP_PRODUCTS
            }
            | {
                f"arn:aws:glue:{_ADP_REGION}:{_ACCOUNT}:table/adp_{_STAGE}_{p}/*"
                for p in _ADP_PRODUCTS
            }
            | {f"arn:aws:glue:{_ADP_REGION}:{_ACCOUNT}:database/{_DIMENSIONS_DB}"}
            | {
                f"arn:aws:glue:{_ADP_REGION}:{_ACCOUNT}:table/{_DIMENSIONS_DB}/{t}"
                for t in _DIMENSION_TABLES
            }
        )
        actual = _as_set(st["Resource"])
        assert actual == expected, (
            f"Glue ARN set drifted.\n  missing: {sorted(expected - actual)}\n"
            f"  extra:   {sorted(actual - expected)}"
        )
        assert len(actual) == 13

    def test_no_allow_statement_on_the_role_reaches_the_customers_table(
        self, ui_template
    ) -> None:
        """`adp_{stage}_dimensions.customers` is PII; nothing on this role may reach it.

        Wildcard-matched, not string-compared: each Allow statement's actions and
        resources are matched against the real `customers` ARNs with IAM's `*`
        and `?`, so `<lake>/*` (what CDK's `grant_read` emits), `<lake>/dim*` or
        Glue `table/<db>/c*` fail here whatever their Sid. A direct S3 read is
        not stopped by Lake Formation, so this is the guard for that path.
        Review dimgrant Cycle 1 W1.
        """
        import re

        def iam_match(pattern: str, value: str, *, case_insensitive: bool = False) -> bool:
            rx = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
            return re.match(rx, value, re.IGNORECASE if case_insensitive else 0) is not None

        glue_customers = (
            f"arn:aws:glue:{_ADP_REGION}:{_ACCOUNT}:table/{_DIMENSIONS_DB}/{_PII_DIMENSION_TABLE}"
        )
        s3_customers = (
            f"arn:aws:s3:::{_ADP_LAKE_BUCKET}/dimensions/{_PII_DIMENSION_TABLE}/data.parquet"
        )
        lake_bucket = f"arn:aws:s3:::{_ADP_LAKE_BUCKET}"
        list_prefix = f"dimensions/{_PII_DIMENSION_TABLE}/"
        # (action, resource) pairs that would read the table or list its objects.
        targets = [
            ("s3:GetObject", s3_customers),
            ("glue:GetTable", glue_customers),
            ("glue:GetPartitions", glue_customers),
        ]

        # Every statement that can apply to the role: inline `Policies` on the
        # role, plus every AWS::IAM::Policy / ManagedPolicy whose `Roles`, or
        # AWS::IAM::RolePolicy whose `RoleName`, references it.
        # `iam.ManagedPolicy(roles=[role])` attaches through `Roles`, not
        # ManagedPolicyArns (review Cycle 2 W3); `CfnRolePolicy` through
        # `RoleName` (Cycle 3 X5).
        role_id, role = next(
            (k, v) for k, v in ui_template["Resources"].items()
            if v["Type"] == "AWS::IAM::Role" and k.startswith("FleetIntelligenceRole")
        )
        statements: list[dict] = []
        for p in role["Properties"].get("Policies", []):
            statements += _resolve(p["PolicyDocument"])["Statement"]
        attached = 0
        for v in ui_template["Resources"].values():
            if v["Type"] not in (
                "AWS::IAM::Policy", "AWS::IAM::ManagedPolicy", "AWS::IAM::RolePolicy"
            ):
                continue
            refs = json.dumps(
                [v["Properties"].get("Roles", []), v["Properties"].get("RoleName")]
            )
            if role_id in refs:
                statements += _resolve(v["Properties"]["PolicyDocument"])["Statement"]
                attached += 1
        assert attached >= 1 and statements, "found no policy attached to FleetIntelligenceRole"

        # Managed policies from outside the template can't be inspected here, so
        # allow only the Lambda logging one.
        managed = [
            json.dumps(_resolve(m)) for m in role["Properties"].get("ManagedPolicyArns", [])
        ]
        assert all("service-role/AWSLambdaBasicExecutionRole" in m for m in managed), managed

        for st in statements:
            if st.get("Effect") != "Allow":
                continue
            assert "NotAction" not in st and "NotResource" not in st, (
                f"Allow with NotAction/NotResource can't be bounded: {st}"
            )
            actions = sorted(_as_set(st["Action"]))
            resources = [r for r in _as_set(st["Resource"]) if isinstance(r, str)]
            for action, target in targets:
                if any(iam_match(a, action, case_insensitive=True) for a in actions):
                    hits = [r for r in resources if iam_match(r, target)]
                    assert not hits, (
                        f"Sid={st.get('Sid')!r} allows {action} on {target} via {hits}"
                    )
            # ListBucket on the lake: the s3:prefix condition must exclude customers.
            if any(iam_match(a, "s3:ListBucket", case_insensitive=True) for a in actions) and any(
                iam_match(r, lake_bucket) for r in resources
            ):
                cond = st.get("Condition", {})
                prefixes = (
                    cond.get("StringLike", {}).get("s3:prefix")
                    or cond.get("StringEquals", {}).get("s3:prefix")
                )
                assert prefixes, f"Sid={st.get('Sid')!r}: unconditioned ListBucket on the lake"
                prefixes = [prefixes] if isinstance(prefixes, str) else prefixes
                assert not any(iam_match(p, list_prefix) for p in prefixes), (
                    f"Sid={st.get('Sid')!r}: s3:prefix {prefixes} can list {list_prefix}"
                )

    def test_dimensions_grants_name_vins_and_never_the_whole_database(
        self, ui_template
    ) -> None:
        """Literal checks on the named statements; the wildcard-matched guard is above."""
        doc = _resolve(_fleet_intelligence_policy_document(ui_template))
        resources: set[str] = set()
        for st in doc["Statement"]:
            resources |= {r for r in _as_set(st.get("Resource", [])) if isinstance(r, str)}

        assert f"arn:aws:glue:{_ADP_REGION}:{_ACCOUNT}:table/{_DIMENSIONS_DB}/*" not in resources
        assert f"arn:aws:s3:::{_ADP_LAKE_BUCKET}/dimensions/*" not in resources
        assert _PII_DIMENSION_TABLE not in json.dumps(doc), (
            f"the {_PII_DIMENSION_TABLE} table (PII) appears in the FI policy"
        )

    def test_every_adp_table_the_code_reads_is_granted(self, ui_template) -> None:
        """Scan the Lambda's SQL and Python for `adp_{stage}_<db>.<table>`.

        This is the check that would have caught
        issues/2026-09-26-fi-adp-rollup-role-lacks-dimensions-grant: the rollup
        SQL joined `adp_{stage}_dimensions.vins` while the grants covered only
        the five products, and every stubbed test and every admin-principal live
        test passed. Each referenced table needs its database ARN, a table ARN
        (`<db>/<table>` or `<db>/*`), and an S3 object grant on its location.
        """
        import re

        src = _DEPLOYMENT.parent / "services" / "fleet_intelligence"
        # Quoted identifiers ("adp_{stage}_dimensions"."vins") count too.
        pattern = re.compile(r'"?adp_\{[a-z_]*\}_([a-z_]+)"?\s*\.\s*"?([a-z_]+)"?')
        referenced: set[tuple[str, str]] = set()
        for path in list(src.rglob("*.py")) + list(src.rglob("*.sql")):
            if "tests" in path.relative_to(src).parts or "__pycache__" in path.parts:
                continue
            referenced |= set(pattern.findall(path.read_text()))

        # Anti-vacuity: the scan must actually find the reads, including the one
        # that was missed.
        assert ("dimensions", "vins") in referenced, sorted(referenced)
        assert len(referenced) >= 6, sorted(referenced)

        glue = _as_set(_statement_by_sid(ui_template, "AdpGlueMetadataRead")["Resource"])
        s3 = _as_set(_statement_by_sid(ui_template, "AdpLakeS3Read")["Resource"])
        arn = f"arn:aws:glue:{_ADP_REGION}:{_ACCOUNT}"
        for db, table in sorted(referenced):
            full_db = f"adp_{_STAGE}_{db}"
            assert f"{arn}:database/{full_db}" in glue, f"{full_db}.{table}: no database grant"
            assert (
                f"{arn}:table/{full_db}/{table}" in glue
                or f"{arn}:table/{full_db}/*" in glue
            ), f"{full_db}.{table}: no table grant"
            location = (
                f"dimensions/{table}/*" if db == "dimensions" else f"curated/{db}/*"
            )
            assert f"arn:aws:s3:::{_ADP_LAKE_BUCKET}/{location}" in s3, (
                f"{full_db}.{table}: no S3 read on {location}"
            )

    def test_glue_grant_never_uses_a_database_wildcard(self, ui_template) -> None:
        st = _statement_by_sid(ui_template, "AdpGlueMetadataRead")
        for arn in _as_set(st["Resource"]):
            assert not arn.endswith(":database/*"), f"database wildcard in Glue grant: {arn}"
            assert not arn.endswith(":table/*"), f"table wildcard in Glue grant: {arn}"

    def test_lake_formation_get_data_access_is_unscoped_by_design(
        self, ui_template
    ) -> None:
        st = _statement_by_sid(ui_template, "LakeFormationGetDataAccess")
        assert _as_set(st["Action"]) == {"lakeformation:GetDataAccess"}
        assert _as_set(st["Resource"]) == {"*"}

    def test_no_statement_mentions_the_deferred_telemetry_product(
        self, ui_template
    ) -> None:
        doc = json.dumps(_fleet_intelligence_policy_document(ui_template))
        assert _EXCLUDED_PRODUCT not in doc, (
            f"{_EXCLUDED_PRODUCT} is scoped out by § D11 but appears in the "
            "FleetIntelligenceRole policy"
        )


# ---------------------------------------------------------------------------
# (d) UI stack — the dead DDB cost grant is gone, the vehicles grant survives
# ---------------------------------------------------------------------------


class TestDeadCostTableGrantRemoved:
    def test_no_statement_grants_dynamodb_on_the_vehicle_costs_table(
        self, ui_template
    ) -> None:
        """§ D4 removes VEHICLE_COSTS_TABLE_NAME; the grant must go with it.

        The table construct itself stays (its CFN exports are imported by
        cms-{stage}-flink — see decisions.md G4-C), but a permission the Lambda
        can no longer use is a permission it should not hold.
        """
        doc = json.dumps(_resolve(_fleet_intelligence_policy_document(ui_template)))
        assert "vehicle-costs" not in doc, (
            "FleetIntelligenceRole still grants DynamoDB on the vehicle-costs table"
        )

    def test_vehicles_table_read_grant_is_still_present(self, ui_template) -> None:
        """The § D2 redesign reads cms-{stage}-storage-vehicles. Do not regress it."""
        doc = _resolve(_fleet_intelligence_policy_document(ui_template))
        stmts = [
            s
            for s in doc["Statement"]
            if any(
                "table/cms-staging-storage-vehicles" in r
                for r in _as_set(s.get("Resource", []))
            )
        ]
        assert stmts, "no statement grants DynamoDB on cms-staging-storage-vehicles"
        actions = set().union(*(_as_set(s["Action"]) for s in stmts))
        for required in ("dynamodb:GetItem", "dynamodb:Scan"):
            assert required in actions, f"{required} missing on the vehicles table"

    def test_vehicles_table_grant_is_not_duplicated(self, ui_template) -> None:
        """RESUME claim 2: the grant already exists — G4 must not add a second."""
        doc = _resolve(_fleet_intelligence_policy_document(ui_template))
        matching = [
            s
            for s in doc["Statement"]
            if f"arn:aws:dynamodb:{_PRIMARY_REGION}:{_ACCOUNT}:table/cms-staging-storage-vehicles"
            in _as_set(s.get("Resource", []))
        ]
        assert len(matching) == 1, (
            f"expected one statement covering the vehicles table, found {len(matching)}"
        )


# ---------------------------------------------------------------------------
# (e) UI stack — FleetIntelligenceFunction env block
# ---------------------------------------------------------------------------


class TestFleetIntelligenceEnvBlock:
    def test_env_keys_are_exactly_the_eight_the_code_reads(self, ui_template) -> None:
        env = _lambda_env(ui_template, "FleetIntelligenceFunction")
        assert set(env) == _EXPECTED_FI_ENV_KEYS, (
            f"env drift.\n  missing: {sorted(_EXPECTED_FI_ENV_KEYS - set(env))}\n"
            f"  extra:   {sorted(set(env) - _EXPECTED_FI_ENV_KEYS)}"
        )

    @pytest.mark.parametrize("key", sorted(_FORBIDDEN_FI_ENV_KEYS))
    def test_dead_env_vars_are_absent(self, ui_template, key) -> None:
        env = _lambda_env(ui_template, "FleetIntelligenceFunction")
        assert key not in env, (
            f"{key} is set but nothing reads it — dead config (RESUME delta § 2)"
        )

    def test_adp_region_is_the_lake_region_not_the_lambda_region(
        self, ui_template
    ) -> None:
        env = _lambda_env(ui_template, "FleetIntelligenceFunction")
        assert _resolve(env["ADP_REGION"]) == _ADP_REGION

    def test_adp_stage_matches_the_deploying_stage(self, ui_template) -> None:
        env = _lambda_env(ui_template, "FleetIntelligenceFunction")
        assert _resolve(env["ADP_STAGE"]) == _STAGE

    def test_athena_workgroup_env_matches_the_workgroup_the_iam_policy_allows(
        self, ui_template
    ) -> None:
        """The env var and the IAM grant must name the same workgroup.

        Divergence here is invisible to a per-side assertion: each side is
        internally consistent and the query is denied at runtime.
        """
        env = _lambda_env(ui_template, "FleetIntelligenceFunction")
        assert _resolve(env["ATHENA_WORKGROUP"]) == _WORKGROUP
        st = _statement_by_sid(ui_template, "AthenaWorkgroupAccess")
        arn = next(iter(_as_set(st["Resource"])))
        assert arn.endswith(f":workgroup/{_resolve(env['ATHENA_WORKGROUP'])}")

    def test_athena_output_loc_matches_the_results_bucket_the_iam_policy_allows(
        self, ui_template
    ) -> None:
        env = _lambda_env(ui_template, "FleetIntelligenceFunction")
        assert _resolve(env["ATHENA_OUTPUT_LOC"]) == _OUTPUT_LOCATION
        st = _statement_by_sid(ui_template, "AthenaResultsBucketAccess")
        assert f"arn:aws:s3:::{_RESULTS_BUCKET}/*" in _as_set(st["Resource"])

    def test_provenance_is_simulated_on_staging(self, ui_template) -> None:
        """§ D6. Staging ADP data is Faker-generated; labelling it `measured`
        would launder synthetic data into a cost figure a user acts on."""
        env = _lambda_env(ui_template, "FleetIntelligenceFunction")
        assert _resolve(env["ADP_DATA_PROVENANCE"]) == "simulated"

    def test_window_months_is_the_d4_default(self, ui_template) -> None:
        env = _lambda_env(ui_template, "FleetIntelligenceFunction")
        assert _resolve(env["FI_WINDOW_MONTHS"]) == "12"


# ---------------------------------------------------------------------------
# (f) UI stack — § D8 DEPLOYMENT_STAGE on main_api
# ---------------------------------------------------------------------------


class TestMainApiDeploymentStageFix:
    def test_main_api_carries_deployment_stage(self, ui_template) -> None:
        """§ D8. The operator's manual patch, made durable.

        Without it a `cdk deploy cms-staging-ui` wipes the hand-set env var and
        every /api/v1/charging and /api/v1/tco route 500s until someone
        re-patches it (issues/2026-09-10-main-api-deployment-stage-env-unset-*).
        """
        env = _lambda_env(ui_template, "FleetAPIFunction")
        assert "DEPLOYMENT_STAGE" in env, (
            "main_api Lambda has no DEPLOYMENT_STAGE — the § D8 fix is absent"
        )
        assert _resolve(env["DEPLOYMENT_STAGE"]) == _STAGE

    def test_deployment_stage_is_not_hardcoded_to_a_single_stage(self) -> None:
        """A literal 'staging' would make prod read the wrong tables.

        Synthesize prod and assert the value tracks the stage rather than the
        value the staging test happened to observe.
        """
        from aws_cdk import App, Environment

        from stacks.ui_stack import UIStack  # noqa: PLC0415

        os.environ["DEPLOYMENT_STAGE"] = "prod"
        try:
            app = App(context={})
            UIStack(
                app,
                "cms-prod-ui",
                env=Environment(account=_ACCOUNT, region=_ADP_REGION),
            )
            template = json.loads(
                json.dumps(app.synth().get_stack_by_name("cms-prod-ui").template)
            )
        finally:
            os.environ["DEPLOYMENT_STAGE"] = _STAGE
        env = _lambda_env(template, "FleetAPIFunction")
        assert _resolve(env["DEPLOYMENT_STAGE"]) == "prod"


# ---------------------------------------------------------------------------
# (g) Prod parity — the same contract, one stage over
# ---------------------------------------------------------------------------


class TestProdSynthParity:
    """Prod is synth-verified only; § F5 blocks its deploy. Synth must still hold."""

    @pytest.fixture(scope="class")
    def prod_templates(self) -> tuple[dict, dict]:
        from aws_cdk import App, Environment

        from stacks.fleet_intelligence_analytics_stack import (  # noqa: PLC0415
            FleetIntelligenceAnalyticsStack,
        )
        from stacks.ui_stack import UIStack  # noqa: PLC0415

        os.environ["DEPLOYMENT_STAGE"] = "prod"
        try:
            app = App(context={})
            UIStack(app, "cms-prod-ui", env=Environment(account=_ACCOUNT, region=_ADP_REGION))
            FleetIntelligenceAnalyticsStack(
                app,
                "cms-prod-ui-analytics",
                stage="prod",
                env=Environment(account=_ACCOUNT, region=_ADP_REGION),
            )
            assembly = app.synth()
            ui = json.loads(json.dumps(assembly.get_stack_by_name("cms-prod-ui").template))
            an = json.loads(
                json.dumps(assembly.get_stack_by_name("cms-prod-ui-analytics").template)
            )
        finally:
            os.environ["DEPLOYMENT_STAGE"] = _STAGE
        return ui, an

    def test_prod_workgroup_and_bucket_are_stage_scoped(self, prod_templates) -> None:
        _, analytics = prod_templates
        wg = next(iter(_resources_of_type(analytics, "AWS::Athena::WorkGroup").values()))
        assert wg["Properties"]["Name"] == "cms-prod-analytics"
        bucket = next(iter(_resources_of_type(analytics, "AWS::S3::Bucket").values()))
        assert bucket["Properties"]["BucketName"] == (
            f"cms-prod-athena-results-{_ACCOUNT}-{_ADP_REGION}"
        )

    def test_prod_glue_arns_target_the_prod_databases(self, prod_templates) -> None:
        ui, _ = prod_templates
        st = _statement_by_sid(ui, "AdpGlueMetadataRead")
        arns = _as_set(st["Resource"])
        assert f"arn:aws:glue:{_ADP_REGION}:{_ACCOUNT}:database/adp_prod_service_records" in arns
        assert f"arn:aws:glue:{_ADP_REGION}:{_ACCOUNT}:database/adp_prod_dimensions" in arns
        assert f"arn:aws:glue:{_ADP_REGION}:{_ACCOUNT}:table/adp_prod_dimensions/vins" in arns
        assert not any("adp_staging_" in a for a in arns), (
            "prod synth references staging databases"
        )

    def test_prod_provenance_stays_simulated_until_f5_clears(self, prod_templates) -> None:
        """§ F5: three of the five products are not published to prod.

        Flipping prod to `measured` before that is real would label absent data
        as measured. The flip signal is the F5 backlog rows, not this deploy.
        """
        ui, _ = prod_templates
        env = _lambda_env(ui, "FleetIntelligenceFunction")
        assert _resolve(env["ADP_DATA_PROVENANCE"]) == "simulated"


# ---------------------------------------------------------------------------
# (h) Naming module — the fail-closed guarantees it documents
# ---------------------------------------------------------------------------


class TestNamingModuleFailsClosed:
    """These import the production module on purpose.

    Elsewhere this file restates § D4's literals rather than importing them, so a
    mutated name fails an independent expectation. Here the contract under test
    *is* the module's own validation behaviour, so there is no tautology to
    avoid — and without these, `_validate_stage` could be deleted outright and
    every other test in this file would still pass. (It could, and did: the G4
    mutation pass caught 21 of 22 contracts and this was the miss.)
    """

    @pytest.mark.parametrize(
        "bad_stage",
        ["", "   ", "STAGING", "staging ", "prod-eu", "my_stage", "stage/1"],
    )
    def test_malformed_stage_raises(self, bad_stage) -> None:
        """Shape validation, not an allowlist.

        Rejected because each of these produces a name that cannot be created or
        cannot be read: empty yields `cms--analytics`; uppercase is illegal in an
        S3 bucket name; hyphens and underscores break either the bucket-name
        segmentation or the Glue database name.
        """
        from stacks import _fleet_intelligence_naming as naming  # noqa: PLC0415

        for fn in (naming.workgroup_name, naming.curated_prefixes):
            with pytest.raises(ValueError):
                fn(bad_stage)
        with pytest.raises(ValueError):
            naming.results_bucket_name(bad_stage, _ACCOUNT)
        with pytest.raises(ValueError):
            naming.adp_lake_bucket_name(bad_stage, _ACCOUNT)
        with pytest.raises(ValueError):
            naming.adp_database_name(bad_stage, "service_records")
        with pytest.raises(ValueError):
            naming.data_provenance(bad_stage)

    @pytest.mark.parametrize("good_stage", ["staging", "prod", "test", "dev", "acme2"])
    def test_shape_valid_stages_are_accepted(self, good_stage) -> None:
        """Positive control — the guard must not pass by rejecting everything.

        `test` and `dev` are load-bearing here: `ui_stack.py` recognises them and
        `test_callback_url_registry.py` synthesizes `cms-test-ui`. A stage
        allowlist of {staging, prod} broke that suite; `acme2` stands for a
        third-party deploying this accelerator under its own stage name.
        """
        from stacks import _fleet_intelligence_naming as naming  # noqa: PLC0415

        assert naming.workgroup_name(good_stage) == f"cms-{good_stage}-analytics"
        assert len(naming.curated_prefixes(good_stage)) == 10
        assert naming.data_provenance(good_stage) in ("measured", "simulated", "derived")

    def test_unknown_stage_defaults_to_simulated_never_measured(self) -> None:
        """A sandbox stage must not inherit a `measured` label by omission."""
        from stacks import _fleet_intelligence_naming as naming  # noqa: PLC0415

        for stage in ("test", "dev", "local", "acme2"):
            assert naming.data_provenance(stage) == "simulated"

    def test_invalid_provenance_override_raises_at_synth_not_at_cold_start(self) -> None:
        """A bad ADP_DATA_PROVENANCE must fail the deploy, not the Lambda.

        The Lambda fails closed on an invalid value, which would take the whole
        Fleet Intelligence surface down at cold start instead of failing the
        deploy that caused it.
        """
        from stacks import _fleet_intelligence_naming as naming  # noqa: PLC0415

        with pytest.raises(ValueError):
            naming.data_provenance("staging", "measuredd")
        with pytest.raises(ValueError):
            naming.data_provenance("staging", "real")

    def test_valid_provenance_override_wins_over_the_stage_default(self) -> None:
        """§ F5's flip must be a config change, not a code change."""
        from stacks import _fleet_intelligence_naming as naming  # noqa: PLC0415

        assert naming.data_provenance("prod", "measured") == "measured"
        assert naming.data_provenance("prod", None) == "simulated"
        assert naming.data_provenance("prod", "  ") == "simulated"

    def test_product_list_matches_the_adp_lake_formation_share(self) -> None:
        """Restated independently — an IAM grant for a database ADP does not
        share is dead, and a shared database the IAM policy omits is unreachable.
        """
        from stacks import _fleet_intelligence_naming as naming  # noqa: PLC0415

        assert set(naming.ADP_PRODUCTS) == set(_ADP_PRODUCTS)
        assert len(naming.ADP_PRODUCTS) == 5
        assert _EXCLUDED_PRODUCT not in naming.ADP_PRODUCTS

    def test_results_bucket_name_fits_s3_limit_for_every_commercial_region(self) -> None:
        """Cross-region-namespace discipline check #2, run rather than asserted.

        The name pins us-east-1, but the budget is computed against the longest
        commercial Region name so a future re-point cannot silently overflow.
        """
        from stacks import _fleet_intelligence_naming as naming  # noqa: PLC0415

        longest_region = "ap-southeast-7"  # 14 chars, current commercial maximum
        for stage in ("staging", "prod"):
            pinned = naming.results_bucket_name(stage, "123456789012")
            hypothetical = pinned.replace(naming.ADP_REGION, longest_region)
            assert len(hypothetical) <= 63, (
                f"{hypothetical} is {len(hypothetical)} chars, over S3's 63 limit"
            )



# ---------------------------------------------------------------------------
# Lifecycle cache refresh (issue 2026-09-25-fleet-lifecycle-athena-queue-delay-504)
# ---------------------------------------------------------------------------


def _fleet_intelligence_fn(template: dict) -> tuple[str, dict]:
    fns = {
        k: v
        for k, v in _resources_of_type(template, "AWS::Lambda::Function").items()
        if k.startswith("FleetIntelligenceFunction")
    }
    assert len(fns) == 1, f"expected one FleetIntelligenceFunction, got {sorted(fns)}"
    return next(iter(fns.items()))


class TestLifecycleCacheRefresh:

    def test_schedule_targets_the_fleet_intelligence_function_with_the_refresh_task(
        self, ui_template
    ) -> None:
        fn_id, _ = _fleet_intelligence_fn(ui_template)
        rules = [
            r for r in _resources_of_type(ui_template, "AWS::Events::Rule").values()
            if any(
                fn_id in json.dumps(t.get("Arn"))
                and "refresh-lifecycle-cache" in json.dumps(t.get("Input"))
                for t in r["Properties"].get("Targets", [])
            )
        ]
        assert len(rules) == 1, (
            f"expected one lifecycle-cache rule targeting {fn_id}, got {len(rules)}"
        )
        props = rules[0]["Properties"]
        assert props["ScheduleExpression"] == "rate(10 minutes)"
        (target,) = props["Targets"]
        assert json.loads(target["Input"]) == {
            "fleetIntelligenceTask": "refresh-lifecycle-cache"
        }, "input must match index._REFRESH_TASK_KEY / _REFRESH_TASK"
        assert target["RetryPolicy"]["MaximumRetryAttempts"] == 0

    def test_refresh_input_matches_the_handler_constants(self, ui_template) -> None:
        """The CDK literal and the handler constants cannot drift apart silently."""
        import sys  # noqa: PLC0415

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from services.fleet_intelligence import index  # noqa: PLC0415

        rule = next(
            r for r in _resources_of_type(ui_template, "AWS::Events::Rule").values()
            if r["Properties"].get("ScheduleExpression") == "rate(10 minutes)"
            and "fleetIntelligenceTask" in json.dumps(r["Properties"].get("Targets"))
        )
        assert json.loads(rule["Properties"]["Targets"][0]["Input"]) == {
            index._REFRESH_TASK_KEY: index._REFRESH_TASK
        }

    def test_function_timeout_outlasts_a_three_query_refresh(self, ui_template) -> None:
        _, fn = _fleet_intelligence_fn(ui_template)
        assert fn["Properties"]["Timeout"] == 900

    def test_async_invocations_are_not_retried(self, ui_template) -> None:
        fn_id, _ = _fleet_intelligence_fn(ui_template)
        configs = [
            c for c in _resources_of_type(ui_template, "AWS::Lambda::EventInvokeConfig").values()
            if fn_id in json.dumps(c["Properties"]["FunctionName"])
        ]
        assert len(configs) == 1
        assert configs[0]["Properties"]["MaximumRetryAttempts"] == 0

    def test_only_events_rule_may_invoke_beyond_api_gateway(self, ui_template) -> None:
        """Each EventBridge permission is scoped to one of the two refresh rules' ARNs.

        No permission may name events.amazonaws.com without a SourceArn, which
        would let any rule in the account invoke the function.
        """
        fn_id, _ = _fleet_intelligence_fn(ui_template)
        perms = [
            p["Properties"] for p in _resources_of_type(ui_template, "AWS::Lambda::Permission").values()
            if fn_id in json.dumps(p["Properties"]["FunctionName"])
            and p["Properties"]["Principal"] == "events.amazonaws.com"
        ]
        sources = sorted(
            "FleetLifecycleCacheRefreshRule" if "FleetLifecycleCacheRefreshRule" in json.dumps(p.get("SourceArn"))
            else "FleetAdpRollupRefreshRule" if "FleetAdpRollupRefreshRule" in json.dumps(p.get("SourceArn"))
            else "UNSCOPED"
            for p in perms
        )
        assert sources == ["FleetAdpRollupRefreshRule", "FleetLifecycleCacheRefreshRule"]



# ---------------------------------------------------------------------------
# ADP rollup refresh rule (spec 2026-09-25-cms-fi-adp-wide-lifecycle § D3, D4)
# ---------------------------------------------------------------------------

# The new hourly rollup refresh rule adds a second EventBridge rule alongside the
# existing "rate(10 minutes)" lifecycle-cache refresh.  Tests below pin every
# observable property:
#   - schedule: rate(1 hour)           (spec D3)
#   - input: {"fleetIntelligenceTask": "refresh-adp-rollup"}  (spec D3)
#   - retry attempts: 0                 (spec D3 "retries 0")
#   - env FI_LIFECYCLE_WINDOW_MONTHS present and "36"  (spec D4)
# The env-key count is bumped from eight to nine in the updated test below.

_ADP_ROLLUP_TASK_INPUT = {"fleetIntelligenceTask": "refresh-adp-rollup"}
_ADP_ROLLUP_SCHEDULE = "rate(1 hour)"
_FI_LIFECYCLE_WINDOW_MONTHS_KEY = "FI_LIFECYCLE_WINDOW_MONTHS"


class TestFleetAdpRollupRefreshRule:
    """CDK guard for the FleetAdpRollupRefreshRule (spec D3)."""

    def _adp_rollup_rule(self, ui_template: dict) -> dict:
        """Return the EventBridge rule whose input is the ADP rollup task."""
        matching = [
            r["Properties"]
            for r in _resources_of_type(ui_template, "AWS::Events::Rule").values()
            if any(
                json.loads(t.get("Input", "{}")) == _ADP_ROLLUP_TASK_INPUT
                for t in r["Properties"].get("Targets", [])
            )
        ]
        assert len(matching) == 1, (
            f"expected exactly one rule with input {_ADP_ROLLUP_TASK_INPUT}, "
            f"got {len(matching)}. Check that FleetAdpRollupRefreshRule is defined "
            "in ui_stack.py."
        )
        return matching[0]

    def test_adp_rollup_rule_exists_with_hourly_schedule(self, ui_template) -> None:
        props = self._adp_rollup_rule(ui_template)
        assert props["ScheduleExpression"] == _ADP_ROLLUP_SCHEDULE, (
            f"expected schedule {_ADP_ROLLUP_SCHEDULE!r}, "
            f"got {props['ScheduleExpression']!r}"
        )

    def test_adp_rollup_rule_input_matches_refresh_adp_rollup_task(
        self, ui_template
    ) -> None:
        props = self._adp_rollup_rule(ui_template)
        (target,) = props["Targets"]
        actual_input = json.loads(target["Input"])
        assert actual_input == _ADP_ROLLUP_TASK_INPUT, (
            f"rule input must be {_ADP_ROLLUP_TASK_INPUT}, got {actual_input}"
        )

    def test_adp_rollup_rule_has_zero_retries(self, ui_template) -> None:
        """spec D3: retries 0 — a failed refresh is retried by the next scheduled run."""
        props = self._adp_rollup_rule(ui_template)
        (target,) = props["Targets"]
        retry = target.get("RetryPolicy", {}).get("MaximumRetryAttempts")
        assert retry == 0, (
            f"FleetAdpRollupRefreshRule must have MaximumRetryAttempts=0, got {retry}"
        )

    def test_adp_rollup_rule_and_lifecycle_cache_rule_are_distinct(
        self, ui_template
    ) -> None:
        """Two rules must co-exist: the existing 10-min cache refresh and the new
        1-hour ADP rollup refresh."""
        rules = _resources_of_type(ui_template, "AWS::Events::Rule")
        fi_rules = [
            r["Properties"]
            for r in rules.values()
            if "fleetIntelligenceTask" in json.dumps(r["Properties"].get("Targets", []))
        ]
        assert len(fi_rules) == 2, (
            f"expected exactly 2 fleet-intelligence EventBridge rules "
            f"(lifecycle-cache refresh + adp-rollup refresh), got {len(fi_rules)}"
        )

    def test_adp_rollup_rule_targets_the_fleet_intelligence_function(
        self, ui_template
    ) -> None:
        fn_id, _ = _fleet_intelligence_fn(ui_template)
        props = self._adp_rollup_rule(ui_template)
        (target,) = props["Targets"]
        assert fn_id in json.dumps(target.get("Arn")), (
            f"FleetAdpRollupRefreshRule target ARN does not reference {fn_id}"
        )

    def test_adp_rollup_input_matches_index_refresh_adp_task_constant(
        self, ui_template
    ) -> None:
        """The CDK literal and index._REFRESH_ADP_TASK cannot drift silently."""
        import sys  # noqa: PLC0415

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from services.fleet_intelligence import index  # noqa: PLC0415

        # index._REFRESH_ADP_TASK must equal "refresh-adp-rollup"
        assert hasattr(index, "_REFRESH_ADP_TASK"), (
            "index._REFRESH_ADP_TASK not found; add the constant to index.py"
        )
        assert json.loads(
            self._adp_rollup_rule(ui_template)["Targets"][0]["Input"]
        ) == {index._REFRESH_TASK_KEY: index._REFRESH_ADP_TASK}


# ---------------------------------------------------------------------------
# D4: FI_LIFECYCLE_WINDOW_MONTHS env var  (spec 2026-09-25-cms-fi-adp-wide-lifecycle § D4)
# ---------------------------------------------------------------------------


class TestFleetIntelligenceLifecycleWindowMonths:
    """FI_LIFECYCLE_WINDOW_MONTHS must be set to "36" in the Lambda env block."""

    def test_fi_lifecycle_window_months_is_present_and_36(self, ui_template) -> None:
        env = _lambda_env(ui_template, "FleetIntelligenceFunction")
        assert _FI_LIFECYCLE_WINDOW_MONTHS_KEY in env, (
            f"{_FI_LIFECYCLE_WINDOW_MONTHS_KEY} missing from FleetIntelligenceFunction env; "
            "add it in ui_stack.py with value '36' (spec D4)"
        )
        assert _resolve(env[_FI_LIFECYCLE_WINDOW_MONTHS_KEY]) == "36", (
            f"expected {_FI_LIFECYCLE_WINDOW_MONTHS_KEY}='36', "
            f"got {_resolve(env[_FI_LIFECYCLE_WINDOW_MONTHS_KEY])!r}"
        )


# ---------------------------------------------------------------------------
# Updated env-key count: eight → nine (spec D4 adds FI_LIFECYCLE_WINDOW_MONTHS)
# ---------------------------------------------------------------------------
# The original test_env_keys_are_exactly_the_eight_the_code_reads in
# TestFleetIntelligenceEnvBlock checks against _EXPECTED_FI_ENV_KEYS (8 keys).
# That set must be expanded to include FI_LIFECYCLE_WINDOW_MONTHS.
# This separate test pins the nine-key contract so the update to
# _EXPECTED_FI_ENV_KEYS cannot be silently skipped.


class TestFleetIntelligenceEnvBlockNineKeys:
    """Spec D4 bumps the env-key count from eight to nine."""

    def test_env_keys_are_exactly_the_nine_the_code_reads(self, ui_template) -> None:
        _expected_nine = _EXPECTED_FI_ENV_KEYS | {_FI_LIFECYCLE_WINDOW_MONTHS_KEY}
        env = _lambda_env(ui_template, "FleetIntelligenceFunction")
        assert set(env) == _expected_nine, (
            f"env drift (expected nine keys).\n"
            f"  missing: {sorted(_expected_nine - set(env))}\n"
            f"  extra:   {sorted(set(env) - _expected_nine)}"
        )
