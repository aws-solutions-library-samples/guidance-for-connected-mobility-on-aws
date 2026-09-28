# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Connected Services subscription plane — data model + API surface.

Spec `2026-09-10-cms-connected-services-subscriptions`, T1.2, T3.0, T3.1, T3.3, T5.1.

## Scope of this stack today

Two DynamoDB tables (T1.2 + T3.1), one REST API with a Cognito authorizer, and
eight Lambdas covering the nine routes Groups 1-3 + T5.1 built:

    POST   /subscriptions
    GET    /subscriptions
    GET    /subscriptions/{id}
    POST   /subscriptions/{id}/scope
    DELETE /subscriptions/{id}/scope/{vin}
    GET    /subscriptions/{id}/records
    GET    /products
    GET    /vehicles/available
    POST   /admin/subscriptions/vehicles/{vin}/available
    POST   /admin/subscribers

Plus one stream-triggered Lambda that acts on `MODIFY`/`INSERT` events on the
vehicles table (T3.3).

## Why one API here and not additions to `ui_stack.py`

Spec Constraints and RESUME.md's concurrency note both call out `ui_stack.py`
as a contended file — five other specs are active in this repo, and touching
it entangles this diff with theirs. `commands_stack.py` set the same precedent
by owning its own `CommandsAPI` rather than mounting into the primary
`CMSAPI`, and its rationale applies verbatim here.

## IAM discipline

Every IAM statement is scoped **exactly** to the table (or index) it needs —
no wildcard resources, no `dynamodb:*`. Each Lambda has its own role, not a
shared one, so a review can inspect the six route surfaces independently. A
single wildcard `LambdaPermission` per Lambda on `api.arn_for_execute_api()`
keeps the CloudFormation template size within API-Gateway limits without
widening what any Lambda can do (the permission grants API Gateway the right
to invoke the Lambda, not the Lambda anything).

## T1.2's original scope note

T1.2 was "table-only" and did not create Lambdas. T3.0 completes that layer by
adding the missing route wiring — the gap RESUME.md's blocker 2 identified.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from aws_cdk import (
    CfnOutput,
    Duration,
    Fn,
    RemovalPolicy,
    Stack,
    aws_apigateway as apigateway,
    aws_cognito as cognito,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
)
from constructs import Construct

#: GSI serving the "my subscriptions" list query — spec D3 calls this "GSI1".
#: Named descriptively rather than positionally to match the repo's existing
#: index naming (`CustomerIdIndex`, `SubmittedByIndex`, `vehicleId-index`).
#: Consumers must reference this constant, not a literal, so the name cannot
#: drift between the table definition and the query that depends on it.
CONSUMER_ID_INDEX = "ConsumerIdIndex"

#: GSI on VehicleAvailability: `sold_to` HASH + `vin` RANGE, projection
#: KEYS_ONLY.  Added T0.4 (spec `2026-09-14-cs-portal-data-model-backend`).
#: Replaces the full-table Scan in `vehicles_available/handler.py` with a
#: per-customer Query so entitlement is enforced at the storage layer.
#: `vin` as range key preserves VIN sort order so the `?after=` cursor stays
#: deterministic per customer without a client-side re-sort.
#: KEYS_ONLY: the query projects only `vin` today, so anything beyond
#: {sold_to, vin} is write amplification for data nothing reads.
SOLD_TO_INDEX = "SoldToIndex"

#: Bundle-time asset staging.
#:
#: Lambdas in `services/connectors/subscriptions/<handler_dir>/` need `_lib/`
#: alongside their `handler.py`. We stage each handler dir into a build folder
#: and overlay `_lib/` in — the same pattern `commands_stack._bundle_commands_lambda`
#: uses, keeping `_lib/` as its own source-of-truth.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBSCRIPTIONS_SRC = os.path.abspath(os.path.join(_HERE, "..", "..", "services", "connectors", "subscriptions"))
_LIB_SRC = os.path.join(_SUBSCRIPTIONS_SRC, "_lib")
_PRODUCTS_JSON_SRC = os.path.join(_SUBSCRIPTIONS_SRC, "products.json")
_BUILD_ROOT = os.path.join(_HERE, ".build", "subscriptions_lambdas")


