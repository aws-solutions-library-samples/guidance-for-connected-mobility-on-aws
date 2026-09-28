"""
Storage Stack - DynamoDB tables matching existing target-account schema exactly
"""

from aws_cdk import (
    Stack,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_events,
    aws_iam as iam,
    aws_logs as logs,
    CfnOutput,
    RemovalPolicy,
    Duration,
    Fn
)
from constructs import Construct
from typing import Dict
import os

class StorageStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Initialize tables dictionary
        self.tables: Dict[str, dynamodb.Table] = {}
        stage = os.environ.get("DEPLOYMENT_STAGE", "dev")
        
        # Vehicle Telemetry Table - matches cms-0a0e68e9-telemetry
        self.tables['telemetry'] = dynamodb.Table(
            self, "TelemetryTable",
            table_name=f"{construct_id}-telemetry",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.NUMBER
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Add GSI for tripId-timestamp queries
        self.tables['telemetry'].add_global_secondary_index(
            index_name="tripId-timestamp-index",
            partition_key=dynamodb.Attribute(
                name="tripId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.NUMBER
            )
        )
        
        # Service History Table - Create if the CFN resource doesn't already own one.
        # Note: CDK's from_table_name is lazy (never throws), so a try/except around
        # it was previously a no-op and the branch that creates the table never ran.
        # Now we always create the table via CloudFormation; DDB will return
        # AlreadyExists if a pre-existing table with this name is owned by another
        # stack, which is an explicit error we want the operator to see.
        self.tables['service_history'] = dynamodb.Table(
            self, "ServiceHistoryTable",
            table_name=f"{construct_id}-service-history",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="serviceDate",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )

        # GSI for service type queries
        self.tables['service_history'].add_global_secondary_index(
            index_name="ServiceTypeIndex",
            partition_key=dynamodb.Attribute(
                name="serviceType",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="serviceDate",
                type=dynamodb.AttributeType.STRING
            )
        )

        # GSI for dealer queries
        self.tables['service_history'].add_global_secondary_index(
            index_name="DealerIndex",
            partition_key=dynamodb.Attribute(
                name="dealerId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="serviceDate",
                type=dynamodb.AttributeType.STRING
            )
        )
        
        # S3 bucket for service invoices - see note on service_history above:
        # from_bucket_name is lazy and doesn't throw, so the except branch never ran.
        # Bucket name suffixed with -{region}-{account} per spec
        # `2026-06-03-cms-storage-bucket-region-suffix` — S3 names are
        # global; suffix prevents cross-region collision on deploy.
        #
        # IMPORTANT — 63-char DNS-compliant limit (S3 + boto3 SDK).
        # The data-shape qualifier MUST stay short: dropping the
        # `-service-` infix from the original `service-invoices` was
        # a regression fix for `ap-northeast-1` (issue
        # `2026-06-03-storage-bucket-name-too-long-ap-northeast-1`).
        # Worst case (staging+14-char-region+12-digit-account): 56
        # chars; full math table in
        # `issues/2026-06-03-storage-bucket-name-too-long-ap-northeast-1/report.md`.
        # Regression assertion lives at
        # `deployment/scripts/test_bucket_name_lengths.py`.
        self.invoice_bucket = s3.Bucket(
            self, "ServiceInvoiceBucket",
            bucket_name=f"{construct_id}-invoices-{self.region}-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ArchiveOldInvoices",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(365)
                        )
                    ]
                )
            ],
            removal_policy=RemovalPolicy.RETAIN
        )
        
        # Outputs
        # ServiceHistoryTableName output removed - generated automatically in loop below
        
        CfnOutput(self, "ServiceInvoiceBucketName",
            value=self.invoice_bucket.bucket_name,
            export_name=f"{construct_id}-service-invoice-bucket"
        )
        
        # Trips Table
        # OEM1 A2.2 — 5 new application-layer columns forward-compatible with NULL/0:
        #   engine_time_total_seconds  — total engine-on time for the trip (seconds)
        #   engine_time_idle_seconds   — engine idle time within the trip (seconds)
        #   fuel_consumed_liters       — total fuel consumed during the trip (liters)
        #   fuel_consumed_idle_liters  — fuel consumed while idling (liters)
        #   max_speed_mph              — maximum speed observed during the trip (mph)
        # DynamoDB has no schema; these are documented here so ops/tooling know
        # the expected attribute names. Existing rows without these attributes
        # are valid — consumers must treat absence as NULL/0.
        self.tables['trips'] = dynamodb.Table(
            self, "TripsTable",
            table_name=f"{construct_id}-trips",
            partition_key=dynamodb.Attribute(
                name="tripId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Add GSI for vehicleId queries (REQUIRED for Lambda API)
        self.tables['trips'].add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            )
        )

        # GSI for vehicle trip list sorted by startTime.
        # Used by GET /api/v1/vehicles/{id}/trips?page=N&limit=M so the API can
        # issue a Query with Limit + ScanIndexForward=false instead of fetching
        # every trip for the vehicle and paginating in memory (previously 7s for
        # vehicles with 600+ trips).
        self.tables['trips'].add_global_secondary_index(
            index_name="vehicleId-startTime-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="startTime",
                type=dynamodb.AttributeType.NUMBER
            )
        )

        # GSI for driver trip lookups.
        # Used by GET /api/v1/drivers/{driverId}/trips — before this existed the
        # Lambda issued a Query with IndexName='driverId-index' against a GSI
        # that wasn't defined, silently failing and rendering an empty trips
        # list on the driver detail page. Historical note: an earlier comment
        # claimed this was "added manually — do not add here to avoid drift",
        # but in fact no such GSI existed in prod. Folding it back into CDK.
        self.tables['trips'].add_global_secondary_index(
            index_name="driverId-index",
            partition_key=dynamodb.Attribute(
                name="driverId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="startTime",
                type=dynamodb.AttributeType.NUMBER
            )
        )

        # Safety Events Table
        self.tables['safety_events'] = dynamodb.Table(
            self, "SafetyEventsTable",
            table_name=f"{construct_id}-safety-events",
            partition_key=dynamodb.Attribute(
                name="eventId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Add GSI for vehicleId queries (REQUIRED for Lambda API)
        self.tables['safety_events'].add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            )
        )
        
        # Add GSI for tripId queries
        self.tables['safety_events'].add_global_secondary_index(
            index_name="tripId-index",
            partition_key=dynamodb.Attribute(
                name="tripId",
                type=dynamodb.AttributeType.STRING
            )
        )
        
        # Add GSI for vehicleId-timestamp queries (proper design for time range queries)
        self.tables['safety_events'].add_global_secondary_index(
            index_name="vehicleId-timestamp-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.NUMBER
            )
        )

        # GSI for driver safety-event lookups.
        # Before this, GET /api/v1/safety-events?driverId=X was a scan-with-filter
        # capped at Limit=100 — it only "saw" matches in the first 100 scanned
        # items, so drivers whose events were later in the scan order appeared
        # to have no events at all. Query on this GSI returns accurate counts.
        self.tables['safety_events'].add_global_secondary_index(
            index_name="driverId-index",
            partition_key=dynamodb.Attribute(
                name="driverId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.NUMBER
            )
        )
        
        # Event Catalog GSIs created manually via AWS CLI
        # See: ./create-gsis.sh
        
        # Maintenance Events Table - Enhanced Schema with Repair Instructions
        # Core: alertId, vehicleId, timestamp, alertType, severity, message, status
        # Management: createdDate, lastUpdated, daysOpen, dueDate, priority, category
        # Cost/Duration: estimatedCost, estimatedDuration
        # Triggers: currentValue, thresholdValue, triggerField, triggerCondition
        # Repair: repairInstructions, manualReference, requiredTools, safetyWarnings
        # Context: currentMileage, driverId, tripId, lat, lng
        # See maintenance_alert_schema.md for complete field documentation
        self.tables['maintenance_events'] = dynamodb.Table(
            self, "MaintenanceEventsTable",
            table_name=f"{construct_id}-maintenance-alerts",
            partition_key=dynamodb.Attribute(
                name="alertId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Add GSI for vehicleId queries (REQUIRED for Lambda API)
        self.tables['maintenance_events'].add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            )
        )
        
        # Add GSI for vehicleId-timestamp queries (proper design for time range queries)
        self.tables['maintenance_events'].add_global_secondary_index(
            index_name="vehicleId-timestamp-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.NUMBER
            )
        )
        
        # Event Catalog GSIs created manually via AWS CLI
        # See: ./create-gsis.sh
        
        # Fleet Management Table
        # Phase 3 OEM1 fleet lifecycle additions (DDB is schemaless — no API calls needed):
        #   data_source         STRING  'vehicle-telemetry' | 'cloud-telemetry'
        #                               Distinguishes FWE-instrumented fleets from OEM1 cloud-fed
        #                               fleets. Existing rows without this attribute are treated as
        #                               'vehicle-telemetry' by all consumers (lazy migration, no backfill).
        #                               Dual-read accepts legacy `onboard-fwe`/`cloud-oem1` until Phase D cleanup (per spec `2026-06-09-cms-data-source-model-refactor`).
        #   transform_manifest_id STRING  FK to Phase 1 manifest registry S3 key
        #                               (e.g., 'transforms/oem1-transform.json').
        #                               Only populated when data_source='cloud-telemetry'. Absent
        #                               for all CMS-native (vehicle-telemetry) fleets.
        self.tables['fleets'] = dynamodb.Table(
            self, "FleetsTable",
            table_name=f"{construct_id}-fleets",
            partition_key=dynamodb.Attribute(
                name="fleetId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Fleet Enrollment Table — maps vehicles to fleets
        # PK: FLEET#{fleetId}, SK: VEHICLE#{vehicleId}
        # GSI: vehicleId → fleetId lookup (used by Flink for per-fleet routing)
        #
        # Stream: NEW_AND_OLD_IMAGES — feeds the FleetMembershipPublisher below,
        # which republishes membership changes to the DMS bus so DMS can maintain
        # its local fleet-membership projection (spec DMS
        # `2026-09-05-dms-fleet-membership-projection`, T4.2). NEW_AND_OLD_IMAGES
        # is load-bearing: REMOVE records carry OldImage only and MODIFY needs
        # both to detect a fleet swap, and the publisher must see fleet changes
        # (spec D3 — an un-enroll not propagated grants access that should be
        # denied). NEW_IMAGE alone loses removes; NEW_IMAGES ONLY would suppress
        # MODIFY diffs the publisher relies on. Enabling a stream on an existing
        # DDB table is an in-place update, not a table replacement, so this is
        # safe on the deployed prod table (verified with `aws dynamodb
        # update-table` docs).
        self.tables['fleet_enrollment'] = dynamodb.Table(
            self, "FleetEnrollmentTable",
            table_name=f"{construct_id}-fleet-enrollment",
            partition_key=dynamodb.Attribute(
                name="PK",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
        )
        
        # GSI for reverse lookup: vehicleId → fleetId
        self.tables['fleet_enrollment'].add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="fleetId",
                type=dynamodb.AttributeType.STRING
            )
        )
        
        # Subscriptions Table — vehicle telemetry subscription plans
        self.tables['subscriptions'] = dynamodb.Table(
            self, "SubscriptionsTable",
            table_name=f"{construct_id}-subscriptions",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Vehicle Link Codes Table — one-time codes for companion app vehicle linking
        self.tables['vehicle_link_codes'] = dynamodb.Table(
            self, "VehicleLinkCodesTable",
            table_name=f"{construct_id}-vehicle-link-codes",
            partition_key=dynamodb.Attribute(
                name="linkCode",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # WebSocket Connections Table — tracks active WS connections per fleet
        self.tables['ws_connections'] = dynamodb.Table(
            self, "WSConnectionsTable",
            table_name=f"{construct_id}-ws-connections",
            partition_key=dynamodb.Attribute(
                name="connectionId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        self.tables['ws_connections'].add_global_secondary_index(
            index_name="fleetId-index",
            partition_key=dynamodb.Attribute(
                name="fleetId",
                type=dynamodb.AttributeType.STRING
            )
        )
        
        # Vehicles Table
        # Schema includes:
        # - vehicleId (PK): VIN or unique identifier
        # - enrollmentStatus: NOT_ENROLLED | PENDING_ACTIVATION | ENROLLED | ACTIVE | INACTIVE
        # - enrolledAt: ISO timestamp when certificate issued
        # - activatedAt: ISO timestamp when first telemetry received
        # - lastSeenAt: ISO timestamp of most recent telemetry
        # - vehicleStatus: UNKNOWN | PARKED | DRIVING | IDLE | CHARGING | MAINTENANCE | OFFLINE
        # - make, model, year, vin, etc.
        #
        # Phase 3 OEM1 fleet lifecycle fields (DDB is schemaless — no API calls needed;
        # set by admin_bulk_enroll, admin_enrollment_poller, admin_status_sync,
        # admin_refresh_vehicle_status; absent for CMS-native vehicles):
        #   oem1_active_sku               STRING   OEM1 product SKU currently subscribed (scalar)
        #   oem1_request_id               NUMBER   Last OEM1 request_id for traceability
        #   oem1_enrollment_status        STRING   IN_PROGRESS | COMPLETED | FAILED |
        #                                          UN_ENROLL_IN_PROGRESS | UNENROLLED | UNKNOWN
        #   oem1_fcs_code                 NUMBER   Last status code from OEM1 Consumer Action policy
        #   oem1_status_message           STRING   Human-readable status string from OEM1
        #   oem1_readiness_summary        STRING   READY | CCS_OFF | TRANSPORT_MODE |
        #                                          NOT_RECENTLY_KEYED_ON | UNKNOWN
        #   oem1_status_refreshed_at      STRING   ISO8601 timestamp of last OEM1 status fetch
        #   subscription_service_activation_date  STRING  ISO8601; set when fcs_code=3 (enrolled)
        self.tables['vehicles'] = dynamodb.Table(
            self, "VehiclesTable",
            table_name=f"{construct_id}-vehicles",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            # Stream: NEW_AND_OLD_IMAGES — required by the Connected Services
            # subscription plane's availability listener (spec
            # `2026-09-10-cms-connected-services-subscriptions` T3.3), which filters
            # MODIFY records where `status` transitions into a connected/active state.
            # Enabled here (rather than in `subscriptions_stack.py`) because a
            # DynamoDB stream flag is a table property, not an event-source-mapping
            # property. Only the mapping lives in the subscription stack. The
            # listener uses `NewImage.status` and `OldImage.status`, so both images
            # are required.
            #
            # This is additive: the vehicles table has no existing stream consumer
            # (verified: `grep -rn "self.tables\\['vehicles'\\]" deployment/` shows
            # only reads and grants, no `add_event_source`), so enabling the stream
            # cannot destabilise an existing consumer. Enabling a stream on a live
            # table is an `UpdateTable` operation at deploy time — supported by
            # CloudFormation and unfelt by consumers of the table's data.
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
        )

        # GSI for VIN -> vehicleId lookup.
        #
        # CVX's triage classifier resolves a VIN to a vehicleId on every call
        # (`lambdas/shared/cms_client_real.py`, querying IndexName="vin-index"). That
        # index had never been defined anywhere in this repo, so the query threw on
        # every invocation and the client silently fell back to a paginated full-table
        # scan. Its own comment estimates that scan at 300-500 ms; measured warm triage
        # was 143 ms against Amazon Nova Sonic's ~1 s bidi tool budget, so the scan was
        # eating most of the headroom on the voice path — and the fallback made a
        # MISSING index look merely like a slow one for months.
        #
        # KEYS_ONLY is sufficient: the caller projects only `vehicleId` and `vin`, and a
        # KEYS_ONLY index already carries the index key plus the table key. Do not widen
        # to INCLUDE without a caller that needs more attributes.
        self.tables['vehicles'].add_global_secondary_index(
            index_name="vin-index",
            partition_key=dynamodb.Attribute(
                name="vin",
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.KEYS_ONLY
        )

        # ── DMS alert publisher (T13.5 option c1) ─────────────────────────
        #
        # Publishes CMS maintenance alerts to the DMS EventBridge bus so DMS can
        # open a repair order from a real detected fault.
        #
        # WHY A STREAM CONSUMER, and not the three things it is not:
        #  - Not the OEM1 `vha_alerts_writer`: that had no CDK construct and no
        #    live Lambda from 2026-06-02 until it was deleted 2026-08-29, and its
        #    `vehicle-alerts` table is empty. See
        #    issues/2026-08-28-vha-alerts-writer-never-wired/.
        #  - Not the simulator's alert branch: that sets threshold-crossing
        #    telemetry VALUES for a processor to detect, and `dtc_codes_active` is
        #    a count rather than a code, so it cannot fill the DTC contract.
        #  - Not a change to the Flink `maintenance-processor`: that app is RUNNING
        #    and already does the detection. Modifying it would mean Java changes,
        #    a jar rebuild and redeploying a live stream processor to add a
        #    side-effect it does not own.
        #
        # The stream on this table was ALREADY enabled (NEW_AND_OLD_IMAGES) with
        # ZERO consumers, so this is purely additive: the detection path is
        # untouched, and removing the event-source mapping fully reverts it.
        #
        # Gated on the DMS bus being configured. Without it the Lambda and its
        # mapping are not created at all — a stage with no DMS gets no consumer
        # rather than a function that no-ops on every alert.
        _dms_bus_name = (
            self.node.try_get_context("dmsEventBusName")
            or os.environ.get("DMS_EVENT_BUS_NAME", "")
        ).strip()

        if _dms_bus_name:
            self.dms_alert_publisher = lambda_.Function(
                self, "DmsAlertPublisher",
                runtime=lambda_.Runtime.PYTHON_3_13,
                handler="handler.handler",
                code=lambda_.Code.from_asset(
                    "../services/data_processing/lambda/dms_alert_publisher"
                ),
                timeout=Duration.seconds(60),
                memory_size=256,
                log_retention=logs.RetentionDays.ONE_MONTH,
                description=(
                    f"{construct_id}: publish maintenance alerts to the DMS bus "
                    "(T13.5 c1)"
                ),
                environment={
                    "DMS_EVENT_BUS_NAME": _dms_bus_name,
                    # Name MUST match what the handler reads. Review cycle 7 caught
                    # this set as `VEHICLES_TABLE` / `MAINTENANCE_ALERTS_TABLE`
                    # while the handler reads `VEHICLES_TABLE_NAME` — so every VIN
                    # lookup hit a nonexistent default table, every alert was
                    # skipped as "VIN not resolved", and the Lambda reported
                    # success with zero batch failures while DMS received nothing.
                    # 42 tests passed through that, because the handler tests stub
                    # the client and the CDK tests asserted CDK's own names.
                    # `stacks/test_dms_alert_publisher.py` now derives the required
                    # keys from the handler source so the two cannot drift again.
                    # The `_TABLE_NAME` suffix is this repo's convention — see
                    # main_api's `VEHICLES_TABLE_NAME` / `MAINTENANCE_ALERTS_TABLE_NAME`.
                    "VEHICLES_TABLE_NAME": self.tables['vehicles'].table_name,
                },
            )

            # Identity-side grant with an explicit ARN rather than
            # `table.grant_read_data()`. Resource-side grants mutate the table's
            # policy and can introduce cross-stack dependency cycles — the same
            # F6.1 lesson the DMS stacks record.
            self.dms_alert_publisher.add_to_role_policy(
                iam.PolicyStatement(
                    sid="DmsAlertPublisherResolveVin",
                    effect=iam.Effect.ALLOW,
                    actions=["dynamodb:GetItem"],
                    resources=[
                        f"arn:aws:dynamodb:{self.region}:{self.account}:table/"
                        f"{self.tables['vehicles'].table_name}"
                    ],
                )
            )

            # PutEvents scoped to the single DMS bus, never "*".
            self.dms_alert_publisher.add_to_role_policy(
                iam.PolicyStatement(
                    sid="DmsAlertPublisherPutEvents",
                    effect=iam.Effect.ALLOW,
                    actions=["events:PutEvents"],
                    resources=[
                        f"arn:aws:events:{self.region}:{self.account}:"
                        f"event-bus/{_dms_bus_name}"
                    ],
                )
            )

            # `report_batch_item_failures=True` is load-bearing, not tuning: the
            # handler returns `batchItemFailures` so one unpublishable alert does
            # not force a retry of the whole batch. Without this flag the field is
            # ignored and a single bad record replays every alert alongside it.
            self.dms_alert_publisher.add_event_source(
                lambda_events.DynamoEventSource(
                    self.tables['maintenance_events'],
                    starting_position=lambda_.StartingPosition.LATEST,
                    batch_size=10,
                    retry_attempts=3,
                    report_batch_item_failures=True,
                )
            )

            CfnOutput(
                self, "DmsAlertPublisherArn",
                value=self.dms_alert_publisher.function_arn,
                description="Lambda publishing maintenance alerts to the DMS bus",
            )

            # ── Fleet Membership Publisher ────────────────────────────────
            #
            # Spec: DMS `2026-09-05-dms-fleet-membership-projection`, T4.2.
            # Republishes fleet-enrollment changes to the DMS bus so DMS can
            # maintain its local vin-keyed projection. Same gate as the alert
            # publisher above — no `dmsEventBusName` context, no function and
            # no event-source mapping. A stage with no DMS gets no consumer,
            # rather than a function that no-ops on every membership change.
            #
            # Not merged with the alert publisher because the two consume
            # different streams (maintenance_events vs fleet_enrollment) and
            # emit different DetailTypes on different code paths. Merging
            # would couple the alert path's cadence and error semantics to
            # the fleet-membership path — a MODIFY that changes fleetId
            # already emits two events, which is nothing like the alerts
            # publisher's one-in-one-out shape.
            self.fleet_membership_publisher = lambda_.Function(
                self, "FleetMembershipPublisher",
                runtime=lambda_.Runtime.PYTHON_3_13,
                handler="handler.handler",
                code=lambda_.Code.from_asset(
                    "../services/data_processing/lambda/fleet_membership_publisher"
                ),
                timeout=Duration.seconds(60),
                memory_size=256,
                log_retention=logs.RetentionDays.ONE_MONTH,
                description=(
                    f"{construct_id}: publish fleet-enrollment changes to the "
                    "DMS bus (DMS spec 2026-09-05 T4.2)"
                ),
                environment={
                    "DMS_EVENT_BUS_NAME": _dms_bus_name,
                    # Env-var names MUST match the handler's os.environ.get()
                    # calls. `test_fleet_membership_publisher.py` parses the
                    # handler source and asserts every required key is
                    # supplied — the guard that would have caught the alert
                    # publisher's Cycle-7 Critical had it existed there.
                    "VEHICLES_TABLE_NAME": self.tables['vehicles'].table_name,
                },
            )

            # Identity-side grant with an explicit ARN. Resource-side grants
            # via `table.grant_read_data()` mutate the table policy and can
            # cause cross-stack cycles — same F6.1 lesson the DMS stacks
            # record.
            self.fleet_membership_publisher.add_to_role_policy(
                iam.PolicyStatement(
                    sid="FleetMembershipPublisherResolveVin",
                    effect=iam.Effect.ALLOW,
                    actions=["dynamodb:GetItem"],
                    resources=[
                        f"arn:aws:dynamodb:{self.region}:{self.account}:table/"
                        f"{self.tables['vehicles'].table_name}"
                    ],
                )
            )

            # PutEvents scoped to the single DMS bus, never "*". Same posture
            # as `DmsAlertPublisherPutEvents`.
            self.fleet_membership_publisher.add_to_role_policy(
                iam.PolicyStatement(
                    sid="FleetMembershipPublisherPutEvents",
                    effect=iam.Effect.ALLOW,
                    actions=["events:PutEvents"],
                    resources=[
                        f"arn:aws:events:{self.region}:{self.account}:"
                        f"event-bus/{_dms_bus_name}"
                    ],
                )
            )

            # `report_batch_item_failures=True` is load-bearing: the handler
            # returns `batchItemFailures` per record. Without this flag the
            # field is ignored and one un-publishable membership change
            # forces a retry of the whole batch. Same reasoning as the
            # sibling alert publisher above.
            self.fleet_membership_publisher.add_event_source(
                lambda_events.DynamoEventSource(
                    self.tables['fleet_enrollment'],
                    starting_position=lambda_.StartingPosition.LATEST,
                    batch_size=10,
                    retry_attempts=3,
                    report_batch_item_failures=True,
                )
            )

            CfnOutput(
                self, "FleetMembershipPublisherArn",
                value=self.fleet_membership_publisher.function_arn,
                description="Lambda publishing fleet-enrollment changes to the DMS bus",
            )

        # Vehicle Certificates Table
        self.tables['vehicle_certificates'] = dynamodb.Table(
            self, "VehicleCertificatesTable",
            table_name=f"{construct_id}-vehicle-certificates",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )

        # Connector Checkpoints Table — multi-connector shared.
        # Naming deviates from this stack's f"{construct_id}-X" template
        # because the table is consumed by connector ECS tasks via the IAM
        # grant in deployment/stacks/connector_stack.py:97, which hardcodes
        # arn:aws:dynamodb:*:*:table/cms-{stage}-connector-checkpoints.
        # Schema is pinned by services/connectors/oem1/checkpoint_store.py:
        #   PK = checkpoint_key (STRING, encoded as f"{flow.hex()}#{shard_id.hex()}")
        #   item attribute "reference" (BINARY) — opaque OEM1 AFTER reference
        # Multi-connector isolation is row-prefix-based at the application layer
        # (the connector embeds connector_name in the composite key); the table
        # itself is shared. See spec
        # 2026-06-01-cms-oem1-transform-manifest-staging-e2e Fix Group C1.5.
        self.tables['connector_checkpoints'] = dynamodb.Table(
            self, "ConnectorCheckpointsTable",
            table_name=f"cms-{stage}-connector-checkpoints",
            partition_key=dynamodb.Attribute(
                name="checkpoint_key",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
        )

        # User Preferences Table
        self.tables['user_preferences'] = dynamodb.Table(
            self, "UserPreferencesTable",
            table_name=f"{construct_id}-user-preferences",
            partition_key=dynamodb.Attribute(
                name="userId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Dashboard Metrics Cache Table
        self.tables['dashboard_metrics_cache'] = dynamodb.Table(
            self, "DashboardMetricsCacheTable",
            table_name=f"{construct_id}-dashboard-metrics-cache",
            partition_key=dynamodb.Attribute(
                name="metricKey",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Drivers Table
        self.tables['drivers'] = dynamodb.Table(
            self, "DriversTable",
            table_name=f"{construct_id}-drivers",
            partition_key=dynamodb.Attribute(
                name="driverId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Remote Commands Table — command execution history
        self.tables['commands'] = dynamodb.Table(
            self, "CommandsTable",
            table_name=f"{construct_id}-commands",
            partition_key=dynamodb.Attribute(
                name="commandId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        self.tables['commands'].add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            )
        )

        # Geofences Table — active geofence definitions
        self.tables['geofences'] = dynamodb.Table(
            self, "GeofencesTable",
            table_name=f"{construct_id}-geofences",
            partition_key=dynamodb.Attribute(
                name="geofenceId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        self.tables['geofences'].add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            )
        )

        # Vehicle Costs Table - monthly TCO rollups per vehicle (fuel, maint, insurance, depreciation, charging)
        #
        # DEPRECATED: 2026-09-10, see spec 2026-09-10-cms-fleet-intelligence-adp-consumer.
        # Fleet Intelligence now sources cost rows from ADP's curated lake over
        # Athena (§ D4), and neither the FleetIntelligenceFunction's env block nor
        # its IAM policy references this table any more.
        #
        # NOT REMOVED, and the spec's stated preference was to remove it. Measured
        # against a synthesized template rather than inferred: this construct emits
        # three CloudFormation exports, and one of them —
        # `cms-{stage}-storage:ExportsOutputFnGetAttVehicleCostsTable...Arn...` — is
        # IMPORTED by cms-{stage}-flink, because flink_stack.py iterates
        # `for table in storage_tables.values()` and calls grant_read_write_data on
        # every one. CloudFormation refuses to delete an export while a consumer
        # imports it, so deleting this construct turns a one-stack change into a
        # sequenced two-stack deploy (flink first to drop the import, then storage),
        # and the two human-named exports may have consumers outside this repo.
        # That sequencing is not G4's scope and would put a deploy-ordering trap in
        # G5's path. Tracked as a follow-on; see the spec's decisions.md G4-C.
        #
        # Do not add new readers. The live cost path is
        # services/fleet_intelligence/adp_source.py.
        self.tables['vehicle_costs'] = dynamodb.Table(
            self, "VehicleCostsTable",
            table_name=f"{construct_id}-vehicle-costs",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="yearMonth",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        self.tables['vehicle_costs'].add_global_secondary_index(
            index_name="fleetId-yearMonth-index",
            partition_key=dynamodb.Attribute(name="fleetId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="yearMonth", type=dynamodb.AttributeType.STRING)
        )

        # Charging Sessions Table - per-session data for BEV vehicles
        self.tables['charging_sessions'] = dynamodb.Table(
            self, "ChargingSessionsTable",
            table_name=f"{construct_id}-charging-sessions",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="sessionStartTime",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        self.tables['charging_sessions'].add_global_secondary_index(
            index_name="fleetId-sessionStartTime-index",
            partition_key=dynamodb.Attribute(name="fleetId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sessionStartTime", type=dynamodb.AttributeType.STRING)
        )

        # Location Snapshots Table - daily fleet utilization per depot for rebalancing analytics
        self.tables['location_snapshots'] = dynamodb.Table(
            self, "LocationSnapshotsTable",
            table_name=f"{construct_id}-location-snapshots",
            partition_key=dynamodb.Attribute(
                name="locationId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="snapshotDate",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        self.tables['location_snapshots'].add_global_secondary_index(
            index_name="snapshotDate-index",
            partition_key=dynamodb.Attribute(name="snapshotDate", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="utilizationPercent", type=dynamodb.AttributeType.NUMBER)
        )
        
        # S3 Datalake Bucket for Iceberg Analytics
        # Use auto-generated bucket name to avoid S3 eventual consistency issues
        self.datalake_bucket = s3.Bucket(
            self, "DatalakeBucket",
            removal_policy=RemovalPolicy.RETAIN,
            versioned=True,
            public_read_access=False,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ArchiveOldData",
                    enabled=True,
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(30)
                        ),
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(90)
                        )
                    ]
                )
            ]
        )
        
        # Warranty Claims Table
        self.tables['warranty_claims'] = dynamodb.Table(
            self, "WarrantyClaimsTable",
            table_name=f"{construct_id}-warranty-claims",
            partition_key=dynamodb.Attribute(name="claimId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.tables['warranty_claims'].add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=dynamodb.Attribute(name="vehicleId", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # DTC History Table
        self.tables['dtc_history'] = dynamodb.Table(
            self, "DTCHistoryTable",
            table_name=f"{construct_id}-dtc-history",
            partition_key=dynamodb.Attribute(name="vehicleId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.NUMBER),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        # Sparse GSI for dedup upsert lookup: keyed (vehicleId, activeCode).
        # CLEARED rows omit activeCode and therefore do NOT appear in this index.
        self.tables['dtc_history'].add_global_secondary_index(
            index_name="active-code-index",
            partition_key=dynamodb.Attribute(name="vehicleId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="activeCode", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # Recalls Table - NHTSA recall campaigns matched against fleet vehicles.
        # PK is the NHTSA campaign number; SK lets us have per-vehicle match rows.
        self.tables['recalls'] = dynamodb.Table(
            self, "RecallsTable",
            table_name=f"{construct_id}-recalls",
            partition_key=dynamodb.Attribute(name="campaignNumber", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="vehicleId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.tables['recalls'].add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=dynamodb.Attribute(name="vehicleId", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # Decision Journal Table (VFO autonomous decisions)
        self.tables['decision_journal'] = dynamodb.Table(
            self, "DecisionJournalTable",
            table_name=f"cms-{os.environ.get('DEPLOYMENT_STAGE', 'prod')}-decision-journal",
            partition_key=dynamodb.Attribute(name="decisionId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.tables['decision_journal'].add_global_secondary_index(
            index_name="vehicleId-timestamp-index",
            partition_key=dynamodb.Attribute(name="vehicleId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.NUMBER),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # VFO Unified Action Queue Table
        # Cross-domain action recommendations the supervisor agent surfaces
        # for human approval (recall grounding plans, rebalancing proposals,
        # warranty filings). Items transition PENDING -> APPROVED | REJECTED.
        # The fleet_command_center UI reads all items via scan (status filter
        # done client-side). Name uses cms-<stage>-vfo-action-queue for
        # consistency with what modules/cms_ui/source/handlers/main_api/index.py
        # expects; this table was previously provisioned by the stubbed
        # cms-<stage>-vfo stack which is not yet deployed.
        self.tables['vfo_action_queue'] = dynamodb.Table(
            self, "VFOActionQueueTable",
            table_name=f"cms-{os.environ.get('DEPLOYMENT_STAGE', 'prod')}-vfo-action-queue",
            partition_key=dynamodb.Attribute(name="actionId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="createdAt", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.tables['vfo_action_queue'].add_global_secondary_index(
            index_name="status-createdAt-index",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="createdAt", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # Vehicle Alerts Table — OEM1 B3.1: VHA 4-event lifecycle state machine
        # Per ADR 2026-06-01 "VHA alerts: new table vs reuse cms-{stage}-event-catalog":
        # PK=vehicleId, SK=wellKnownIndicator → one active row per vehicle+indicator pair.
        # No GSI needed for Phase B3: UI queries on PK alone via DDB Query.
        #
        # ⚠️ THIS TABLE HAS NEVER BEEN WRITTEN. It is EMPTY on staging (0 items,
        # verified 2026-08-29) and nothing populates it.
        #
        # This comment previously read "Written by vha_alerts_writer Lambda
        # (source=oem1-vha on every write)". That was never true. Commit 8fec058c
        # ("B3.1 VHA alerts table + DDB writer Lambda + 4-event lifecycle tests")
        # shipped the handler, this table and 7 passing tests, but never added a CDK
        # construct for the Lambda — so the writer existed only as source and ran
        # nowhere, from 2026-06-02 until it was deleted on 2026-08-29.
        #
        # The comment is called out rather than quietly fixed because it did real
        # damage: a `grep -rln vha_alerts_writer deployment/stacks/` matched THIS LINE
        # and returned storage_stack.py, which reads as "this stack deploys it". That
        # misled a later session into believing the writer was live. A grep for a
        # component's name proves a MENTION, not a deployment.
        # See issues/2026-08-28-vha-alerts-writer-never-wired/.
        #
        # WHERE ALERTS ACTUALLY LIVE: `cms-{stage}-storage-maintenance-alerts`, which
        # main_api reads and four UI components render. It is populated by
        # `services/simulation/enhanced_historical_data_injector.py` (hand-run bulk
        # backfill) and by the realtime simulator's per-trip alert branch. If you are
        # looking for the alerts the product shows, that is the table, not this one.
        #
        # This table is RETAINED rather than removed: dropping it from the template
        # would delete a deployed resource as a side effect of a code cleanup. Its
        # removal is a deliberate decision for whoever retires OEM1 B3.1.
        #
        # Attributes the deleted writer would have set (DDB is schemaless; kept for
        # ops archaeology only):
        #   indicator_state      STRING  ACTIVE | CLEARED
        #   fired_at             STRING  ISO-8601 UTC
        #   cleared_at           STRING  ISO-8601 UTC (null until Clear Warning)
        #   dtc_raw              STRING  raw DTC value (null if no DTC)
        #   dtc_system           STRING  ENGINE | BRAKE | SAFETY | ... (null if no DTC)
        #   dtc_cleared          BOOL    false until Clear DTC event
        #   dtc_cleared_at       STRING  ISO-8601 UTC (null until Clear DTC)
        #   severity             STRING  HIGH | CRITICAL
        #   symptom_key          STRING  from OEM1 event tags
        #   customer_action_key  STRING  from OEM1 event tags
        #   source               STRING  always "oem1-vha"
        #   shard_key_format     STRING  "vehicle" | "device"
        #   updated_at           STRING  ISO-8601 UTC (set on every write)
        self.tables['vehicle_alerts'] = dynamodb.Table(
            self, "VehicleAlertsTable",
            table_name=f"{construct_id}-vehicle-alerts",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="wellKnownIndicator",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )

        # OEM1 Enrollment Requests Table (Phase 3 — spec 2026-06-05-cms-oem1-fleet-bulk-management)
        # Stores per-OEM1-request_id history for bulk enroll/unenroll operations.
        # Region-suffixed per cross-region namespace discipline (C6):
        #   cms-staging-storage-oem1-enrollment-requests-ap-northeast-1-123456789012 = 71 chars
        #   DynamoDB table-name limit = 255 chars — fits comfortably.
        #
        # Schema (DDB is schemaless; attributes documented for ops/tooling):
        #   request_id (PK)       NUMBER   OEM1 request_id from enroll/unenroll 202 response
        #   request_type          STRING   'ENROLL' | 'UN_ENROLL'
        #   vins                  SS       Set of VINs in this request
        #   sku                   STRING   OEM1 product SKU
        #   fleet_id              STRING   CMS fleet for this batch (ENROLL only)
        #   submitted_at          STRING   ISO8601
        #   submitted_by          STRING   Cognito sub of the submitter
        #   customer_id           STRING   OEM1 customer/account id (for quota tracking)
        #   driver_assignments    STRING   JSON-serialized {vin: driver_id} map (ENROLL only)
        #   status_summary        STRING   Last poll snapshot (last_polled_at, counts per fcs_code)
        #   last_polled_at        STRING   ISO8601; updated by admin_enrollment_poller
        #   terminal_at           STRING   ISO8601; set when all VINs reach terminal status
        #   hard_delete           BOOL     If true, poller hard-deletes vehicle on terminal UN_ENROLL
        #   client_request_id     STRING   Optional UUID-v4 from caller for idempotency dedup
        #                                  (absent for legacy/CLI callers — GSI is sparse)
        #   oem1_request_id       NUMBER   Alias of request_id for GSI replay convenience
        #   accepted_count        NUMBER   Count of VINs accepted by OEM1
        #   pre_flight_failure_count NUMBER Count of VINs that failed liteCheck
        #   expires_at            NUMBER   TTL epoch seconds (submitted_at + 90 days)
        self.tables['oem1_enrollment_requests'] = dynamodb.Table(
            self, "OEM1EnrollmentRequestsTable",
            table_name=f"{construct_id}-oem1-enrollment-requests-{self.region}-{self.account}",
            partition_key=dynamodb.Attribute(
                name="request_id",
                type=dynamodb.AttributeType.NUMBER
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            time_to_live_attribute="expires_at",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )

        # GSI 1: submitted_by + submitted_at — "list my recent submissions" UI view
        self.tables['oem1_enrollment_requests'].add_global_secondary_index(
            index_name="SubmittedByIndex",
            partition_key=dynamodb.Attribute(
                name="submitted_by",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="submitted_at",
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )

        # GSI 2: customer_id + submitted_at — quota counter for admin_enroll_quota Lambda
        # (reads ENROLL rows in last 60 min keyed by customer_id per AQ2 / decisions.md 002)
        self.tables['oem1_enrollment_requests'].add_global_secondary_index(
            index_name="CustomerIdIndex",
            partition_key=dynamodb.Attribute(
                name="customer_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="submitted_at",
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )

        # GSI 3: fleet_id + submitted_at — fleet-scoped historical submission view
        self.tables['oem1_enrollment_requests'].add_global_secondary_index(
            index_name="FleetIdIndex",
            partition_key=dynamodb.Attribute(
                name="fleet_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="submitted_at",
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )

        # GSI 4: client_request_id — HASH-only sparse GSI for idempotency dedup
        # (rev 3.1 decision 014). SPARSE: rows without client_request_id (legacy callers,
        # CLI seed) are NOT indexed. admin_bulk_enroll / admin_bulk_unenroll query
        # this index when clientRequestId is present to replay a cached response
        # without re-calling OEM1. Projection includes fields needed for replay.
        self.tables['oem1_enrollment_requests'].add_global_secondary_index(
            index_name="ClientRequestIdIndex",
            partition_key=dynamodb.Attribute(
                name="client_request_id",
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.INCLUDE,
            non_key_attributes=[
                "request_id",
                "status_summary",
                "accepted_count",
                "pre_flight_failure_count",
                "oem1_request_id",
                "submitted_by",
                "submitted_at",
            ]
        )

        # PM Schedules Table — preventive maintenance schedule records per vehicle.
        # PK: vehicleId  SK: scheduleId
        # GSI fleetId-nextDueDate-index: the due/overdue query path (fleet × date window).
        # Spec: 2026-09-02-cms-fleet-intelligence-v1 § D6
        self.tables['pm_schedules'] = dynamodb.Table(
            self, "PmSchedulesTable",
            table_name=f"{construct_id}-pm-schedules",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="scheduleId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        self.tables['pm_schedules'].add_global_secondary_index(
            index_name="fleetId-nextDueDate-index",
            partition_key=dynamodb.Attribute(name="fleetId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="nextDueDate", type=dynamodb.AttributeType.STRING)
        )

        # Add datalake bucket name to tables dictionary for downstream services
        self.tables['datalake_bucket_name'] = self.datalake_bucket.bucket_name
        
        # Outputs for each table
        for table_name, table in self.tables.items():
            # Skip non-table entries (like datalake_bucket_name)
            if not hasattr(table, 'table_name'):
                continue
            # Replace underscores with hyphens for export names
            export_name = table_name.replace('_', '-')
            CfnOutput(
                self, f"{table_name.title().replace('_', '')}TableName",
                value=table.table_name,
                export_name=f"{construct_id}-{export_name}-table-name"
            )
            
            CfnOutput(
                self, f"{table_name.title().replace('_', '')}TableArn",
                value=table.table_arn,
                export_name=f"{construct_id}-{export_name}-table-arn"
            )

        # Vehicles-table stream ARN — exported explicitly so the Connected Services
        # subscription stack (spec 2026-09-10-cms-connected-services-subscriptions
        # T3.3) can attach a DynamoEventSource without a same-stack table
        # reference. The stream itself is enabled on the table above; this is the
        # cross-stack handle to it. Kept minimal — one extra output rather than a
        # loop-time change that would export streams for every table.
        CfnOutput(
            self, "VehiclesTableStreamArn",
            value=self.tables['vehicles'].table_stream_arn or "",
            description=(
                "Vehicles-table DynamoDB stream ARN (NEW_AND_OLD_IMAGES). "
                "Consumed by the Connected Services subscription plane's "
                "availability listener."
            ),
            export_name=f"{construct_id}-vehicles-stream-arn",
        )
        
        # S3 Datalake Bucket Outputs
        CfnOutput(
            self, "DatalakeBucketName",
            value=self.datalake_bucket.bucket_name,
            export_name=f"{construct_id}-datalake-bucket-name"
        )
        
        CfnOutput(
            self, "DatalakeBucketArn",
            value=self.datalake_bucket.bucket_arn,
            export_name=f"{construct_id}-datalake-bucket-arn"
        )
        
        # TODO: Add ElastiCache Redis for real-time vehicle state
        # Will be added to MSK stack for proper VPC co-location
