# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CMS subscriber for DMS repair-order status events.

Spec `2026-09-02-cms-dms-service-convergence` T4.4.

## Why a new stack rather than an addition to storage_stack.py

`storage_stack.py` already owns the CMS→DMS direction: the `dmsEventBusName`
gate, `DmsAlertPublisher` and the fleet-membership publisher all live there, so
co-locating the inbound rule beside them would have been the obvious choice.

Two reasons not to. First, direction: that stack's DMS content is all *outbound*
(DynamoDB Streams → EventBridge), while this is *inbound* (EventBridge → Lambda →
DynamoDB). They share a bus name and nothing else. Second and decisive at the
time of writing, `storage_stack.py` carries ~110 lines of uncommitted work from a
concurrent session whose spec ownership is unresolved; editing it would have made
this spec's diff inseparable from theirs. That is the mechanism behind
`issues/2026-09-02-cross-slug-commit-misattribution/`, and a separate file avoids
it structurally rather than by care.

## Gated on the DMS bus being configured

No `dmsEventBusName`, no stack resources at all — matching the posture
`storage_stack.py` takes for the publishers. A stage with no DMS gets no
subscriber, rather than a rule that matches nothing and a Lambda that is never
invoked. The absence is then visible in the template instead of being a
successfully-deployed no-op.

## The event is a trigger, not a payload (spec D4)