def _bundle_subscriptions_lambda(handler_dir_name: str) -> str:
    """Stage a subscriptions Lambda's asset with the `_lib/` overlay.

    Copies `services/connectors/subscriptions/<handler_dir_name>/` into
    `deployment/stacks/.build/subscriptions_lambdas/<handler_dir_name>/`,
    overlays `_lib/` at `_lib/`, and copies `products.json` alongside
    (loaded by `subscription_crud.handler._load_catalog` and
    `products.handler._load_catalog`, which resolve it relative to the
    parent dir of the handler).

    Re-stages on every synth so source edits are picked up. Returns the
    absolute path to the staged asset directory.
    """
    src = os.path.join(_SUBSCRIPTIONS_SRC, handler_dir_name)
    if not os.path.isdir(src):
        raise FileNotFoundError(f"handler source not found: {src}")
    if not os.path.isdir(_LIB_SRC):
        raise FileNotFoundError(f"_lib source not found: {_LIB_SRC}")

    dst = os.path.join(_BUILD_ROOT, handler_dir_name)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(dst, exist_ok=True)

    # Copy handler `.py` files (skip `tests/`, `__pycache__`).
    for f in os.listdir(src):
        fp = os.path.join(src, f)
        if os.path.isfile(fp) and f.endswith(".py") and f != "__init__.py":
            shutil.copy2(fp, dst)
    # `__init__.py` is expected — copy an empty one if the source lacks it.
    if not os.path.isfile(os.path.join(dst, "__init__.py")):
        with open(os.path.join(dst, "__init__.py"), "w", encoding="utf-8"):
            pass

    # Overlay `_lib/` under the handler dir. The handlers use a fallback
    # `sys.path` insert (`try: from _lib... except ModuleNotFoundError`) that
    # looks one dir up from the handler file. Because Lambda's asset is the
    # handler dir itself, we drop `_lib/` at the same level as `handler.py`
    # for the primary import path, then leave the fallback for local tests.
    shutil.copytree(
        _LIB_SRC,
        os.path.join(dst, "_lib"),
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "tests", "*.pyc", ".pytest_cache"),
    )

    # `products.json` is loaded by `subscription_crud` and `products` handlers,
    # both of which check same-dir-as-handler first (Lambda flat-bundle layout)
    # then parent-dir (source-tree layout) — see either handler's
    # `_load_catalog` docstring. Only same-dir needs to exist in the deployed
    # asset; copying to `parent` here landed one level ABOVE the asset root
    # actually uploaded to Lambda and was never live at runtime. Fixed
    # 2026-09-12 — see issues/2026-09-12-products-catalog-flat-bundle-path-500/.
    if os.path.isfile(_PRODUCTS_JSON_SRC):
        shutil.copy2(_PRODUCTS_JSON_SRC, os.path.join(dst, "products.json"))

    # Install requirements if present.
    req = os.path.join(src, "requirements.txt")
    if os.path.isfile(req):
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", req,
             "-t", dst, "-q", "--upgrade"],
            check=True,
        )

    return os.path.abspath(dst)


