#!/usr/bin/env python3
"""
Connected Mobility Solution - Modular CDK Application

This application provides a modular approach to deploying the CMS infrastructure
with separate stacks for each major component.
"""

import os
from aws_cdk import App, Environment, Aspects
from stacks.data_processing_stack import DataProcessingStack
from stacks.iot_stack import IoTStack
from stacks.msk_stack import MSKStack
from stacks.flink_stack import FlinkStack
from stacks.storage_stack import StorageStack
from stacks.ui_stack import UIStack
from stacks.waf_stack import WafStack
from stacks.fleet_intelligence_analytics_stack import FleetIntelligenceAnalyticsStack
from stacks.telemetry_integration_stack import TelemetryIntegrationStack
from stacks.fwe_telemetry_stack import FweTelemetryStack
from stacks.simulation_stack import SimulationStack
from stacks.commands_stack import CommandsStack
from stacks.eval_user_stack import EvalUserStack
from aspects.bucket_retain_aspect import BucketRetainAspect
from aspects.domain_alias_guard import enforce_ui_domain_alias

# Committed source-of-truth for the CMS UI custom domains, by stage.
# enforce_ui_domain_alias (called after the UI stack is built) aborts synth if a
# home-region deploy for one of these stages would drop the alias — i.e. the
# uiCustomDomain/uiCustomDomainCertArn context was not supplied. The matching
# domain/cert live in config/<stage>.env so the canonical deploy supplies them.
# Replace these placeholder values with your own custom domains, or leave the
# map empty to skip the alias-guard check entirely (default *.cloudfront.net).
UI_CUSTOM_DOMAIN_BY_STAGE = {
    # stage: (expected_alias, home_region)
    "staging": ("staging.fleet.example.com", "us-west-2"),
    "prod": ("fleet.example.com", "us-east-1"),
}

# Configuration
AWS_ACCOUNT = os.environ.get('CDK_DEFAULT_ACCOUNT')
# Region precedence: explicit AWS_REGION (set by harness/CI/operator for THIS
# deploy) wins over CDK_DEFAULT_REGION (auto-derived by `aws configure` from
# operator's primary region — often a stale leak across cross-region work).
# Fallback chain: AWS_REGION → CDK_DEFAULT_REGION → 'us-west-2'.
# Issue: 2026-06-04-cms-app-py-region-precedence-inversion (fixed 2026-06-08).
AWS_REGION = os.environ.get('AWS_REGION') or os.environ.get('CDK_DEFAULT_REGION') or 'us-west-2'
DEPLOYMENT_STAGE = os.environ.get('DEPLOYMENT_STAGE', 'dev')
MSK_CLUSTER_ARN = os.environ.get('MSK_CLUSTER_ARN')
MSK_VPC_ID = os.environ.get('MSK_VPC_ID')
MSK_SECURITY_GROUP_ID = os.environ.get('MSK_SECURITY_GROUP_ID')
MSK_SUBNET_IDS = os.environ.get('MSK_SUBNET_IDS', '').split(',') if os.environ.get('MSK_SUBNET_IDS') else None
FLINK_ALARM_EMAIL = os.environ.get('FLINK_ALARM_EMAIL')

app = App()

# Add CDK-nag security checks (optional - comment out if cdk-nag not installed)
try:
    from cdk_nag import AwsSolutionsChecks
    Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
    print("✅ CDK-nag security checks enabled")
except ImportError:
    print("⚠️  CDK-nag not installed - skipping security checks")

# Environment configuration
env = Environment(account=AWS_ACCOUNT, region=AWS_REGION)

# Stack naming convention
stack_prefix = f"cms-{DEPLOYMENT_STAGE}"

# 0. Data Processing Stack (Signal Catalog, Transform Manifests)
data_processing_stack = DataProcessingStack(
    app,
    f"{stack_prefix}-data-processing",
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Data Processing Foundation"
)
Aspects.of(data_processing_stack).add(BucketRetainAspect())

# 1. Storage Stack (DynamoDB tables) - needed by Flink and UI
storage_stack = StorageStack(
    app, 
    f"{stack_prefix}-storage",
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Storage Layer"
)
Aspects.of(storage_stack).add(BucketRetainAspect())

# 2. MSK Stack (VPC + Kafka + Redis) - single VPC for all data services
msk_stack = None
if not MSK_CLUSTER_ARN:
    msk_stack = MSKStack(
        app, 
        f"{stack_prefix}-msk",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - VPC, Messaging & Cache Layer"
    )

# 3. IoT Stack (Fleet Management Interface)
iot_stack = IoTStack(
    app, 
    f"{stack_prefix}-iot",
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Fleet Management Interface"
)

