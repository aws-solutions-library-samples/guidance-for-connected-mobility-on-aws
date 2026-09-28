"""
UI Stack - Frontend, API Gateway, and Cognito authentication
"""

import os
import sys
from datetime import datetime, timezone
from aws_cdk import (
    Stack,
    aws_cognito as cognito,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_apigateway as apigateway,
    aws_apigatewayv2 as apigatewayv2,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_location as location,
    aws_certificatemanager as acm,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_sns as sns,
    aws_kms as kms,
    aws_events as events,
    aws_events_targets as events_targets,
    custom_resources as custom_resource,
    CustomResource,
    CfnOutput,
    Duration,
    Fn,
    Size
)
from constructs import Construct
from typing import Dict

# Fail-closed synth guard for the account-provisioning feature gate.
# Same import shape app.py uses for aspects/{bucket_retain_aspect,domain_alias_guard}.
from aspects.provisioning_guards import (
    _check_brand_audit_summary,
    _grep_fail_open_patched,
    _require_client_idp_config,
    _require_external_signup_config,
    _require_internal_provisioning_config,
    _require_provisioning_guards,
    _require_signup_flag_coupled,
)
from stacks.cognito_pool_waf import CognitoPoolWaf
from stacks import _fleet_intelligence_naming as fi_naming
import json
import boto3
import botocore


def _lookup_stack_output(stack_name: str, output_key: str, region: str) -> str:
    """Resolve a sibling stack's output at synth time.
    Returns empty string if the stack or output doesn't exist yet -
    this lets the UI stack be deployed standalone before siblings."""
    try:
        cfn = boto3.client('cloudformation', region_name=region)
        resp = cfn.describe_stacks(StackName=stack_name)
        for output in resp['Stacks'][0].get('Outputs', []):
            if output['OutputKey'] == output_key:
                return output['OutputValue']
    except (botocore.exceptions.ClientError, botocore.exceptions.NoCredentialsError, Exception):
        pass
    return ""


def _lookup_cognito_domain(user_pool_id: str, region: str) -> str:
    """Return the user pool's Hosted UI domain (auth.<region>.amazoncognito.com)
    or empty string if none configured yet."""
    try:
        cidp = boto3.client('cognito-idp', region_name=region)
        resp = cidp.describe_user_pool(UserPoolId=user_pool_id)
        domain = resp.get('UserPool', {}).get('Domain')
        if domain:
            return f"{domain}.auth.{region}.amazoncognito.com"
    except Exception:
        pass
    return ""


def _lookup_secret_version_id(secret_name: str, region: str) -> str:
    """Return the AWSCURRENT version id of *secret_name*, or ``""`` if the secret
    does not exist yet or AWS credentials are unavailable.

    Why this exists.  ``SetDemoUserPassword`` (``Custom::CmsDemoUserPassword``) must
    re-run its handler whenever the demo password is rotated so the new value is
    propagated to Cognito.  CloudFormation sends an ``Update`` event only when at
    least one resource property has changed.  ``SecretVersionId`` carries the
    ``AWSCURRENT`` version id so that a rotation (which increments the version)
    produces a property diff → ``Update`` → handler re-applies the new value.

    The degrade-to-``""`` contract mirrors ``_lookup_stack_output`` and
    ``_lookup_cognito_domain``:

    * First deploy — secret does not exist yet → ``ResourceNotFoundException`` →
      returns ``""``; ``Create`` still runs because the resource is new.
    * Synth without credentials → ``NoCredentialsError`` → returns ``""``; this
      produces a spurious ``Update`` on the next credentialled synth (idempotent
      re-apply, documented in spec § Design as the honest trade-off).
    * Steady state, no rotation → same version id; no property diff; no ``Update``.
    * After rotation → new version id; property diff → ``Update`` → password
      re-applied.

    Regressed:  n/a — this is a new helper, introduced by
    ``.kiro/specs/2026-08-04-cms-demo-credential-out-of-template/``.
    """
    try:
        sm = boto3.client("secretsmanager", region_name=region)
        resp = sm.describe_secret(SecretId=secret_name)
        for version_id, stages in resp.get("VersionIdsToStages", {}).items():
            if "AWSCURRENT" in stages:
                return version_id
    except (botocore.exceptions.ClientError, botocore.exceptions.NoCredentialsError):
        pass
    except Exception:
        pass
    return ""


# Keys that may legitimately appear to be credential-shaped in a custom-resource
# property or CfnOutput but are known to be safe (e.g. they resolve to an ARN
# or a token, not a plaintext credential value).  Per-entry comments are
# mandatory — adding a key is a visible diff so the next reviewer knows it was
# deliberate.
_CREDENTIAL_KEY_ALLOWLIST: frozenset = frozenset({
    # ARN of the Secrets Manager secret — resolves to a token at synth time;
    # never carries the plaintext password.
    "DemoUserPasswordSecretArn",
    # The version id of AWSCURRENT — a UUID-shaped opaque id, not a credential.
    "SecretVersionId",
    # The secret ARN passed as a handler property — this is an ARN, not the value.
    "SecretArn",
    # ARN of the shared persona-passwords secret (JSON keyed by email).
    # Resolves to a token / ARN at synth time; never carries plaintext passwords.
    # Adopted via Secret.from_secret_name_v2 — spec constraint: must NOT recreate
    # the secret over the existing name (would invalidate the 2026-08-05 rotations).
    "DemoPersonaPasswordsSecretArn",
})


def _assert_no_plaintext_credential_in_template(stack: "Stack") -> None:  # noqa: F821
    """Raise ``ValueError`` if the synthesized template contains a plaintext
    credential in any custom-resource property, ``CfnOutput``, or
    ``AWS::SecretsManager::Secret.Properties.SecretString``.

    Why this exists.  ``deployment/stacks/ui_stack.py`` previously wrote the CMS
    demo password into the CloudFormation template in three places, including a
    ``CfnOutput`` named ``DefaultUserPassword`` readable by any principal holding
    ``cloudformation:DescribeStacks``.  The exposure was identified and dropped
    three times (2026-05-26, 2026-06-19, 2026-08-04) without enforcement.

    Failure mode that motivated this.  Each of the three prior reviews classified
    the finding as a *suggestion* or a *deferred action*, and the absence of a
    synth-time gate meant a spec that removed the exposure could not prove the fix
    held — just as ``DRIVER_SELF_GUARD_ENABLED=true`` was committed on 2026-07-16
    but the guard sat inert in prod for 18 days until the same fail-closed pattern
    caught it.  See ``issues/2026-08-04-prod-demo-credential-plaintext-in-cfn-template/``.

    Two independent checks; both are load-bearing:

    1. **Value check.**  If ``CMS_DEMO_DEFAULT_PASSWORD`` is set in the environment,
       its value must not appear in any resolved property or output value.  Fails on
       any synth where the deploying shell still exports the old credential.

    2. **Shape check.**  Any property key or output name matching
       ``/password|passwd|secret|credential/i`` whose resolved value is a plain
       string literal raises.  Unresolved tokens, ``Fn::`` intrinsics, and ARN-shaped
       values pass.  This check is what gives the guard teeth once
       ``CMS_DEMO_DEFAULT_PASSWORD`` stops being exported — which is exactly the state
       this spec creates.  A guard implementing only the value check is **inert** on
       any synth where the env var is unset, i.e. every synth in the new world.

    Covered resource types:

    - ``Custom::*`` and ``AWS::CloudFormation::CustomResource`` — all properties.
    - ``AWS::SecretsManager::Secret`` — the ``SecretString`` property only.  This
      closes the gap where a future contributor reaches for
      ``SecretValue.unsafe_plain_text()`` on a *new* secret.  The correct alternative
      is ``generate_secret_string``, which sets no ``SecretString`` property at all.
      See ``issues/2026-08-04-prod-demo-credential-plaintext-in-cfn-template/`` § Fix
      Group 2.
    - ``CfnOutput`` — the output value.

    Allowlist: ``_CREDENTIAL_KEY_ALLOWLIST`` (module-level frozenset).  Adding an
    entry is a visible diff; the per-entry comment is mandatory.
    """
    import re

    credential_pattern = re.compile(
        r"password|passwd|secret|credential", re.IGNORECASE
    )
    # ARN-shaped values are exempt from the shape check — they are resource
    # identifiers, not secrets.
    arn_pattern = re.compile(r"^arn:[a-z0-9\-]+:")

    env_value = os.environ.get("CMS_DEMO_DEFAULT_PASSWORD", "")

    issue_ref = "issues/2026-08-04-prod-demo-credential-plaintext-in-cfn-template/"

    def _check_value(resolved, key: str, resource_path: str) -> None:
        """Apply both checks to a single resolved value."""
        # ---- Check 1: value check (env-var match) ----
        if env_value and isinstance(resolved, str) and env_value in resolved:
            raise ValueError(
                f"Plaintext credential detected in template: "
                f"resource={resource_path!r}, key={key!r} contains the value of "
                f"CMS_DEMO_DEFAULT_PASSWORD. "
                f"See {issue_ref}"
            )

        # ---- Check 2: shape check (credential-named key with plain string) ----
        if key in _CREDENTIAL_KEY_ALLOWLIST:
            return
        if not credential_pattern.search(key):
            return
        # Key matches /password|passwd|secret|credential/i — inspect the value.
        if not isinstance(resolved, str):
            # A dict / list is a Token / Fn:: intrinsic — safe.
            return
        if arn_pattern.match(resolved):
            # ARN-shaped literal — exempt.
            return
        # Non-ARN plain string in a credential-shaped key — this is the exposure.
        raise ValueError(
            f"Plaintext credential detected in template: "
            f"resource={resource_path!r}, key={key!r} has a plain string value "
            f"in a credential-shaped property/output. "
            f"Use a Secrets Manager reference or a CDK token instead. "
            f"See {issue_ref}"
        )

    # Walk every node in the construct tree.
    from aws_cdk import CfnResource as _CfnResource, CfnOutput as _CfnOutput

    for node in stack.node.find_all():
        # ---- Custom resources ----
        if isinstance(node, _CfnResource):
            cfn_type = getattr(node, "cfn_resource_type", "") or ""

            if (
                cfn_type.startswith("Custom::")
                or cfn_type == "AWS::CloudFormation::CustomResource"
            ):
                # Properties are in node._cfn_properties (L1 construct internal).
                raw_props = getattr(node, "_cfn_properties", {}) or {}
                for prop_key, prop_val in raw_props.items():
                    resolved = stack.resolve(prop_val)
                    resource_path = node.node.path
                    _check_value(resolved, prop_key, resource_path)

            elif cfn_type == "AWS::SecretsManager::Secret":
                # Catch a future contributor reaching for SecretValue.unsafe_plain_text()
                # on a new secret — that path writes the literal into SecretString and
                # this guard would not have fired under the old Custom::* filter.
                # The correct alternative is generate_secret_string, which does NOT
                # set the secretString property at all.  See spec § Decision 1.
                raw_props = getattr(node, "_cfn_properties", {}) or {}
                secret_string_val = raw_props.get("secretString")
                if secret_string_val is not None:
                    resolved = stack.resolve(secret_string_val)
                    resource_path = node.node.path
                    # Use key name "SecretString" so _check_value's credential pattern
                    # fires (contains "secret").
                    _check_value(resolved, "SecretString", resource_path)

        # ---- CfnOutputs ----
        elif isinstance(node, _CfnOutput):
            output_id = node.node.id
            raw_val = getattr(node, "value", None)
            if raw_val is not None:
                resolved = stack.resolve(raw_val)
                _check_value(resolved, output_id, f"Outputs/{output_id}")


def _build_bedrock_agents_dict(stack, import_stack_name: str = "") -> dict:
    """(Retired 2026-09-06) VFO Bedrock Agents stack was removed per spec
    `2026-09-05-cms-vfo-teardown`. The runtimeConfig `bedrockAgent.agents`
    key is retained as an empty dict for one-release back-compat with any
    external consumer that might read it; new consumers should target
    `services/fleet_intelligence/` (Tier 1) or CVX's `/assistant/chat`
    endpoint instead.
    """
    _ = (stack, import_stack_name)  # accepted for signature stability; ignored
    return {}


# Stages that may deploy with the driver-self guard OFF. Deliberately a denylist of
# known development stages rather than an allowlist of deployed ones: an
# unrecognised or misspelled stage must fail closed, because the failure mode of
# guessing wrong is a production API that does not constrain driver-scoped tokens.
_GUARD_OPTIONAL_STAGES = frozenset({"", "dev", "development", "local", "test"})


# ── External-UI callback-URL registry (spec 2026-09-03-cms-auth-api-core-extraction) ──
#
# Every standalone frontend that shares this user pool and this app client MUST
# register its OAuth origin here — one entry per frontend, one line to add.
#
# Background: the DMS dealer console (an external frontend on its own
# CloudFront distribution) was the first frontend to need this treatment.
# It shares the pool and client per that spec's Decision 2, but lives on
# its own CloudFront distribution whose default domain is allocated at
# deploy time and cannot be derived. A CDK context key carrying the
# https:// origin is the established pattern.
#
# How the registry works: when a key in this list is set via CDK context
# (`-c <key>=https://…`), UIStack appends `<origin>/auth/callback` to
# CallbackURLs and `<origin>/` to LogoutURLs on the UserPoolClient. Entries are
# appended in list order, after the primary CMS UI origin.
#
# When ALL entries are unset (the default), the emitted UserPoolClient properties
# are byte-identical to the pre-registry state. This is what keeps
# `test_client_idp_config.py::test_staging_still_gets_a_real_callback` — an
# exact-equality assertion on CallbackURLs — passing untouched.
#
# To onboard a new frontend authenticating against this pool:
#   1. Add its CDK context key name to this list (one line).
#   2. Pass `-c <key>=https://<your-origin>` in your deploy command.
#   Do NOT add a new `if _<frontend>_callback_origin:` block elsewhere in this
#   file — that is the copy-paste pattern this registry was created to eliminate.
#
# Security: every value is validated to be an https:// origin at synth time.
# An http:// or bare-host value raises ValueError before CDK produces a template.
EXTERNAL_UI_CALLBACK_CONTEXT_KEYS: list[str] = [
    "dmsUiCallbackOrigin",   # DMS standalone-UI (spec 2026-08-31-dms-standalone-ui, T3.2)
    "connectedServicesUiCallbackOrigin",  # Connected Services portal (spec 2026-09-04-cms-connected-services-portal-v2, T9.2)
]


# 2026-09-02: _SSO_GATED_ADMIN_FORBIDDEN_STAGES + _sso_gated_admin_default()
# removed. The consumer (main_api._sso_gated_admin_enabled + the groupless→admin
# branch it gated) was deleted in the same commit — see
# issues/2026-09-02-cms-sso-gated-admin-branch-removed/. Preserving the
# fail-closed synth-time helpers with no consumer would be dead code.


def _require_driver_self_guard() -> str:
    """Return the DRIVER_SELF_GUARD_ENABLED value, failing synth if a deployed
    stage would ship it disabled.

    Why this exists. The value is read from the DEPLOYING SHELL's environment. The
    canonical Make target extracts it from `config/<stage>.env` and exports it, but
    a bare `cdk deploy` does not — and the old default was a silent `'false'`. So
    any deploy path that skipped the Make target shipped a security control turned
    off, with no error and an `UPDATE_COMPLETE` stack.

    That is not hypothetical. `DRIVER_SELF_GUARD_ENABLED=true` was committed to
    `config/prod.env` on 2026-07-16 (`bd75a5c`, "closes P0 security gap"), prod was
    deployed on 2026-07-29, and the deployed Lambda still reported `false` on
    2026-08-03 — 18 days during which the fix was believed shipped and was not.
    Full account: `issues/2026-08-03-prod-driver-self-guard-inert/`.

    Raising at synth converts that silent 18-day exposure into a deploy that stops
    before it starts. Same fail-closed-at-synth pattern as
    `aspects/domain_alias_guard.py` in this repo, and as the CVX kiosk guardrail and
    `cmsKmsKeyArn` guards in the sibling repo.

    A dev stage may still deploy with the guard off; that is what
    `_GUARD_OPTIONAL_STAGES` is for.
    """
    raw = os.environ.get('DRIVER_SELF_GUARD_ENABLED', '')
    enabled = raw.strip().lower() in ('1', 'true', 'yes', 'on')
    stage = os.environ.get('DEPLOYMENT_STAGE', '').strip().lower()

    if not enabled and stage not in _GUARD_OPTIONAL_STAGES:
        raise ValueError(
            f"DRIVER_SELF_GUARD_ENABLED is {raw!r} for DEPLOYMENT_STAGE={stage!r}. "
            "Deploying this stage with the driver-self guard disabled would leave "
            "main_api unable to constrain driver-scoped tokens to the self-service "
            "allowlist — the P0 gap that sat open in prod for 18 days "
            "(issues/2026-08-03-prod-driver-self-guard-inert/).\n"
            "Fix: deploy via the canonical Make target, which exports the value "
            "from config/<stage>.env — e.g. `make prod-deploy` or `make phase1 "
            "DEPLOYMENT_STAGE=<stage>`. To deploy a bare `cdk deploy`, export it "
            "yourself: `export DRIVER_SELF_GUARD_ENABLED=true`.\n"
            f"If this genuinely is a development stage, set DEPLOYMENT_STAGE to one "
            f"of {sorted(_GUARD_OPTIONAL_STAGES)!r} — unrecognised stages fail "
            "closed on purpose."
        )
    return 'true' if enabled else 'false'


class UIStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, 
                 storage_tables: Dict[str, dynamodb.Table] = None,
                 redis_endpoint: str = None,
                 msk_stack=None,
                 data_processing_api_endpoint: str = None,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        self._data_processing_api_endpoint = data_processing_api_endpoint or ""
        
        # Resolve VPC and Redis from MSK stack (single VPC architecture)
        if msk_stack:
            redis_endpoint = msk_stack.redis_endpoint
            self._data_vpc = msk_stack.vpc
            self._data_sg = msk_stack.msk_security_group
        else:
            self._data_vpc = None
            self._data_sg = None
        
        # Amazon Location Services resources
        # Provider switched from Esri → HERE on 2026-05-29 for parity with
        # prod's manually-created `cvs_location_map_test2` HERE map (see
        # issues/2026-05-29-staging-map-provider-esri-not-here/report.md).
        # Prod additionally has a legacy hand-created HERE map outside CDK;
        # the IAM unauthenticated role still allowlists that legacy map ARN
        # below for backward compatibility, but new deploys (staging) get
        # HERE tiles directly from the CDK-managed map.
        # NOTE: AWS::Location::Map, PlaceIndex, RouteCalculator do NOT support
        # in-place update of Style/DataSource AND require non-empty *_name
        # properties at the CFN schema level (CFN early validation rejects
        # change sets where these are absent — confirmed 2026-05-29 attempt).
        # Auto-generated names are therefore not an option. Approach taken
        # instead: deterministic rename with a `-here` provider-suffix so the
        # new physical names differ from the old. Different physical names →
        # CFN performs CREATE-new + DELETE-old (no name-conflict failure).
        self.map = location.CfnMap(
            self, "CMSVehicleMap",
            map_name=f"{construct_id}-vehicle-map-here",
            configuration=location.CfnMap.MapConfigurationProperty(
                style="VectorHereExplore"
            ),
            description="Map for Connected Mobility Solution vehicle tracking",
            pricing_plan="RequestBasedUsage"
        )
        
        self.place_index = location.CfnPlaceIndex(
            self, "CMSPlaceIndex",
            index_name=f"{construct_id}-place-index-here",
            data_source="Here",
            description="Place index for Connected Mobility Solution",
            pricing_plan="RequestBasedUsage"
        )
        
        # Route calculator for simulation routing
        self.route_calculator = location.CfnRouteCalculator(
            self, "CMSRouteCalculator",
            calculator_name=f"{construct_id}-route-calculator",
            data_source="Here",
            description="Route calculator for Connected Mobility Solution simulation"
        )
        self.route_calculator_name = self.route_calculator.calculator_name
        
        # Use actual table names from storage stack (with suffixes)
        # Table names — hardcoded to avoid cross-stack CloudFormation exports
        # (storage stack tables were created outside CDK for this deployment)
        storage_prefix = construct_id.replace('-ui', '-storage')
        table_names = {
            'fleets': f"{storage_prefix}-fleets",
            'vehicles': f"{storage_prefix}-vehicles",
            'trips': f"{storage_prefix}-trips",
            'telemetry': f"{storage_prefix}-telemetry",
            'safety_events': f"{storage_prefix}-safety-events",
            'maintenance_events': f"{storage_prefix}-maintenance-alerts",
            'user_preferences': f"{storage_prefix}-user-preferences",
            'dashboard_metrics_cache': f"{storage_prefix}-dashboard-metrics-cache",
            'vehicle_certificates': f"{storage_prefix}-vehicle-certificates",
            'drivers': f"{storage_prefix}-drivers",
            'service_history': f"{storage_prefix}-service-history",
            'fleet_enrollment': f"{storage_prefix}-fleet-enrollment",
            # Written by MaintenanceProcessor (threshold path) AND
            # FWTelemetryProcessor (authentic UDS path). Read by the Fleet
            # API Lambda's /api/v1/vehicles/{vehicleId}/dtcs route.
            'dtc_history': f"{storage_prefix}-dtc-history",
            # NOT under storage_prefix. Created by data_processing_stack.py:143
            # as cms-{stage}-campaigns. Every other entry in this dict is a
            # storage-stack table, which is why this one was originally missed
            # and CAMPAIGNS_TABLE_NAME shipped unwired — see
            # issues/2026-09-02-campaign-guard-queries-wrong-stage-table.
            'campaigns': f"{construct_id.replace('-ui', '')}-campaigns",
        }
        
        ws_connections_table_name = f"{storage_prefix}-ws-connections"
        vehicle_link_codes_table_name = f"{storage_prefix}-vehicle-link-codes"
        
        # Cognito User Pool
        self.user_pool = cognito.UserPool(
            self, "CMSUserPool",
            user_pool_name=f"{construct_id}-users",
            self_sign_up_enabled=self.node.try_get_context('cms.allow_self_signup') in (True, 'true', '1'),
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True
            ),
            custom_attributes={
                "fleetIds": cognito.StringAttribute(mutable=True),
                # vehicleIds already exists in the pool (added post-creation)
                # ── VSA / iOS-app schema (piggyback) ────────────────────────
                # The iOS demo personas (Samantha / Marcus / Priya) need these
                # attributes per `seed_driver_users.py`. Schema mirrors the
                # legacy CVX VSA pool
                # so this pool can replace it: one
                # CMS-deployed pool serves both Fleet Manager web users AND
                # iOS app users in any region. Closes the cross-region
                # defect surfaced by clean-deploy run 14
                # (`InvalidParameterException: Type for attribute
                # {custom:tenantId} could not be determined`).
                # Issue: issues/2026-06-04-cms-vsa-pool-id-region-aware-fallback/
                "tenantId": cognito.StringAttribute(mutable=False),
                "driverId": cognito.StringAttribute(mutable=False),
                "role": cognito.StringAttribute(mutable=True),
                "vehicleId": cognito.StringAttribute(mutable=True),
                "provisionedVia": cognito.StringAttribute(mutable=False),
                # ── DMS per-dealer grants ───────────────────────────────────
                # Consumed by the DMS accelerator's API handlers
                # (`source/handlers/auth.py::authorize_dealer_scope`) to decide
                # which dealers a caller may read. Same shape and role as
                # `fleetIds` above: a delimiter-separated list of IDs scoping an
                # otherwise group-authorized caller to specific resources.
                #
                # MUTABLE, deliberately, and NOT by analogy with
                # `provisionedVia` immediately above. Dealer grants change over
                # a user's life — reassignment, adding a second rooftop, and
                # revocation. An immutable attribute can only be written when
                # the user is CREATED (AWS: "You can only write a value to an
                # immutable attribute when you create a user"), so immutability
                # here would make regranting impossible without deleting and
                # recreating the account. `fleetIds` is mutable for the same
                # reason; `tenantId` / `driverId` / `provisionedVia` are
                # immutable because they are facts of origin, not grants.
                #
                # ONE-WAY DOOR: per AWS, custom attributes cannot be deleted or
                # renamed once created (`AddCustomAttributes`: "You can't delete
                # custom attributes after you create them"). This name is
                # permanent from first deploy. Additive-only schema changes do
                # NOT replace the pool — the CFN SchemaAttribute sub-properties
                # are "No interruption" and the CDK docs state "during a user
                # pool update, you can add new schema attributes" — but confirm
                # `cdk diff` shows an attribute add and never a Replace on the
                # pool logical id before deploying. Pool replacement would
                # delete every user in the pool.
                #
                # Spec: DMS `2026-08-26-dms-accelerator-v1` OQ2, resolved
                # 2026-08-28 to per-dealer grants. DMS-side enforcement is
                # already committed and FAILS CLOSED without this attribute:
                # absent grants deny every dealer, so deploying this is what
                # makes the DMS dealer endpoints reachable at all.
                "dealerIds": cognito.StringAttribute(mutable=True),
                # `districtIds` — DMS per-DISTRICT grants, one level up from
                # `dealerIds` above. Consumed by
                # `source/handlers/auth.py::authorize_district_scope` for the
                # `/dms/districts/{districtId}/*` reads, which previously
                # authorized the `district-manager` group and then trusted the
                # district ID in the path — any district manager could read any
                # district.
                #
                # PLURAL deliberately. A single-district claim would be cheaper
                # to reason about, but a manager covering two districts is an
                # ordinary case, and widening a claim later is a second
                # migration against an attribute that cannot be renamed.
                #
                # MUTABLE for the same reason as `dealerIds`: this is an
                # entitlement, not a fact of origin. Territory reassignment is
                # routine.
                #
                # Chosen over deriving district membership from `dealerIds`
                # (i.e. "your districts are the districts of your dealers"),
                # which needs no new attribute but makes membership implicit:
                # adding a rooftop to a district would silently shrink a
                # manager's view until someone updated their dealer grants, and
                # the symptom — data missing from a dashboard — does not look
                # like an authorization problem.
                "districtIds": cognito.StringAttribute(mutable=True),
                # ── AVX standing identity contract § 5.1 ──────────────────
                # `customerId` — resolves the owner standing on the AVX findings
                # and actions routes. Keyed on `owner_id` (CMS `sold_to`) in the
                # CVX lambda. See CVX spec `2026-09-24-avx-standing-claims-provisioning`.
                #
                # MUTABLE — irreversibly. Custom attributes cannot be removed or
                # have mutability changed once created. Mutability is required here:
                # 16 existing users must be backfilled via assign_standing_claims.py,
                # and future provisioning scripts need to write it.
                # ONE-WAY DOOR: the attribute name and mutability are permanent from
                # first deploy. See the `dealerIds` comment above for the general
                # constraint on Cognito custom attributes.
                "customerId": cognito.StringAttribute(mutable=True),
            },
            keep_original=cognito.KeepOriginalAttrs(email=True),
        )
        
        # Cognito User Pool Groups — role-based access
        cognito.CfnUserPoolGroup(
            self, "PlatformAdminGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="platform-admin",
            description="Full access to all fleets, system config, OEM connectors"
        )
        
        cognito.CfnUserPoolGroup(
            self, "FleetOperatorGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="fleet-operator",
            description="Manage own fleet vehicles, trips, telemetry, drivers"
        )
        
        cognito.CfnUserPoolGroup(
            self, "FleetViewerGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="fleet-viewer",
            description="Read-only access to own fleet dashboards"
        )

        # fleet-guest — scoped read-only for SELF-REGISTERED external users.
        #
        # Separate from fleet-viewer on purpose. Despite its name, fleet-viewer is an
        # UNSCOPED global-read role in main_api (`has_unscoped_access = is_admin or
        # is_viewer`), so assigning it to self-registered users — as spec
        # 2026-08-07-cms-account-provisioning-model originally specified — would give
        # any internet registrant read access to every fleet. fleet-guest is never
        # unscoped; it reads only the fleets in its `custom:fleetIds`, which the
        # provisioning trigger sets to a single purpose-built public demo fleet.
        #
        # See decisions.md 2026-08-10 "Phase B assigns fleet-guest, not fleet-viewer"
        # and issues/2026-08-10-cms-demo-external-exposure/.
        cognito.CfnUserPoolGroup(
            self, "FleetGuestGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="fleet-guest",
            description=(
                "Scoped read-only access to the public demo fleet only. "
                "Assigned automatically to self-registered external users."
            )
        )

        # ── DMS Dealer Management System groups ───────────────────────────────
        #
        # These seven groups are CMS Cognito declarations only. Authorization is
        # enforced server-side by the DMS API (guidance-for-dealer-management-system-
        # on-aws, source/handlers/auth.py). The UI gate in RequireGroup.tsx performs
        # a best-effort UX guard only — it is NOT the authorization boundary.
        #
        # Each description says where enforcement actually happens, to prevent the
        # same confusion that produced the fleet-viewer unscoped-read surprise
        # recorded at ui_stack.py:668 — where a UI-only flag was mistaken for backend
        # authorization. See DMS spec 2026-08-26-dms-accelerator-v1 for group semantics.
        cognito.CfnUserPoolGroup(
            self, "DmsDealerAdminGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="dealer-admin",
            description=(
                "Full access to a dealer's data. "
                "Enforced server-side by DMS API auth.py; CMS gates UI only."
            )
        )

        cognito.CfnUserPoolGroup(
            self, "DmsServiceAdvisorGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="service-advisor",
            description=(
                "Manage service lane appointments, ROs, and customer communications. "
                "Enforced server-side by DMS API auth.py; CMS gates UI only."
            )
        )

        cognito.CfnUserPoolGroup(
            self, "DmsFandIManagerGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="f-and-i-manager",
            description=(
                "Finance and insurance deal workflow access. "
                "Enforced server-side by DMS API auth.py; CMS gates UI only."
            )
        )

        cognito.CfnUserPoolGroup(
            self, "DmsDistrictManagerGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="district-manager",
            description=(
                "Cross-dealer portfolio view scoped to custom:districtIds. "
                "Enforced server-side by DMS API auth.py; CMS gates UI only."
            )
        )

        cognito.CfnUserPoolGroup(
            self, "DmsPartsManagerGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="parts-manager",
            description=(
                "Parts inventory, orders, and fitment management. "
                "Enforced server-side by DMS API auth.py; CMS gates UI only."
            )
        )

        cognito.CfnUserPoolGroup(
            self, "DmsBdcRepGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="bdc-rep",
            description=(
                "Business Development Center inbound/outbound lead handling. "
                "Enforced server-side by DMS API auth.py; CMS gates UI only."
            )
        )

        cognito.CfnUserPoolGroup(
            self, "DmsViewerGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="dms-viewer",
            description=(
                "Read-only DMS dashboard access (reports, performance). "
                "Enforced server-side by DMS API auth.py; CMS gates UI only."
            )
        )

        # dms-technician is the one inversion of the house convention above:
        # every other DMS group is enforced by the DMS API (auth.py), but
        # technician invoke access is enforced by the CMS commands Lambda
        # (services/commands/commands_lambda.py) via an active-RO predicate.
        # The description names the CMS commands Lambda explicitly so it is
        # self-documenting and does not get mistaken for a DMS-side gate.
        # Reuses custom:dealerIds (the existing pool attribute) — custom:dealershipId
        # does not exist and is NOT added here (F3, spec 2026-09-10-cms-dms-sovd).
        cognito.CfnUserPoolGroup(
            self, "DmsTechnicianGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="dms-technician",
            description=(
                "Physical-access diagnostic invoke for dealer technicians. "
                "Enforced server-side by CMS commands Lambda (commands_lambda.py); "
                "DMS gates UI only."
            )
        )

        # ── AVX standing identity groups ───────────────────────────────────────
        #
        # These two groups are declared in the CMS pool for the AVX standing
        # identity contract (CVX spec 2026-09-24-avx-standing-claims-provisioning).
        # They grant NO CMS backend access — the CMS main_api `_OPERATOR_GROUPS`
        # set is {platform-admin, fleet-operator, fleet-viewer}, and neither of
        # these groups appears there. A token whose only group is vehicle-owner or
        # fleet-driver remains outside the CMS operator path and retains CMS
        # driver-self access when custom:driverId is present.
        #
        # Authorization is enforced by the CVX lambda, not by CMS. These groups
        # serve as a second admin-set factor alongside the standing claim attribute.
        cognito.CfnUserPoolGroup(
            self, "AvxVehicleOwnerGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="vehicle-owner",
            description=(
                "AVX standing identity: vehicle owner (custom:customerId). "
                "Grants NO CMS backend access — not in _OPERATOR_GROUPS. "
                "Enforced by CVX lambda (vsa-staging-api-avx-findings/actions)."
            )
        )

        cognito.CfnUserPoolGroup(
            self, "AvxFleetDriverGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="fleet-driver",
            description=(
                "AVX standing identity: fleet driver (custom:vehicleId). "
                "Grants NO CMS backend access — not in _OPERATOR_GROUPS. "
                "Enforced by CVX lambda (vsa-staging-api-avx-findings/actions)."
            )
        )

        # ── REGIONAL WAF on the user pool ─────────────────────────────────────
        #
        # This is a SECOND web ACL, distinct from the CLOUDFRONT-scoped one in
        # waf_stack.py, and it is the one that actually sees registration traffic.
        # Signup does not traverse CloudFront: there is no SPA signup route, the auth
        # model redirects to Cognito's own managed-login domain, and the distribution
        # has no API origin. So the CloudFront ACL's `/signup*` rate rule can never
        # match, which is how "prod has a WAF attached" went green while the endpoint
        # it named stayed unprotected. See decisions.md 2026-08-10 § "WAF is deployed
        # and attached, but the signup rate-limit rule is on the wrong surface".
        #
        # It lives inside this stack rather than in waf_stack.py because a REGIONAL web
        # ACL and its association must be in the POOL's region (us-west-2 staging /
        # us-east-1 prod), while waf_stack.py is pinned to us-east-1 for CloudFront.
        # Same region as the pool means no cross-region plumbing and no second deploy
        # phase. The association is a one-way Association -> Pool edge, so it cannot
        # recreate the trigger dependency cycle that `00eff297` fixed.
        #
        # Unconditional, matching the CloudFront ACL: this protects sign-in, which every
        # stage has, not just signup, which only Phase B turns on.
        CognitoPoolWaf(
            self,
            "CognitoPoolWaf",
            stage=os.environ.get("DEPLOYMENT_STAGE", "dev"),
            user_pool_arn=self.user_pool.user_pool_arn,
        )

        # ── Operator alert channel for the provisioning triggers ──────────────
        #
        # Created UNCONDITIONALLY, on every stage, even when neither trigger gate is on.
        # Two reasons: an operator can subscribe once and keep the subscription across
        # gate flips (subscription is a manual step — see docs/DEPLOYMENT.md), and a
        # topic that only appears when Phase B is enabled is a topic nobody is subscribed
        # to on the day Phase B is enabled.
        #
        # KMS-encrypted with the AWS-managed SNS key, matching FlinkAlarmsTopic /
        # SimulationAlarmsTopic in this repo and satisfying cdk-nag AwsSolutions-SNS2.
        #
        # This does NOT breach the pull-only boundary in
        # ~/.kiro/steering/agentic-tiers.md: that boundary governs an agent reaching a
        # CUSTOMER, not infrastructure reaching an OPERATOR. The CVX Tier 2 close-out's
        # follow-on #14 is the cautionary case in the other direction — two alarms
        # shipped with empty AlarmActions, so an unattended failure notified nobody.
        security_operators_topic = sns.Topic(
            self,
            "SecurityOperatorsTopic",
            topic_name=f"{construct_id}-security-operators",
            display_name=f"CMS {construct_id} account-provisioning alerts",
            master_key=kms.Alias.from_alias_name(
                self, "SecurityOperatorsSnsKey", "alias/aws/sns"
            ),
        )
        self.security_operators_topic = security_operators_topic

        def _alarm_on_trigger_errors(
            *, construct_name: str, alarm_suffix: str, fn: lambda_.Function, why: str
        ) -> cloudwatch.Alarm:
            """Errors > 0 in a 5-minute window on a Cognito trigger Lambda.

            Any error on either trigger means sign-in or registration is broken for the
            affected users, so the threshold is zero and one datapoint is enough —
            there is no "acceptable rate" of failed authentication.

            `treat_missing_data=NOT_BREACHING`: no invocations is the normal state on a
            quiet demo stage, and Errors=0 is a valid non-alarm state.
            """
            alarm = cloudwatch.Alarm(
                self,
                construct_name,
                alarm_name=f"{construct_id}-{alarm_suffix}",
                alarm_description=why,
                metric=fn.metric_errors(
                    period=Duration.minutes(5), statistic="Sum"
                ),
                threshold=0,
                comparison_operator=(
                    cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD
                ),
                evaluation_periods=1,
                datapoints_to_alarm=1,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            alarm.add_alarm_action(cw_actions.SnsAction(security_operators_topic))
            alarm.add_ok_action(cw_actions.SnsAction(security_operators_topic))
            return alarm

        # ── Provisioning Lambda (opt-in: cms.enable_internal_auto_provisioning) ──
        #
        # Creates ONE Lambda bound to TWO trigger slots — see decisions.md
        # 2026-08-10 "Phase A wires BOTH post-confirmation and post-authentication"
        # for the full rationale (post-authentication is the only trigger that fires
        # for an already-existing federated user's sign-in; post-confirmation is the
        # only trigger that fires on a brand-new user's *first* federated sign-in).
        #
        # A Cognito pool accepts exactly one function per trigger operation:
        #   - add_trigger(POST_CONFIRMATION, fn)   → provisioning path
        #   - add_trigger(POST_AUTHENTICATION, fn) → reassertion path
        # Phase B MUST NOT bind a second function to POST_CONFIRMATION — it would
        # silently overwrite this one. Phase B activates the external-signup branch
        # by setting EXTERNAL_SELF_SIGNUP_GROUP in this function's environment.
        #
        # IAM scope: pool ARN only — never "*".  TWO actions required:
        #   AdminAddUserToGroup         — assign the configured group (idempotent: Cognito
        #                                 no-ops for a user already in the group, so no
        #                                 separate membership check is needed)
        #   AdminUpdateUserAttributes   — write custom:fleetIds on the EXTERNAL signup path
        #                                 only.  It does NOT write custom:provisionedVia:
        #                                 that attribute is immutable, the write could never
        #                                 succeed, and failing closed on it took prod
        #                                 sign-in down on 2026-08-11.  See
        #                                 issues/2026-08-11-phase-a-trigger-immutable-
        #                                 attribute-signin-outage/.
        #
        # AdminListGroupsForUser was granted here until 2026-08-11 and removed as unused —
        # the handler never called it, because AdminAddUserToGroup is already idempotent.
        #
        # Gated on context so customers who do not need Federate auto-provisioning
        # get a zero-footprint default (Must-Have 8 in spec.md).
        enable_provisioning = self.node.try_get_context(
            "cms.enable_internal_auto_provisioning"
        ) in (True, "true", "1")

        # Phase B gate. Guest scoping is validated at synth so a misconfigured
        # external-signup deploy fails closed rather than either granting unscoped
        # read (fleet-viewer) or producing usable-nothing accounts (no fleet scope).
        enable_external_signup = self.node.try_get_context(
            "cms.enable_external_self_signup"
        ) in (True, "true", "1")
        _require_external_signup_config(
            stage_name=os.environ.get("DEPLOYMENT_STAGE", ""),
            enable_external_signup=enable_external_signup,
            external_group=os.environ.get("EXTERNAL_SELF_SIGNUP_GROUP", ""),
            external_fleet_ids=os.environ.get("EXTERNAL_SELF_SIGNUP_FLEET_IDS", ""),
        )

        # The four-blocker Phase B gate. Refuses to synthesise a prod stack that opens
        # public registration while any precondition is unverified: no WAF, fail-open
        # authz unpatched in the source tree, the signup flag not deliberately set, or
        # the brand/PII audit still open.
        #
        # This call was MISSING until 2026-08-11. The guard was authored in Group 2 with
        # 33 passing tests and imported by nobody, so `cdk synth cms-prod-ui
        # -c cms.enable_external_self_signup=true` succeeded — the exact condition
        # Group 8's assertion (4) exists to detect, and the second time in this spec that
        # a tested guard turned out to have no call site (the first was the Makefile
        # context translation, security-review Cycle 1 Suggestion 3). A guard with no
        # caller is indistinguishable from no guard, and it is worse, because its test
        # suite reports green.
        #
        # Called only when external signup is REQUESTED. The guard fails closed on an
        # unrecognised stage regardless of flags, which is right when someone is asking
        # for Phase B but would break synth for a customer using a stage name of their
        # own ("qa") while enabling nothing. The repo-state helpers are likewise only
        # evaluated on that path, so a routine synth does no filesystem greps.
        #
        # NOTE: decisions.md 2026-08-10 records a FIFTH precondition — the app client's
        # `write_attributes` must not expose authorization-bearing custom attributes.
        # That is satisfied in code as of 2026-08-10 (commit 2dab2dca) and asserted by
        # stacks/test_client_write_attributes.py, but it is not a parameter of this
        # guard's signature. Adding it here is a follow-on.
        # Runs UNCONDITIONALLY, unlike the guard below. The misconfiguration it catches —
        # pool signup enabled with no provisioning wiring behind it — is by definition a
        # state where enable_external_signup is False, so a guard gated on that flag could
        # never see it. That asymmetry was security-review Cycle 1 Suggestion 4.
        _require_signup_flag_coupled(
            stage_name=os.environ.get("DEPLOYMENT_STAGE", ""),
            signup_flag=self.node.try_get_context("cms.allow_self_signup")
            in (True, "true", "1"),
            enable_external_signup=enable_external_signup,
        )

        if enable_external_signup:
            _require_provisioning_guards(
                stage_name=os.environ.get("DEPLOYMENT_STAGE", ""),
                enable_internal_auto=enable_provisioning,
                enable_external_signup=True,
                # Same context key the CloudFront distribution reads further down.
                waf_acl_arn=(self.node.try_get_context("wafWebAclArn") or "").strip(),
                fail_open_authz_patched=_grep_fail_open_patched(),
                signup_flag=self.node.try_get_context("cms.allow_self_signup")
                in (True, "true", "1"),
                brand_audit_closed=_check_brand_audit_summary(),
            )

        if enable_provisioning or enable_external_signup:
            # The provisioning Lambda serves BOTH gates, and either one alone needs it:
            #   - Phase A (enable_provisioning) needs POST_CONFIRMATION for a federated
            #     user's first sign-in AND POST_AUTHENTICATION to reassert on later ones.
            #   - Phase B (enable_external_signup) needs POST_CONFIRMATION to assign the
            #     scoped guest group after a self-registration is confirmed.
            # A pool accepts exactly one function per trigger operation, so Phase B must
            # activate the same function's external branch rather than bind its own —
            # binding a second POST_CONFIRMATION would silently overwrite Phase A's.
            # Phase-B-only is a legitimate customer configuration: the function is then
            # created and bound to POST_CONFIRMATION alone, since reassertion is Phase A's
            # concern. See decisions.md 2026-08-10.
            #
            # Fail closed at synth if the INTERNAL gate is on but its identity config is
            # incomplete. Without this, the trigger deploys and silently no-ops on
            # every federated sign-in, leaving each new user groupless — a lockout
            # or an unintended platform-admin depending on whether the fail-open
            # authz fix is deployed. See security-review.md Cycle 1 Suggestion 2.
            # (The EXTERNAL gate has its own guard, _require_external_signup_config,
            # called unconditionally above.)
            if enable_provisioning:
                _require_internal_provisioning_config(
                    stage_name=os.environ.get("DEPLOYMENT_STAGE", ""),
                    enable_internal_auto=True,
                    idp_provider_name=os.environ.get("INTERNAL_IDP_PROVIDER_NAME", ""),
                    auto_assign_group=os.environ.get("INTERNAL_AUTO_ASSIGN_GROUP", ""),
                    # Explicit acknowledgement required to auto-assign an UNSCOPED read
                    # group to everyone the IdP authenticates. See security-review.md
                    # Cycle 1 Suggestion 5.
                    allow_unscoped_read_group=os.environ.get(
                        "INTERNAL_ALLOW_UNSCOPED_READ_GROUP", ""
                    ).strip().lower() in ("true", "1"),
                )

            # Explicit log group with bounded retention — matches the
            # demo_password_setter pattern above:
            # - RetentionDays.ONE_WEEK limits blast radius if a future edit
            #   accidentally logs sensitive data.
            # - Deterministic log-group name ties CloudWatch searches to stage.
            provisioning_logs = logs.LogGroup(
                self,
                "ProvisioningLambdaLogs",
                log_group_name=f"/aws/lambda/{construct_id}-provisioning",
                retention=logs.RetentionDays.ONE_WEEK,
            )

            # IAM execution role — principle of least privilege per spec § IAM.
            # Resource is the pool ARN, never "*".  Group 5's security reviewer
            # will fail any "*" resource on these Cognito admin actions.
            #
            # The Cognito policy is attached as a SEPARATE iam.Policy below rather than
            # passed as `inline_policies=` here. That is a CloudFormation dependency
            # requirement, not style:
            #
            #   inline_policies puts the pool ARN INSIDE the Role resource, giving
            #   Role -> Pool. The function depends on its Role, and add_trigger() makes
            #   the Pool depend on the function's ARN. So
            #       Pool -> Function -> Role -> Pool
            #   is a cycle, and CloudFormation refuses the changeset. Observed twice on
            #   the 2026-08-10 staging deploy, reported as a ~80-resource cycle.
            #
            #   A standalone Policy is its own resource that nothing else depends on:
            #       Pool -> Function -> Role,  Policy -> {Role, Pool}
            #   which is acyclic while keeping the grant scoped to the single pool ARN.
            #
            # Do not fold this back into inline_policies.
            provisioning_role = iam.Role(
                self,
                "ProvisioningLambdaRole",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                description=(
                    "Execution role for the CMS provisioning trigger Lambda. "
                    "Grants pool-scoped Cognito admin actions for group assignment."
                ),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "service-role/AWSLambdaBasicExecutionRole"
                    ),
                ],
            )
            provisioning_role.attach_inline_policy(
                iam.Policy(
                    self,
                    "ProvisioningLambdaCognitoPolicy",
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "cognito-idp:AdminAddUserToGroup",
                                "cognito-idp:AdminUpdateUserAttributes",
                            ],
                            # Scoped to the pool ARN — not "*".
                            resources=[self.user_pool.user_pool_arn],
                        )
                    ],
                )
            )

            # Lambda function — one function, two trigger slots.
            # Runtime: PYTHON_3_13 (matches handler.py's spec constraint:
            # "Python 3.13 runtime, no C-extension deps").
            # Code: from_asset pointing at the provisioning package created in
            # Group 3 of this spec.
            # Env vars: all per spec.md § "Runtime environment variables per
            # trigger"; no Amazon-specific values in source — those live in
            # publish-excluded deployment/config/{staging,prod}.env.
            provisioning_lambda = lambda_.Function(
                self,
                "ProvisioningLambda",
                runtime=lambda_.Runtime.PYTHON_3_13,
                handler="handler.handler",
                code=lambda_.Code.from_asset(
                    "lambdas/cognito_triggers/provisioning"
                ),
                role=provisioning_role,
                log_group=provisioning_logs,
                timeout=Duration.seconds(10),
                memory_size=256,
                environment={
                    # USER_POOL_ID is deliberately NOT passed here.
                    #
                    # Doing so creates a CloudFormation CIRCULAR DEPENDENCY that fails
                    # changeset creation: `Ref CMSUserPool` in this function's env is a
                    # Function -> Pool edge, while add_trigger() below puts the
                    # function's ARN in the pool's LambdaConfig, a Pool -> Function
                    # edge. Observed 2026-08-10 on the staging deploy — CFN reported a
                    # cycle spanning ~80 resources, which is what a Cognito
                    # trigger-plus-pool-id-in-env pair looks like from the far side.
                    #
                    # It is also unnecessary. `userPoolId` is a COMMON parameter on
                    # every Cognito trigger event (verified in the AWS docs during
                    # Group 1 research), and handler.py:252 already reads
                    # `event.get("userPoolId", ...)` first. The env var was added as
                    # "belt-and-braces" and the braces broke the deploy.
                    #
                    # Do not re-add it. The IAM policy may still reference the pool ARN
                    # because role policies are separate resources — the cycle comes
                    # specifically from the FUNCTION referencing the pool.
                    #
                    # INTERNAL_IDP_PROVIDER_NAME and INTERNAL_AUTO_ASSIGN_GROUP
                    # live in publish-excluded config/{stage}.env; the Make target
                    # exports them before cdk deploy.  Use os.environ.get with ""
                    # default so offline synth does not raise.
                    "INTERNAL_IDP_PROVIDER_NAME": os.environ.get(
                        "INTERNAL_IDP_PROVIDER_NAME", ""
                    ),
                    "INTERNAL_AUTO_ASSIGN_GROUP": os.environ.get(
                        "INTERNAL_AUTO_ASSIGN_GROUP", ""
                    ),
                    # INTERNAL_ALLOWED_EMAIL_DOMAINS is optional (belt-and-braces
                    # for shared IdPs).  Amazon does not set it; defaults to "".
                    "INTERNAL_ALLOWED_EMAIL_DOMAINS": os.environ.get(
                        "INTERNAL_ALLOWED_EMAIL_DOMAINS", ""
                    ),
                    # EXTERNAL_SELF_SIGNUP_GROUP is absent during Phase-A-only
                    # operation, making case (ii) unreachable until Phase B
                    # activates it.  Set by Phase B's wiring task.
                    "EXTERNAL_SELF_SIGNUP_GROUP": os.environ.get(
                        "EXTERNAL_SELF_SIGNUP_GROUP", ""
                    ),
                    # Fleet(s) a self-registered external user is scoped to. Required
                    # for fleet-guest to see anything: the group is scoped, so an
                    # empty value yields a usable-nothing account. See decisions.md
                    # 2026-08-10 "Phase B assigns fleet-guest, not fleet-viewer".
                    "EXTERNAL_SELF_SIGNUP_FLEET_IDS": os.environ.get(
                        "EXTERNAL_SELF_SIGNUP_FLEET_IDS", ""
                    ),
                },
            )

            # Bind the single function to BOTH trigger slots.
            # POST_CONFIRMATION — provisioning path: fires on a federated user's
            #   first sign-in (PreSignUp → PostConfirmation → PreTokenGeneration).
            # POST_AUTHENTICATION — reassertion path: fires on every subsequent
            #   sign-in (PreAuthentication → PostAuthentication → PreTokenGeneration).
            # Both are needed; neither alone covers all cases.
            # add_trigger() also grants cognito.amazonaws.com the lambda:InvokeFunction
            # permission automatically (CDK adds the resource policy entry).
            self.user_pool.add_trigger(
                cognito.UserPoolOperation.POST_CONFIRMATION,
                provisioning_lambda,
            )
            # POST_AUTHENTICATION is Phase A's reassertion path only. A Phase-B-only
            # deploy does not bind it: there is no federated identity to reassert, and
            # binding it would run the handler on every password sign-in for no reason.
            if enable_provisioning:
                self.user_pool.add_trigger(
                    cognito.UserPoolOperation.POST_AUTHENTICATION,
                    provisioning_lambda,
                )

            # One Errors alarm covers both trigger slots, because the function is one
            # function. The Errors metric does not distinguish trigger source, and for v1
            # that is sufficient: either path failing means sign-in is broken, so the
            # response is the same. Per-path attribution would need a log-based metric on
            # the handler's structured `event_source` field — a follow-on, not a v1 gap.
            _alarm_on_trigger_errors(
                construct_name="ProvisioningErrorsAlarm",
                alarm_suffix="provisioning-errors",
                fn=provisioning_lambda,
                why=(
                    "Cognito provisioning trigger failed. Federated users are not "
                    "receiving their group, so new sign-ins land groupless — which is a "
                    "lockout on every main_api route. Check the Lambda logs."
                ),
            )

        # ── Pre-sign-up denylist Lambda (Phase B: cms.enable_external_self_signup) ──
        #
        # Refuses self-registration from disposable-mailbox providers. This is the
        # control that would have stopped the three inbox.testmail.app accounts found
        # holding privilege in the prod pool on 2026-08-07, two of them with explicit
        # platform-admin.
        #
        # IAM is CloudWatch Logs ONLY. The handler makes no Cognito calls — it either
        # returns the event or raises — so granting it Cognito admin actions would be
        # privilege for its own sake on a function reachable by every registration.
        #
        # The pool has exactly ONE pre-sign-up slot, shared by self-registration,
        # first federated sign-in (PreSignUp_ExternalProvider) and operator account
        # creation (PreSignUp_AdminCreateUser). The handler checks triggerSource before
        # parsing anything, so the latter two pass through untouched; that behaviour is
        # test-enforced in the handler's own suite because a denylist applied to all
        # three is a self-inflicted lockout no denial-path test would notice.
        if enable_external_signup:
            pre_sign_up_logs = logs.LogGroup(
                self,
                "PreSignUpLambdaLogs",
                log_group_name=f"/aws/lambda/{construct_id}-pre-signup",
                retention=logs.RetentionDays.ONE_WEEK,
            )
            pre_sign_up_lambda = lambda_.Function(
                self,
                "PreSignUpLambda",
                runtime=lambda_.Runtime.PYTHON_3_13,
                handler="handler.handler",
                code=lambda_.Code.from_asset("lambdas/cognito_triggers/pre_sign_up"),
                log_group=pre_sign_up_logs,
                # stdlib-only, no network calls; 5s is generous for a set lookup.
                timeout=Duration.seconds(5),
                memory_size=128,
                # No environment: the denylist is bundled with the code so the trigger
                # never depends on another service being reachable at signup time.
            )
            self.user_pool.add_trigger(
                cognito.UserPoolOperation.PRE_SIGN_UP,
                pre_sign_up_lambda,
            )

            # Errors here are MORE urgent than they look: this one slot serves
            # self-registration AND first federated sign-in AND admin account creation,
            # so a failure blocks all three at once.
            _alarm_on_trigger_errors(
                construct_name="PreSignUpErrorsAlarm",
                alarm_suffix="pre-signup-errors",
                fn=pre_sign_up_lambda,
                why=(
                    "Cognito pre-sign-up trigger failed. This slot also serves first "
                    "federated sign-in and admin account creation, so registration, "
                    "employee federation and operator provisioning are all blocked."
                ),
            )

            # Denial volume — informational, not a failure signal. A handful of denials
            # is the control working; a spike is someone enumerating the denylist or
            # scripting registrations.
            #
            # The filter matches a bare token, NOT a JSON path. Metric filters read the
            # raw log event, and the Python runtime's default text format prefixes logger
            # output with `[LEVEL]\t<time>\t<request-id>\t`, so `{ $.outcome = "denied" }`
            # would depend on an assumption about log formatting that has not been
            # observed. `handler._DENIAL_METRIC_TOKEN` is pinned by a test on both its
            # value and its shape, because if it drifts this alarm goes quiet while
            # continuing to report OK.
            denial_metric_namespace = f"CMS/{construct_id}/Provisioning"
            logs.MetricFilter(
                self,
                "PreSignUpDenialsMetricFilter",
                log_group=pre_sign_up_logs,
                filter_name=f"{construct_id}-pre-signup-denials",
                filter_pattern=logs.FilterPattern.all_terms("presignup_denied"),
                metric_namespace=denial_metric_namespace,
                metric_name="PreSignUpDenials",
                metric_value="1",
                # Emit 0 when nothing matches, so the alarm has data to evaluate rather
                # than sitting in INSUFFICIENT_DATA on a quiet stage.
                default_value=0,
            )
            denials_alarm = cloudwatch.Alarm(
                self,
                "PreSignUpDenialsAlarm",
                alarm_name=f"{construct_id}-pre-signup-denials",
                alarm_description=(
                    "More than 100 registrations denied by the disposable-mailbox "
                    "denylist in 5 minutes. Informational: expected to be a trickle, so "
                    "a spike suggests scripted signups or denylist enumeration."
                ),
                metric=cloudwatch.Metric(
                    namespace=denial_metric_namespace,
                    metric_name="PreSignUpDenials",
                    period=Duration.minutes(5),
                    statistic="Sum",
                ),
                threshold=100,
                comparison_operator=(
                    cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD
                ),
                evaluation_periods=1,
                datapoints_to_alarm=1,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            denials_alarm.add_alarm_action(
                cw_actions.SnsAction(security_operators_topic)
            )

        # User Pool Client
        # Build the identity provider list dynamically so Federate is only
        # wired in when the env vars to create the IdP above are set.
        # ── Identity providers the app client may use ─────────────────────────
        #
        # TWO INDEPENDENT CONCERNS, deliberately decoupled after the 2026-08-11 prod
        # outage (issues/2026-08-11-prod-federate-client-config-reset/):
        #
        #   CREATING an IdP on the pool  needs FEDERATE_CLIENT_ID + _SECRET.
        #   PERMITTING an existing IdP for the client needs only its NAME.
        #
        # Conflating them is what broke prod. The AmazonFederate provider had been
        # created out of band on 2026-04-27 and was never in the template, while the
        # client's IdP list was computed from the two credential env vars — which live
        # in no config file and no secret, so they are absent on any normal deploy.
        # The template therefore always described a client WITHOUT Federate, and the
        # only reason prod worked is that no client-touching deploy had happened since
        # the manual setup. The `write_attributes` change forced one, CloudFormation
        # enforced template state, and all 9 platform-admins lost Federate sign-in.
        #
        # CLIENT_EXTRA_IDPS (comma-separated provider names, publish-excluded stage
        # config) permits providers that already exist on the pool, whether this stack
        # created them or not. It must NOT be gated on the credentials.
        idp_list = [cognito.UserPoolClientIdentityProvider.COGNITO]
        _federate_creds_present = bool(
            os.environ.get("FEDERATE_CLIENT_ID", "").strip()
            and os.environ.get("FEDERATE_CLIENT_SECRET", "").strip()
        )
        if _federate_creds_present:
            idp_list.append(cognito.UserPoolClientIdentityProvider.custom("AmazonFederate"))
        # `cognito-only` is a sentinel meaning "this stage deliberately has no federated
        # provider", not a provider name. It must be skipped HERE as well as in the guard:
        # an earlier revision skipped it only in the guard, and staging synthesised
        # `SupportedIdentityProviders: ['COGNITO', 'cognito-only']` — a client permitting a
        # provider that does not exist, which is the same broken-sign-in outcome this whole
        # change exists to prevent, reintroduced by the fix for it. Caught by asserting on
        # the synthesised template rather than trusting the edit.
        _extra_raw = [p.strip() for p in os.environ.get("CLIENT_EXTRA_IDPS", "").split(",")]
        _cognito_only = [p.lower() for p in _extra_raw if p] == ["cognito-only"]
        for _extra in ([] if _cognito_only else _extra_raw):
            if not _extra or _extra.upper() == "COGNITO":
                continue
            if _extra == "AmazonFederate" and _federate_creds_present:
                continue  # already added above; a duplicate entry fails at deploy
            idp_list.append(cognito.UserPoolClientIdentityProvider.custom(_extra))

        # ── OAuth callback URLs ───────────────────────────────────────────────
        #
        # Leaving `o_auth` unset does NOT mean "no OAuth config": aws-cdk-lib enables
        # the authorization-code and implicit flows and supplies a PLACEHOLDER callback
        # of `https://example.com`. That placeholder was the second half of the same
        # outage — the SPA sends `${window.location.origin}/auth/callback`
        # (SimpleAuthProvider.tsx:386), which Cognito rejected because it was not in
        # the allowlist.
        #
        # Derived from the custom-domain context rather than a new config key, so it
        # cannot drift from the domain the SPA is actually served on. When no custom
        # domain is configured (dev), the CDK default stands and the guard below
        # exempts dev stages.
        _ui_domain = (self.node.try_get_context("uiCustomDomain") or "").strip()

        # ── External-UI callback-URL registry ────────────────────────────────────
        #
        # Each entry in EXTERNAL_UI_CALLBACK_CONTEXT_KEYS names a CDK context key
        # whose value is the https:// origin of a frontend that shares this pool.
        # When set, its /auth/callback and / URLs are appended to the UserPoolClient.
        # When unset (the default), the client properties are byte-identical to before
        # this registry existed — which is what keeps
        # `test_client_idp_config.py::test_staging_still_gets_a_real_callback` passing.
        #
        # See EXTERNAL_UI_CALLBACK_CONTEXT_KEYS (module-level) for the canonical list
        # and the instructions for onboarding a new frontend. Do NOT add a new
        # if-block here — add one line to the registry instead.
        _external_origins: list[str] = []
        for _ctx_key in EXTERNAL_UI_CALLBACK_CONTEXT_KEYS:
            _origin = (self.node.try_get_context(_ctx_key) or "").strip().rstrip("/")
            if not _origin:
                continue
            if not _origin.startswith("https://"):
                # Fail closed — an http:// or bare-host value would register a
                # non-TLS redirect target on a client that carries real tokens.
                raise ValueError(
                    f"{_ctx_key} must be an https:// origin "
                    f"(got {_origin!r})"
                )
            _external_origins.append(_origin)

        _oauth_kwargs = {}
        if _ui_domain:
            _callback_urls = [f"https://{_ui_domain}/auth/callback"]
            _logout_urls = [f"https://{_ui_domain}/"]
            for _ext_origin in _external_origins:
                _callback_urls.append(f"{_ext_origin}/auth/callback")
                _logout_urls.append(f"{_ext_origin}/")
            _oauth_kwargs["o_auth"] = cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True, implicit_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                    cognito.OAuthScope.PHONE,
                    cognito.OAuthScope.COGNITO_ADMIN,
                ],
                callback_urls=_callback_urls,
                logout_urls=_logout_urls,
            )

        _require_client_idp_config(
            stage_name=os.environ.get("DEPLOYMENT_STAGE", ""),
            ui_custom_domain=_ui_domain,
            extra_idps=os.environ.get("CLIENT_EXTRA_IDPS", ""),
            federate_creds_present=_federate_creds_present,
        )

        self.user_pool_client = cognito.UserPoolClient(
            self, "CMSUserPoolClient",
            user_pool=self.user_pool,
            generate_secret=False,
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True
            ),
            supported_identity_providers=idp_list,
            **_oauth_kwargs,
            # ── Client write allowlist — blocks custom-attribute injection ────
            #
            # WITHOUT this, a SELF-REGISTERED user can set any custom attribute in
            # their own SignUp call and it PERSISTS. Verified empirically against
            # live staging Cognito on 2026-08-10: a SignUp passing
            # `custom:driverId=INJECTED-DRIVER-1` was accepted and the value was
            # readable on the resulting user.
            #
            # That matters because `custom:driverId` is an AUTHORIZATION INPUT —
            # main_api's `_classify_driver_self` grants driver-scoped access on the
            # strength of it. So a stranger registering through Phase B could obtain
            # driver-self access with NO group at all, routing around `fleet-guest`
            # and every group-based control in this spec.
            #
            # Note the AWS docs' `WriteAttributes` text describes ACCESS-TOKEN writes
            # ("after your user authenticates ... their access token authorizes them
            # to set or modify their own attribute value"), and says the unset default
            # is "the Standard attributes". Both are true and neither covers SignUp,
            # which happens BEFORE authentication — which is why this had to be tested
            # against the service rather than reasoned from the documentation.
            #
            # `email` and `fullname` (the `name` attribute) are REQUIRED here, not
            # optional: the AmazonFederate IdP maps `email` -> EMAIL and
            # `name` -> GIVEN_NAME, and the docs are explicit that a client lacking
            # write access to a mapped attribute makes Cognito THROW when it updates
            # that attribute on IdP sign-in. Omitting them would break Federate
            # sign-in for all 9 prod platform-admins.
            #
            # No custom attributes are listed, deliberately. Nothing in the web or iOS
            # client writes attributes with a user token (grepped 2026-08-10), and every
            # legitimate custom-attribute write in this system is server-side via
            # `AdminUpdateUserAttributes` — the provisioning trigger, the seed scripts,
            # and the de-privilege script — none of which is governed by this allowlist.
            #
            # Do not add a custom attribute here without re-checking whether it feeds
            # an authorization decision.
            write_attributes=cognito.ClientAttributes().with_standard_attributes(
                email=True,
                fullname=True,
            ),
        )

        CfnOutput(self, 'UserPoolArnExport',
                  value=self.user_pool.user_pool_arn,
                  export_name=f'{construct_id}-user-pool-arn',
                  description='User Pool ARN for cross-stack import by simulation/commands/predictive-agent stacks')

        # Hosted UI domain (Cognito-provided subdomain under amazoncognito.com).
        # OPT-IN — only created when COGNITO_DOMAIN_PREFIX is explicitly set
        # (or when Federate IdP env vars are set, since Federate needs the
        # Hosted UI domain to redirect to).
        #
        # The name is globally unique per region. Set COGNITO_DOMAIN_PREFIX to
        # a value that's unlikely to collide (e.g. <org>-<stage>-<region>-cms).
        # If another account in the same region already registered this prefix,
        # deploy will fail at early validation. Change the prefix and retry.
        #
        # NOTE: The Federate OIDC IdP (if used) only redirects back to URIs
        # that are on its allowlist. If you change the prefix after Federate
        # is wired up, request the new URI be added to the Federate allowlist
        # before users try to SSO.
        #
        # Why this isn't created by default: this CDK ships to customers who
        # deploy under their own accounts. Baking a specific prefix in means
        # the first customer to deploy "wins" the name and all subsequent
        # deploys collide. Customers opt in by setting the env var.
        cognito_domain_prefix = os.environ.get("COGNITO_DOMAIN_PREFIX", "").strip()

        # Amazon Federate OIDC IdP (opt-in via env vars).
        # When the Federate team provisions a Cognito-integrated client in
        # their self-service portal, they give you:
        #   - client_id (e.g. 'cms-demo-cognito')
        #   - client_secret
        #   - issuer (e.g. 'https://idp-integ.federate.amazon.com' for integ,
        #              or https://idp.federate.amazon.com for prod)
        # Set FEDERATE_CLIENT_ID, FEDERATE_CLIENT_SECRET, FEDERATE_ISSUER to
        # enable the 'Sign in with Amazon Federate' button. The Federate team
        # must also allowlist {cognito_domain}/oauth2/idpresponse.
        federate_client_id = os.environ.get("FEDERATE_CLIENT_ID", "").strip()
        federate_client_secret = os.environ.get("FEDERATE_CLIENT_SECRET", "").strip()
        federate_issuer = os.environ.get(
            "FEDERATE_ISSUER", "https://idp-integ.federate.amazon.com"
        ).strip()
        federate_enabled = bool(federate_client_id and federate_client_secret)

        # Federate requires the Hosted UI domain for its OAuth2 redirect, so
        # if the operator enabled Federate without setting a domain prefix,
        # that's a config error — fail loudly rather than silently.
        if federate_enabled and not cognito_domain_prefix:
            raise ValueError(
                "FEDERATE_CLIENT_ID/FEDERATE_CLIENT_SECRET are set but "
                "COGNITO_DOMAIN_PREFIX is not. Federate requires a Cognito "
                "Hosted UI domain for its OAuth2 redirect. Set "
                "COGNITO_DOMAIN_PREFIX to a unique prefix (e.g. "
                "'<org>-<stage>-cms') and redeploy."
            )

        if cognito_domain_prefix:
            self.user_pool_domain = cognito.UserPoolDomain(
                self, "CMSUserPoolDomain",
                user_pool=self.user_pool,
                cognito_domain=cognito.CognitoDomainOptions(
                    domain_prefix=cognito_domain_prefix
                ),
            )
            CfnOutput(
                self, "CognitoHostedUIDomain",
                value=f"{cognito_domain_prefix}.auth.{self.region}.amazoncognito.com",
                description="Cognito Hosted UI domain - used for Federate / OAuth2 flows",
                export_name=f"{construct_id}-cognito-domain"
            )

        if federate_enabled:
            self.federate_idp = cognito.CfnUserPoolIdentityProvider(
                self, "AmazonFederateIdP",
                user_pool_id=self.user_pool.user_pool_id,
                provider_name="AmazonFederate",
                provider_type="OIDC",
                provider_details={
                    "client_id": federate_client_id,
                    "client_secret": federate_client_secret,
                    "oidc_issuer": federate_issuer,
                    "authorize_scopes": "openid email profile",
                    "attributes_request_method": "GET",
                    "attributes_url_add_attributes": "false",
                },
                attribute_mapping={
                    "email": "EMAIL",
                    "name": "GIVEN_NAME",
                    "username": "sub",
                },
            )
            # User pool client must depend on the IdP so CFN creates it first
            self.user_pool_client.node.add_dependency(self.federate_idp)

        # Identity Pool
        self.identity_pool = cognito.CfnIdentityPool(
            self, "CMSIdentityPool",
            identity_pool_name=f"{construct_id}-identity",
            allow_unauthenticated_identities=self.node.try_get_context('cms.allow_unauth_map_auth') in (True, 'true', '1'),
            cognito_identity_providers=[
                cognito.CfnIdentityPool.CognitoIdentityProviderProperty(
                    client_id=self.user_pool_client.user_pool_client_id,
                    provider_name=self.user_pool.user_pool_provider_name
                )
            ]
        )
        
        # IAM role for unauthenticated users to access Location Services
        allow_unauth_map_auth = self.node.try_get_context('cms.allow_unauth_map_auth') in (True, 'true', '1')
        if allow_unauth_map_auth:
            unauthenticated_role = iam.Role(
                self, "CognitoUnauthenticatedRole",
                assumed_by=iam.FederatedPrincipal(
                    "cognito-identity.amazonaws.com",
                    {
                        "StringEquals": {
                            "cognito-identity.amazonaws.com:aud": self.identity_pool.ref
                        },
                        "ForAnyValue:StringLike": {
                            "cognito-identity.amazonaws.com:amr": "unauthenticated"
                        }
                    },
                    "sts:AssumeRoleWithWebIdentity"
                ),
                inline_policies={
                    "LocationServicesPolicy": iam.PolicyDocument(
                        statements=[
                            # New geo-maps actions for maps
                            iam.PolicyStatement(
                                effect=iam.Effect.ALLOW,
                                actions=[
                                    "geo-maps:GetTile",
                                    "geo-maps:GetStaticMap"
                                ],
                                resources=[
                                    f"arn:aws:geo-maps:{self.region}::provider/default",
                                    f"arn:aws:geo-maps:{self.region}::provider/default/*"
                                ]
                            ),
                            # Legacy geo actions for backward compatibility.
                            # Allow both the canonical UI-provisioned map
                            # (`cms-prod-ui-vehicle-map`, VectorEsriStreets)
                            # AND the legacy Here-source demo map
                            # (`cvs_location_map_test2`, VectorHereExplore).
                            # The CDK creates the canonical Esri map, but
                            # the demo's visual branding has historically
                            # used the Here vector style — operators flip
                            # the runtime config's `mapAuth.mapName` between
                            # the two depending on the look they want, so
                            # the IAM policy needs to permit either map for
                            # signed-out (pre-auth) tile loads.
                            #
                            # See `regenerate-runtime-config` Makefile
                            # target: it auto-prefers the Here map when
                            # present so the runtime config and IAM stay
                            # in sync without manual edits.
                            iam.PolicyStatement(
                                effect=iam.Effect.ALLOW,
                                actions=[
                                    "geo:GetMap*",
                                    "geo:DescribeMap"
                                ],
                                resources=[
                                    self.map.attr_arn,
                                    f"arn:aws:geo:{self.region}:{self.account}:map/cvs_location_map_test2",
                                ]
                            )
                        ]
                    )
                }
            )
        
        # IAM role for authenticated users to access Location Services
        authenticated_role = iam.Role(
            self, "CognitoAuthenticatedRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                {
                    "StringEquals": {
                        "cognito-identity.amazonaws.com:aud": self.identity_pool.ref
                    },
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": "authenticated"
                    }
                },
                "sts:AssumeRoleWithWebIdentity"
            ),
            inline_policies={
                "LocationServicesPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "geo:GetMap*",
                                "geo:DescribeMap",
                                "geo:SearchPlaceIndex*",
                                "geo:GetPlace",
                                "geo:CalculateRoute*"
                            ],
                            resources=[
                                f"arn:aws:geo:{self.region}:{self.account}:map/*",
                                f"arn:aws:geo:{self.region}:{self.account}:place-index/*",
                                f"arn:aws:geo:{self.region}:{self.account}:route-calculator/*"
                            ]
                        )
                    ]
                ),
                # Bedrock Agent invocation for the CMS UI's chat/assistant features.
                # Scoped to agent-aliases in this account/region only.
                "BedrockAgentInvokePolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:InvokeAgent",
                            ],
                            resources=[
                                f"arn:aws:bedrock:{self.region}:{self.account}:agent-alias/*",
                            ]
                        )
                    ]
                )
            }
        )
        
        # Attach roles to identity pool
        auth_roles: dict = {"authenticated": authenticated_role.role_arn}
        if allow_unauth_map_auth:
            auth_roles["unauthenticated"] = unauthenticated_role.role_arn
        cognito.CfnIdentityPoolRoleAttachment(
            self, "IdentityPoolRoleAttachment",
            identity_pool_id=self.identity_pool.ref,
            roles=auth_roles,
        )
        
        # Private S3 bucket (secure)
        # Bucket name is suffixed with -{account}-{region} per spec
        # `2026-06-03-cms-ui-frontend-bucket-region-suffix` — S3 bucket
        # names are GLOBAL; the (account, region) tuple prevents
        # cross-region collisions when the same construct is deployed
        # in more than one region (e.g. clean-deploy harness validation
        # in ap-northeast-1 against a live us-west-2 staging stack).
        # Mirrors the storage_stack.py + data_processing_stack.py pattern.
        self.frontend_bucket = s3.Bucket(
            self, "FrontendBucket",
            bucket_name=f"{construct_id}-frontend-{self.account}-{self.region}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            public_read_access=False
        )
        
        # Origin Access Control for secure CloudFront access.
        # OAC names occupy CloudFront's PARTITION-GLOBAL namespace, so the name
        # MUST be a function of {stage, account, region} — not {stage} alone.
        # Hashing self.stack_id was region-BLIND: stack_id is an unresolved CDK
        # token at synth time whose string form is identical across regions, so
        # both us-west-2 and ap-northeast-1 computed the same OAC name and
        # collided (AlreadyExists). Mirror the FrontendBucket {account}-{region}
        # pattern. See issues/2026-06-16-cms-ui-oac-cross-region-name-collision/
        # and ~/.kiro/steering/cross-region-namespace.md (check #1).
        import hashlib
        stack_hash = hashlib.md5(
            f"{construct_id}-{self.account}-{self.region}".encode()
        ).hexdigest()[:8]
        oac = cloudfront.CfnOriginAccessControl(
            self, "FrontendOAC",
            origin_access_control_config=cloudfront.CfnOriginAccessControl.OriginAccessControlConfigProperty(
                name=f"{construct_id}-oac-{self.region}-{stack_hash}",
                origin_access_control_origin_type="s3",
                signing_behavior="always",
                signing_protocol="sigv4"
            )
        )

        # Optional custom domain for the CloudFront distribution.
        #
        # Opt in by setting BOTH:
        #   -c uiCustomDomain=cms.example.com
        #   -c uiCustomDomainCertArn=arn:aws:acm:us-east-1:<account>:certificate/<id>
        #
        # The cert MUST be in us-east-1 (CloudFront requirement) and must already
        # be ISSUED. The hosted zone for the domain must exist; if it lives in
        # the same AWS account we'll also upsert the A-record alias. If it
        # doesn't (e.g., delegated zones outside the account), set
        # -c uiCustomDomainManageDns=false and the stack will just attach
        # the cert+alias to CloudFront, leaving DNS to whatever manages it.
        #
        # CROSS-REGION GUARD (optional, recommended for any operator who
        # may deploy the same stage to a second region):
        #   -c uiCustomDomainRegion=us-west-2     # staging home region
        #   -c uiCustomDomainRegion=us-east-1     # prod    home region
        #
        # CloudFront aliases (CNAMEs) are partition-global — the same alias
        # cannot be attached to two distributions across regions, so a manual
        # cross-region deploy of `cms-staging-ui` that inherits the primary
        # region's `uiCustomDomain` from a stale `deployment/cdk.context.json`
        # collides with HTTP 409 (alias already in use). When
        # `uiCustomDomainRegion` is set AND it does NOT equal the stack's
        # region (`Stack.of(self).region`), this stack SKIPS attaching the
        # domain, certificate, Route53 A-record alias, and CustomDomainURL
        # output. The distribution falls back to the default
        # `*.cloudfront.net` URL. A clear stderr warning is emitted naming
        # both the configured home region and the active stack region so the
        # operator sees the intentional skip.
        #
        # When `uiCustomDomainRegion` is UNSET, behavior is exactly as today
        # (region-agnostic attach when the pair is set). This preserves the
        # us-west-2 staging + us-east-1 prod deploys byte-for-byte and is the
        # supported backward-compatible default.
        #
        # Without the pair-opt-in, the distribution behaves exactly as
        # before: CloudFront default cert, default <dist-id>.cloudfront.net
        # URL only.
        ui_custom_domain = (self.node.try_get_context("uiCustomDomain") or "").strip()
        ui_custom_domain_cert_arn = (
            self.node.try_get_context("uiCustomDomainCertArn") or ""
        ).strip()
        ui_custom_domain_manage_dns = str(
            self.node.try_get_context("uiCustomDomainManageDns") or "true"
        ).lower() not in ("false", "0", "no")
        ui_custom_domain_region = (
            self.node.try_get_context("uiCustomDomainRegion") or ""
        ).strip()

        # Pair-must-be-set validation (preserved — surfaces partial configs
        # before the region guard so the operator gets the more actionable
        # error first).
        if (ui_custom_domain or ui_custom_domain_cert_arn) and not (
            ui_custom_domain and ui_custom_domain_cert_arn
        ):
            raise ValueError(
                "uiCustomDomain and uiCustomDomainCertArn must both be set "
                "(or both unset). Got domain={!r} cert_arn={!r}".format(
                    ui_custom_domain, ui_custom_domain_cert_arn
                )
            )

        # Region guard: when uiCustomDomainRegion is set and does not match
        # this stack's region, intentionally SKIP attaching the partition-
        # global CNAME so we don't 409-collide with the home-region
        # distribution. Backward-compatible: empty region context = today's
        # behavior.
        ui_custom_domain_region_skip = bool(
            ui_custom_domain
            and ui_custom_domain_cert_arn
            and ui_custom_domain_region
            and ui_custom_domain_region != self.region
        )
        if ui_custom_domain_region_skip:
            print(
                f"  [ui_stack] uiCustomDomainRegion={ui_custom_domain_region!r} "
                f"!= Stack.region={self.region!r} for {construct_id}; "
                f"SKIPPING custom domain attachment "
                f"(domain={ui_custom_domain!r}). Distribution will use the "
                f"default *.cloudfront.net URL in this region. This is the "
                f"cross-region-namespace guard — set uiCustomDomainRegion to "
                f"the active region to attach the domain here.",
                file=sys.stderr,
            )

        # Single source of truth for the three downstream consumers
        # (CloudFront distribution_kwargs, Route53 ARecord, CustomDomainURL
        # CfnOutput). When False, the distribution + DNS + output all
        # collectively skip in a consistent way.
        ui_custom_domain_attached = (
            bool(ui_custom_domain)
            and bool(ui_custom_domain_cert_arn)
            and not ui_custom_domain_region_skip
        )
        # Exposed for the synth-time domain-alias guard (app.py ::
        # enforce_ui_domain_alias). Authoritative signal of whether the
        # custom-domain alias is attached in this synth — the guard fails fast
        # if a home-region staging deploy would drop it.
        self.ui_custom_domain_attached = ui_custom_domain_attached

        distribution_kwargs = {}

        # ── WAF attach (Phase B blocker #2) ──────────────────────────────────
        # CloudFront takes the WebACL *ARN* for WAFv2 (the `web_acl_id` prop name is
        # historical — it accepted a WAF Classic id). Supplied via context because the
        # WebACL lives in us-east-1 while this stack may be us-west-2, and CloudFormation
        # has no cross-region exports. Same idiom as uiCustomDomainCertArn above.
        # Absent context = no WAF attached, which is why Phase B's blocker gate checks
        # the LIVE distribution rather than trusting synth.
        _waf_web_acl_arn = (self.node.try_get_context("wafWebAclArn") or "").strip()
        if _waf_web_acl_arn:
            distribution_kwargs["web_acl_id"] = _waf_web_acl_arn
            print(f"  [ui_stack] WAF WebACL attached to {construct_id}")
        if ui_custom_domain_attached:
            cert = acm.Certificate.from_certificate_arn(
                self, "UiCustomDomainCert", ui_custom_domain_cert_arn
            )
            distribution_kwargs["domain_names"] = [ui_custom_domain]
            distribution_kwargs["certificate"] = cert

        # ── Edge auth gate — staging-only ────────────────────────────────────
        # Reference an externally-created CloudFront Key Group by ID. The Key
        # Group itself, the underlying RSA key pair, the KMS encryption key,
        # the Secrets Manager secret, and the external SSO gate's onboard
        # call are all configured out-of-band by the operator per the
        # internal staging-gate runbook. CDK ONLY references the existing
        # Key Group ID via cdk.context.json.
        #
        # Gating: construct_id == "cms-staging-ui" AND a non-empty
        # stagingGateKeyGroupId context. Prod (cms-prod-ui) and dev
        # (cms-dev-ui) never enter this branch — verified at synth time.
        staging_gate_key_group_id = (
            self.node.try_get_context("stagingGateKeyGroupId") or ""
        ).strip()
        is_staging_ui = construct_id == "cms-staging-ui"
        staging_gate_trusted_key_groups = []
        if is_staging_ui and staging_gate_key_group_id:
            staging_gate_key_group = cloudfront.KeyGroup.from_key_group_id(
                self, "StagingGateKeyGroup", staging_gate_key_group_id
            )
            staging_gate_trusted_key_groups = [staging_gate_key_group]
            print(
                f"  [ui_stack] staging edge auth gate ENABLED for "
                f"{construct_id} via Key Group ID {staging_gate_key_group_id}"
            )
        elif is_staging_ui and not staging_gate_key_group_id:
            print(
                "  [ui_stack] stagingGateKeyGroupId context not set; "
                "cms-staging-ui distribution will deploy WITHOUT the staging "
                "edge auth gate. Set -c stagingGateKeyGroupId=<id> in "
                "cdk.context.json once gate onboarding is complete (see the "
                "internal staging-gate runbook)."
            )

        # CloudFront distribution with OAC.
        #
        # The default cache behavior gets `trusted_key_groups` ONLY when the
        # staging edge auth gate is active (staging-only — see context-read
        # block above). The /js/cfs-handler.js path-pattern gets its own
        # additional behavior with NO trusted-key-group binding so the
        # handler script can load while the user is unauthenticated.
        #
        # Custom error responses:
        # - 404 → /index.html → 200  (preserved unchanged for SPA routing)
        # - 403 → /error/403.html → 403 when gate active (gate template)
        # - 403 → /index.html     → 200 when gate inactive (legacy SPA fallback
        #                                                  for missing keys)
        default_behavior_kwargs = dict(
            origin=origins.S3BucketOrigin(
                self.frontend_bucket,
                origin_access_control_id=oac.attr_id
            ),
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
        )
        if staging_gate_trusted_key_groups:
            default_behavior_kwargs["trusted_key_groups"] = staging_gate_trusted_key_groups

        staging_gate_additional_behaviors = None
        if staging_gate_trusted_key_groups:
            # CloudFront Function: rewrite SPA deep-link paths to /index.html so
            # S3+OAC serves a real object. Without this, paths like
            # /vehicles/management/VEH-xxx have no S3 key — S3 returns 403 (not
            # 404) under OAC, which re-triggers the auth gate even with valid
            # signed cookies.
            spa_rewrite_fn = cloudfront.Function(
                self, "SpaRewriteFunction",
                code=cloudfront.FunctionCode.from_inline("""
function handler(event) {
    var request = event.request;
    var uri = request.uri;
    // Rewrite paths with no file extension (SPA routes) to /index.html.
    // Exclude known static asset prefixes.
    if (!uri.includes('.') && uri !== '/') {
        request.uri = '/index.html';
    }
    return request;
}
"""),
                runtime=cloudfront.FunctionRuntime.JS_2_0,
            )

            # /js/cfs-handler.js MUST load BEFORE auth — explicit no-gate
            # path-pattern behavior so the handler script is fetchable
            # before any trusted-key-group check runs.
            staging_gate_additional_behaviors = {
                "/js/cfs-handler.js": cloudfront.BehaviorOptions(
                    origin=origins.S3BucketOrigin(
                        self.frontend_bucket,
                        origin_access_control_id=oac.attr_id
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                    # No trusted_key_groups — explicitly public.
                ),
            }
            default_behavior_kwargs["function_associations"] = [
                cloudfront.FunctionAssociation(
                    function=spa_rewrite_fn,
                    event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                )
            ]

        staging_gate_error_responses = [
            cloudfront.ErrorResponse(
                http_status=404,
                response_http_status=200,
                response_page_path="/index.html"
            ),
        ]
        if staging_gate_trusted_key_groups:
            # Gate active: 403 must serve the gate template AND keep the
            # 403 status code (cfs-handler.js uses the response to drive its
            # redirect logic). ttl=0 prevents CloudFront from caching the 403
            # error response — without this, after signing succeeds and cookies
            # are set, the next request to the same path still gets the cached
            # 403 for up to 5 minutes (CloudFront's default error TTL).
            staging_gate_error_responses.append(cloudfront.ErrorResponse(
                http_status=403,
                response_http_status=403,
                response_page_path="/error/403.html",
                ttl=Duration.seconds(0)
            ))
        else:
            # Gate inactive: preserve the existing SPA fallback for missing
            # S3 keys (matches prod behavior).
            staging_gate_error_responses.append(cloudfront.ErrorResponse(
                http_status=403,
                response_http_status=200,
                response_page_path="/index.html"
            ))

        self.distribution = cloudfront.Distribution(
            self, "FrontendDistribution",
            default_behavior=cloudfront.BehaviorOptions(**default_behavior_kwargs),
            additional_behaviors=staging_gate_additional_behaviors,
            default_root_object="index.html",
            error_responses=staging_gate_error_responses,
            **distribution_kwargs
        )

        # Upsert the A-record alias when the hosted zone is in this account
        # AND the operator opted in to DNS management. The lookup is best-
        # effort — if the zone isn't here, we skip silently and trust the
        # operator to manage DNS externally (this is the common path for
        # delegated zones managed outside this account).
        #
        # Gated on `ui_custom_domain_attached` (NOT just the raw pair) so
        # the cross-region guard above flows through to DNS — we don't
        # write an A-record alias to a distribution that didn't get the
        # custom domain attached.
        if ui_custom_domain_attached and ui_custom_domain_manage_dns:
            # Infer the zone name by stripping the leading subdomain (e.g.
            # "cms.example.com" → zone "example.com").
            # Fall back to the full domain if there's no subdomain.
            zone_name = ".".join(ui_custom_domain.split(".")[1:]) or ui_custom_domain
            try:
                r53 = boto3.client('route53', region_name=self.region)
                zones = r53.list_hosted_zones_by_name(DNSName=zone_name, MaxItems="5")
                matching = [
                    z for z in zones.get('HostedZones', [])
                    if z['Name'].rstrip('.') == zone_name and not z.get('Config', {}).get('PrivateZone')
                ]
                # Also accept a zone named after the full custom domain (e.g.
                # Supernova delegations create a zone for the full subdomain).
                if not matching:
                    zones = r53.list_hosted_zones_by_name(DNSName=ui_custom_domain, MaxItems="5")
                    matching = [
                        z for z in zones.get('HostedZones', [])
                        if z['Name'].rstrip('.') == ui_custom_domain and not z.get('Config', {}).get('PrivateZone')
                    ]
                    if matching:
                        zone_name = ui_custom_domain
                if matching:
                    zone = route53.HostedZone.from_hosted_zone_attributes(
                        self, "UiCustomDomainZone",
                        hosted_zone_id=matching[0]['Id'].split('/')[-1],
                        zone_name=zone_name,
                    )
                    # When zone_name == ui_custom_domain the record_name is the
                    # apex (empty string); otherwise it's the subdomain label.
                    record_name = None if zone_name == ui_custom_domain else ui_custom_domain
                    route53.ARecord(
                        self, "UiCustomDomainAlias",
                        zone=zone,
                        record_name=record_name,
                        target=route53.RecordTarget.from_alias(
                            route53_targets.CloudFrontTarget(self.distribution)
                        ),
                    )
            except Exception as e:
                # Don't fail the synth if the DNS lookup hits permissions or
                # network issues — the cert+alias on CloudFront is still correct.
                print(f"  [ui_stack] Route53 zone lookup for '{zone_name}' "
                      f"failed; skipping A-record: {e}")
        
        # Bucket policy to allow CloudFront OAC access
        bucket_policy = iam.PolicyDocument(
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                    actions=["s3:GetObject"],
                    resources=[f"{self.frontend_bucket.bucket_arn}/*"],
                    conditions={
                        "StringEquals": {
                            "AWS:SourceArn": f"arn:aws:cloudfront::{self.account}:distribution/{self.distribution.distribution_id}"
                        }
                    }
                )
            ]
        )
        
        s3.CfnBucketPolicy(
            self, "FrontendBucketPolicy",
            bucket=self.frontend_bucket.bucket_name,
            policy_document=bucket_policy
        )
        
        # Lambda execution role with minimal permissions
        lambda_role = iam.Role(
            self, "LambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
            ],
            inline_policies={
                "AppAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                                "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"
                            ],
                            resources=[
                                f"arn:aws:dynamodb:{self.region}:{self.account}:table/*",
                                # Scoped to the one GSI main_api actually queries (DTC dedup
                                # active-code-index) — not all indexes on all tables.
                                f"arn:aws:dynamodb:{self.region}:{self.account}:table/{storage_prefix}-dtc-history/index/active-code-index",
                            ]
                        ),
                        # D-G5e (spec 2026-09-02-cms-dms-service-convergence T5.2):
                        # VIN→vehicleId resolution for GET/POST /api/v1/service-history.
                        # vehicleId != vin on some vehicles (divergent on 3/8 sampled;
                        # issues/2026-09-05-vehicleid-diverges-from-vin/). Step 2 of the
                        # two-step resolver queries vin-index on the vehicles table when
                        # GetItem misses. Without this statement step 2 fails AccessDenied
                        # at runtime while every unit test passes green — a GSI query needs
                        # the index ARN, not just the table ARN (which `table/*` grants).
                        # Scoped to a SINGLE literal ARN; NOT `index/*` — no scan fallback
                        # by design (storage_stack.py:498 records that a scan fallback
                        # "made a MISSING index look merely like a slow one for months").
                        # Pinned by deployment/stacks/test_main_api_vin_index_grant.py:
                        # asserts the literal ARN is present AND that no resource ends in
                        # `/index/*` (so a widening edit fails immediately).
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["dynamodb:Query"],
                            resources=[
                                f"arn:aws:dynamodb:{self.region}:{self.account}:table/{storage_prefix}-vehicles/index/vin-index",
                            ]
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "iot:CreateThing", "iot:CreateKeysAndCertificate", "iot:CreatePolicy",
                                "iot:AttachThingPrincipal", "iot:AttachPrincipalPolicy"
                            ],
                            resources=["*"]
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "cognito-idp:ListUsers", "cognito-idp:AdminCreateUser",
                                "cognito-idp:AdminDeleteUser", "cognito-idp:AdminUpdateUserAttributes",
                                "cognito-idp:AdminAddUserToGroup", "cognito-idp:AdminRemoveUserFromGroup",
                                "cognito-idp:AdminListGroupsForUser", "cognito-idp:AdminDisableUser",
                                "cognito-idp:AdminEnableUser", "cognito-idp:AdminResetUserPassword",
                                "cognito-idp:AdminSetUserPassword",
                            ],
                            resources=[self.user_pool.user_pool_arn]
                        ),
                        # Read + lock/unlock the driver-facing (VSA) pool from
                        # the CMS operator UI. Scoped down to Get + enable/disable
                        # + attribute update only — no create/delete here (the
                        # seed script owns provisioning via admin_create_user
                        # in the seed-side boto3 session).
                        #
                        # AdminUpdateUserAttributes is required for /api/v1/driver-users/*
                        # PATCH routes (e.g., editing a driver's name/phone from
                        # the operator UI) — ported from a previous inline hotfix
                        # on the live role that wasn't in CDK.
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "cognito-idp:AdminGetUser",
                                "cognito-idp:AdminEnableUser",
                                "cognito-idp:AdminDisableUser",
                                "cognito-idp:AdminUpdateUserAttributes",
                            ],
                            resources=[
                                # Part A 2026-06-16: scope to the resolved driver pool
                                # (defaults to the in-stack CMS pool) — removes the prior
                                # `userpool/*` wildcard that granted Admin* over every pool
                                # in the account when vsaUserPoolId was unset.
                                f"arn:aws:cognito-idp:{self.region}:{self.account}:userpool/"
                                + (self.node.try_get_context('vsaUserPoolId') or self.user_pool.user_pool_id)
                            ]
                        ),
                        # (Retired 2026-09-06 per spec `2026-09-05-cms-vfo-teardown`)
                        # The VFO knowledge-base S3 bucket was deleted. Grants
                        # for `s3:ListBucket` + `s3:GetObject` on
                        # `cms-{stage}-vfo-knowledge-base-{region}-{account}`
                        # removed here.
                    ]
                )
            }
        )
        
        # API Lambda functions
        self.api_functions = {}
        
        # Fleet management API
        # Extra Cognito pools (e.g. a SEPARATE driver-facing pool) trusted by the
        # CMS UI API in addition to the CMS operator pool. Reads the same env var
        # the data-processing stack consumes (CMS_EXTRA_USER_POOL_IDS). Needed only
        # for a multi-pool topology; in the consolidated single-pool deployment
        # (staging) the iOS app and Fleet UI already share self.user_pool, so this
        # is left empty. The driver-vs-operator distinction is enforced by the
        # main_api claim-based guard (DRIVER_SELF_GUARD_ENABLED), NOT by pool id.
        cms_extra_pool_ids = [
            p.strip() for p in os.environ.get('CMS_EXTRA_USER_POOL_IDS', '').split(',')
            if p.strip()
        ]

        # ── Connected Services consumer wiring (spec
        # 2026-09-10-cms-connected-services-consumer, T2.3a) ──────────────────
        #
        # Three values the main_api Connected Services proxy routes need. All
        # three are runtime-only failures if wrong — the stack reports
        # UPDATE_COMPLETE either way — so they are pinned by
        # stacks/tests/test_ui_stack_connected_services_consumer.py, which
        # DERIVES the two key names from connected_services_proxy.ENV_* rather
        # than restating them.
        _cs_stage = construct_id.replace("-ui", "").split("-", 1)[-1]

        # (1) The producer's API endpoint, imported from the CFN export
        # `subscriptions_stack.py` already publishes, so it cannot drift from
        # the deployed URL.
        #
        # THE IMPORT IS GATED, DELIBERATELY. `cms-{stage}-ui` deploys on every
        # stage; `cms-{stage}-subscriptions` is opt-in behind
        # DEPLOY_SUBSCRIPTIONS (app.py:323). An ungated Fn.import_value would
        # therefore make THIS stack un-deployable everywhere the producer is
        # absent — including prod today and every clean-deploy run — with "No
        # export named cms-{stage}-subscriptions-api-endpoint found", and would
        # additionally pin the producer's output forever, because CloudFormation
        # refuses to delete or change an export another stack imports. Bricking
        # the highest-blast-radius stack in the repo to save one env var is not
        # a trade worth making, so the import is gated on the same signal that
        # decides whether the export exists at all.
        #
        # Empty is a SUPPORTED configuration, not a deploy failure: the route
        # fails closed at runtime with an explanatory 502 `config_missing`.
        # Same posture, and same reasoning, as DMS_API_ENDPOINT below.
        #
        # Context override first, for a producer this stack cannot import (a
        # different account or region has no importable export). Note the guard
        # is on DEPLOY_SUBSCRIPTIONS — a real string — and never on the
        # truthiness of the import itself: a CFN token is ALWAYS truthy and
        # never empty, which is the trap CVX's vsa-core-stack.ts hit in three
        # places (`if (arnProp)` granting unconditionally).
        _cs_producer_endpoint_ctx = (
            self.node.try_get_context('csProducerApiEndpoint') or ''
        ).strip()
        if _cs_producer_endpoint_ctx:
            _cs_producer_endpoint = _cs_producer_endpoint_ctx
        elif os.environ.get('DEPLOY_SUBSCRIPTIONS') == 'true':
            _cs_producer_endpoint = Fn.import_value(
                f"cms-{_cs_stage}-subscriptions-api-endpoint"
            )
        else:
            _cs_producer_endpoint = ''

        # (2) CMS's own subscription_id. Not a credential (the password in the
        # same secret is), so it does not warrant a per-invocation secret read.
        # Per-environment value — never a literal here, per the publish surface.
        _cs_subscription_id = (
            self.node.try_get_context('csSubscriptionId')
            or os.environ.get('CS_SUBSCRIPTION_ID', '')
        ).strip()

        # (3) The subscriber credential's secret name. Passed explicitly because
        # the handler CANNOT derive it: the name embeds the account id, which a
        # Lambda cannot know without an STS call. Building it once here also
        # means the IAM grant below and the reader consume the same expression,
        # so they cannot drift — the property test_dms_api_endpoint_env.py
        # exists to protect. Matches scripts/provision-cms-subscriber.py:193.
        _cs_subscriber_secret_name = (
            f"cms-{_cs_stage}-connected-services-subscriber-"
            f"{self.region}-{self.account}"
        )

        # (4) Feed-cache table (T2.6). DERIVED, like the secret name above and for
        # the same reason: a Lambda cannot know the account without an STS call,
        # and threading it as context would create a second source of truth for a
        # name that must match `connected_services_consumer_stack.py:166` exactly.
        # A mismatch is not a synth error — it is a GetItem against a table that
        # does not exist, which `DynamoFeedCache` swallows by design, so the only
        # symptom would be a cache that never hits and a feed that is merely
        # slow. `test_feed_cache_table_name_matches_the_consumer_stack` compares
        # the two expressions so drift fails at test time instead.
        #
        # Empty is NOT used here: unlike the producer endpoint, this name is
        # always computable. Whether the TABLE exists is a separate question, and
        # the answer is "not until `cms-{stage}-connected-services-consumer` is
        # deployed" — which the cache handles as a permanent miss rather than an
        # error, so pointing at a not-yet-created table is safe and self-healing.
        _cs_feed_cache_table_name = (
            f"cms-{_cs_stage}-storage-cs-feed-cache-"
            f"{self.region}-{self.account}"
        )

        # Freshness window. The CS producer's own feed refresh cadence is not
        # published to CMS, so this window is a judgment call: long enough to
        # avoid spending a producer round trip per page view for data that
        # cannot have changed, short enough that a demo doesn't show visibly
        # stale numbers. Overridable per stage via env for a demo that wants
        # to see every write land immediately (`0` disables the cache
        # entirely); the handler falls back to its own documented default
        # when this is absent or unparseable, so it can never be the reason the
        # feed stops working.
        _cs_feed_cache_ttl_seconds = os.environ.get(
            'CS_FEED_CACHE_TTL_SECONDS', '60'
        ).strip() or '60'

        # The one new IAM statement this spec adds. Attached to the main_api
        # execution role after its construction below.
        #
        # NO DynamoDB statement accompanies it. The feed-cache table is covered
        # by the existing AppAccess grant on `table/*` for every action the
        # proxy could need, and no code reads or writes that cache yet
        # (connected_services_proxy.py has zero DynamoDB references), so the
        # only additional action a cache would want is BatchWriteItem. Granting
        # it now would be privilege with no reachable consumer — the shape CVX's
        # Tier 2 agent shipped with live bedrock:InvokeModel grants and no
        # caller. Its ABSENCE is asserted by
        # test_no_batchwriteitem_grant_without_a_consumer; whichever task
        # implements the cache write owns both the grant and that test.
        _cs_secret_read_statement = iam.PolicyStatement(
            sid="ConnectedServicesSubscriberSecretRead",
            effect=iam.Effect.ALLOW,
            actions=["secretsmanager:GetSecretValue"],
            # The `-??????` suffix wildcard is REQUIRED, not defensive — the
            # same finding this file already records at its other secret grant,
            # where omitting it cost a prod deploy. Secrets Manager appends a
            # 6-character random suffix at creation and IAM ARN matching has no
            # implicit trailing wildcard, so the suffix-less ARN authorises
            # NOTHING: GetSecretValue returns AccessDeniedException against the
            # real secret. Scoped to this ONE secret — a `cms-{stage}-*` prefix
            # would grant read on every CMS secret on the stage.
            resources=[
                f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:"
                f"{_cs_subscriber_secret_name}-??????"
            ],
        )

        fleet_api_kwargs = {
            'runtime': lambda_.Runtime.PYTHON_3_9,
            'handler': 'index.handler',
            'role': lambda_role,
            'code': lambda_.Code.from_asset("../modules/cms_ui/source/handlers/main_api"),
            'environment': {
                'FLEETS_TABLE_NAME': table_names['fleets'],
                'VEHICLES_TABLE_NAME': table_names['vehicles'],
                'TRIPS_TABLE_NAME': table_names['trips'],
                'TELEMETRY_TABLE_NAME': table_names['telemetry'],
                'SAFETY_EVENTS_TABLE_NAME': table_names['safety_events'],
                'MAINTENANCE_ALERTS_TABLE_NAME': table_names['maintenance_events'],
                'DTC_HISTORY_TABLE_NAME': table_names['dtc_history'],
                # Read by the vehicle-detail hasCampaign projection. MUST be set
                # explicitly: the handler no longer guesses a table name from
                # DEPLOYMENT_STAGE, because that default ("prod") made prod work
                # by accident and silently pointed every other stage at prod's
                # table. See issues/2026-09-02-campaign-guard-queries-wrong-stage-table.
                'CAMPAIGNS_TABLE_NAME': table_names['campaigns'],
                'USER_PREFERENCES_TABLE_NAME': table_names['user_preferences'],
                'DASHBOARD_METRICS_CACHE_TABLE': table_names['dashboard_metrics_cache'],
                'VEHICLE_CERTIFICATES_TABLE_NAME': table_names['vehicle_certificates'],
                'DRIVERS_TABLE_NAME': table_names['drivers'],
                # Enable the main_api driver-self guard: a token carrying
                # custom:driverId and NOT in an operator group is classified as a
                # driver acting on their own behalf and constrained to a
                # self-service allowlist (claim a vehicle). Claim-based (not
                # pool-id based) so it works in the consolidated pool where the
                # iOS app and Fleet UI share one Cognito pool.
                #
                # FAIL CLOSED on deployed stages — see _require_driver_self_guard
                # below. This value is read from the DEPLOYING SHELL's
                # environment, which means any deploy path that does not export it
                # ships the guard OFF with no error and an UPDATE_COMPLETE stack.
                # That is exactly what happened to prod: the fix was committed to
                # config/prod.env on 2026-07-16 and prod was deployed on
                # 2026-07-29, yet the deployed Lambda still read 'false' for 18
                # days because that deploy bypassed the env-extracting Make target.
                # See issues/2026-08-03-prod-driver-self-guard-inert/.
                'DRIVER_SELF_GUARD_ENABLED': _require_driver_self_guard(),
                # 2026-09-02: CMS_SSO_GATED_ADMIN env var removed. Post-env-collapse
                # the pre-env-collapse edge gate is gone, so the "authenticated
                # implies Amazon identity" premise for the groupless→admin promotion
                # no longer holds. The branch that read this env var was deleted from
                # main_api/index.py in the same commit. See
                # issues/2026-09-02-cms-sso-gated-admin-branch-removed/.
                'SERVICE_HISTORY_TABLE_NAME': table_names['service_history'],
                # Stage-scoped VFO action queue. Two fleet-actions routes previously
                # hardcoded the PROD table with no override, so staging both READ and
                # WROTE production rows (the approve/reject route calls update_item).
                # See issues/2026-08-10-cms-vfo-action-queue-hardcoded-prod-table/.
                'VFO_ACTION_QUEUE_TABLE_NAME': construct_id.replace('-ui', '-vfo-action-queue'),
                'FLEET_ENROLLMENT_TABLE_NAME': table_names['fleet_enrollment'],
                'VEHICLE_LINK_CODES_TABLE_NAME': vehicle_link_codes_table_name,
                'SUBSCRIPTIONS_TABLE_NAME': f"{storage_prefix}-subscriptions",
                'USER_POOL_ID': self.user_pool.user_pool_id,
                'CLIENT_ID': self.user_pool_client.user_pool_client_id,
                # VSA (driver-facing) Cognito pool. Used by /api/v1/driver-users/*
                # routes to show/manage driver Cognito accounts from the CMS UI.
                # Defaults to the in-stack CMS pool — operator + driver share one
                # pool in single-pool deployments (e.g. staging / Tokyo). Override
                # via `-c vsaUserPoolId=...` to point at a SEPARATE driver pool in
                # multi-pool deployments. (Part A 2026-06-16: previously defaulted
                # to '' which fail-closed the driver-account panel when the context
                # was unset — the Tokyo panel error.)
                'VSA_USER_POOL_ID': self.node.try_get_context('vsaUserPoolId') or self.user_pool.user_pool_id,
                'REDIS_ENDPOINT': redis_endpoint if redis_endpoint else '',
                'SIGNAL_CATALOG_TABLE': f'cms-{construct_id.replace("-ui", "").split("-", 1)[-1]}-signal-catalog',
                'MODEL_MANIFEST_TABLE_NAME': f'cms-{construct_id.replace("-ui", "").split("-", 1)[-1]}-model-manifest',
                # Event catalog (DTC/PID + trigger_signal metadata) — drives the
                # Trip Simulator event dropdowns. Without this the handler falls
                # back to `cms-{DEPLOYMENT_STAGE|prod}-event-catalog`, which in a
                # staging account resolves to the non-existent cms-prod table and
                # silently degrades to the hardcoded EVENT_CATALOG (no dtc_code /
                # trigger_signal → blank "Signal: —" in the modal).
                'EVENT_CATALOG_TABLE': f'cms-{construct_id.replace("-ui", "").split("-", 1)[-1]}-event-catalog',
                # DMS API base URL, read by GET /api/v1/dealers (spec
                # 2026-09-02-cms-dms-service-convergence T4.2). Server-side
                # read-through per D3 — the browser never learns this value, so
                # it is deliberately NOT a runtimeConfig key.
                #
                # Empty is a legitimate value and does NOT need to fail the
                # deploy: a stage with no DMS should get a 503 with an
                # explanation from the one route that needs it, not an
                # un-deployable UI stack. Same posture as the `dmsEventBusName`
                # gate in storage_stack.py — no DMS configured, no DMS
                # integration, and the absence is visible rather than silently
                # substituted. The handler fails closed and says why; it never
                # falls back to a hardcoded roster, which is the thing T4.2
                # deleted.
                'DMS_API_ENDPOINT': (
                    self.node.try_get_context('dmsApiEndpoint')
                    or os.environ.get('DMS_API_ENDPOINT', '')
                ).strip(),
                # ── Connected Services consumer (T2.3a) ──────────────────────
                # Built above; see the block before `fleet_api_kwargs` for why
                # the endpoint import is gated and why the secret name is passed
                # explicitly rather than derived handler-side.
                #
                # NOT `CONNECTED_SERVICES_API_ENDPOINT`, which
                # config/staging.env:207 sets to the placeholder
                # https://api.example.invalid for the separate
                # 2026-09-03-cms-connected-services-portal spec. Two specs
                # sharing one variable is how one team's deploy silently changes
                # another team's runtime.
                'CS_PRODUCER_API_ENDPOINT': _cs_producer_endpoint,
                'CS_SUBSCRIPTION_ID': _cs_subscription_id,
                'CS_SUBSCRIBER_SECRET_NAME': _cs_subscriber_secret_name,
                'CS_FEED_CACHE_TABLE_NAME': _cs_feed_cache_table_name,
                'CS_FEED_CACHE_TTL_SECONDS': _cs_feed_cache_ttl_seconds,
                # § D8 of spec 2026-09-10-cms-fleet-intelligence-adp-consumer.
                # Replicates, inside CDK, an env var an operator had been setting
                # by hand on the deployed Lambda. Any `cdk deploy cms-{stage}-ui`
                # wiped that patch, and for the window between the deploy and the
                # re-patch every /api/v1/charging and /api/v1/tco route 500s —
                # or worse, a stage-derived table name resolves to PROD's table.
                # Derived from construct_id, not from the deploying shell, for the
                # same reason DRIVER_SELF_GUARD_ENABLED above should have been:
                # a deploy path that forgets to export it must not be able to
                # change behaviour silently.
                # Closes issues/2026-09-10-main-api-deployment-stage-env-unset-charging-tco-locations-hit-prod-tables.
                'DEPLOYMENT_STAGE': construct_id.replace("-ui", "").split("-", 1)[-1],
            },
            'timeout': Duration.seconds(60),
            'memory_size': 1024,
        }

        # Add VPC config if MSK stack provides it (needed for Redis access)
        if self._data_vpc:
            fleet_api_kwargs['vpc'] = self._data_vpc
            fleet_api_kwargs['vpc_subnets'] = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
            if self._data_sg:
                fleet_api_kwargs['security_groups'] = [self._data_sg]

        self.api_functions['fleet'] = lambda_.Function(
            self, "FleetAPIFunction",
            **fleet_api_kwargs
        )

        # Connected Services subscriber-secret read (T2.3a). Attached here
        # rather than inside the AppAccess PolicyDocument above so it lands as
        # its own statement with its own Sid, which is what lets the guard suite
        # assert on it individually instead of on a merged action list.
        lambda_role.add_to_policy(_cs_secret_read_statement)
        
        # API Gateway
        self.api = apigateway.RestApi(
            self, "CMSAPI",
            rest_api_name=f"{construct_id}-api",
            description="CMS API Gateway",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"]
            )
        )

        # Gateway Responses — return CORS headers on 4XX/5XX so browsers
        # don't mask auth errors as opaque CORS failures.
        for resp_type in [
            apigateway.ResponseType.DEFAULT_4_XX,
            apigateway.ResponseType.DEFAULT_5_XX,
        ]:
            self.api.add_gateway_response(
                f"GatewayResponse{resp_type.response_type}",
                type=resp_type,
                response_headers={
                    "Access-Control-Allow-Origin": "'*'",
                    "Access-Control-Allow-Headers": "'Content-Type,Authorization'",
                    "Access-Control-Allow-Methods": "'GET,POST,PUT,DELETE,OPTIONS'",
                },
            )
        
        # Cognito Authorizer
        # Trusts the CMS operator pool plus any driver-self / partner pools listed
        # in CMS_EXTRA_USER_POOL_IDS (see cms_extra_pool_ids above). The main_api
        # handler's DRIVER_SELF_POOL_IDS guard constrains those extra pools to a
        # self-service allowlist, so widening the authorizer does NOT widen what a
        # driver token can actually do.
        extra_user_pools = [
            cognito.UserPool.from_user_pool_id(self, f"ExtraUserPool{i}", pid)
            for i, pid in enumerate(cms_extra_pool_ids)
        ]
        cognito_authorizer = apigateway.CognitoUserPoolsAuthorizer(
            self, "CMSCognitoAuthorizer",
            cognito_user_pools=[self.user_pool, *extra_user_pools],
            authorizer_name=f"{construct_id}-cognito-auth"
        )
        
        # API resources - match target-account structure
        api_resource = self.api.root.add_resource("api")
        v1_resource = api_resource.add_resource("v1")
        
        # Use allow_test_invoke=False to prevent per-route Lambda::Permission bloat.
        # A single wildcard permission is added below instead.
        fleet_integration = apigateway.LambdaIntegration(
            self.api_functions['fleet'],
            allow_test_invoke=False
        )
        
        # Single wildcard permission — covers all routes/stages/methods
        self.api_functions['fleet'].add_permission(
            "APIGatewayInvokeAll",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=self.api.arn_for_execute_api()
        )
        
        # Realtime endpoints
        realtime_resource = self.api.root.add_resource("realtime")
        realtime_vehicles = realtime_resource.add_resource("vehicles")
        realtime_vehicles.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        realtime_trips = realtime_resource.add_resource("trips")
        realtime_trips.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        
        # IoT endpoint
        iot_endpoint = self.api.root.add_resource("discover-iot-endpoint")
        iot_endpoint.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        
        # Core v1 endpoints — kept explicit to stay within Lambda policy size limit
        # Additional routes (subscriptions, users, drivers, etc.) are handled by
        # the /api/v1/{proxy+} resource created outside CDK via API Gateway API.
        fleets_resource = v1_resource.add_resource("fleets")
        fleets_resource.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        fleets_resource.add_method("POST", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        fleet_id_resource = fleets_resource.add_resource("{fleetId}")
        fleet_id_resource.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        fleet_vehicles = fleet_id_resource.add_resource("vehicles")
        fleet_vehicles.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)

        vehicles_resource = v1_resource.add_resource("vehicles")
        vehicles_resource.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        vehicles_resource.add_method("POST", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        vehicle_locations = vehicles_resource.add_resource("locations")
        vehicle_locations.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        vehicle_id_resource = vehicles_resource.add_resource("{vehicleId}")
        vehicle_id_resource.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        vehicle_trips = vehicle_id_resource.add_resource("trips")
        vehicle_trips.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        trip_id_resource = vehicle_trips.add_resource("{tripId}")
        trip_id_resource.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        vehicle_safety = vehicle_id_resource.add_resource("safety-alerts")
        vehicle_safety.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        vehicle_maintenance = vehicle_id_resource.add_resource("maintenance-alerts")
        vehicle_maintenance.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)

        trips_resource = v1_resource.add_resource("trips")
        trips_resource.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        safety_alerts = v1_resource.add_resource("safety-alerts")
        safety_alerts.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        maintenance_alerts = v1_resource.add_resource("maintenance-alerts")
        maintenance_alerts.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)

        dashboard_resource = v1_resource.add_resource("dashboard")
        dashboard_metrics = dashboard_resource.add_resource("metrics")
        dashboard_metrics.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        dashboard_comparison = dashboard_resource.add_resource("fleet-comparison")
        dashboard_comparison.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)

        # ── Fleet Intelligence API (spec 2026-09-02-cms-fleet-intelligence-v1) ──
        # A SEPARATE handler from main_api (which this spec may not edit). Reads the
        # vehicles table and read/writes pm_schedules. Its own least-privilege role
        # rather than reusing main_api's broad role. One resource subtree, not seven
        # top-level paths (§ D2, R2), every method Cognito-authorized.
        #
        # v1.1 (spec 2026-09-10-cms-fleet-intelligence-adp-consumer § D4): cost rows
        # now come from ADP's curated lake over Athena, not from the DDB
        # `vehicle-costs` stub. The DDB grant and the VEHICLE_COSTS_TABLE_NAME env
        # var are both gone; the table construct itself survives in storage_stack.py
        # because its CFN exports are imported by cms-{stage}-flink (see that file's
        # deprecation note and the spec's decisions.md G4-C).
        fi_vehicles_table = table_names['vehicles']
        fi_pm_table = f"{storage_prefix}-pm-schedules"

        # Stage string, derived the same way the neighbouring env vars derive it
        # (`SIGNAL_CATALOG_TABLE`, `EVENT_CATALOG_TABLE`): cms-staging-ui -> staging.
        fi_stage = construct_id.replace("-ui", "").split("-", 1)[-1]

        def _ddb_arns(name):
            base = Stack.of(self).format_arn(
                service="dynamodb", resource="table", resource_name=name
            )
            return [base, f"{base}/index/*"]

        # ── DO NOT ADD `role_name=` HERE WITHOUT READING THIS ──────────────────
        # Deliberately has NO explicit `role_name`, so the physical name carries a
        # CDK-generated suffix (currently
        # `cms-staging-ui-FleetIntelligenceRole18594AC3-2bC21ilVPEkD`).
        #
        # An explicit `role_name` IS the durable fix, and it is a filed P1
        # follow-on — but adding it here **alone is worse than leaving it**, and
        # that is the non-obvious part:
        #
        # ADP's Lake Formation grants are pinned to this role's ARN, threaded as
        # `ADP_CMS_CONSUMER_ROLE_ARN` into `adp-staging-foundation-governance`.
        # Setting `role_name` forces a CloudFormation **replacement** of the role,
        # which orphans every grant against the old ARN. The Fleet Intelligence
        # Cost surfaces then return 500 `AthenaCursorError` until ADP is
        # redeployed against the new ARN.
        #
        # So committing `role_name` without deploying it in the same sitting arms
        # a trap: the NEXT person to deploy `cms-staging-ui` for any unrelated
        # reason breaks the Cost surfaces and has no idea why. Granting ahead does
        # not help — Lake Formation rejects a grant to a principal that does not
        # exist yet.
        #
        # The required sequence, all in one sitting, with someone watching:
        #   1. add `role_name` here  ->  deploy `cms-staging-ui` (Cost 500s from here)
        #   2. repoint `ADP_CMS_CONSUMER_ROLE_ARN` to the new stable ARN
        #      ->  deploy `adp-staging-foundation-governance`
        #   3. re-verify all four FI routes (cpm, cpm/outliers, lifecycle, pm)
        #
        # Rationale and the empirical proof that the grants must target the ROLE
        # (not `:root` — CMS and ADP are the same account, where `:root` confers
        # nothing on an individual IAM role) are in
        # `.kiro/specs/2026-09-10-cms-fleet-intelligence-adp-consumer/decisions.md`.
        fleet_intelligence_role = iam.Role(
            self, "FleetIntelligenceRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        fleet_intelligence_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchGetItem"],
            resources=_ddb_arns(fi_vehicles_table),
        ))
        fleet_intelligence_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan",
                "dynamodb:PutItem", "dynamodb:UpdateItem",
            ],
            resources=_ddb_arns(fi_pm_table),
        ))

        # ── ADP consumer grants (spec 2026-09-10-... § D4) ────────────────────
        # Eight statements, each with an explicit Sid so the guard suite can
        # assert on them individually. § D4's prose says "six" and then tabulates
        # seven; the eighth is AdpLakeBucketLocation, split out below because
        # GetBucketLocation cannot work where § D4 put it.
        #
        # The Athena workgroup and results bucket are created by
        # cms-{stage}-ui-analytics in us-east-1. This stack is us-west-2 on
        # staging, so it authorizes them by NAME — CloudFormation cannot export a
        # value across Regions. Both sides compute the name from
        # _fleet_intelligence_naming so they cannot drift apart.
        fi_athena_region = fi_naming.ADP_REGION
        fi_workgroup = fi_naming.workgroup_name(fi_stage)
        fi_results_bucket = fi_naming.results_bucket_name(fi_stage, self.account)
        fi_adp_lake_bucket = fi_naming.adp_lake_bucket_name(fi_stage, self.account)

        fleet_intelligence_role.add_to_policy(iam.PolicyStatement(
            sid="AthenaWorkgroupAccess",
            actions=[
                "athena:StartQueryExecution",
                "athena:GetQueryExecution",
                "athena:GetQueryResults",
            ],
            resources=[
                f"arn:aws:athena:{fi_athena_region}:{self.account}:workgroup/{fi_workgroup}"
            ],
        ))
        # Bucket-level and object-level actions in one statement is correct here:
        # both ARN forms are present and there is no condition to mis-scope.
        fleet_intelligence_role.add_to_policy(iam.PolicyStatement(
            sid="AthenaResultsBucketAccess",
            actions=[
                "s3:PutObject",
                "s3:GetObject",
                "s3:GetBucketLocation",
                "s3:ListBucket",
            ],
            resources=[
                f"arn:aws:s3:::{fi_results_bucket}",
                f"arn:aws:s3:::{fi_results_bucket}/*",
            ],
        ))
        # Object reads on exactly the five shared products' curated prefixes,
        # plus the one dimension table the ADP rollup joins (dimensions/vins).
        # Never the whole `dimensions/` prefix: it also holds `customers` (PII).
        # § D4 also lists s3:GetBucketLocation here — deliberately omitted: it is
        # a bucket-level action and authorizes nothing against a `.../*` ARN.
        # Granting a bucket action on an object ARN is the defect
        # 2026-09-01-dms-customer-master-adp shipped with all tests green and
        # found only by reading the synthesized template. It lives in
        # AdpLakeBucketLocation below.
        fleet_intelligence_role.add_to_policy(iam.PolicyStatement(
            sid="AdpLakeS3Read",
            actions=["s3:GetObject"],
            resources=[
                f"arn:aws:s3:::{fi_adp_lake_bucket}/curated/{product}/*"
                for product in fi_naming.ADP_PRODUCTS
            ] + [
                f"arn:aws:s3:::{fi_adp_lake_bucket}/dimensions/{table}/*"
                for table in fi_naming.ADP_DIMENSION_TABLES
            ],
        ))
        fleet_intelligence_role.add_to_policy(iam.PolicyStatement(
            sid="AdpLakeS3List",
            actions=["s3:ListBucket"],
            resources=[f"arn:aws:s3:::{fi_adp_lake_bucket}"],
            conditions={
                "StringLike": {
                    "s3:prefix": list(fi_naming.curated_prefixes(fi_stage))
                    + list(fi_naming.dimension_prefixes(fi_stage))
                }
            },
        ))
        # Athena calls GetBucketLocation before reading any object. Unconditioned
        # on purpose: s3:prefix is only populated for List operations, so a
        # conditioned GetBucketLocation never matches and the query fails.
        fleet_intelligence_role.add_to_policy(iam.PolicyStatement(
            sid="AdpLakeBucketLocation",
            actions=["s3:GetBucketLocation"],
            resources=[f"arn:aws:s3:::{fi_adp_lake_bucket}"],
        ))
        # ADP's lake is SSE-KMS. Without this the read fails with a KMS
        # AccessDenied after every unit test has passed.
        fleet_intelligence_role.add_to_policy(iam.PolicyStatement(
            sid="AdpLakeKmsDecrypt",
            actions=["kms:Decrypt", "kms:DescribeKey"],
            resources=[f"arn:aws:kms:{fi_athena_region}:{self.account}:key/*"],
            conditions={
                "StringEquals": {
                    "kms:ViaService": f"s3.{fi_athena_region}.amazonaws.com"
                }
            },
        ))
        # One catalog ARN + one database + one table wildcard per product, plus
        # the dimensions database and its `vins` table by name (not `/*`: the
        # database also holds `customers`, which is PII).
        # Never `database/*`: that widening passes any "at least one in-scope
        # resource" check while granting metadata read across every ADP domain.
        fi_dimensions_db = fi_naming.adp_database_name(
            fi_stage, fi_naming.ADP_DIMENSIONS_DATABASE
        )
        fleet_intelligence_role.add_to_policy(iam.PolicyStatement(
            sid="AdpGlueMetadataRead",
            actions=[
                "glue:GetDatabase",
                "glue:GetTable",
                "glue:GetTables",
                "glue:GetPartitions",
            ],
            resources=(
                [f"arn:aws:glue:{fi_athena_region}:{self.account}:catalog"]
                + [
                    f"arn:aws:glue:{fi_athena_region}:{self.account}:database/"
                    f"{fi_naming.adp_database_name(fi_stage, product)}"
                    for product in fi_naming.ADP_PRODUCTS
                ]
                + [
                    f"arn:aws:glue:{fi_athena_region}:{self.account}:table/"
                    f"{fi_naming.adp_database_name(fi_stage, product)}/*"
                    for product in fi_naming.ADP_PRODUCTS
                ]
                + [
                    f"arn:aws:glue:{fi_athena_region}:{self.account}:database/"
                    f"{fi_dimensions_db}"
                ]
                + [
                    f"arn:aws:glue:{fi_athena_region}:{self.account}:table/"
                    f"{fi_dimensions_db}/{table}"
                    for table in fi_naming.ADP_DIMENSION_TABLES
                ]
            ),
        ))
        # lakeformation:GetDataAccess does not support resource-level scoping.
        # Actual containment is the Lake Formation grant on ADP's side (five
        # databases plus dimensions.vins, SELECT+DESCRIBE, no grant option) plus
        # the Glue and S3 statements above. Same rationale CVX records for its
        # Tier 2 role.
        fleet_intelligence_role.add_to_policy(iam.PolicyStatement(
            sid="LakeFormationGetDataAccess",
            actions=["lakeformation:GetDataAccess"],
            resources=["*"],
        ))

        fleet_intelligence_fn = lambda_.Function(
            self, "FleetIntelligenceFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            role=fleet_intelligence_role,
            code=lambda_.Code.from_asset(
                "../services/fleet_intelligence",
                # `.pytest_cache` matters as much as the rest: without it, a synth
                # run from a tree where pytest has executed bundles ~14 KB of cache
                # into the Lambda, so the asset HASH differs by whether tests were
                # run. That makes deploys non-reproducible and shows up as a
                # spurious code change in `cdk diff`. Found 2026-09-13 while
                # packaging this module by hand for a targeted code update.
                # `.ruff_cache`/`.mypy_cache` are pre-empted for the same reason.
                exclude=[
                    "tests",
                    "**/__pycache__",
                    "*.pyc",
                    ".pytest_cache",
                    ".ruff_cache",
                    ".mypy_cache",
                    # `*.md` added 2026-09-13 (G7): the module gained a README
                    # documenting the ADP read path. Documentation is not runtime
                    # code, and excluding it keeps the asset to the 8 files the
                    # handler actually imports. Distinct in kind from the cache
                    # entries above — a README is deterministic, so it would not
                    # have made the hash vary — but it WOULD have shown up as a
                    # spurious FleetIntelligenceFunction code change in the next
                    # `cdk diff` for no behavioural reason.
                    "*.md",
                ],
            ),
            environment={
                # PM stays on DDB (§ D1). Vehicles is read for the fleetId lookup
                # and for the § D2 redesign's vin filter.
                'VEHICLES_TABLE_NAME': fi_vehicles_table,
                'PM_SCHEDULES_TABLE_NAME': fi_pm_table,

                # ── ADP consumer (spec 2026-09-10-... § D4) ───────────────────
                # Exactly the nine keys services/fleet_intelligence/ reads.
                # § D4 additionally prescribes ADP_LAKE_BUCKET and ADP_ACCOUNT;
                # both are omitted because no handler code reads either. Seven
                # tests monkeypatch ADP_LAKE_BUCKET, which makes it look
                # load-bearing from the test side, but the CDK builds every lake
                # ARN it needs at synth time so nothing has to travel to the
                # Lambda. Shipping them would be the `Dead VITE vars` shape —
                # config that three separate sessions have since misdescribed
                # because nobody wrote down which mechanism was live.
                'ADP_STAGE': fi_stage,
                'ADP_REGION': fi_athena_region,
                'ATHENA_WORKGROUP': fi_workgroup,
                'ATHENA_OUTPUT_LOC': fi_naming.results_output_location(
                    fi_stage, self.account
                ),

                # § D6 — provenance envelope. Stage-derived rather than read
                # straight from the deploy shell, with config/<stage>.env able to
                # override: an unset value fails the Lambda closed at cold start,
                # and a WRONG value labels Faker-generated cost figures as
                # measured. Flip prod to 'measured' when § F5's publish blockers
                # clear.
                'ADP_DATA_PROVENANCE': fi_naming.data_provenance(
                    fi_stage, os.environ.get('ADP_DATA_PROVENANCE')
                ),

                # § D2 — default window when the handler does not override.
                'FI_WINDOW_MONTHS': '12',

                # § D4 (spec 2026-09-25-cms-fi-adp-wide-lifecycle) — ADP
                # lifecycle view uses a 36-month window so the full ownership
                # arc (purchase → typical 3-year ownership) is visible.
                # Both the CMS-fleet lifecycle path and adp_rollup read this key.
                # The CPM routes read FI_WINDOW_MONTHS (12 mo) so neither path
                # couples the other's horizon.
                'FI_LIFECYCLE_WINDOW_MONTHS': '36',
            },
            # 900s (Lambda's max), up from 30s, for the scheduled lifecycle
            # cache refresh below: it waits out Athena queue delay (up to 354s
            # observed) across three sequential queries. API Gateway still cuts
            # HTTP callers off at 29s; request-path Athena reads keep
            # adp_source's own 60-poll budget, so they cannot run long.
            # Issue 2026-09-25-fleet-lifecycle-athena-queue-delay-504.
            timeout=Duration.seconds(900),
            memory_size=512,
        )
        # A failed refresh is retried by the next scheduled run, 10 minutes
        # later. Lambda's default of two async retries would triple Athena load
        # during exactly the queue congestion that caused the failure.
        fleet_intelligence_fn.configure_async_invoke(retry_attempts=0)
        fleet_lifecycle_cache_rule = events.Rule(
            self, "FleetLifecycleCacheRefreshRule",
            description=(
                "Rebuilds the cached Athena inputs behind "
                "GET /api/v1/fleet-intelligence/lifecycle"
            ),
            schedule=events.Schedule.rate(Duration.minutes(10)),
        )
        fleet_lifecycle_cache_rule.add_target(events_targets.LambdaFunction(
            fleet_intelligence_fn,
            event=events.RuleTargetInput.from_object(
                {"fleetIntelligenceTask": "refresh-lifecycle-cache"}
            ),
            retry_attempts=0,
        ))

        # § D3 (spec 2026-09-25-cms-fi-adp-wide-lifecycle): hourly rule that
        # triggers the ADP-wide lifecycle rollup computation and writes the
        # pre-computed artifact consumed by GET /lifecycle?scope=adp.
        # A failed run is retried by the next scheduled run (1 hour later);
        # no immediate retry to avoid queuing up multiple expensive Athena
        # queries during a congestion spike.
        fleet_adp_rollup_rule = events.Rule(
            self, "FleetAdpRollupRefreshRule",
            description=(
                "Refreshes the pre-computed ADP lifecycle rollup artifact "
                "consumed by GET /api/v1/fleet-intelligence/lifecycle?scope=adp"
            ),
            schedule=events.Schedule.rate(Duration.hours(1)),
        )
        fleet_adp_rollup_rule.add_target(events_targets.LambdaFunction(
            fleet_intelligence_fn,
            event=events.RuleTargetInput.from_object(
                {"fleetIntelligenceTask": "refresh-adp-rollup"}
            ),
            retry_attempts=0,
        ))
        fleet_intelligence_integration = apigateway.LambdaIntegration(
            fleet_intelligence_fn, allow_test_invoke=False
        )
        fleet_intelligence_fn.add_permission(
            "APIGatewayInvokeFleetIntelligence",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=self.api.arn_for_execute_api(),
        )

        def _fi_method(res, http):
            res.add_method(
                http, fleet_intelligence_integration,
                authorizer=cognito_authorizer,
                authorization_type=apigateway.AuthorizationType.COGNITO,
            )

        fi_root = v1_resource.add_resource("fleet-intelligence")
        fi_cpm = fi_root.add_resource("cpm")
        _fi_method(fi_cpm, "GET")
        _fi_method(fi_cpm.add_resource("outliers"), "GET")
        fi_lifecycle = fi_root.add_resource("lifecycle")
        # NEW 2026-09-14 (spec 2026-09-14-cms-fleet-lifecycle-view Fix Group 1):
        # wire the GET method to the bare /lifecycle node so the fleet-scoped
        # route is reachable. Without this line the resource existed but had
        # no methods, so authenticated requests returned 404 from API Gateway
        # and never reached the Lambda's _handle_fleet_lifecycle dispatch.
        _fi_method(fi_lifecycle, "GET")
        _fi_method(fi_lifecycle.add_resource("{vehicleId}"), "GET")
        fi_pm = fi_root.add_resource("pm")
        fi_pm_schedules = fi_pm.add_resource("schedules")
        _fi_method(fi_pm_schedules, "GET")
        _fi_method(fi_pm_schedules, "POST")
        _fi_method(fi_pm.add_resource("compliance"), "GET")
        fi_pm_schedule_id = fi_pm_schedules.add_resource("{id}")
        _fi_method(fi_pm_schedule_id.add_resource("complete"), "POST")

        # /api/v1/{proxy+} created manually via API Gateway API (not managed by CDK)
        # to avoid Lambda policy size limit. Covered by existing prod/*/* permission.

        # Root-level proxy for any other endpoints
        proxy_resource = self.api.root.add_resource("{proxy+}")
        proxy_resource.add_method("ANY", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        
        # ── WebSocket API for real-time fleet telemetry ──────────────────
        ws_handler = lambda_.Function(
            self, "WSHandler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="websocket_handler.handler",
            code=lambda_.Code.from_asset("../services/websocket/lambda"),
            environment={
                'WS_CONNECTIONS_TABLE': ws_connections_table_name,
            },
            timeout=Duration.seconds(30),
            memory_size=256,
        )
        
        self.ws_api = apigatewayv2.CfnApi(
            self, "CMSWebSocketAPI",
            name=f"{construct_id}-ws",
            protocol_type="WEBSOCKET",
            route_selection_expression="$request.body.action",
        )
        
        ws_integration = apigatewayv2.CfnIntegration(
            self, "WSIntegration",
            api_id=self.ws_api.ref,
            integration_type="AWS_PROXY",
            integration_uri=f"arn:aws:apigateway:{self.region}:lambda:path/2015-03-31/functions/{ws_handler.function_arn}/invocations",
        )
        
        # ── Lambda authorizer for WebSocket $connect ──────────────────────────
        # Default: $connect requires a Cognito JWT passed as ?token=<jwt>.
        # Opt-in (cms.allow_unauth_websocket=true): all routes anonymous — demo only.
        allow_unauth_ws = self.node.try_get_context(
            "cms.allow_unauth_websocket"
        ) in (True, "true", "1")

        ws_authorizer = None
        if not allow_unauth_ws:
            from stacks._ws_authorizer_bundle import bundle_ws_authorizer
            ws_authorizer_handler = lambda_.Function(
                self, "WSAuthorizerHandler",
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.X86_64,
                handler="ws_authorizer.handler",
                # Bundle PyJWT[crypto] + cryptography (linux x86_64 wheels) WITH the
                # source. Plain from_asset would omit the deps → the authorizer would
                # ModuleNotFoundError at cold start and reject every $connect. No
                # Docker; see stacks/_ws_authorizer_bundle.py.
                code=lambda_.Code.from_asset(bundle_ws_authorizer()),
                environment={
                    "USER_POOL_ID": self.user_pool.user_pool_id,
                    "USER_POOL_CLIENT_ID": self.user_pool_client.user_pool_client_id,
                    # AWS_REGION is injected automatically by the Lambda runtime.
                },
                timeout=Duration.seconds(10),
                memory_size=256,
            )
            ws_authorizer = apigatewayv2.CfnAuthorizer(
                self, "WSAuthorizer",
                api_id=self.ws_api.ref,
                name=f"{construct_id}-ws-authorizer",
                authorizer_type="REQUEST",
                authorizer_uri=(
                    f"arn:aws:apigateway:{self.region}:lambda:path/"
                    f"2015-03-31/functions/{ws_authorizer_handler.function_arn}/invocations"
                ),
                identity_source=["route.request.querystring.token"],
            )
            ws_authorizer_handler.add_permission(
                "WSAuthorizerInvokePermission",
                principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
                source_arn=(
                    f"arn:aws:execute-api:{self.region}:{self.account}:"
                    f"{self.ws_api.ref}/authorizers/*"
                ),
            )

        for route_key in ["$connect", "$disconnect", "$default"]:
            safe_name = route_key.replace("$", "")
            route_kwargs: dict = {
                "api_id": self.ws_api.ref,
                "route_key": route_key,
                "target": f"integrations/{ws_integration.ref}",
            }
            # $connect carries the authorizer; $disconnect and $default run on
            # already-authenticated connections per the AWS WebSocket API design.
            if route_key == "$connect" and ws_authorizer is not None:
                route_kwargs["authorization_type"] = "CUSTOM"
                route_kwargs["authorizer_id"] = ws_authorizer.ref
            apigatewayv2.CfnRoute(self, f"WSRoute{safe_name}", **route_kwargs)
        
        apigatewayv2.CfnStage(
            self, "WSStage",
            api_id=self.ws_api.ref,
            stage_name="live",
            auto_deploy=True,
        )
        
        ws_handler.add_permission(
            "WSInvokePermission",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=f"arn:aws:execute-api:{self.region}:{self.account}:{self.ws_api.ref}/*",
        )
        
        ws_handler.add_to_role_policy(
            iam.PolicyStatement(
                actions=["execute-api:ManageConnections"],
                resources=[f"arn:aws:execute-api:{self.region}:{self.account}:{self.ws_api.ref}/live/*"],
            )
        )

        # WS connections table grants. The handler:
        #   $connect    → PutItem  (registers connectionId + fleetId)
        #   $disconnect → DeleteItem
        #   $default    → GetItem  (looks up the caller's fleet)
        # Plus Query on the fleetId-index GSI so ws-fanout can list a fleet's
        # active connections. The table lives in storage_stack; we build the
        # ARN from the same name-string both stacks share (line 187 here,
        # line 394 in storage_stack.py) instead of taking a cross-stack
        # construct dependency. Was missing entirely — caused HTTP 502 on
        # every $connect (issue 2026-05-28-cms-staging-ws-502-iam-gap).
        ws_handler.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:PutItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                ],
                resources=[
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/{ws_connections_table_name}",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/{ws_connections_table_name}/index/*",
                ],
            )
        )

        ws_endpoint = f"wss://{self.ws_api.ref}.execute-api.{self.region}.amazonaws.com/live"
        CfnOutput(self, "WebSocketEndpoint", value=ws_endpoint, export_name=f"{construct_id}-ws-endpoint")
        
        # Resolve sibling stack outputs at synth time so the runtime config is
        # populated even when env vars aren't exported. Priority:
        #   1. Explicit env var (lets users override without redeploying siblings)
        #   2. Synth-time CloudFormation describe-stacks lookup
        #   3. Empty string (downstream code handles missing endpoints)
        #
        # runtimeConfig endpoint-race hardening (issue
        # 2026-06-15-runtimeconfig-endpoint-race-hardening): when sibling
        # stacks don't yet exist at UI-synth time, the lookup falls back to
        # "" silently and ships an unusable runtimeConfig.json to S3.
        # _resolve_endpoint now tracks every empty fallback in
        # unresolved_endpoints and emits a stderr warning so the operator can
        # see the gap during `cdk synth` / `cdk deploy`. The list is also
        # baked into runtimeConfig.json under `_unresolved_endpoints` for
        # post-deploy inspection (a smoke test in tests/e2e/test_clean_deploy.py
        # asserts no key in this list corresponds to a stack that IS deployed).
        stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
        region = self.region
        unresolved_endpoints: list = []

        def _resolve_endpoint(
            env_var: str,
            stack_suffix: str,
            output_keys: list,
            config_key: str,
        ) -> str:
            """Resolve a sibling-stack endpoint at synth time.

            Returns "" and records ``config_key`` in ``unresolved_endpoints``
            (with a stderr warning) when neither the env var nor any
            ``describe-stacks`` lookup yields a value. ``config_key`` is the
            runtimeConfig.json field name, which is what makes the warning
            and the post-deploy provenance useful.
            """
            val = os.environ.get(env_var, "").strip()
            if val:
                return val
            stack_name = f"cms-{stage}-{stack_suffix}"
            for key in output_keys:
                v = _lookup_stack_output(stack_name, key, region)
                if v:
                    return v
            unresolved_endpoints.append(config_key)
            print(
                f"⚠️  [runtimeConfig] {config_key} unresolved at synth time "
                f"(env={env_var} unset; cms-{stage}-{stack_suffix} "
                f"outputs {output_keys} not reachable). "
                f"Empty value will ship to S3 unless `make regenerate-runtime-config` "
                f"runs after the sibling stack deploys.",
                file=sys.stderr,
                flush=True,
            )
            return ""

        # data-processing endpoint has a third source: an explicit
        # constructor arg from app.py that wins over both env var and
        # describe-stacks lookup. Track that for provenance too.
        if self._data_processing_api_endpoint:
            data_processing_endpoint = self._data_processing_api_endpoint
        else:
            data_processing_endpoint = _resolve_endpoint(
                "DATA_PROCESSING_API_ENDPOINT",
                "data-processing",
                ["APIEndpoint"],
                "dataProcessingApiEndpoint",
            )
        simulation_endpoint = _resolve_endpoint(
            "SIMULATION_API_ENDPOINT",
            "simulation",
            ["SimulationApiUrl"],
            "simulationApiEndpoint",
        )
        commands_endpoint = _resolve_endpoint(
            "COMMANDS_API_ENDPOINT",
            "commands",
            ["CommandsApiUrl"],
            "commandsApiEndpoint",
        )

        # Cognito domain: prefer env var, else look up what's attached to this
        # stack's user pool (only resolvable on re-deploys, not first deploy).
        cognito_domain = os.environ.get("COGNITO_DOMAIN", "").strip()
        if not cognito_domain:
            existing_pool_id = _lookup_stack_output(f"cms-{stage}-ui", "UserPoolId", region)
            if existing_pool_id:
                cognito_domain = _lookup_cognito_domain(existing_pool_id, region)
        if not cognito_domain:
            # cognito_domain is optional (Federate is opt-in), but flag it for
            # provenance so operators know whether the empty value is by
            # design (no Federate) or a misconfiguration (env var missing).
            unresolved_endpoints.append("cognitoDomain")
            print(
                "⚠️  [runtimeConfig] cognitoDomain unresolved at synth time "
                "(env=COGNITO_DOMAIN unset; user pool has no Hosted UI domain). "
                "Federate / Hosted UI login will be unavailable until COGNITO_DOMAIN "
                "is set or a domain is attached to the user pool.",
                file=sys.stderr,
                flush=True,
            )

        # Bedrock-agents sibling stack — retired 2026-09-06 per spec
        # `2026-09-05-cms-vfo-teardown`. Context key kept read-only for
        # back-compat; value is ignored, no Fn.ImportValue is emitted.
        bedrock_agents_stack_name = ""
        _ = self.node.try_get_context("bedrockAgentsStackName")  # accepted, ignored

        # Create dynamic runtime config after API is created
        runtime_config = {
            "awsRegion": self.region,
            "mapAuth": {
                "identityPoolClient": f"cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}",
                "mapName": self.map.ref,
                "identityPoolId": self.identity_pool.ref
            },
            "locationServices": {
                "mapName": self.map.ref,
                "placeIndexName": self.place_index.ref, 
                "routeCalculatorName": self.route_calculator_name,
                "region": self.region,
                "enabled": True
            },
            "isDemoMode": "false",
            # 2026-09-02: showDemoButtons + demoPasswords emission removed. The
            # last consumers (Quick-login buttons on the login page, auto-sign-in
            # useEffect in SimpleAuthProvider, persona-switch dropdown in the
            # top-nav account menu) were deleted in commits 2ca6f6f7, 1349ef78,
            # and the current change. See
            # issues/2026-09-02-cms-persona-dropdown-removed/ for context.
            "apiEndpoint": self.api.url,
            "wsEndpoint": ws_endpoint,
            "userPreferencesApiEndpoint": self.api.url,
            "awsCredentials": {
                "region": self.region,
                "identityPoolId": self.identity_pool.ref,
                "userPoolId": self.user_pool.user_pool_id,
                "userPoolWebClientId": self.user_pool_client.user_pool_client_id
            },
            "dataProcessingApiEndpoint": data_processing_endpoint,
            "simulationApiEndpoint": simulation_endpoint,
            "commandsApiEndpoint": commands_endpoint,
            "cognitoDomain": cognito_domain,
            # Bedrock agents block — retired 2026-09-06 per spec
            # `2026-09-05-cms-vfo-teardown`. Kept as empty strings/dict for
            # one-release back-compat with any external consumer that reads
            # runtimeConfig.json directly. The web chat routes to CVX's
            # `/assistant/chat` endpoint via `vsaApiEndpoint`; the fleet-view
            # surface is served by `services/fleet_intelligence/` (Tier 1).
            # Whole block is safe to delete in a follow-on release once no
            # reader remains.
            "bedrockAgent": {
                "agentId": "",
                "agentAliasId": "",
                "region": self.region,
                "agents": _build_bedrock_agents_dict(self, bedrock_agents_stack_name),
            },
            # Provenance metadata for runtimeConfig endpoint-race hardening
            # (issue 2026-06-15-runtimeconfig-endpoint-race-hardening).
            #
            # `_resolved_at`: ISO-8601 UTC timestamp captured at synth time.
            # `regenerate-runtime-config` overwrites this object in S3, so a
            # post-deploy refresh will surface a newer timestamp than the
            # original synth-baked value. Useful for distinguishing "ran the
            # regenerate target" vs "still on the original synth-time bake".
            #
            # `_unresolved_endpoints`: list of runtimeConfig field names that
            # fell back to "" at synth time because the corresponding sibling
            # stack didn't exist or didn't have the expected output. The
            # post-deploy smoke test in tests/e2e/test_clean_deploy.py asserts
            # that no key in this list corresponds to a stack that IS
            # currently deployed (i.e. unresolved == legitimately absent).
            "_resolved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "_unresolved_endpoints": list(unresolved_endpoints),
        }

        # ── DMS console URL (spec 2026-08-31-dms-standalone-ui T4.2) ─────────
        #
        # Consumed ONLY by the frontend's `DealerPersonaBanner`, which nudges a
        # dealer-persona user toward the standalone DMS app. Opt-in per stage:
        #
        #   -c dmsUiUrl=https://<dms-console-host>
        #
        # Absent by default, and the banner degrades to link-free text when it is
        # absent, so an unset key yields a correct page rather than a dead link.
        #
        # NOT a reinstatement of `dmsApiEndpoint`, which T4.4 removed: CMS no
        # longer calls the DMS API. This is a human-navigable URL only.
        #
        # Deliberately NOT hardcoded. The first implementation baked a specific
        # external hostname (of the form `<name>.awsi.aws.dev`) into the bundle;
        # that host does not exist (the `awsi.aws.dev` parent zone is not ours)
        # and `awsi.aws.dev` is an internal hostname, so `make ui-quick-deploy`'s
        # pre-sync secret scan correctly refused to ship the build.
        _dms_ui_url = (self.node.try_get_context("dmsUiUrl") or "").strip()
        if _dms_ui_url:
            if not _dms_ui_url.startswith("https://"):
                raise ValueError(
                    f"dmsUiUrl must be an https:// URL (got {_dms_ui_url!r})"
                )
            runtime_config["dmsUiUrl"] = _dms_ui_url

        # Deploy frontend assets from the built React app
        frontend_deployment = s3deploy.BucketDeployment(
            self, "FrontendDeployment",
            sources=[
                s3deploy.Source.asset("../modules/cms_ui/source/frontend/build"),
                s3deploy.Source.json_data("runtimeConfig.json", runtime_config)
            ],
            destination_bucket=self.frontend_bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
            memory_limit=512,
            ephemeral_storage_size=Size.mebibytes(1024)
        )
        
        # Create default user with custom resource
        default_user_email = "FleetManager@example.com"
        # The demo user's password is generated by Secrets Manager and never placed
        # in the CloudFormation template.  CMS_DEMO_DEFAULT_PASSWORD is no longer
        # read here — see issues/2026-08-04-prod-demo-credential-plaintext-in-cfn-template
        # for the three exposure sites this replaces and the prior history of the defect.

        # --- Secrets Manager secret (generated server-side; value never in template) ---
        # Precedent: deployment/stacks/eval_user_stack.py:44-56
        # password_length=24 with require_each_included_type satisfies the pool
        # policy at ui_stack.py:266-272 (min 8, lower, upper, digits, symbols).
        # exclude_characters strips shell-hazardous chars so the value is safe to
        # pass through a shell for the staging demo-button path.
        demo_password_secret = secretsmanager.Secret(
            self,
            "DemoUserPassword",
            secret_name=f"cms-{stage}-demo-user-password",
            description="Auto-generated demo user password; never appears in the CloudFormation template.",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=24,
                require_each_included_type=True,
                exclude_characters=" $\"'\\`",
            ),
        )

        # --- IAM role for the demo-password setter Lambda ---
        # AWSLambdaBasicExecutionRole covers CloudWatch Logs.
        # Two additional statements, least-privilege scoped to exact ARNs:
        demo_password_setter_role = iam.Role(
            self,
            "DemoPasswordSetterRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
        )
        demo_password_setter_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[demo_password_secret.secret_arn],
            )
        )
        demo_password_setter_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cognito-idp:AdminSetUserPassword"],
                resources=[self.user_pool.user_pool_arn],
            )
        )

        # --- Explicit log group with bounded retention ---
        # RetentionDays.ONE_WEEK bounds the blast radius if a future edit
        # accidentally logs sensitive data, and ensures the group is not left at
        # the CloudWatch never-expire default.
        demo_password_setter_logs = logs.LogGroup(
            self,
            "DemoPasswordSetterLogs",
            log_group_name=f"/aws/lambda/cms-{stage}-demo-password-setter",
            retention=logs.RetentionDays.ONE_WEEK,
        )

        # --- Inline Lambda: fetches secret, calls adminSetUserPassword ---
        # Code.from_inline → ZipFile property → cfnresponse is available.
        # Handler contract (each point test-asserted by test_demo_credential_guard.py):
        #   - Never logs the secret value or any derivative.
        #   - Returns no Data (prevents Fn::GetAtt leakage).
        #   - Responds on every path, including exceptions.
        #   - Scrubs exception text before passing to cfnresponse reason=.
        #   - Delete is a no-op success.
        # Precedent: deployment/stacks/telemetry_integration_stack.py:64-93
        demo_password_setter_fn = lambda_.Function(
            self,
            "DemoPasswordSetterFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            role=demo_password_setter_role,
            timeout=Duration.minutes(2),
            log_group=demo_password_setter_logs,
            code=lambda_.Code.from_inline(
                """
import boto3
import cfnresponse
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def handler(event, context):
    request_type = event.get('RequestType', '')
    props = event.get('ResourceProperties', {})
    secret_arn = props.get('SecretArn', '')
    user_pool_id = props.get('UserPoolId', '')
    username = props.get('Username', '')
    version_id = props.get('SecretVersionId', '')

    # Delete is a no-op: the Cognito user is removed with the pool.
    if request_type == 'Delete':
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        return

    logger.info(
        'DemoPasswordSetter: RequestType=%s SecretVersionId=%s',
        request_type, version_id,
    )
    try:
        sm = boto3.client('secretsmanager')
        secret_value = sm.get_secret_value(SecretId=secret_arn)['SecretString']

        cognito = boto3.client('cognito-idp')
        cognito.admin_set_user_password(
            UserPoolId=user_pool_id,
            Username=username,
            Password=secret_value,
            Permanent=True,
        )
        logger.info('DemoPasswordSetter: password set successfully')
        # Return no Data — nothing must be retrievable via Fn::GetAtt.
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
    except Exception as exc:
        # Scrub the exception text before surfacing it in CloudFormation
        # events.  botocore InvalidPasswordException can echo policy detail;
        # a naive str(exc) in this path is the most likely accidental-
        # disclosure route in the whole design.
        exc_type = type(exc).__name__
        safe_reason = f'DemoPasswordSetter failed: {exc_type} (details redacted)'
        logger.error('DemoPasswordSetter error: %s', exc_type)
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, reason=safe_reason)
"""
            ),
        )

        # --- Custom resource: drives adminSetUserPassword via the Lambda ---
        # SecretVersionId is resolved at synth time so that a rotation (which
        # increments the AWSCURRENT version) produces a property diff → CloudFormation
        # sends an Update event → handler re-applies the new value.  Degrades to ""
        # on first deploy (secret not yet created) or when synth runs without
        # credentials (spurious-but-idempotent re-apply — documented trade-off in
        # spec § Design and decisions.md).
        secret_version_id = _lookup_secret_version_id(
            f"cms-{stage}-demo-user-password", self.region
        )

        set_demo_user_password = CustomResource(
            self,
            "SetDemoUserPassword",
            service_token=demo_password_setter_fn.function_arn,
            resource_type="Custom::CmsDemoUserPassword",
            properties={
                "SecretArn": demo_password_secret.secret_arn,
                "UserPoolId": self.user_pool.user_pool_id,
                "Username": default_user_email,
                "SecretVersionId": secret_version_id,
            },
        )

        default_user_resource = custom_resource.AwsCustomResource(
            self, "DefaultUserResource",
            on_create=custom_resource.AwsSdkCall(
                service="CognitoIdentityServiceProvider",
                action="adminCreateUser",
                parameters={
                    "UserPoolId": self.user_pool.user_pool_id,
                    "Username": default_user_email,
                    "MessageAction": "SUPPRESS",
                    # TemporaryPassword intentionally omitted: Cognito generates one
                    # internally and it is never used because the custom resource
                    # (SetDemoUserPassword) sets the permanent password immediately.
                    "UserAttributes": [
                        {"Name": "email", "Value": default_user_email},
                        {"Name": "email_verified", "Value": "true"}
                    ]
                },
                physical_resource_id=custom_resource.PhysicalResourceId.of("default-user-create")
            ),
            policy=custom_resource.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=[
                        "cognito-idp:AdminCreateUser",
                    ],
                    resources=[self.user_pool.user_pool_arn]
                )
            ])
        )

        # Dependency order: DefaultUserResource → secret → SetDemoUserPassword
        # (the secret already depends on nothing at synth; set_demo_user_password
        # must wait for the user to exist before it can set the password).
        set_demo_user_password.node.add_dependency(default_user_resource)
        set_demo_user_password.node.add_dependency(demo_password_secret)

        # Add default user to fleet-operator group.
        #
        # De-privilege 2026-08-07 (spec 2026-08-05-cms-demo-identity-model, Group A2):
        #   FleetManager@example.com no longer holds platform-admin.  fleet-operator is
        #   the minimum group that enables fleet read+write (including OEM1 enroll/unenroll
        #   per admin_bulk_enroll/handler.py:329) without cross-fleet admin, fleet-create,
        #   or user-management surfaces.  Admin demo surfaces are reserved for a separately-
        #   named account not targeted by any convenience affordance.
        #
        # COORDINATION with deprivilege_demo_personas.py:
        #   This on_create fires only when CDK creates the resource (new deployments or
        #   resource replacement).  For the live staging pool, the script
        #   deployment/scripts/deprivilege_demo_personas.py --stage staging --apply
        #   performs the actual platform-admin removal + fleet-operator addition on the
        #   existing account.  CDK and the script converge to the same target state.
        add_to_admin_group_resource = custom_resource.AwsCustomResource(
            self, "DefaultUserAdminGroupResource",
            on_create=custom_resource.AwsSdkCall(
                service="CognitoIdentityServiceProvider",
                action="adminAddUserToGroup",
                parameters={
                    "UserPoolId": self.user_pool.user_pool_id,
                    "Username": default_user_email,
                    "GroupName": "fleet-operator",
                },
                physical_resource_id=custom_resource.PhysicalResourceId.of("default-user-admin-group")
            ),
            policy=custom_resource.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=["cognito-idp:AdminAddUserToGroup"],
                    resources=[self.user_pool.user_pool_arn]
                )
            ])
        )
        add_to_admin_group_resource.node.add_dependency(set_demo_user_password)

        # -----------------------------------------------------------------------
        # Persona passwords for agent1, engineer, and kevin.dispatch
        # -----------------------------------------------------------------------
        # The three non-FleetManager demo personas each need a permanent password
        # applied to their Cognito account.  Their passwords live in the EXISTING
        # JSON secret `cms-<stage>-demo-persona-passwords` (keyed by email),
        # created and rotated 2026-08-05 by rotate_demo_login.py.
        #
        # ADOPTION CONTRACT (binding — must not be violated):
        #   We ADOPT this secret via Secret.from_secret_name_v2 rather than
        #   declaring a new `secretsmanager.Secret(...)` over the same name.
        #   Declaring a new Secret over an existing name would:
        #     (a) replace the secret, rotating the value CloudFormation generates,
        #         invalidating the 2026-08-05 logins; and
        #     (b) emit a AWS::SecretsManager::Secret with a GenerateSecretString
        #         that replaces the live JSON structure (keyed-by-email) with a
        #         flat random string — breaking the handler's JSON parse.
        #   Secret.from_secret_name_v2 produces NO CloudFormation resource in the
        #   template; it resolves at deploy time via a dynamic reference.
        #
        # on_update keying:
        #   `SecretVersionId` is resolved at synth time by _lookup_secret_version_id
        #   (same helper used for the FleetManager secret above).  When the secret
        #   is rotated (via rotate_demo_login.py --apply) the AWSCURRENT version id
        #   changes, so the next cdk synth sees a property diff → CloudFormation
        #   sends Update → the handler re-applies the new value to Cognito.
        #   This is identical to the FleetManager construct's keying, but uses the
        #   persona-passwords secret name.
        #
        # Spec ref: .kiro/specs/2026-08-05-cms-demo-identity-model/ task
        #   "Extend the CDK persona-password construct to all four personas"
        # Predecessor spec that shipped the FleetManager pattern:
        #   .kiro/specs/2026-08-04-cms-demo-credential-out-of-template/
        # -----------------------------------------------------------------------

        # Adopt the shared persona-passwords secret (JSON keyed by email).
        # No CloudFormation resource is created; the ARN is resolved via a
        # dynamic reference at deploy time.
        demo_persona_passwords_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "DemoPersonaPasswordsSecret",
            secret_name=f"cms-{stage}-demo-persona-passwords",
        )

        # IAM role shared across the three persona-password setter Lambdas.
        # AWSLambdaBasicExecutionRole covers CloudWatch Logs.
        # Two additional statements scoped to exact ARNs:
        #   - GetSecretValue on the one persona-passwords secret
        #   - AdminSetUserPassword on the one user pool
        demo_persona_password_setter_role = iam.Role(
            self,
            "DemoPersonaPasswordSetterRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
        )
        demo_persona_password_setter_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                # The `-??????` suffix wildcard is REQUIRED, not defensive.
                #
                # `Secret.from_secret_name_v2()` builds `.secret_arn` from the secret
                # NAME, so it carries no 6-character random suffix — Secrets Manager
                # appends one at creation. IAM ARN matching is exact with no implicit
                # trailing wildcard, so granting the suffix-less ARN authorises NOTHING:
                # GetSecretValue returns AccessDeniedException against the real secret.
                #
                # This is not hypothetical. Without the wildcard the prod deploy of
                # 2026-08-10T17:00Z failed — all three SetDemoPersonaPassword custom
                # resources raised ClientError and CloudFormation rolled the stack back,
                # which also reverted the FleetAPIFunction fail-open security fix that
                # deploy existed to ship. Re-deployed successfully at 17:12Z with this
                # wildcard in place. See
                # issues/2026-08-10-cms-persona-secret-arn-suffix-iam-mismatch/.
                #
                # The sibling `DemoPasswordSetterRole` above does not need this because
                # its secret is CDK-CREATED, so `Ref` resolves to the complete ARN. The
                # difference is adopted-vs-created, and it is easy to miss when copying
                # the working pattern onto an adopted secret.
                resources=[f"{demo_persona_passwords_secret.secret_arn}-??????"],
            )
        )
        demo_persona_password_setter_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cognito-idp:AdminSetUserPassword"],
                resources=[self.user_pool.user_pool_arn],
            )
        )

        # Explicit log group — bounded retention so a future logging mistake does
        # not write to a never-expire group.
        demo_persona_password_setter_logs = logs.LogGroup(
            self,
            "DemoPersonaPasswordSetterLogs",
            log_group_name=f"/aws/lambda/cms-{stage}-demo-persona-password-setter",
            retention=logs.RetentionDays.ONE_WEEK,
        )

        # Inline Lambda: reads the JSON persona-passwords secret, extracts the
        # password for the named email, and calls adminSetUserPassword.
        #
        # Handler contract (same as DemoPasswordSetterFn above):
        #   - Reads SecretString as JSON, extracts password for Username.
        #   - Never logs the password value or any derivative.
        #   - Returns no Data (nothing retrievable via Fn::GetAtt).
        #   - Responds on every path including exceptions.
        #   - Scrubs exception text before passing to cfnresponse reason=.
        #   - Delete is a no-op success.
        #   - KeyError (email not in JSON) → FAILED with safe_reason, not a crash.
        demo_persona_password_setter_fn = lambda_.Function(
            self,
            "DemoPersonaPasswordSetterFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            role=demo_persona_password_setter_role,
            timeout=Duration.minutes(2),
            log_group=demo_persona_password_setter_logs,
            code=lambda_.Code.from_inline(
                """
import boto3
import cfnresponse
import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def handler(event, context):
    request_type = event.get('RequestType', '')
    props = event.get('ResourceProperties', {})
    secret_arn = props.get('SecretArn', '')
    user_pool_id = props.get('UserPoolId', '')
    username = props.get('Username', '')
    version_id = props.get('SecretVersionId', '')

    # Delete is a no-op: the Cognito user is removed with the pool.
    if request_type == 'Delete':
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        return

    logger.info(
        'DemoPersonaPasswordSetter: RequestType=%s Username=%s SecretVersionId=%s',
        request_type, username, version_id,
    )
    try:
        sm = boto3.client('secretsmanager')
        raw = sm.get_secret_value(SecretId=secret_arn)['SecretString']
        passwords = json.loads(raw)

        if username not in passwords:
            safe_reason = (
                f'DemoPersonaPasswordSetter: Username {username!r} not found in '
                'persona-passwords secret (KeyError). Ensure rotate_demo_login.py '
                'has been run for this persona.'
            )
            logger.error('DemoPersonaPasswordSetter: %s', safe_reason)
            cfnresponse.send(event, context, cfnresponse.FAILED, {}, reason=safe_reason)
            return

        password = passwords[username]

        cognito = boto3.client('cognito-idp')
        cognito.admin_set_user_password(
            UserPoolId=user_pool_id,
            Username=username,
            Password=password,
            Permanent=True,
        )
        logger.info('DemoPersonaPasswordSetter: password set successfully for %s', username)
        # Return no Data — nothing must be retrievable via Fn::GetAtt.
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
    except Exception as exc:
        # Scrub the exception text before surfacing it in CloudFormation events.
        # botocore InvalidPasswordException can echo policy detail; a naive
        # str(exc) in this path is the most likely accidental-disclosure route.
        exc_type = type(exc).__name__
        safe_reason = f'DemoPersonaPasswordSetter failed: {exc_type} (details redacted)'
        logger.error('DemoPersonaPasswordSetter error: %s', exc_type)
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, reason=safe_reason)
"""
            ),
        )

        # Resolve the AWSCURRENT version id of the persona-passwords secret.
        # Same degrade-to-"" contract as the FleetManager helper above:
        #   - First deploy or no credentials → "" → spurious-but-idempotent Update
        #   - After rotate_demo_login.py --apply → new version id → real Update
        persona_passwords_version_id = _lookup_secret_version_id(
            f"cms-{stage}-demo-persona-passwords", self.region
        )

        # Persona accounts that live in the persona-passwords JSON secret.
        # Tuple: (logical_id_suffix, email, group_to_add)
        # group_to_add may be None if the persona's group assignment is
        # handled elsewhere or is not yet stable across both stages.
        _persona_accounts = [
            ("Agent",       "agent1@cms-fleet.io",           "agent"),
            ("Engineer",    "engineer@example.com",           "product-engineer"),
            ("Dispatcher",  "kevin.dispatch@example.com",    "dispatcher"),
        ]

        for _suffix, _email, _group in _persona_accounts:
            _set_password_resource = CustomResource(
                self,
                f"SetDemoPersonaPassword{_suffix}",
                service_token=demo_persona_password_setter_fn.function_arn,
                resource_type="Custom::CmsDemoPersonaPassword",
                properties={
                    # SecretArn: the adopted persona-passwords secret ARN.
                    # Allowed in _CREDENTIAL_KEY_ALLOWLIST (ARN, not a value).
                    "SecretArn": demo_persona_passwords_secret.secret_arn,
                    "UserPoolId": self.user_pool.user_pool_id,
                    # Username identifies which JSON key to extract.
                    "Username": _email,
                    # SecretVersionId keys on_update so a rotation triggers
                    # a CloudFormation Update → handler re-applies the new value.
                    "SecretVersionId": persona_passwords_version_id,
                },
            )
            # Dependency: the persona user must exist before the password is set.
            # The three personas are seeded by seed_persona_users.py outside CDK,
            # so there is no create-user custom resource to depend on here.
            # The password-setter depends on the adopted secret reference being
            # resolved, which is implicit (from_secret_name_v2 has no construct
            # dependency to add).

        # Location Services resources are created above
        
        CfnOutput(
            self, "UserPoolId",
            value=self.user_pool.user_pool_id,
            export_name=f"{construct_id}-user-pool-id"
        )
        
        CfnOutput(
            self, "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
            export_name=f"{construct_id}-user-pool-client-id"
        )
        
        CfnOutput(
            self, "IdentityPoolId",
            value=self.identity_pool.ref,
            export_name=f"{construct_id}-identity-pool-id"
        )
        
        CfnOutput(
            self, "CloudFrontURL",
            value=f"https://{self.distribution.distribution_domain_name}",
            export_name=f"{construct_id}-cloudfront-url"
        )

        # Surface the friendly URL when a custom domain is configured.
        # Operators typically share this URL with users instead of the
        # CloudFront default domain. Gated on `ui_custom_domain_attached`
        # so a region-guarded skip does NOT publish a misleading URL that
        # points at the home-region distribution.
        if ui_custom_domain_attached:
            CfnOutput(
                self, "CustomDomainURL",
                value=f"https://{ui_custom_domain}",
                export_name=f"{construct_id}-custom-domain-url"
            )
        
        CfnOutput(
            self, "APIEndpoint",
            value=self.api.url,
            export_name=f"{construct_id}-api-endpoint"
        )
        
        # Default user credentials
        CfnOutput(
            self, "DefaultUserEmail",
            value=default_user_email,
            description="Default user email for Fleet Manager login"
        )
        
        CfnOutput(
            self, "DemoUserPasswordSecretArn",
            value=demo_password_secret.secret_arn,
            description=(
                "ARN of the Secrets Manager secret holding the demo user password. "
                "Retrieve with: aws secretsmanager get-secret-value "
                "--secret-id cms-<stage>-demo-user-password --query SecretString --output text"
            ),
            export_name=f"{construct_id}-demo-password-secret-arn",
        )
        
        CfnOutput(
            self, "RouteCalculatorName",
            value=self.route_calculator_name,
            description="Location Services route calculator name for telemetry simulation"
        )
        
        # Location Services outputs
        CfnOutput(
            self, "LocationServicesMapName",
            value=self.map.ref,
            description="Amazon Location Services Map name",
            export_name=f"{construct_id}-map-name"
        )
        
        CfnOutput(
            self, "LocationServicesPlaceIndexName", 
            value=self.place_index.ref,
            description="Amazon Location Services Place Index name",
            export_name=f"{construct_id}-place-index-name"
        )

        # Synth-time guard: fail closed if any plaintext credential reaches the
        # template.  Called LAST so all constructs are in the tree.
        # See _assert_no_plaintext_credential_in_template docstring for why this is
        # non-optional and why it implements BOTH the value check and the shape check.
        _assert_no_plaintext_credential_in_template(self)