class SubscriptionsStack(Stack):
    """The Connected Services subscription-plane stack."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        stage: str | None = None,
        vehicles_table_arn: str | None = None,
        vehicles_table_stream_arn: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stage = stage or os.environ.get("DEPLOYMENT_STAGE", "dev")

        # Cross-stack references from `storage_stack.py` (`cms-{stage}-storage`):
        # the vehicles table lives there, and this stack's availability listener
        # needs its stream ARN (T3.3). Imports keep the two stacks decoupled at
        # code level — this stack does not construct any Table object it does
        # not own.
        storage_construct_id = f"cms-{stage}-storage"
        # Actual vehicles table shipped by storage_stack.py is
        # `cms-{stage}-storage-vehicles`, not `cms-{stage}-vehicles`. Bug in
        # the original Groups 1-3 code (commit 3d543488) that survived both
        # review gates because the vehicles_available unit tests mocked the
        # same wrong string. See issues/2026-09-11-subscriptions-vehicles-
        # table-name-mismatch/. Fixed 2026-09-11 during T5.1 deploy verification.
        vehicles_table_name = f"cms-{stage}-storage-vehicles"
        vehicles_table_arn = vehicles_table_arn or (
            f"arn:aws:dynamodb:{self.region}:{self.account}:table/{vehicles_table_name}"
        )
        # `table_stream_arn` is exported by storage_stack (see its
        # `VehiclesTableStreamArn` output). Prefer the injected ARN when the
        # caller supplies one (unit tests, alternate wiring); fall back to the
        # cross-stack import for the normal deploy path.
        vehicles_stream_arn = (
            vehicles_table_stream_arn
            or Fn.import_value(f"{storage_construct_id}-vehicles-stream-arn")
        )
        telemetry_table_name = f"cms-{stage}-storage-telemetry"
        telemetry_table_arn = (
            f"arn:aws:dynamodb:{self.region}:{self.account}:table/{telemetry_table_name}"
        )
        # Maintenance-alerts table (diagnostics product, T4.1).
        # Base PK is alertId; records are queried via GSI vehicleId-timestamp-index.
        # The table is live on staging with 3,704 items (verified 2026-09-12 D8).
        maintenance_alerts_table_name = f"cms-{stage}-storage-maintenance-alerts"
        maintenance_alerts_table_arn = (
            f"arn:aws:dynamodb:{self.region}:{self.account}"
            f":table/{maintenance_alerts_table_name}"
        )
        # Charging-sessions table (charging product, T4.2d).
        # Base PK is vehicleId; records are queried on sessionStartTime (ISO-8601
        # sort key, dispatched via sort_key_kind="iso8601" in source_dispatch.py).
        # No GSI is used — the table is queried directly on the base PK + SK.
        # Live on staging with 10 seeded rows (verified T4.2d).
        charging_sessions_table_name = f"cms-{stage}-storage-charging-sessions"
        charging_sessions_table_arn = (
            f"arn:aws:dynamodb:{self.region}:{self.account}"
            f":table/{charging_sessions_table_name}"
        )

        # ── Meridian ingestion cross-stack refs — REMOVED 2026-09-19 ──────
        # Was: spec 2026-09-11-cms-cs-meridian-ingestion's Pattern-2 side-
        # loading pipeline (MeridianIngestionStack -> cs-source-telemetry
        # DynamoDB table, written to directly by a puller Lambda, bypassing
        # cms-telemetry-preprocessed entirely). Removed per
        # docs/data-products-design.md §6.3 ("No side-loading... A direct
        # row write bypasses cms-telemetry-preprocessed and produces no
        # trips, no safety events, no maintenance alerts and no geofence
        # hits — silently"). The correct, Kafka-native Meridian pipeline
        # (MSK topic cs-product-meridian-ev -> generic OEMTelemetryProcessor)
        # is unrelated to this stack and is untouched by this removal — see
        # issues/2026-09-19-remove-meridian-side-loading-pipeline/.

        # ── Subscription table (spec D3, T1.2) ────────────────────────────
        self.subscription_table = dynamodb.Table(
            self,
            "SubscriptionTable",
            table_name=f"cms-{stage}-storage-subscriptions-{self.region}-{self.account}",
            partition_key=dynamodb.Attribute(
                name="subscription_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
        )
        self.subscription_table.add_global_secondary_index(
            index_name=CONSUMER_ID_INDEX,
            partition_key=dynamodb.Attribute(
                name="consumer_id",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ── VehicleAvailability table (spec D3, T3.1) ─────────────────────
        self.vehicle_availability_table = dynamodb.Table(
            self,
            "VehicleAvailabilityTable",
            table_name=f"cms-{stage}-storage-vehicle-availability-{self.region}-{self.account}",
            partition_key=dynamodb.Attribute(
                name="vin",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
        )
        # ── SoldToIndex GSI (T0.4, spec 2026-09-14-cs-portal-data-model-backend) ──
        # Additive: base table key schema (vin HASH) is UNCHANGED — KeySchema is
        # immutable, and the table is RETAIN'd, so a re-key would force a
        # Replacement that deadlocks on the retained table's name. See
        # decisions.md § "Availability scoping" finding 4.
        #
        # `sold_to` HASH + `vin` RANGE. The range key preserves VIN sort order
        # so `_fetch_availability_vins`'s `?after=` cursor stays deterministic
        # per customer without a client-side re-sort.
        # KEYS_ONLY: the query projects only `vin`, so anything beyond
        # {sold_to, vin} is write amplification for data nothing reads.
        #
        # A row without `sold_to` is absent from this index (sparse GSI),
        # making it invisible to every customer-scoped query — fail-closed
        # and correct for the 4 existing un-attributed rows.
        self.vehicle_availability_table.add_global_secondary_index(
            index_name=SOLD_TO_INDEX,
            partition_key=dynamodb.Attribute(
                name="sold_to",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="vin",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.KEYS_ONLY,
        )

        # ── Table CfnOutputs (kept from T1.2 + T3.1) ──────────────────────
        CfnOutput(
            self, "SubscriptionTableName",
            value=self.subscription_table.table_name,
            description=(
                "Subscription plane table. Consumers read this as "
                "SUBSCRIPTION_PLANE_TABLE_NAME — deliberately NOT "
                "SUBSCRIPTIONS_TABLE_NAME, an unrelated pre-existing table "
                "(docs/tech.md finding F2)."
            ),
            export_name=f"{construct_id}-subscription-table-name",
        )
        CfnOutput(
            self, "SubscriptionTableArn",
            value=self.subscription_table.table_arn,
            description="Subscription plane table ARN",
            export_name=f"{construct_id}-subscription-table-arn",
        )
        CfnOutput(
            self, "SubscriptionConsumerIdIndexName",
            value=CONSUMER_ID_INDEX,
            description="GSI1 (spec D3) - consumer_id hash + created_at range",
            export_name=f"{construct_id}-subscription-consumer-index-name",
        )
        CfnOutput(
            self, "VehicleAvailabilityTableName",
            value=self.vehicle_availability_table.table_name,
            description="Vehicle availability index - VEHICLE_AVAILABILITY_TABLE_NAME.",
            export_name=f"{construct_id}-vehicle-availability-table-name",
        )
        CfnOutput(
            self, "VehicleAvailabilityTableArn",
            value=self.vehicle_availability_table.table_arn,
            description="Vehicle availability index ARN",
            export_name=f"{construct_id}-vehicle-availability-table-arn",
        )

        # ── Cognito authorizer (T3.0) ─────────────────────────────────────
        # Imports the CMS user-pool ARN via cross-stack ref, same pattern as
        # `commands_stack.py:378-381`. No separate pool — the `subscriber` and
        # `connected-services` groups live in the CMS pool.
        ui_construct_id = f"cms-{stage}-ui"
        user_pool_arn = Fn.import_value(f"{ui_construct_id}-user-pool-arn")
        user_pool = cognito.UserPool.from_user_pool_arn(
            self, "ImportedUserPool", user_pool_arn
        )
        cognito_authorizer = apigateway.CognitoUserPoolsAuthorizer(
            self, "SubscriptionsCognitoAuthorizer",
            cognito_user_pools=[user_pool],
            authorizer_name=f"{construct_id}-cognito-auth",
        )
        auth_kwargs = dict(
            authorizer=cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
        )

        # ── REST API (T3.0) ───────────────────────────────────────────────
        api = apigateway.RestApi(
            self, "SubscriptionsApi",
            rest_api_name=f"cms-{stage}-subscriptions-api",
            description="CMS Connected Services subscription plane",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"],
            ),
        )
        # Gateway responses on 4XX/5XX so browsers don't mask auth denials as
        # opaque CORS failures. Same pattern `ui_stack.py:2244-2258` uses.
        for resp_type in [
            apigateway.ResponseType.DEFAULT_4_XX,
            apigateway.ResponseType.DEFAULT_5_XX,
        ]:
            api.add_gateway_response(
                f"GatewayResponse{resp_type.response_type}",
                type=resp_type,
                response_headers={
                    "Access-Control-Allow-Origin": "'*'",
                    "Access-Control-Allow-Headers": "'Content-Type,Authorization'",
                    "Access-Control-Allow-Methods": "'GET,POST,PUT,DELETE,OPTIONS'",
                },
            )

        # ── Shared Lambda env ─────────────────────────────────────────────
        _common_env = {
            "DEPLOYMENT_STAGE": stage,
            "SUBSCRIPTION_PLANE_TABLE_NAME": self.subscription_table.table_name,
            "SUBSCRIPTION_CONSUMER_INDEX": CONSUMER_ID_INDEX,
            "VEHICLE_AVAILABILITY_TABLE_NAME": self.vehicle_availability_table.table_name,
            "VEHICLE_AVAILABILITY_SOLD_TO_INDEX": SOLD_TO_INDEX,
            "VEHICLES_TABLE_NAME": vehicles_table_name,
            "TELEMETRY_TABLE_NAME": telemetry_table_name,
        }

        # ── Helper: per-Lambda role + narrow log grants ───────────────────
        def _role(role_id: str, function_name: str) -> iam.Role:
            r = iam.Role(
                self, role_id,
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                description=f"{construct_id} - {role_id}",
            )
            r.add_to_policy(iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/{function_name}",
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/{function_name}:*",
                ],
            ))
            return r

        # ── Route helper ──────────────────────────────────────────────────
        def _wire(fn: lambda_.Function) -> apigateway.LambdaIntegration:
            integration = apigateway.LambdaIntegration(fn, allow_test_invoke=False)
            fn.add_permission(
                "SubscriptionsApiInvoke",
                principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
                source_arn=api.arn_for_execute_api(),
            )
            return integration

        # =====================================================================
        # subscription_crud — POST/GET /subscriptions, GET /subscriptions/{id}
        # =====================================================================
        crud_fn_name = f"cms-{stage}-subscriptions-crud"
        crud_role = _role("SubscriptionsCrudRole", crud_fn_name)
        crud_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem", "dynamodb:GetItem"],
            resources=[self.subscription_table.table_arn],
        ))
        crud_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[f"{self.subscription_table.table_arn}/index/{CONSUMER_ID_INDEX}"],
        ))
        crud_asset_path = _bundle_subscriptions_lambda("subscription_crud")

        crud_create = lambda_.Function(
            self, "SubscriptionCrudCreate",
            function_name=f"{crud_fn_name}-create",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.create_handler",
            code=lambda_.Code.from_asset(crud_asset_path),
            role=crud_role,
            timeout=Duration.seconds(15),
            memory_size=256,
            environment=_common_env,
        )
        crud_list = lambda_.Function(
            self, "SubscriptionCrudList",
            function_name=f"{crud_fn_name}-list",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.list_handler",
            code=lambda_.Code.from_asset(crud_asset_path),
            role=crud_role,
            timeout=Duration.seconds(15),
            memory_size=256,
            environment=_common_env,
        )
        crud_detail = lambda_.Function(
            self, "SubscriptionCrudDetail",
            function_name=f"{crud_fn_name}-detail",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.detail_handler",
            code=lambda_.Code.from_asset(crud_asset_path),
            role=crud_role,
            timeout=Duration.seconds(15),
            memory_size=256,
            environment=_common_env,
        )

        # =====================================================================
        # subscription_scope — POST/DELETE /subscriptions/{id}/scope[...]
        # =====================================================================
        scope_fn_name = f"cms-{stage}-subscriptions-scope"
        scope_role = _role("SubscriptionsScopeRole", scope_fn_name)
        scope_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:UpdateItem", "dynamodb:GetItem"],
            resources=[self.subscription_table.table_arn],
        ))
        scope_asset_path = _bundle_subscriptions_lambda("subscription_scope")

        scope_add = lambda_.Function(
            self, "SubscriptionScopeAdd",
            function_name=f"{scope_fn_name}-add",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.add_handler",
            code=lambda_.Code.from_asset(scope_asset_path),
            role=scope_role,
            timeout=Duration.seconds(15),
            memory_size=256,
            environment=_common_env,
        )
        scope_remove = lambda_.Function(
            self, "SubscriptionScopeRemove",
            function_name=f"{scope_fn_name}-remove",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.remove_handler",
            code=lambda_.Code.from_asset(scope_asset_path),
            role=scope_role,
            timeout=Duration.seconds(15),
            memory_size=256,
            environment=_common_env,
        )

        # =====================================================================
        # subscription_records — GET /subscriptions/{id}/records
        # =====================================================================
        records_fn_name = f"cms-{stage}-subscriptions-records"
        records_role = _role("SubscriptionsRecordsRole", records_fn_name)
        # Subscription table: GetItem for the row, UpdateItem for the quota
        # reservation.
        records_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:UpdateItem"],
            resources=[self.subscription_table.table_arn],
        ))
        # Vehicles table's vin-index GSI for VIN -> vehicleId resolution.
        records_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[f"{vehicles_table_arn}/index/vin-index"],
        ))
        # Telemetry table: read-only Query for the actual data.
        records_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[telemetry_table_arn],
        ))
        # Maintenance-alerts table + its GSI: read-only Query for the diagnostics
        # product (T4.1). Scoped to both the base table ARN (required by DynamoDB
        # even when only the GSI is queried) and the GSI ARN itself. No wildcard.
        records_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[
                maintenance_alerts_table_arn,
                f"{maintenance_alerts_table_arn}/index/vehicleId-timestamp-index",
            ],
        ))
        # Charging-sessions table: read-only Query for the charging product (T4.2d).
        # No GSI is used — records are queried on the base PK (vehicleId) directly.
        # No Scan, no wildcard resource.
        records_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[charging_sessions_table_arn],
        ))
        # CS source telemetry Query grant — REMOVED 2026-09-19, same removal
        # as the cross-stack refs above. See that comment for the full
        # rationale and issues/2026-09-19-remove-meridian-side-loading-pipeline/.
        # Env var — records handler's source_dispatch reads this when the
        # product's `source` field is `cs-meridian`.
        records_env = dict(_common_env)
        # Env var — records handler reads this for the diagnostics product (T4.1).
        records_env["MAINTENANCE_ALERTS_TABLE_NAME"] = maintenance_alerts_table_name
        # Env var — records handler reads this for the charging product (T4.2d).
        records_env["CHARGING_SESSIONS_TABLE_NAME"] = charging_sessions_table_name
        records_asset_path = _bundle_subscriptions_lambda("subscription_records")
        records_fn = lambda_.Function(
            self, "SubscriptionRecords",
            function_name=records_fn_name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.records_handler",
            code=lambda_.Code.from_asset(records_asset_path),
            role=records_role,
            timeout=Duration.seconds(30),
            memory_size=512,
            environment=records_env,
        )

        # =====================================================================
        # products — GET /products (catalog, Lambda-bundled JSON)
        # =====================================================================
        products_fn_name = f"cms-{stage}-subscriptions-products"
        products_role = _role("SubscriptionsProductsRole", products_fn_name)
        products_asset_path = _bundle_subscriptions_lambda("products")
        products_fn = lambda_.Function(
            self, "SubscriptionsProducts",
            function_name=products_fn_name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.products_handler",
            code=lambda_.Code.from_asset(products_asset_path),
            role=products_role,
            timeout=Duration.seconds(5),
            memory_size=128,
            environment=_common_env,
        )

        # =====================================================================
        # vehicles_available — GET /vehicles/available (T3.4)
        # =====================================================================
        vehicles_available_fn_name = f"cms-{stage}-subscriptions-vehicles-available"
        vehicles_available_role = _role("SubscriptionsVehiclesAvailableRole", vehicles_available_fn_name)
        # T0.4: replaced dynamodb:Scan with dynamodb:Query on the SoldToIndex GSI.
        # The base-table Scan grant is removed — `_fetch_availability_vins` now
        # queries the GSI per customer from `custom:customerIds`; a full-table
        # Scan is no longer in the handler.  The base-table ARN is still listed
        # alongside the GSI ARN because DynamoDB's IAM model requires it when
        # the action is against an index of that table (the service validates
        # against the base ARN).
        vehicles_available_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[
                self.vehicle_availability_table.table_arn,
                f"{self.vehicle_availability_table.table_arn}/index/{SOLD_TO_INDEX}",
            ],
        ))
        vehicles_available_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[f"{self.subscription_table.table_arn}/index/{CONSUMER_ID_INDEX}"],
        ))
        vehicles_available_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[f"{vehicles_table_arn}/index/vin-index"],
        ))
        # T0.5: vin-index is KEYS_ONLY — non-projected attributes (producer, sold_to)
        # are read from the base table via GetItem after resolving vehicleId.
        vehicles_available_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem"],
            resources=[vehicles_table_arn],
        ))
        vehicles_available_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[telemetry_table_arn],
        ))
        vehicles_available_asset_path = _bundle_subscriptions_lambda("vehicles_available")
        vehicles_available_fn = lambda_.Function(
            self, "VehiclesAvailable",
            function_name=vehicles_available_fn_name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.available_handler",
            code=lambda_.Code.from_asset(vehicles_available_asset_path),
            role=vehicles_available_role,
            timeout=Duration.seconds(30),
            memory_size=512,
            environment=_common_env,
        )

        # =====================================================================
        # admin_mark_available — POST /admin/subscriptions/vehicles/{vin}/available (T3.2)
        # =====================================================================
        admin_mark_fn_name = f"cms-{stage}-subscriptions-admin-mark-available"
        admin_mark_role = _role("SubscriptionsAdminMarkAvailableRole", admin_mark_fn_name)
        admin_mark_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:UpdateItem"],
            resources=[self.vehicle_availability_table.table_arn],
        ))
        # T0.4: admin_mark_available now looks up `sold_to` from the vehicles
        # table before calling mark_available(), so it can denormalise the
        # attribute onto the availability row.  Query on vin-index GSI resolves
        # VIN → vehicleId; base-table ARN is included per DynamoDB IAM rules.
        # GetItem on the base table fetches sold_to (vin-index is KEYS_ONLY).
        admin_mark_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[
                vehicles_table_arn,
                f"{vehicles_table_arn}/index/vin-index",
            ],
        ))
        admin_mark_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem"],
            resources=[vehicles_table_arn],
        ))
        admin_mark_asset_path = _bundle_subscriptions_lambda("admin_mark_available")
        admin_mark_fn = lambda_.Function(
            self, "SubscriptionsAdminMarkAvailable",
            function_name=admin_mark_fn_name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.mark_handler",
            code=lambda_.Code.from_asset(admin_mark_asset_path),
            role=admin_mark_role,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment=_common_env,
        )

        # =====================================================================
        # admin_provision_subscriber — POST /admin/subscribers (T5.1)
        #
        # T5.1 was promoted 2026-09-11 out of "droppable Group 5" into a hard
        # prerequisite for T3.7. See decisions.md 2026-09-11 for the reasoning.
        # The four Cognito operations are scoped exactly to the imported CMS
        # user pool ARN, mirroring the D6 persona split: this Lambda writes to
        # the pool via a per-Lambda role, never a shared one.
        # =====================================================================
        admin_provision_fn_name = f"cms-{stage}-subscriptions-admin-provision-subscriber"
        admin_provision_role = _role(
            "SubscriptionsAdminProvisionSubscriberRole", admin_provision_fn_name,
        )
        # Narrow the four AdminXxx actions to the imported pool ARN. No wildcard
        # resource, no `cognito-idp:*`. AdminGetUser covers the collision path
        # (UsernameExistsException -> 409 with existing status) and the
        # finalise-new-user path (fetch the sub of the freshly-created user).
        # AdminListGroupsForUser (added FG3.1) supports the collision-heal path:
        # detecting whether the existing user is in the `subscriber` group.
        admin_provision_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "cognito-idp:AdminCreateUser",
                "cognito-idp:AdminGetUser",
                "cognito-idp:AdminListGroupsForUser",
                "cognito-idp:AdminAddUserToGroup",
                "cognito-idp:AdminUpdateUserAttributes",
            ],
            resources=[user_pool.user_pool_arn],
        ))
        # Env carries USER_POOL_ID in addition to _common_env — the Lambda uses
        # the id (not the ARN) for AdminXxx calls, and the id is exposed via
        # CDK's imported-pool token so no extra ui_stack export is needed.
        admin_provision_env = {
            **_common_env,
            "USER_POOL_ID": user_pool.user_pool_id,
        }
        admin_provision_asset_path = _bundle_subscriptions_lambda("admin_provision_subscriber")
        admin_provision_fn = lambda_.Function(
            self, "SubscriptionsAdminProvisionSubscriber",
            function_name=admin_provision_fn_name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.provision_handler",
            code=lambda_.Code.from_asset(admin_provision_asset_path),
            role=admin_provision_role,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment=admin_provision_env,
        )

        # =====================================================================
        # simulate_vehicle — GET /simulate/vehicles + POST /simulate/start/{vid} (FG2.2)
        #
        # Deferred half of Fix Group 2 of spec 2026-09-14-cs-portal-data-model-backend.
        # Handler docstring in services/connectors/subscriptions/simulate_vehicle/handler.py
        # § "Route wiring — NOT YET WIRED (FG2.2)" enumerates the exact wiring
        # requirements this block satisfies:
        #   1. _require_operator is already the first call in both handler entrypoints
        #      (list_vehicles_handler line 325, simulate_start_handler line 367); the
        #      source-structural ratchet test test_wiring_requires_authz.py flips to its
        #      wired branch as soon as this block references "simulate_vehicle".
        #   2. IAM is scoped narrowly per operation — Scan for list, GetItem for start —
        #      via two separate roles so future changes can't accidentally widen either.
        #   3. Behavioural authz coverage is in test_handler.py's TestAuthorizationListVehicles
        #      and TestAuthorizationSimulateStart (7 cases each, mutation-verified).
        # =====================================================================
        simulate_asset_path = _bundle_subscriptions_lambda("simulate_vehicle")

        simulate_list_fn_name = f"cms-{stage}-subscriptions-simulate-list-vehicles"
        simulate_list_role = _role(
            "SubscriptionsSimulateListVehiclesRole", simulate_list_fn_name,
        )
        # Scan on the vehicles table — the list itself.
        simulate_list_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Scan"],
            resources=[vehicles_table_arn],
        ))
        # T5.0 readiness annotation (spec 2026-09-20-trip-intent-param-contract) reads
        # three further tables to compute `simulation_ready` / `not_ready_reasons` /
        # `ready_count`. Granted as three separate statements, one ARN each, rather than
        # a wildcard — the handler scans exactly these three and nothing else.
        #
        # Names mirror `simulate_vehicle/handler.py:336-342`, which resolves each from an
        # env var with these as defaults. If a deployment ever overrides those env vars,
        # these grants must move with them or readiness silently degrades.
        #
        # Added 2026-09-21 closing security-review S1. Without them the three fetchers
        # AccessDenied, each returns None, and EVERY vehicle reports
        # `simulation_ready: null` + `readiness_unavailable` — security-safe (the UI
        # leaves options selectable and `simulate_start_handler` stays authoritative) but
        # functionally dead: T5.0's whole output would be the unknown state on every row.
        # The live staging role held Scan on the vehicles table ONLY (verified read-only
        # 2026-09-21), so this was a real deploy-time regression waiting on the next
        # `make phase1`, not a theoretical gap.
        fleet_enrollment_table_arn = (
            f"arn:aws:dynamodb:{self.region}:{self.account}"
            f":table/cms-{stage}-storage-fleet-enrollment"
        )
        vehicle_certificates_table_arn = (
            f"arn:aws:dynamodb:{self.region}:{self.account}"
            f":table/cms-{stage}-storage-vehicle-certificates"
        )
        campaigns_table_arn = (
            f"arn:aws:dynamodb:{self.region}:{self.account}"
            f":table/cms-{stage}-campaigns"
        )
        for _readiness_table_arn in (
            fleet_enrollment_table_arn,
            vehicle_certificates_table_arn,
            campaigns_table_arn,
        ):
            simulate_list_role.add_to_policy(iam.PolicyStatement(
                actions=["dynamodb:Scan"],
                resources=[_readiness_table_arn],
            ))
        simulate_list_fn = lambda_.Function(
            self, "SubscriptionsSimulateListVehicles",
            function_name=simulate_list_fn_name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.list_vehicles_handler",
            code=lambda_.Code.from_asset(simulate_asset_path),
            role=simulate_list_role,
            timeout=Duration.seconds(15),
            memory_size=256,
            environment=_common_env,
        )

        simulate_start_fn_name = f"cms-{stage}-subscriptions-simulate-start"
        simulate_start_role = _role(
            "SubscriptionsSimulateStartRole", simulate_start_fn_name,
        )
        simulate_start_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem"],
            resources=[vehicles_table_arn],
        ))
        # T6.1 — the ONE genuinely new permission in spec
        # 2026-09-15-cms-cs-campaign-ownership.  `/simulate/start` stopped being a
        # resolve-only route and now invokes the simulation Lambda.
        #
        # Scoped to that single function ARN, deliberately:
        #   * NOT `resources=["*"]` — that would let this Lambda invoke every
        #     function in the account, including the admin Lambdas in this same
        #     stack, from a route whose only gate is one Cognito group.
        #   * NOT a `:*` version/alias suffix — this stack invokes $LATEST by name.
        #   * NOT `lambda:*` — invoke only.  No `UpdateFunctionCode`,
        #     no `AddPermission`, no `GetFunction`.
        #
        # The function name is constructed here rather than imported from
        # simulation_stack to avoid a cross-stack export: simulation_stack sets
        # `function_name=f"{prefix}-simulation-api"` (simulation_stack.py:866) with
        # the same `cms-{stage}` prefix, so the two are pinned by the shared naming
        # convention. `test_subscriptions_stack_simulate_invoke_grant.py` restates
        # this ARN longhand and asserts an exact set, so widening the resource to a
        # wildcard fails that test rather than silently passing.
        simulation_function_name = f"cms-{stage}-simulation-api"
        simulate_start_role.add_to_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[
                f"arn:aws:lambda:{self.region}:{self.account}:function:{simulation_function_name}",
            ],
        ))
        simulate_start_fn = lambda_.Function(
            self, "SubscriptionsSimulateStart",
            function_name=simulate_start_fn_name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.simulate_start_handler",
            code=lambda_.Code.from_asset(simulate_asset_path),
            role=simulate_start_role,
            timeout=Duration.seconds(15),
            memory_size=256,
            # SIMULATION_FUNCTION_NAME is read by `handler._required_env`, which
            # raises rather than guessing.  It is set HERE and not in
            # `_common_env` on purpose — this is the only Lambda in the stack that
            # may invoke the simulation Lambda, and putting the name in the shared
            # env block would advertise the target to nine functions that hold no
            # grant for it.  A declared-but-unthreaded env key is the defect class
            # behind issues/2026-09-13-cs-consumer-context-keys-never-threaded/;
            # the guard test asserts this key is present on this function.
            environment={**_common_env, "SIMULATION_FUNCTION_NAME": simulation_function_name},
        )

        # =====================================================================
        # availability_listener — DDB Streams on vehicles table (T3.3)
        # =====================================================================
        listener_fn_name = f"cms-{stage}-subscriptions-availability-listener"
        listener_role = _role("SubscriptionsAvailabilityListenerRole", listener_fn_name)
        listener_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:UpdateItem"],
            resources=[self.vehicle_availability_table.table_arn],
        ))
        # T0.4: listener now reads `sold_to` from the vehicle row to denormalise
        # it onto the availability row.  Stream images may already carry `sold_to`
        # (vehicles table NEW_AND_OLD_IMAGES), so the listener prefers the image
        # attribute first and only falls back to a vehicles-table lookup when absent.
        # Query on vin-index GSI resolves VIN → vehicleId; GetItem on the base
        # table fetches sold_to (vin-index is KEYS_ONLY).
        listener_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[
                vehicles_table_arn,
                f"{vehicles_table_arn}/index/vin-index",
            ],
        ))
        listener_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem"],
            resources=[vehicles_table_arn],
        ))
        # Stream consumer IAM — scoped to the vehicles table's stream ARN.
        # The IAM `Resource` covers the *current* stream ARN plus any future
        # rotation of the same table via a wildcard suffix. This is broader
        # than the mapping's `event_source_arn` (which is the concrete ARN)
        # but still bounded to one table's stream family.
        listener_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "dynamodb:DescribeStream",
                "dynamodb:GetRecords",
                "dynamodb:GetShardIterator",
                "dynamodb:ListStreams",
            ],
            resources=[
                vehicles_stream_arn,
                f"{vehicles_table_arn}/stream/*",
            ],
        ))
        listener_asset_path = _bundle_subscriptions_lambda("availability_listener")
        listener_fn = lambda_.Function(
            self, "AvailabilityListener",
            function_name=listener_fn_name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(listener_asset_path),
            role=listener_role,
            timeout=Duration.seconds(30),
            memory_size=256,
            environment=_common_env,
        )
        # Filter at DDB-native level to MODIFY + INSERT before the Lambda pays
        # the invoke cost. Full status-transition check runs in the handler.
        listener_fn.add_event_source_mapping(
            "VehiclesTableStreamSource",
            event_source_arn=vehicles_stream_arn,
            starting_position=lambda_.StartingPosition.LATEST,
            batch_size=25,
            retry_attempts=3,
            report_batch_item_failures=True,
            filters=[
                lambda_.FilterCriteria.filter({
                    "eventName": lambda_.FilterRule.is_equal("INSERT"),
                }),
                lambda_.FilterCriteria.filter({
                    "eventName": lambda_.FilterRule.is_equal("MODIFY"),
                }),
            ],
        )

        # =====================================================================
        # Route wiring
        # =====================================================================
        api_root = api.root

        # /subscriptions
        subs_res = api_root.add_resource("subscriptions")
        subs_res.add_method("POST", _wire(crud_create), **auth_kwargs)
        subs_res.add_method("GET", _wire(crud_list), **auth_kwargs)

        # /subscriptions/{id}
        sub_id = subs_res.add_resource("{id}")
        sub_id.add_method("GET", _wire(crud_detail), **auth_kwargs)

        # /subscriptions/{id}/scope
        scope_res = sub_id.add_resource("scope")
        scope_res.add_method("POST", _wire(scope_add), **auth_kwargs)

        # /subscriptions/{id}/scope/{vin}
        scope_vin = scope_res.add_resource("{vin}")
        scope_vin.add_method("DELETE", _wire(scope_remove), **auth_kwargs)

        # /subscriptions/{id}/records
        records_res = sub_id.add_resource("records")
        records_res.add_method("GET", _wire(records_fn), **auth_kwargs)

        # /products
        products_res = api_root.add_resource("products")
        products_res.add_method("GET", _wire(products_fn), **auth_kwargs)

        # /vehicles/available
        vehicles_res = api_root.add_resource("vehicles")
        vehicles_available_res = vehicles_res.add_resource("available")
        vehicles_available_res.add_method("GET", _wire(vehicles_available_fn), **auth_kwargs)

        # /admin/subscriptions/vehicles/{vin}/available
        admin_res = api_root.add_resource("admin")
        admin_subs = admin_res.add_resource("subscriptions")
        admin_subs_vehicles = admin_subs.add_resource("vehicles")
        admin_subs_vehicles_vin = admin_subs_vehicles.add_resource("{vin}")
        admin_subs_vehicles_vin_available = admin_subs_vehicles_vin.add_resource("available")
        admin_subs_vehicles_vin_available.add_method(
            "POST", _wire(admin_mark_fn), **auth_kwargs
        )

        # /admin/subscribers (T5.1) — reuse the `admin` resource created above
        admin_subscribers = admin_res.add_resource("subscribers")
        admin_subscribers.add_method("POST", _wire(admin_provision_fn), **auth_kwargs)

        # /simulate/vehicles (GET) + /simulate/start/{vid} (POST) — FG2.2 wiring.
        # default_cors_preflight_options at the RestApi level auto-adds OPTIONS
        # to each new resource; without these resources declared, the browser's
        # preflight OPTIONS fails and the frontend fetch is blocked with a CORS
        # "does not have HTTP ok status" error (2026-09-15 UAT finding).
        simulate_res = api_root.add_resource("simulate")
        simulate_vehicles_res = simulate_res.add_resource("vehicles")
        simulate_vehicles_res.add_method(
            "GET", _wire(simulate_list_fn), **auth_kwargs
        )
        simulate_start_res = simulate_res.add_resource("start")
        # POST on BOTH forms. `/simulate/start/{vid}` was the only wired form before
        # T6.1, but the handler reads `vehicle_id` from the BODY and never read
        # `pathParameters` — so that route could not succeed for any input, while
        # `subscriptionsClient.ts:337` posts to `/simulate/start`, a resource that
        # existed with no method. Under a Cognito authorizer that combination
        # answers 404 to an authed caller and 401 to an unauth one, so an unauth
        # probe reads as "route works". It was latent only because
        # `resolveSimulationPath` had zero call sites; T6.2 gives it one.
        # The handler now accepts either form.
        #
        # `_wire` is called ONCE and the integration reused: it adds a
        # `fn.add_permission("SubscriptionsApiInvoke", ...)` under a fixed construct
        # id, so a second call raises "There is already a Construct with name
        # 'SubscriptionsApiInvoke'" at synth. One permission covers both routes —
        # its `source_arn` is `api.arn_for_execute_api()`, the whole API.
        simulate_start_integration = _wire(simulate_start_fn)
        simulate_start_res.add_method(
            "POST", simulate_start_integration, **auth_kwargs
        )
        simulate_start_vid = simulate_start_res.add_resource("{vid}")
        simulate_start_vid.add_method(
            "POST", simulate_start_integration, **auth_kwargs
        )

        # ── API URL output ────────────────────────────────────────────────
        CfnOutput(
            self, "SubscriptionsApiUrl",
            value=api.url,
            description=(
                "Subscription plane REST API endpoint — add to the frontend's "
                "runtimeConfig as subscriptionsApiEndpoint. Same authorizer as "
                "the CMS main API (imports its user pool)."
            ),
            export_name=f"{construct_id}-api-endpoint",
        )