# 4. Telemetry Integration Stack (MSK-IoT connectivity)
if os.environ.get('DEPLOY_TELEMETRY_INTEGRATION') == 'true':
    telemetry_integration_stack = TelemetryIntegrationStack(
        app,
        f"{stack_prefix}-telemetry-integration",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Telemetry Integration"
    )

# 5. Flink Stack (With MSK VPC configuration)
flink_stack = FlinkStack(
    app, 
    f"{stack_prefix}-flink",
    storage_tables=storage_stack.tables,
    msk_stack=msk_stack,
    # Propagate MSK env-var config so the standalone `make deploy-flink` path
    # (where MSK_CLUSTER_ARN is set so app.py skips msk_stack creation)
    # routes through FlinkStack's `elif msk_cluster_arn:` branch and sets
    # msk_available=True. Without this, FlinkStack falls through to the
    # `else: msk_available=False` branch and the IAM auth block is skipped
    # entirely — exactly the latent regression class that caused the
    # 2026-06-11 staging Flink outage. See
    # issues/2026-06-11-flink-stack-deploy-blockers.
    msk_cluster_arn=MSK_CLUSTER_ARN,
    msk_vpc_id=MSK_VPC_ID,
    msk_security_group_id=MSK_SECURITY_GROUP_ID,
    msk_subnet_ids=MSK_SUBNET_IDS,
    flink_alarm_email=FLINK_ALARM_EMAIL,
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Flink Deployment"
)
Aspects.of(flink_stack).add(BucketRetainAspect())

# 5b. FWE Telemetry Stack (CMS-native FleetWise Edge agent processing — 
#     does NOT depend on the AWS IoT FleetWise managed service, which is
#     closed to new customers Apr 2026; CMS replaces it natively.)
# Unconditional per spec 2026-09-25-cms-fleetwise-consolidation § Decision (Option B):
# FleetWise is mainline infrastructure, not an optional feature. The prior
# 'if DEPLOY_FLEETWISE == true' guard was dead in practice — config/staging.env
# always set the flag to true.
fwe_telemetry_stack = FweTelemetryStack(
    app,
    f"{stack_prefix}-fleetwise",
    env=env,
    description="Guidance for Connected Mobility (SO9618) - FleetWise Integration"
)

# 6. UI Stack (Frontend and API) — uses MSK stack VPC for Redis access
# ── CloudFront WAF (Phase B blocker #2) ───────────────────────────────────────
# CLOUDFRONT-scoped WAFv2 lives in us-east-1 regardless of the primary region — the
# WafStack pins that itself. Deployed for ALL stages so staging and prod do not have
# materially different security postures (staging runs the managed groups in COUNT).
#
# The WebACL ARN reaches the CloudFront distribution in ui_stack via CDK context
# (-c wafWebAclArn=...), NOT via a cross-region CFN export, which CloudFormation does
# not support. This is the same idiom the repo already uses for uiCustomDomainCertArn,
# a us-east-1 ACM ARN consumed by that same distribution. The WafStack also publishes
# the ARN to SSM at /cms/<stage>/ui-waf/web-acl-arn for operator discoverability.
#
# Two-phase on first deploy: deploy cms-<stage>-ui-waf, read the ARN, then deploy
# cms-<stage>-ui with the context flag. Documented in docs/DEPLOYMENT.md.
waf_stack = WafStack(
    app,
    f"cms-{DEPLOYMENT_STAGE}-ui-waf",
    stage=DEPLOYMENT_STAGE,
    env=env,
)

ui_stack = UIStack(
    app, 
    f"{stack_prefix}-ui",
    storage_tables=storage_stack.tables,
    msk_stack=msk_stack,
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Presentation Layer"
)
Aspects.of(ui_stack).add(BucketRetainAspect())