The rule matches on `source` and `detail-type` only. It does NOT filter on any
`detail` field, and the handler reads four of the eight allowlisted fields —
deliberately not `customer_channel_pref` or `quiet_hours_applicable`, which are
notification-delivery decisions DMS owns. `RO_STATUS_CHANGED_FIELDS` is not
widened; D4 is explicit that widening it to serve a CMS need would erode a
boundary that keeps model output out of customer-facing messages.
"""
from __future__ import annotations

import os

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
)
from constructs import Construct

#: Event selectors. Literals, matched against DMS's emitter.
#:
#: DMS `source/_lib/events.py` publishes `Source="dms.service-lane"` /
#: `DetailType="dms.ro.status_changed"`. A rule that matches nothing deploys
#: perfectly happily and fires never, so these are pinned by
#: `test_dms_service_events_stack.py` rather than left as inline strings.
DMS_EVENT_SOURCE = "dms.service-lane"
DMS_RO_STATUS_DETAIL_TYPE = "dms.ro.status_changed"

#: GSI the invalidator queries to resolve VIN -> vehicleId.
#:
#: Defined on the vehicles table at `storage_stack.py:512` (PK `vin`, KEYS_ONLY).
#: Named here so the IAM grant and the handler cannot drift apart silently — the
#: handler's own `_VIN_INDEX` is asserted equal to this value by
#: `test_dms_service_events_stack.py`.
VIN_INDEX_NAME = "vin-index"


class DmsServiceEventsStack(Stack):
    """EventBridge rule + invalidator Lambda + marker table."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        stage: str,
        vehicles_table_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.rule = None
        self.invalidator = None
        self.markers_table = None

        dms_bus_name = (
            self.node.try_get_context("dmsEventBusName")
            or os.environ.get("DMS_EVENT_BUS_NAME", "")
        ).strip()

        if not dms_bus_name:
            # Deliberately silent. `app.py` only instantiates this stack when the
            # operator opts in, and a stage without a DMS bus should synthesize
            # an empty stack rather than fail — same trade as the publishers.
            return

        # ── Marker table ──────────────────────────────────────────────────
        #
        # Records "CMS's cached view of this vehicle's service work is stale".
        # A dedicated table rather than a column on `service-history`, because
        # that table is still the PRIMARY store until Group 5's T5.2 turns it
        # into a read cache — and mutating live service history from an event
        # handler, before the cache semantics are settled, is not recoverable.
        # T3.3 already recorded the same constraint for the backfill.
        #
        # Keyed on vehicleId alone: the marker's claim is per-vehicle, and a
        # per-RO key would make the read path enumerate to answer "is this
        # vehicle stale". Last-writer-wins is correct here — staleness is not
        # more true twice.
        self.markers_table = dynamodb.Table(
            self, "DmsServiceCacheMarkers",
            table_name=f"cms-{stage}-dms-service-cache-markers",
            partition_key=dynamodb.Attribute(
                name="vehicleId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            # DESTROY, not RETAIN, and this is the one place that is right for a
            # DMS-related table: the contents are a derived staleness hint that
            # is fully reconstructible from the next event or the next read.
            # Retaining it on stack delete would leave an orphan carrying no
            # information worth keeping. Contrast the S3 buckets covered by
            # BucketRetainAspect, which hold data with no other copy.
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=False,
        )

        # ── Invalidator Lambda ────────────────────────────────────────────
        self.invalidator = lambda_.Function(
            self, "DmsRoCacheInvalidator",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="handler.handler",
            code=lambda_.Code.from_asset(
                "../services/data_processing/lambda/dms_ro_cache_invalidator"
            ),
            timeout=Duration.seconds(30),
            memory_size=256,
            log_retention=logs.RetentionDays.ONE_MONTH,
            description=(
                f"{construct_id}: mark CMS service cache stale on "
                f"{DMS_RO_STATUS_DETAIL_TYPE} (T4.4)"
            ),
            environment={
                # Names MUST match what the handler reads. The handler RAISES on
                # an unset marker-table name rather than defaulting, because a
                # wrong default writes markers nobody reads while reporting
                # success — the failure storage_stack.py:2118 records for the
                # sibling publisher, where 42 tests passed through it.
                "SERVICE_CACHE_MARKERS_TABLE_NAME": self.markers_table.table_name,
                "VEHICLES_TABLE_NAME": vehicles_table_name,
            },
        )

        # Identity-side grants with explicit ARNs rather than
        # `table.grant_*_data()`. Resource-side grants mutate the target table's
        # policy and can introduce cross-stack dependency cycles — the vehicles
        # table belongs to StorageStack, so a resource-side grant here would
        # couple the two stacks. Same F6.1 lesson storage_stack.py records.
        #
        # Query on the `vin-index` GSI, NOT Scan on the table. Review Cycle 4
        # caught the handler issuing an unpaginated scan, where a VIN past the
        # first 1 MB page resolves to nothing and the marker is silently dropped.
        # The grant is scoped to the INDEX ARN because that is what a GSI query
        # requires, and deliberately does NOT include the table ARN or
        # `dynamodb:Scan` — so a revert to scanning fails on permissions rather
        # than degrading quietly. Pinned by
        # `test_no_scan_grant_so_a_revert_to_scanning_cannot_run_silently`.
        self.invalidator.add_to_role_policy(
            iam.PolicyStatement(
                sid="DmsRoCacheInvalidatorResolveVin",
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:Query"],
                resources=[
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/"
                    f"{vehicles_table_name}/index/{VIN_INDEX_NAME}"
                ],
            )
        )
        # Write-only on the markers table. No read grant: this Lambda never needs
        # to read a marker, and the reader is Group 5's code in a different role.
        # Least privilege here is cheap and makes the direction of the data flow
        # legible from the policy alone.
        self.invalidator.add_to_role_policy(
            iam.PolicyStatement(
                sid="DmsRoCacheInvalidatorWriteMarker",
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:PutItem"],
                resources=[self.markers_table.table_arn],
            )
        )

        # ── EventBridge rule ──────────────────────────────────────────────
        #
        # `from_event_bus_name` rather than creating a bus: the DMS bus is owned
        # by the DMS account/stack. CMS subscribes to it; it does not define it.
        dms_bus = events.EventBus.from_event_bus_name(
            self, "DmsEventBus", dms_bus_name
        )

        self.rule = events.Rule(
            self, "DmsRoStatusChangedRule",
            rule_name=f"cms-{stage}-dms-ro-status-changed",
            description=(
                "Mark CMS's service cache stale when a DMS repair order changes "
                "status (spec 2026-09-02-cms-dms-service-convergence T4.4)"
            ),
            event_bus=dms_bus,
            event_pattern=events.EventPattern(
                source=[DMS_EVENT_SOURCE],
                detail_type=[DMS_RO_STATUS_DETAIL_TYPE],
                # No `detail` filter, on purpose. Filtering on a detail field
                # would make the rule depend on the event's payload shape, and
                # D4's whole point is that CMS treats this event as a trigger.
            ),
        )
        self.rule.add_target(
            targets.LambdaFunction(
                self.invalidator,
                # A failed invocation is retried by EventBridge; after retries it
                # is dropped. A DLQ is the obvious hardening and is deliberately
                # deferred to Group 5, where the read path decides whether a
                # missed marker matters — with read-through-always it does not,
                # and a queue nobody drains is its own defect.
                retry_attempts=3,
            )
        )

        CfnOutput(
            self, "DmsServiceCacheMarkersTableName",
            value=self.markers_table.table_name,
            description="Table Group 5's read path consults for cache staleness",
        )
        CfnOutput(
            self, "DmsRoStatusChangedRuleName",
            value=self.rule.rule_name,
            description="EventBridge rule subscribing CMS to DMS RO status changes",
        )
