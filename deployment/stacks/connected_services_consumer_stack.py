# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Connected Services subscription plane — CMS-side consumer cache table.

Spec `2026-09-10-cms-connected-services-consumer`, T1.5 (Group 1).

## Scope of this stack today

T1.5 is deliberately **cache-table-only**. The proxy Lambda (T1.4 skeleton,
T2.3 wired) lives inside the existing ``main_api`` function, not in a new
Lambda here, so this stack has no Lambda + no API Gateway + no IAM grants
that require a principal. IAM grants to the ``main_api`` Lambda's role are
added by T2.3 in the stack that owns that Lambda, not here.

## Why a new stack rather than an addition to storage_stack.py

The consumer spec's `tasks.md` T1.5 provides an explicit fallback:

    Constraints: check `currentspec.md` before editing `storage_stack.py`
    — 4+ other specs are concurrently active in this repo; if any of them
    are mid-edit on that file, use the new-stack fallback named above
    instead of risking a collision.

At Group 1 build time (2026-09-10), ``deployment/app.py`` had uncommitted
changes from a concurrent session (the producer spec's own T1.2 addition to
wire ``DEPLOY_SUBSCRIPTIONS``). ``storage_stack.py`` itself was clean, but
the user's build instruction directed the fallback path if *either*
file was contended — matching the same structural argument the producer's
own ``subscriptions_stack.py:19-32`` already records for itself:

    Editing [storage_stack.py] would entangle this spec's diff with theirs.
    That is the mechanism behind
    `issues/2026-09-02-cross-slug-commit-misattribution/`; a separate file
    avoids it structurally rather than by care.

So this table lives in its own stack for the same reason. Consumers
(``main_api`` Lambda) read the table by name via the env var
``CS_FEED_CACHE_TABLE_NAME`` — the ``CfnOutput`` here exists mainly for
operators, not for cross-stack dependency import, so a future move back
into ``storage_stack.py`` (once contention clears) would be a table-name
preservation exercise, not a stack-boundary redesign.

## Table naming — region-suffixed

Per `~/.kiro/steering/cross-region-namespace-discipline.md` check 1: the
name is a deterministic function of ``{stage, account, region}``:

    cms-{stage}-storage-cs-feed-cache-{region}-{account}

DynamoDB table names are *account-region* scoped, so this suffix is not
strictly required to avoid an AWS-level collision. It is applied anyway
because the discipline doc's check 1 asks for the deterministic function
even for account-region-scoped resources, and to match the closest
structural precedent (``storage_stack.py:1125`` and
``subscriptions_stack.py:139``).

Length budget check (discipline check 2), worst case across the 34
commercial regions (longest = 14 chars, e.g. ``ap-northeast-1``) with a
12-digit account:

    len("cms-staging-storage-cs-feed-cache-") = 34
    + 14 (region) + 1 ("-") + 12 (account)   = 61 chars

DynamoDB's table-name limit is 255, so this fits with headroom.

## TTL — cache freshness, not the source of truth

Spec D3 is explicit: this table is a **cache**, not a copy of the
subscription's authoritative state. Cache rows carry a ``ttl`` attribute
(epoch seconds), and DynamoDB's TTL feature evicts stale rows within ~48
hours of expiry. The proxy Lambda (T1.4/T2.1) sets ``ttl`` at write time to
``now + freshness_window_seconds``, and its cache-read path treats a row
whose ``ttl < now`` as a cache miss (re-fetches from the producer) rather
than trusting DynamoDB's eventual eviction — DynamoDB TTL is a
best-effort reclamation, not a read consistency guarantee.

## Retention

Same as ``subscriptions_stack.py``: no DynamoDB retain Aspect exists in this
repo (``deployment/aspects/`` has only ``BucketRetainAspect``, which walks
``CfnBucket`` only). ``RemovalPolicy.RETAIN`` is set explicitly. A cache
table is more defensibly ``DESTROY`` than a subscription table, since the
data is by construction re-fetchable — but RETAIN is the safer default
under the same discipline that governs every other DDB table in this repo,
and the cost of a retained empty cache table is negligible.