# ── Fleet Intelligence analytics (Athena workgroup + results bucket) ──────────
# Spec 2026-09-10-cms-fleet-intelligence-adp-consumer § D4. Pinned to us-east-1
# by the stack itself: Athena can only read the Glue Data Catalog in its own
# Region, ADP's catalog and lake are us-east-1, and an Athena results bucket must
# be in the query's Region. On staging (us-west-2 primary) that makes this a
# cross-Region stack; on prod (us-east-1 primary) it is same-Region and still a
# separate stack, so both stages exercise one code path.
#
# ui_stack authorizes the workgroup and bucket BY NAME via
# stacks/_fleet_intelligence_naming.py — no CFN export, which CloudFormation
# cannot do across Regions anyway. Same idiom as cms-<stage>-ui-waf above.
#
# Deploy ordering: this stack must be deployed before the first live Fleet
# Intelligence cost query, because the workgroup and bucket must exist. It does
# not need to precede the cms-<stage>-ui deploy — the IAM policy is built from
# strings and synthesizes either way. See docs/DEPLOYMENT.md.
fleet_intelligence_analytics_stack = FleetIntelligenceAnalyticsStack(
    app,
    f"{stack_prefix}-ui-analytics",
    stage=DEPLOYMENT_STAGE,
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Fleet Intelligence Analytics (Athena, us-east-1)",
)
Aspects.of(fleet_intelligence_analytics_stack).add(BucketRetainAspect())
# Synth-time guard: abort if a domain-bearing UI stage would synthesize WITHOUT
# its custom-domain alias in its home region (issue
# 2026-06-18-cms-ui-domain-alias-context-conditional-deploy-risk).
_ui_domain_guard = UI_CUSTOM_DOMAIN_BY_STAGE.get(DEPLOYMENT_STAGE)
if _ui_domain_guard:
    _expected_alias, _home_region = _ui_domain_guard
    enforce_ui_domain_alias(
        region=AWS_REGION,
        ui_custom_domain_attached=getattr(ui_stack, "ui_custom_domain_attached", False),
        expected_alias=_expected_alias,
        home_region=_home_region,
    )

# 6b. Eval User Stack (staging only) — dedicated Cognito user for Tier 3 eval pipeline
if DEPLOYMENT_STAGE == "staging":
    eval_user_stack = EvalUserStack(
        app,
        f"{stack_prefix}-eval-user",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Eval Pipeline User (staging only)"
    )
    eval_user_stack.add_dependency(ui_stack)

# data_processing_stack consumes Fn.import_value for ui user-pool-arn — enforce ordering
data_processing_stack.add_dependency(ui_stack)

# 7. Predictive Maintenance Agent Stack (Optional - deploy separately if needed)
if os.environ.get('DEPLOY_PREDICTIVE_AGENT') == 'true':
    from stacks.predictive_agent_stack import PredictiveAgentStack
    
    predictive_agent_stack = PredictiveAgentStack(
        app,
        f"{stack_prefix}-predictive-agent",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Predictive Maintenance Agent"
    )
    predictive_agent_stack.add_dependency(ui_stack)

# 7b. (Removed 2026-09-06) Bedrock Agents Stack — Virtual Fleet Operator + 4
# specialist agents were retired per spec `2026-09-05-cms-vfo-teardown`. The
# Tier 1 replacement `services/fleet_intelligence/` fills the fleet-view surface
# deterministically. See `~/.kiro/steering/agentic-tiers.md`.

# 8. Simulation Stack (Optional - ECS Fargate simulation service)
if os.environ.get('DEPLOY_SIMULATION') == 'true':
    simulation_stack = SimulationStack(
        app,
        f"{stack_prefix}-simulation",
        ui_stack=ui_stack,
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Simulation Service"
    )
    simulation_stack.add_dependency(ui_stack)

# 9. Commands Stack (Remote vehicle commands via IoT Core)
commands_stack = CommandsStack(
    app,
    f"{stack_prefix}-commands",
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Remote Commands"
)
commands_stack.add_dependency(ui_stack)

# 9b. WebSocket Fanout Stack (ECS Fargate: Kafka -> API Gateway WebSocket)
if os.environ.get('DEPLOY_WS_FANOUT', 'true') == 'true':
    from stacks.ws_fanout_stack import WsFanoutStack
    ws_fanout_stack = WsFanoutStack(
        app,
        f"{stack_prefix}-ws-fanout",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - WebSocket Telemetry Fanout"
    )

# 10. Amazon Connect Stack (Optional - Contact center for escalation)
if os.environ.get('DEPLOY_CONNECT') == 'true':
    from stacks.connect_stack import ConnectStack

    connect_stack = ConnectStack(
        app,
        f"{stack_prefix}-connect",
        storage_tables=storage_stack.tables,
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Amazon Connect Contact Center"
    )


# OEM Connector Stack (conditional — only when CONNECTOR_NAME env var is set)
if os.environ.get('CONNECTOR_NAME'):
    from stacks.connector_stack import ConnectorStack
    connector_stack = ConnectorStack(
        app,
        "ConnectorStack",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - OEM Connector Service"
    )

