"""
Amazon Connect Stack — Contact center for fleet driver escalation.

Deploys:
- Connect instance (CONNECT_MANAGED identity)
- Operating hours (24/7 for P0/P1, business hours for P2)
- Queues: P0-Emergency, P1-Urgent, P2-Routine
- Contact flows: escalation-with-context, callback-to-driver
- Context lookup Lambda (reads escalation records from action queue)
- Customer Profiles domain with Vehicle object type

Gated by: DEPLOY_CONNECT=true
"""

import os
import json
from aws_cdk import (
    Stack, Duration, CfnOutput,
    aws_connect as connect,
    aws_customerprofiles as profiles,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
)
from constructs import Construct


class ConnectStack(Stack):
    def __init__(self, scope: Construct, id: str, *, storage_tables=None, **kwargs):
        super().__init__(scope, id, **kwargs)

        stage = os.environ.get("DEPLOYMENT_STAGE", "dev")
        prefix = f"cms-{stage}"

        # ─── Connect Instance ─────────────────────────────────────────────
        instance = connect.CfnInstance(self, "ConnectInstance",
            identity_management_type="CONNECT_MANAGED",
            attributes=connect.CfnInstance.AttributesProperty(
                inbound_calls=True,
                outbound_calls=True,
                contactflow_logs=True,
                auto_resolve_best_voices=True,
            ),
            instance_alias=f"{prefix}-contact-center",
        )

        # ─── Operating Hours ──────────────────────────────────────────────
        all_days = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
        weekdays = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]

        hours_24x7 = connect.CfnHoursOfOperation(self, "Hours24x7",
            instance_arn=instance.attr_arn,
            name=f"{prefix}-24x7",
            time_zone="US/Eastern",
            config=[connect.CfnHoursOfOperation.HoursOfOperationConfigProperty(
                day=day,
                start_time=connect.CfnHoursOfOperation.HoursOfOperationTimeSliceProperty(hours=0, minutes=0),
                end_time=connect.CfnHoursOfOperation.HoursOfOperationTimeSliceProperty(hours=23, minutes=59),
            ) for day in all_days],
        )

        business_hours = connect.CfnHoursOfOperation(self, "BusinessHours",
            instance_arn=instance.attr_arn,
            name=f"{prefix}-business-hours",
            time_zone="US/Eastern",
            config=[connect.CfnHoursOfOperation.HoursOfOperationConfigProperty(
                day=day,
                start_time=connect.CfnHoursOfOperation.HoursOfOperationTimeSliceProperty(hours=7, minutes=0),
                end_time=connect.CfnHoursOfOperation.HoursOfOperationTimeSliceProperty(hours=19, minutes=0),
            ) for day in weekdays],
        )

        # ─── Queues ───────────────────────────────────────────────────────
        queue_p0 = connect.CfnQueue(self, "QueueP0",
            instance_arn=instance.attr_arn,
            name=f"{prefix}-P0-Emergency",
            description="Critical safety — roadside assist, imminent danger",
            hours_of_operation_arn=hours_24x7.attr_hours_of_operation_arn,
        )
        queue_p1 = connect.CfnQueue(self, "QueueP1",
            instance_arn=instance.attr_arn,
            name=f"{prefix}-P1-Urgent",
            description="Urgent service — DTC critical, brake failure",
            hours_of_operation_arn=hours_24x7.attr_hours_of_operation_arn,
        )
        queue_p2 = connect.CfnQueue(self, "QueueP2",
            instance_arn=instance.attr_arn,
            name=f"{prefix}-P2-Routine",
            description="Routine — scheduling, warranty questions",
            hours_of_operation_arn=business_hours.attr_hours_of_operation_arn,
        )

        # ─── Context Lookup Lambda ────────────────────────────────────────
        action_queue_table = f"cms-{stage}-vfo-action-queue"

        context_fn = lambda_.Function(self, "ContextLookup",
            function_name=f"{prefix}-connect-context-lookup",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            timeout=Duration.seconds(8),
            memory_size=256,
            log_retention=logs.RetentionDays.ONE_MONTH,
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../../lambdas/connect-context-lookup")
            ),
            environment={
                "ACTION_QUEUE_TABLE": action_queue_table,
                "CMS_STAGE": stage,
            },
        )
        context_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:Query"],
            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/{action_queue_table}"],
        ))
        context_fn.add_permission("ConnectInvoke",
            principal=iam.ServicePrincipal("connect.amazonaws.com"),
            source_arn=instance.attr_arn,
        )

        # Associate Lambda with Connect
        connect.CfnIntegrationAssociation(self, "LambdaAssoc",
            instance_id=instance.attr_id,
            integration_type="LAMBDA_FUNCTION",
            integration_arn=context_fn.function_arn,
        )

        # ─── Contact Flows ────────────────────────────────────────────────
        escalation_flow_content = self._load_flow(
            "escalation-with-context.json",
            ContextLookupLambdaArn=context_fn.function_arn,
            P0QueueArn=queue_p0.attr_queue_arn,
            P1QueueArn=queue_p1.attr_queue_arn,
            P2QueueArn=queue_p2.attr_queue_arn,
        )
        escalation_flow = connect.CfnContactFlow(self, "EscalationFlow",
            instance_arn=instance.attr_arn,
            name=f"{prefix}-escalation-with-context",
            description="AI agent escalation — severity routing with vehicle context",
            type="CONTACT_FLOW",
            content=escalation_flow_content,
        )

        callback_flow_content = self._load_flow(
            "callback-to-driver.json",
            ContextLookupLambdaArn=context_fn.function_arn,
            P0QueueArn=queue_p0.attr_queue_arn,
        )
        callback_flow = connect.CfnContactFlow(self, "CallbackFlow",
            instance_arn=instance.attr_arn,
            name=f"{prefix}-callback-to-driver",
            description="Outbound call to driver on P0 — greets with context, bridges to agent",
            type="CONTACT_FLOW",
            content=callback_flow_content,
        )

        # ─── Customer Profiles ────────────────────────────────────────────
        profiles_domain = profiles.CfnDomain(self, "ProfilesDomain",
            domain_name=f"{prefix}-vehicle-profiles",
            default_expiration_days=365,
        )

        profiles.CfnObjectType(self, "VehicleObjectType",
            domain_name=profiles_domain.domain_name,
            object_type_name="Vehicle",
            description="Connected vehicle — VIN, make/model, DTCs, service history",
            allow_profile_creation=True,
            fields=[
                profiles.CfnObjectType.FieldMapProperty(field_name="vin", object_type_field=profiles.CfnObjectType.ObjectTypeFieldProperty(source="_source.vin", content_type="STRING")),
                profiles.CfnObjectType.FieldMapProperty(field_name="vehicleId", object_type_field=profiles.CfnObjectType.ObjectTypeFieldProperty(source="_source.vehicleId", content_type="STRING")),
                profiles.CfnObjectType.FieldMapProperty(field_name="make", object_type_field=profiles.CfnObjectType.ObjectTypeFieldProperty(source="_source.make", content_type="STRING")),
                profiles.CfnObjectType.FieldMapProperty(field_name="model", object_type_field=profiles.CfnObjectType.ObjectTypeFieldProperty(source="_source.model", content_type="STRING")),
                profiles.CfnObjectType.FieldMapProperty(field_name="year", object_type_field=profiles.CfnObjectType.ObjectTypeFieldProperty(source="_source.year", content_type="STRING")),
                profiles.CfnObjectType.FieldMapProperty(field_name="activeDtcs", object_type_field=profiles.CfnObjectType.ObjectTypeFieldProperty(source="_source.activeDtcs", content_type="STRING")),
                profiles.CfnObjectType.FieldMapProperty(field_name="driverName", object_type_field=profiles.CfnObjectType.ObjectTypeFieldProperty(source="_source.driverName", content_type="STRING")),
                profiles.CfnObjectType.FieldMapProperty(field_name="driverPhone", object_type_field=profiles.CfnObjectType.ObjectTypeFieldProperty(source="_source.driverPhone", content_type="STRING")),
            ],
        )

        # ─── Outputs ──────────────────────────────────────────────────────
        CfnOutput(self, "ConnectInstanceArn", value=instance.attr_arn)
        CfnOutput(self, "ConnectInstanceId", value=instance.attr_id)
        CfnOutput(self, "P0EmergencyQueueArn", value=queue_p0.attr_queue_arn)
        CfnOutput(self, "P1UrgentQueueArn", value=queue_p1.attr_queue_arn)
        CfnOutput(self, "P2RoutineQueueArn", value=queue_p2.attr_queue_arn)
        CfnOutput(self, "EscalationFlowId", value=escalation_flow.attr_contact_flow_arn.split("/")[-1])
        CfnOutput(self, "CallbackFlowId", value=callback_flow.attr_contact_flow_arn.split("/")[-1])
        CfnOutput(self, "ContextLookupArn", value=context_fn.function_arn)

    def _load_flow(self, filename: str, **replacements) -> str:
        flow_dir = os.path.join(os.path.dirname(__file__), "contact-flows")
        with open(os.path.join(flow_dir, filename)) as f:
            content = f.read()
        for key, value in replacements.items():
            content = content.replace(f"${{{key}}}", value)
        return content