## Schema

DynamoDB is schemaless; attributes documented here so consumers do not have
to grep the proxy Lambda to know the shape:

    subscription_id (PK)  STRING  CMS's own subscription_id (from Secrets
                                   Manager, one row per environment). Same
                                   value across every cached record for a
                                   given environment; provides a partition
                                   key that is stable across cache warms.
    record_key (SK)       STRING  Either ``latest`` (for the most recent
                                   list-pull response) or a per-VIN key of
                                   the form ``vin#<VIN>`` (for enrolled-
                                   state lookups by vehicle detail).
    payload               STRING  JSON-encoded producer response, verbatim,
                                   as returned by ``GET /subscriptions/{id}/
                                   records``. Not decomposed here — the
                                   cache stores what the API returned so
                                   changes to the producer's response shape
                                   do not require a cache-table migration.
    cached_at             STRING  ISO-8601 timestamp of when this cache
                                   entry was written. Surfaced in the UI
                                   as "as of <cached_at>".
    ttl                   NUMBER  Epoch seconds; DynamoDB evicts rows whose
                                   ``ttl`` is in the past. Also enforced by
                                   the Lambda's read path (see TTL note).

The primary key ``(subscription_id, record_key)`` matches the two shapes of
read this cache supports:

- **List view**: ``GetItem({subscription_id, record_key='latest'})`` — one
  round trip returns the last pulled full list.
- **Per-VIN detail**: ``GetItem({subscription_id, record_key='vin#<VIN>'})``
  — one round trip returns the last known payload for one VIN.

No GSI is needed at this phase — both reads are point-lookups, not scans.
If Group 2 introduces a read that needs "give me every cached VIN payload
for CMS's subscription regardless of key", it should query on the partition
key alone (``KeyConditionExpression='subscription_id = :sub'``) rather than
introducing a GSI.
"""
from __future__ import annotations

import os

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class ConnectedServicesConsumerStack(Stack):
    """CMS-side Connected Services feed cache (spec D3)."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        stage: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stage = stage or os.environ.get("DEPLOYMENT_STAGE", "dev")

        # ── Feed cache table (spec D3) ─────────────────────────────────────
        #
        # This is a *cache*, not the authoritative store — the authoritative
        # store is the producer's Subscription table + records source. A
        # future edit that turns this into a copy of the producer's state
        # would violate spec D1 and desync silently. See docstring above for
        # the read paths this schema supports.
        self.feed_cache_table = dynamodb.Table(
            self,
            "ConnectedServicesFeedCacheTable",
            table_name=(
                f"cms-{stage}-storage-cs-feed-cache-"
                f"{self.region}-{self.account}"
            ),
            partition_key=dynamodb.Attribute(
                name="subscription_id",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="record_key",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            # RETAIN even for a cache: matches every other DDB table in this
            # repo, and the cost of a retained empty table is negligible.
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=(
                dynamodb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=True,
                )
            ),
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            # TTL enforced by DynamoDB best-effort eviction + Lambda read
            # path (see docstring "TTL" section). Attribute name must match
            # what the Lambda writes.
            time_to_live_attribute="ttl",
        )

        # ── Outputs ────────────────────────────────────────────────────────
        #
        # Consumers read the table by name via env var, not by CloudFormation
        # cross-stack import — this output is for operator convenience
        # (`aws cloudformation describe-stacks --query ...`) and diagnostic
        # tooling.
        CfnOutput(
            self,
            "FeedCacheTableName",
            value=self.feed_cache_table.table_name,
            description=(
                "CMS-side Connected Services feed cache table. Consumers "
                "(main_api Lambda) read this as CS_FEED_CACHE_TABLE_NAME. "
                "See docstring in this stack for schema."
            ),
            export_name=f"{construct_id}-feed-cache-table-name",
        )
        CfnOutput(
            self,
            "FeedCacheTableArn",
            value=self.feed_cache_table.table_arn,
            description="Feed cache table ARN — for cross-stack IAM grants in Group 2",
            export_name=f"{construct_id}-feed-cache-table-arn",
        )