# Connected Services UI Stack (Optional — second frontend for the Connected Services portal)
# Spec: .kiro/specs/2026-09-03-cms-connected-services-portal/ (T2.1)
# Opt-in via DEPLOY_CONNECTED_SERVICES_UI=true so a bare `make deploy-all` is unaffected
# until the portal is ready for production. The synth-connected-services Makefile target
# sets this env var directly so the stack can be synthesized in isolation.
if os.environ.get('DEPLOY_CONNECTED_SERVICES_UI') == 'true':
    from stacks.connected_services_ui_stack import ConnectedServicesUiStack
    connected_services_ui_stack = ConnectedServicesUiStack(
        app,
        f"{stack_prefix}-connected-services-ui",
        stage=DEPLOYMENT_STAGE,
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Connected Services Portal UI"
    )
    Aspects.of(connected_services_ui_stack).add(BucketRetainAspect())

# Connected Services Subscription Plane Stack (Optional — subscription data model)
# Spec: .kiro/specs/2026-09-10-cms-connected-services-subscriptions/ (T1.2)
#
# Opt-in via DEPLOY_SUBSCRIPTIONS=true so a bare `make deploy-all` does not
# create tables for a capability that has no wired routes yet — the same posture
# DEPLOY_CONNECTED_SERVICES_UI and DEPLOY_DMS_SERVICE_EVENTS take above. Group 1
# is table-only; the Lambdas that read it are wired in a later group.
if os.environ.get('DEPLOY_SUBSCRIPTIONS') == 'true':
    from stacks.subscriptions_stack import SubscriptionsStack
    subscriptions_stack = SubscriptionsStack(
        app,
        f"{stack_prefix}-subscriptions",
        stage=DEPLOYMENT_STAGE,
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Connected Services Subscription Plane"
    )

# Connected Services Consumer Stack (Optional — CMS-side feed cache table)
# Spec: .kiro/specs/2026-09-10-cms-connected-services-consumer/ (T1.5)
#
# Opt-in via DEPLOY_CONNECTED_SERVICES_CONSUMER=true. Group 1 is cache-table-only;
# the main_api proxy routes that read/write this cache are wired in Group 2 (T2.3)
# and remain gated on the producer's Group 3 gate passing on staging. Deploying
# this table alone is a no-op — no Lambda reads or writes it until Group 2 lands.
if os.environ.get('DEPLOY_CONNECTED_SERVICES_CONSUMER') == 'true':
    from stacks.connected_services_consumer_stack import ConnectedServicesConsumerStack
    connected_services_consumer_stack = ConnectedServicesConsumerStack(
        app,
        f"{stack_prefix}-connected-services-consumer",
        stage=DEPLOYMENT_STAGE,
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Connected Services Consumer Cache"
    )

# DMS Service Events Stack (Optional — CMS subscribes to DMS repair-order status)
# Spec: 2026-09-02-cms-dms-service-convergence T4.4
#
# Opt-in via DEPLOY_DMS_SERVICE_EVENTS=true so a bare `make deploy-all` is
# unaffected until Group 5's read path consumes the markers this writes. The
# stack ALSO gates internally on `dmsEventBusName`: opting in on a stage with no
# DMS bus synthesizes an empty stack rather than a rule that matches nothing.
# Two gates because they answer different questions — "do we want this yet" and
# "is there a bus to subscribe to".
if os.environ.get('DEPLOY_DMS_SERVICE_EVENTS') == 'true':
    from stacks.dms_service_events_stack import DmsServiceEventsStack
    dms_service_events_stack = DmsServiceEventsStack(
        app,
        f"{stack_prefix}-dms-service-events",
        stage=DEPLOYMENT_STAGE,
        vehicles_table_name=storage_stack.tables['vehicles'].table_name,
        env=env,
        description="Guidance for Connected Mobility (SO9618) - DMS Service Event Subscriber"
    )
    dms_service_events_stack.add_dependency(storage_stack)

# Meridian Ingestion Stack — REMOVED 2026-09-19.
# Was: spec .kiro/specs/2026-09-11-cms-cs-meridian-ingestion/'s Pattern-2
# side-loading telemetry pipe (generator -> HTTP ingest -> cs-source-telemetry
# DynamoDB -> puller -> direct put_item into cms-storage-telemetry, bypassing
# cms-telemetry-preprocessed entirely). Removed per
# docs/data-products-design.md §6.3's explicit "No side-loading" rule: a
# direct row write produces no trips, no safety events, no maintenance
# alerts, and no geofence hits — silently. The correct, Kafka-native Meridian
# pipeline (MSK topic cs-product-meridian-ev -> generic OEMTelemetryProcessor,
# live-verified 2026-09-13/14) is a different, unrelated stack (no
# MeridianIngestionStack involved) and is untouched by this removal.
# See issues/2026-09-19-remove-meridian-side-loading-pipeline/.

app.synth()
