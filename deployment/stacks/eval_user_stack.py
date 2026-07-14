"""CDK stack: provision a dedicated Cognito user for the Tier 3 eval pipeline.

Only deployed in staging (DEPLOYMENT_STAGE=staging). Prod deploys skip this
stack entirely. The eval user has read-only scope to test fleets/vehicles.
Password is auto-generated and stored in Secrets Manager; CI retrieves it
at eval-run time via the CMS_EVAL_PASSWORD env populated from Secrets Manager.
"""
from __future__ import annotations

import os

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Fn,
    Stack,
    aws_cognito as cognito,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class EvalUserStack(Stack):
    """Provisions a dedicated cms-eval-runner Cognito user for staging eval runs."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        deployment_stage = os.environ.get("DEPLOYMENT_STAGE", "dev")
        if deployment_stage != "staging":
            raise ValueError(
                f"EvalUserStack must only be deployed when DEPLOYMENT_STAGE=staging; "
                f"got {deployment_stage!r}. Exclude this stack from prod synthesis."
            )

        # Resolve user pool ID + client ID from the ui_stack exports.
        # ui_stack exports: {construct_id}-user-pool-id / {construct_id}-user-pool-client-id
        # where construct_id = cms-{stage}-ui
        ui_stack_id = f"cms-{deployment_stage}-ui"
        user_pool_id = Fn.import_value(f"{ui_stack_id}-user-pool-id")
        user_pool_client_id = Fn.import_value(f"{ui_stack_id}-user-pool-client-id")

        # Auto-generate the eval user's password via Secrets Manager.
        eval_password_secret = secretsmanager.Secret(
            self,
            "EvalUserPassword",
            secret_name=f"cms-{deployment_stage}-eval-runner-password",
            description="Password for cms-eval-runner Cognito user used by Tier 3 eval pipeline.",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                exclude_characters=" $\"'",
                password_length=24,
                require_each_included_type=True,
            ),
        )

        # Create the eval user in the existing user pool.
        # CfnUserPoolUser is the only CDK mechanism for creating a Cognito user.
        # NOTE: The CMS UI user pool is configured with UsernameAttributes=[email],
        # which means email IS the sign-in username. We MUST pass the email as the
        # username field; passing a non-email value yields
        # "Invalid request provided: AWS::Cognito::UserPoolUser".
        # NOTE: Cognito does not allow setting a permanent password at creation time
        # via CloudFormation. After deploy, run admin_set_user_password with the
        # secret value. See docs/DEPLOYMENT.md for the post-deploy runbook step.
        eval_user_email = "cms-eval-runner@example.invalid"
        cognito.CfnUserPoolUser(
            self,
            "EvalUser",
            user_pool_id=user_pool_id,
            username=eval_user_email,
            user_attributes=[
                cognito.CfnUserPoolUser.AttributeTypeProperty(
                    name="email_verified",
                    value="true",
                ),
            ],
            message_action="SUPPRESS",  # don't send invitation email
        )

        # CFN outputs
        CfnOutput(
            self,
            "EvalUserName",
            value=eval_user_email,
            export_name=f"cms-{deployment_stage}-eval-user-name",
        )
        CfnOutput(
            self,
            "EvalPasswordSecretArn",
            value=eval_password_secret.secret_arn,
            export_name=f"cms-{deployment_stage}-eval-password-secret-arn",
        )
        # Re-export the pool + client IDs so the eval pipeline can read them
        # from a single stack without depending on the ui_stack directly.
        CfnOutput(
            self,
            "EvalUserPoolId",
            value=user_pool_id,
            export_name=f"cms-{deployment_stage}-eval-user-pool-id",
        )
        CfnOutput(
            self,
            "EvalUserPoolClientId",
            value=user_pool_client_id,
            export_name=f"cms-{deployment_stage}-eval-user-pool-client-id",
        )
