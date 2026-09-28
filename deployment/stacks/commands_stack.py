"""
Commands Stack - Remote vehicle commands via IoT Core MQTT.

Architecture:
  UI -> API Gateway -> commands_lambda -> IoT Core publish -> vehicle
  Vehicle -> IoT Core response topic -> IoT Rule -> command_response_handler -> DDB update

SOVD extension (spec 2026-09-01-cms-remote-diagnostics-sovd):
  - commands_lambda gains _send_sovd_command() for read_dtcs / clear_dtcs
  - New IoT rule routes cms/commands/things/+/executions/+/sovd/response to
    the existing command_response_handler Lambda
  - New S3 bucket for oversized SOVD payloads (> 80 KB inline threshold)
  - _lib.fleet_membership overlaid at bundle-time (same pattern as SimulationStack)
"""
import os
import shutil
from aws_cdk import (
    Aspects, Fn, Stack, Duration, RemovalPolicy, CfnOutput,
    aws_cognito as cognito,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_apigateway as apigateway,
    aws_iot as iot,
    aws_logs as logs,
    aws_s3 as s3,
)
from constructs import Construct

# Import the retain aspect — wired at stack level so synth fails if the new
# SOVD bucket is misconfigured (same pattern as storage_stack and ui_stack in app.py).
import sys
_ASPECTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ASPECTS_DIR not in sys.path:
    sys.path.insert(0, _ASPECTS_DIR)
from aspects.bucket_retain_aspect import BucketRetainAspect

# ── Commands Lambda asset bundling ────────────────────────────────────────────
# The commands Lambda lives in `services/commands/` but depends on
# `_lib/fleet_membership.py` from the connector package (spec § Design § 6).
# A naive `Code.from_asset("../../services/commands")` would miss `_lib/`,
# causing a cold-start `ModuleNotFoundError`.
#
# This mirrors `_bundle_sim_lambda()` in simulation_stack.py (commit 6bd5b1ef).
# Source-of-truth for `_lib/` remains the connector location — do NOT copy
# `_lib/` into `services/commands/` directly.
_COMMANDS_LAMBDA_SRC_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "services", "commands",
))
_CONNECTOR_LIB_SRC_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "services", "connectors", "oem1", "_lib",
))
# Source-of-truth for _shared/ is services/_shared/ — do NOT copy files out of it.
# See services/_shared/__init__.py for the packaging rationale (F28 root cause).
_SHARED_SRC_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "services", "_shared",
))
_COMMANDS_LAMBDA_BUILD_DIR = os.path.join(
    os.path.dirname(__file__), ".build", "commands_lambda",
)


def _bundle_commands_lambda() -> str:
    """Stage the commands Lambda asset with the shared ``_lib/`` and ``_shared/`` overlays.

    Copies ``services/commands/`` into the build dir, installs dependencies
    from ``requirements.txt``, overlays ``services/connectors/oem1/_lib/``
    at ``_lib/``, and overlays ``services/_shared/`` at ``_shared/``.
    Re-stages on every synth so source edits are picked up.
    Returns the absolute path to the staged asset directory.

    Raises ``FileNotFoundError`` if either overlay source directory is missing.

    Source-of-truth discipline (F28 lesson):
    - ``_lib/``    → services/connectors/oem1/_lib/ — do NOT copy into services/commands/
    - ``_shared/`` → services/_shared/              — do NOT copy into services/commands/
    A duplicated safety table (e.g. routine_catalog.py) is the drift that caused F27.
    An import that works locally proves nothing about what ships; assert presence at synth.
    """
    if not os.path.isdir(_CONNECTOR_LIB_SRC_DIR):
        raise FileNotFoundError(
            f"Commands Lambda _lib source dir not found: {_CONNECTOR_LIB_SRC_DIR}. "
            "The connector _lib is source-of-truth; do NOT copy it into services/commands/."
        )
    if not os.path.isdir(_SHARED_SRC_DIR):
        raise FileNotFoundError(
            f"Commands Lambda _shared source dir not found: {_SHARED_SRC_DIR}. "
            "services/_shared/ is source-of-truth; do NOT copy modules out of it."
        )
    dst = _COMMANDS_LAMBDA_BUILD_DIR
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(dst, exist_ok=True)

    # Copy .py files from services/commands/ into the build dir
    for f in os.listdir(_COMMANDS_LAMBDA_SRC_DIR):
        if f.endswith('.py'):
            shutil.copy2(os.path.join(_COMMANDS_LAMBDA_SRC_DIR, f), dst)

    # Install Python dependencies into the build dir
    import subprocess
    requirements_path = os.path.join(_COMMANDS_LAMBDA_SRC_DIR, 'requirements.txt')
    if os.path.isfile(requirements_path):
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '-r', requirements_path,
             '-t', dst, '-q', '--upgrade'],
            check=True,
        )

    # Overlay _lib/ from the connector package (idempotent — dirs_exist_ok=True)
    shutil.copytree(
        _CONNECTOR_LIB_SRC_DIR,
        os.path.join(dst, "_lib"),
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "tests", "*.pyc", ".pytest_cache"),
    )

    # Overlay _shared/ from services/_shared/ — source-of-truth for runtime modules
    # shared across more than one deployment unit (F28: routine_catalog must ship in
    # the Lambda bundle, not only in deployment/scripts/).
    # imported as `_shared.routine_catalog` in commands_lambda.py (F2.2).
    shutil.copytree(
        _SHARED_SRC_DIR,
        os.path.join(dst, "_shared"),
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "tests", "*.pyc", ".pytest_cache"),
    )

    return os.path.abspath(dst)


class CommandsStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
        prefix = f'cms-{stage}'

        # Stage the Lambda asset with the _lib overlay
        commands_asset_path = _bundle_commands_lambda()

        # ── Commands API Lambda ──────────────────────────────────────
        commands_lambda = lambda_.Function(self, 'CommandsApi',
            function_name=f'{prefix}-commands-api',
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler='commands_lambda.handler',
            code=lambda_.Code.from_asset(commands_asset_path),
            timeout=Duration.seconds(15),
            memory_size=256,
            environment={
                'DEPLOYMENT_STAGE': stage,
                'COMMANDS_TABLE': f'{prefix}-storage-commands',
                'SIGNAL_CATALOG_TABLE': f'{prefix}-signal-catalog',
                'GEOFENCES_TABLE': f'{prefix}-storage-geofences',
                # SOVD env vars (spec § Design § 6 + § Interfaces)
                'SOVD_RESPONSES_BUCKET': f'{prefix}-storage-sovd-responses-{self.region}-{self.account}',
                'FLEET_ENROLLMENT_TABLE_NAME': f'{prefix}-storage-fleet-enrollment',
                'DTC_HISTORY_TABLE': f'{prefix}-storage-dtc-history',
                # DMS technician authz (T2.2 / spec 2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5):
                # _check_technician_ro_access queries vin-index on this table to establish
                # physical presence for dms-technician callers.  The table is owned by
                # DMS (dms-{stage}-repair-orders) but lives in the same AWS account.
                'DMS_REPAIR_ORDERS_TABLE': f'dms-{stage}-repair-orders',
                # Driver-self remote commands: the assigned-vehicle lookup reads this
                # table per request (issue 2026-09-26-ios-controls-403-driver-self-commands-api).
                'DRIVERS_TABLE': f'{prefix}-storage-drivers',
            })

        # IoT publish + DDB access
        commands_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=['iot:Publish'], resources=['*']))
        commands_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:PutItem', 'dynamodb:GetItem', 'dynamodb:Query', 'dynamodb:Scan'],
            resources=[
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-commands',
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-commands/index/*',
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-signal-catalog',
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-geofences',
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-geofences/index/*',
                # Required by `_send_command`'s vehicleId -> VIN lookup. Without it
                # the GetItem raised AccessDenied, was swallowed, and `vin` fell back
                # to the vehicleId — so the FWE protobuf request was addressed to
                # cms/commands/things/<vehicleId>/... while the agent subscribes on
                # cms/commands/things/<VIN>/... (its MQTT clientId is the VIN). The
                # message went to an unsubscribed topic for every vehicle.
                # See issues/2026-08-04-fwe-remote-commands-not-actuating/ § D1.
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-vehicles',
            ]))

        # SOVD IAM: commands Lambda role
        # s3:PutObject + s3:GetObject on the SOVD responses bucket (spec § Interfaces)
        commands_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=['s3:PutObject', 's3:GetObject'],
            resources=[
                f'arn:aws:s3:::{prefix}-storage-sovd-responses-{self.region}-{self.account}',
                f'arn:aws:s3:::{prefix}-storage-sovd-responses-{self.region}-{self.account}/*',
            ]))
        # dynamodb:Query on vehicleId-index (needed by fleet_membership.resolve_vins_to_fleets)
        commands_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:Query'],
            resources=[
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-fleet-enrollment/index/vehicleId-index',
            ]))
        # Driver-self remote commands: read ONE driver row (assignedVehicleId + status)
        # to decide which vehicle a driver may command. GetItem only, on the table
        # only: this role must not be able to write or scan driver records.
        commands_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:GetItem'],
            resources=[
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-drivers',
            ]))
        # dynamodb:PutItem + dynamodb:UpdateItem on dtc-history (spec § Design § 7)
        commands_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:PutItem', 'dynamodb:UpdateItem'],
            resources=[
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-dtc-history',
            ]))

        # DMS repair-orders: dynamodb:Query on the table AND its vin-index (T2.2).
        # A grant on the table alone does NOT authorize a GSI query — IAM requires a
        # separate resource entry for the index ARN.  Without the index grant,
        # _check_technician_ro_access raises AccessDenied on every technician invoke.
        # Table is DMS-owned (dms-{stage}-repair-orders) but in the same AWS account.
        commands_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:Query'],
            resources=[
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/dms-{stage}-repair-orders',
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/dms-{stage}-repair-orders/index/vin-index',
            ]))

        # ── Response Handler Lambda ──────────────────────────────────
        response_handler = lambda_.Function(self, 'CommandResponseHandler',
            function_name=f'{prefix}-command-response-handler',
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler='command_response_handler.handler',
            # Must be the SAME `.build` asset the commands API uses, not the raw
            # source directory. `command_response_handler._parse_protobuf` imports
            # `command_response_pb2`, which imports `google.protobuf` — a dependency
            # that only exists in `.build` (populated by the `pip install -t` above).
            # Bundling from `services/commands/` shipped the handler without it, so
            # every protobuf response died with `ModuleNotFoundError: No module named
            # 'google'` and the DDB row stayed at SENT. The JSON branch (MQTT-direct
            # simulators) was unaffected, which is why this went unnoticed.
            # See issues/2026-08-04-fwe-remote-commands-not-actuating/ § D2.
            code=lambda_.Code.from_asset(commands_asset_path),
            timeout=Duration.seconds(10),
            memory_size=128,
            environment={
                'DEPLOYMENT_STAGE': stage,
                'COMMANDS_TABLE': f'{prefix}-storage-commands',
                # SOVD env vars (spec § Design § 7)
                'SOVD_RESPONSES_BUCKET': f'{prefix}-storage-sovd-responses-{self.region}-{self.account}',
                'DTC_HISTORY_TABLE': f'{prefix}-storage-dtc-history',
            })

        response_handler.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:UpdateItem', 'dynamodb:GetItem'],
            resources=[f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-commands']))

        # SOVD IAM: response handler Lambda role
        # s3:GetObject on the SOVD responses bucket (needed for generate_presigned_url
        # on the history read route). s3:PutObject is NOT granted here — the response
        # handler does not upload to S3 (sidecar uploads; handler only extracts the key).
        # FG2.5 (SR Cycle 2 Suggestion #1): removed s3:PutObject to tighten least-privilege.
        response_handler.add_to_role_policy(iam.PolicyStatement(
            actions=['s3:GetObject'],
            resources=[
                f'arn:aws:s3:::{prefix}-storage-sovd-responses-{self.region}-{self.account}',
                f'arn:aws:s3:::{prefix}-storage-sovd-responses-{self.region}-{self.account}/*',
            ]))
        # dynamodb:PutItem + dynamodb:UpdateItem on dtc-history
        response_handler.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:PutItem', 'dynamodb:UpdateItem'],
            resources=[
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-dtc-history',
            ]))

        # ── IoT Rule - route FWE protobuf command responses to Lambda ──
        fwe_response_rule = iot.CfnTopicRule(self, 'FweCommandResponseRule',
            rule_name=f'{prefix.replace("-", "_")}_fwe_command_response_rule',
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                aws_iot_sql_version='2016-03-23',
                # topic(n) is 1-indexed, so for
                #   cms/commands/things/<VIN>/executions/<id>/response/protobuf
                # the VIN is topic(4). topic(3) is the literal "things", which is
                # what this logged for every FWE response until 2026-08-04.
                sql="SELECT encode(*, 'base64') AS b64_payload, topic(4) AS vehicleId FROM 'cms/commands/things/+/executions/+/response/protobuf'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        lambda_=iot.CfnTopicRule.LambdaActionProperty(
                            function_arn=response_handler.function_arn,
                        )
                    )
                ],
                rule_disabled=False,
            ))

        # ── IoT Rule - route legacy JSON command responses to Lambda ──
        response_rule = iot.CfnTopicRule(self, 'CommandResponseRule',
            rule_name=f'{prefix.replace("-", "_")}_command_response_rule',
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                # Pinned 2016-03-23 for the same reason as SovdResponseRule below
                # (issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/).
                # Behaviour-identical for this path's current payload, which is
                # flat scalars — commandId / commandName / vehicleId / status /
                # reason / resultValue / respondedAt, published by
                # realtime_telemetry_simulator.py:2203 and :5677. The latent trap
                # this closes is `resultValue`: it echoes the inbound command's
                # `value`, so the moment any command carries a nested object or
                # array there, 2015-10-08 would silently flatten or drop it.
                aws_iot_sql_version='2016-03-23',
                sql="SELECT * FROM 'cms/commands/+/response'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        lambda_=iot.CfnTopicRule.LambdaActionProperty(
                            function_arn=response_handler.function_arn,
                        )
                    )
                ],
                rule_disabled=False,
            ))

        # ── IoT Rule - route SOVD responses to Lambda (spec § Design § 7) ──
        # Topic: cms/commands/things/{VIN}/executions/{execId}/sovd/response
        # Cordoned from FWE by the trailing /sovd/response sub-path (FWE subscribes
        # on .../executions/+/request/protobuf with a literal /protobuf suffix —
        # it cannot receive SOVD publishes). See spec § D2 rationale point 3.
        sovd_response_rule = iot.CfnTopicRule(self, 'SovdResponseRule',
            rule_name=f'{prefix.replace("-", "_")}_sovd_response_rule',
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                # MUST be 2016-03-23. Omitting this property defaults the rule to
                # the original 2015-10-08 SQL version, which does NOT support nested
                # JSON objects or arrays: with `SELECT *` it silently empties
                # top-level arrays to [], DROPS arrays nested inside an object, and
                # FLATTENS nested objects into their parent. Scalars survive, so the
                # row looks healthy while the payload has been mangled.
                #
                # That shipped and destroyed every array in every SOVD response:
                # `lamp_self_check` lost `lamps` and `cell_balance_check` lost
                # `cell_voltages`, leaving rows whose stored verdict contradicted
                # their own payload, and `sovd_read_dtcs` lost its per-ECU DTC arrays
                # (0 rows ever reached dtc-history with source='sovd').
                # See issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/.
                #
                # The FWE rule above is immune for a different reason — it wraps the
                # payload in encode(*, 'base64') — and already sets this explicitly.
                # Guarded by deployment/stacks/tests/test_iot_rule_sql_version.py.
                aws_iot_sql_version='2016-03-23',
                sql="SELECT *, topic() AS topic FROM 'cms/commands/things/+/executions/+/sovd/response'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        lambda_=iot.CfnTopicRule.LambdaActionProperty(
                            function_arn=response_handler.function_arn,
                        )
                    )
                ],
                rule_disabled=False,
            ))

        # Allow IoT to invoke the response handler
        response_handler.add_permission('IoTInvoke',
            principal=iam.ServicePrincipal('iot.amazonaws.com'),
            source_arn=f'arn:aws:iot:{self.region}:{self.account}:rule/{prefix.replace("-", "_")}_command_response_rule')
        response_handler.add_permission('IoTInvokeFwe',
            principal=iam.ServicePrincipal('iot.amazonaws.com'),
            source_arn=f'arn:aws:iot:{self.region}:{self.account}:rule/{prefix.replace("-", "_")}_fwe_command_response_rule')
        # Allow IoT to invoke the response handler for the SOVD rule
        response_handler.add_permission('IoTInvokeSovd',
            principal=iam.ServicePrincipal('iot.amazonaws.com'),
            source_arn=f'arn:aws:iot:{self.region}:{self.account}:rule/{prefix.replace("-", "_")}_sovd_response_rule')

        # ── SOVD Responses S3 Bucket ─────────────────────────────────
        # Cross-region namespace discipline (spec § Constraints + cross-region-namespace.md):
        # bucket name includes BOTH region and account suffixes to prevent collisions
        # across regions and accounts. RemovalPolicy.RETAIN is explicit (never rely on
        # CDK defaults for globally-named buckets). Versioning off (single-write per
        # correlationId — retries use new correlationId). 30-day lifecycle expiry.
        # FG2.4 (SR Cycle 2 Warning #4): enforce_ssl=True synthesizes a bucket policy
        # denying non-TLS access; block_public_access=BLOCK_ALL is defense-in-depth
        # against future account-default changes.
        sovd_bucket = s3.Bucket(self, 'SovdResponsesBucket',
            bucket_name=f'{prefix}-storage-sovd-responses-{self.region}-{self.account}',
            removal_policy=RemovalPolicy.RETAIN,
            versioned=False,
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id='expire-sovd-responses-30d',
                    expiration=Duration.days(30),
                    enabled=True,
                )
            ],
        )

        # ── BucketRetainAspect ─────────────────────────────────────────
        # Wire the aspect at stack level so synth fails if any globally-named
        # bucket in this stack lacks RemovalPolicy.RETAIN. This is the same
        # pattern as storage_stack and ui_stack in app.py. The app.py does NOT
        # yet wire BucketRetainAspect for CommandsStack — the aspect is wired
        # here at self (the stack) to cover the new SOVD bucket.
        Aspects.of(self).add(BucketRetainAspect())

        # ── API Gateway ──────────────────────────────────────────────
        api = apigateway.RestApi(self, 'CommandsAPI',
            rest_api_name=f'{prefix}-commands-api',
            description='CMS Remote Commands API',
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=['Content-Type', 'Authorization'],
            ))

        # Cognito authorizer — imports User Pool ARN from ui_stack via cross-stack ref
        ui_construct_id = f'cms-{stage}-ui'
        user_pool_arn = Fn.import_value(f'{ui_construct_id}-user-pool-arn')
        user_pool = cognito.UserPool.from_user_pool_arn(self, 'ImportedUserPool', user_pool_arn)
        cognito_authorizer = apigateway.CognitoUserPoolsAuthorizer(
            self, 'CommandsCognitoAuthorizer',
            cognito_user_pools=[user_pool],
            authorizer_name=f'{construct_id}-cognito-auth'
        )
        auth_kwargs = dict(authorizer=cognito_authorizer,
                           authorization_type=apigateway.AuthorizationType.COGNITO)

        integration = apigateway.LambdaIntegration(commands_lambda)

        # /api/commands/catalog
        api_res = api.root.add_resource('api')
        cmd_res = api_res.add_resource('commands')
        catalog = cmd_res.add_resource('catalog')
        catalog.add_method('GET', integration, **auth_kwargs)

        # /api/commands/{vehicleId}
        vehicle_cmd = cmd_res.add_resource('{vehicleId}')
        vehicle_cmd.add_method('POST', integration, **auth_kwargs)  # send command / sovd
        vehicle_cmd.add_method('GET', integration, **auth_kwargs)    # get history

        # /api/commands/{vehicleId}/routines — the Stage 3 routines catalog surface.
        # F32 (Fix Group 4, spec 2026-09-02-cms-diagnostics-platform): this resource
        # was authored in the Lambda handler (`commands_lambda.py:302, _get_routines`)
        # during Wave 3 but never wired into API Gateway, so the deployed endpoint
        # returned 403 "Missing Authentication Token" on every request. The panel's
        # useEffect fired, browser blocked the response as a CORS error (missing route
        # → API GW skips CORS headers), and the routines section rendered nothing.
        # Instance 8 of this spec's shape-vs-function pattern: the Lambda side was
        # tested, the frontend transport was tested, but the API Gateway routing
        # between them was not — and no deployed-endpoint smoke test would have
        # exercised it either.
        vehicle_routines = vehicle_cmd.add_resource('routines')
        vehicle_routines.add_method('GET', integration, **auth_kwargs)  # list routines for this vehicle's powertrain

        # /api/geofences
        gf_res = api_res.add_resource('geofences')
        gf_res.add_method('POST', integration, **auth_kwargs)  # create geofence
        gf_vehicle = gf_res.add_resource('{vehicleId}')
        gf_vehicle.add_method('GET', integration, **auth_kwargs)     # list geofences for vehicle
        gf_vehicle.add_method('DELETE', integration, **auth_kwargs)   # delete geofence

        # ── Outputs ──────────────────────────────────────────────────
        CfnOutput(self, 'CommandsApiUrl', value=api.url,
                  description='Commands API endpoint - add to runtimeConfig as commandsApiEndpoint')
        CfnOutput(self, 'CommandResponseRuleName',
                  value=f'{prefix.replace("-", "_")}_command_response_rule')
        CfnOutput(self, 'SovdResponseRuleName',
                  value=f'{prefix.replace("-", "_")}_sovd_response_rule',
                  description='IoT rule routing SOVD responses to the command response handler')
        CfnOutput(self, 'SovdResponsesBucketName',
                  value=sovd_bucket.bucket_name,
                  description='S3 bucket for oversized SOVD payloads (> 80 KB inline threshold)')
