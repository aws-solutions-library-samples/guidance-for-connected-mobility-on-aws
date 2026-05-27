"""Bedrock Agents Stack — codifies the 4 CMS fleet-management agents.

What this creates
-----------------
- One shared IAM service role used by all agents (model invoke + inference
  profiles + optional tools Lambda invoke + optional KB access).
- Four Bedrock Agents:
    * cms-cost-agent
    * cms-maintenance-agent
    * cms-rebalancing-agent
    * cms-recall-warranty-agent
- One `prod` alias per agent (the alias ID is what the UI invokes).

Source of truth
---------------
Agent configuration is loaded from JSON snapshots in
``deployment/scripts/bedrock_agents_snapshot/``. Those files are exported from
the reference-working region (us-east-2) and committed to the repo. Edit them
there, not in this file, so changes are traceable.

Dependencies
------------
- Foundation model: ``us.anthropic.claude-sonnet-4-20250514-v1:0`` inference
  profile. Must be enabled in the target region via Bedrock console (model
  access grants).
- Tools Lambda (optional): pass via context
  ``-c bedrockAgentToolsLambdaArn=arn:aws:lambda:...:function:cms-prod-vfo-tools``.
  If not provided, action groups are skipped and the agent can still answer
  questions but cannot call tools.
"""

import json
import os
from typing import Any, Dict, List, Optional

from aws_cdk import (
    Stack,
    Duration,
    aws_bedrock as bedrock,
    aws_iam as iam,
    aws_lambda as lambda_,
    CfnOutput,
)
from constructs import Construct


SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "bedrock_agents_snapshot",
)

AGENT_SNAPSHOTS = [
    "cms-cost-agent",
    "cms-maintenance-agent",
    "cms-rebalancing-agent",
    "cms-recall-warranty-agent",
]

# Supervisor agent delegates to the above specialists. Built LAST so we can
# reference the specialists' alias ARNs in its agentCollaborators config.
SUPERVISOR_SNAPSHOT = "cms-virtual-fleet-operator"


class BedrockAgentsStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        tools_lambda_arn: Optional[str] = None,
        deploy_tools_lambda: bool = False,
        foundation_model_override: Optional[str] = None,
        kb_bucket_name: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self._model_override = foundation_model_override
        deployment_stage = construct_id.split("-")[1] if "-" in construct_id else "dev"

        # ── Shared agent service role ────────────────────────────────────
        # Mirrors the permissions of the CMS agent role.  The role name is
        # region-scoped because IAM is a global service but the agents live
        # per-region — the us-east-2 environment's role shouldn't collide
        # with the us-east-1 deploy.
        #
        # Uses the AWS-managed AmazonBedrockFullAccess policy (same as the
        # us-east-2 hand-rolled role). Narrower inline policies caused the
        # AssociateAgentCollaborator call to fail with "insufficient
        # permissions" during supervisor creation — Bedrock's multi-agent
        # collaboration uses a set of Bedrock-internal actions that aren't
        # publicly documented, so we rely on the managed policy rather than
        # trying to enumerate them.
        self.agent_role = iam.Role(
            self,
            "BedrockAgentRole",
            role_name=f"cms-{deployment_stage}-bedrock-agent-role-{self.region}",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description=f"Shared service role for CMS Bedrock agents ({deployment_stage}/{self.region})",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonBedrockFullAccess"),
                # Agents query DynamoDB directly via some of the function tools.
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonDynamoDBReadOnlyAccess"),
            ],
        )

        # Model invoke (including cross-region via inference profiles).
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/*",
                    # Inference profiles route to foundation models in other regions.
                    "arn:aws:bedrock:*::foundation-model/anthropic.*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:application-inference-profile/*",
                ],
            )
        )
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:GetInferenceProfile"],
                resources=[f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*"],
            )
        )

        # Tools Lambda invoke (optional).
        if tools_lambda_arn:
            self.agent_role.add_to_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["lambda:InvokeFunction"],
                    resources=[tools_lambda_arn],
                )
            )

        # ── Tools Lambda (optional, self-deployed) ───────────────────────
        # When deploy_tools_lambda=True, we build cms-<stage>-vfo-tools
        # alongside the agents from ``services/vfo-pipeline/vfo_tools.py``.
        # This keeps the Bedrock agents deployment self-contained per region —
        # no cross-region Lambda invocation, no dependency on a separate VFO
        # deployment workflow.
        #
        # If tools_lambda_arn is ALSO provided, it takes precedence (lets
        # operators BYO a custom tools Lambda).
        self.tools_lambda: Optional[lambda_.Function] = None
        if deploy_tools_lambda and not tools_lambda_arn:
            self.tools_lambda = self._create_tools_lambda(deployment_stage, kb_bucket_name)
            tools_lambda_arn = self.tools_lambda.function_arn

            # Permit agents to invoke the Lambda at runtime.
            self.agent_role.add_to_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["lambda:InvokeFunction"],
                    resources=[tools_lambda_arn],
                )
            )
            # And grant Bedrock the resource-based permission (the inverse
            # direction — required for Bedrock Agent action groups).
            self.tools_lambda.add_permission(
                "BedrockAgentInvoke",
                principal=iam.ServicePrincipal("bedrock.amazonaws.com"),
                action="lambda:InvokeFunction",
                source_account=self.account,
            )

        # ── Agents ───────────────────────────────────────────────────────
        self.agents: Dict[str, bedrock.CfnAgent] = {}
        self.aliases: Dict[str, bedrock.CfnAgentAlias] = {}

        # Build the 4 specialist agents first. The supervisor depends on their
        # alias ARNs for collaborator routing.
        for snapshot_name in AGENT_SNAPSHOTS:
            snapshot_path = os.path.join(SNAPSHOT_DIR, f"{snapshot_name}.json")
            if not os.path.exists(snapshot_path):
                raise FileNotFoundError(
                    f"Agent snapshot missing: {snapshot_path}\n"
                    "Re-export from the reference-working region using "
                    "deployment/scripts/export_bedrock_agents.py."
                )
            with open(snapshot_path) as f:
                snap = json.load(f)

            agent = self._build_agent(snapshot_name, snap, tools_lambda_arn)
            self.agents[snapshot_name] = agent

            # prod alias — Bedrock auto-materializes a new version on create.
            alias = bedrock.CfnAgentAlias(
                self,
                f"{self._pascal(snapshot_name)}ProdAlias",
                agent_alias_name="prod",
                agent_id=agent.attr_agent_id,
                description=f"Production alias for {snapshot_name}",
            )
            alias.add_dependency(agent)
            self.aliases[snapshot_name] = alias

        # Build the supervisor last so we can wire the specialists as collaborators.
        supervisor_path = os.path.join(SNAPSHOT_DIR, f"{SUPERVISOR_SNAPSHOT}.json")
        if os.path.exists(supervisor_path):
            with open(supervisor_path) as f:
                supervisor_snap = json.load(f)

            supervisor = self._build_supervisor_agent(
                SUPERVISOR_SNAPSHOT, supervisor_snap, tools_lambda_arn
            )
            self.agents[SUPERVISOR_SNAPSHOT] = supervisor

            supervisor_alias = bedrock.CfnAgentAlias(
                self,
                f"{self._pascal(SUPERVISOR_SNAPSHOT)}ProdAlias",
                agent_alias_name="prod",
                agent_id=supervisor.attr_agent_id,
                description=f"Production alias for {SUPERVISOR_SNAPSHOT}",
            )
            supervisor_alias.add_dependency(supervisor)
            self.aliases[SUPERVISOR_SNAPSHOT] = supervisor_alias

        # ── Outputs ──────────────────────────────────────────────────────
        # Exported so ui_stack.py can Fn::ImportValue them without needing a
        # hard CDK dependency.
        for snapshot_name, agent in self.agents.items():
            CfnOutput(
                self,
                f"{self._pascal(snapshot_name)}AgentId",
                value=agent.attr_agent_id,
                export_name=f"{construct_id}-{snapshot_name}-agent-id",
                description=f"Agent ID for {snapshot_name}",
            )
        for snapshot_name, alias in self.aliases.items():
            CfnOutput(
                self,
                f"{self._pascal(snapshot_name)}ProdAliasId",
                value=alias.attr_agent_alias_id,
                export_name=f"{construct_id}-{snapshot_name}-prod-alias-id",
                description=f"Prod alias ID for {snapshot_name}",
            )

        CfnOutput(
            self,
            "BedrockAgentRoleArn",
            value=self.agent_role.role_arn,
            export_name=f"{construct_id}-agent-role-arn",
        )

        # Primary agent for the landing-page chat — points at the supervisor if
        # present, otherwise falls back to the maintenance agent.
        primary_key = SUPERVISOR_SNAPSHOT if SUPERVISOR_SNAPSHOT in self.agents else "cms-maintenance-agent"
        self.primary_agent_id = self.agents[primary_key].attr_agent_id
        self.primary_alias_id = self.aliases[primary_key].attr_agent_alias_id
        self.primary_agent_name = primary_key

        CfnOutput(
            self,
            "PrimaryAgentId",
            value=self.primary_agent_id,
            export_name=f"{construct_id}-primary-agent-id",
            description=f"Agent ID used by the UI's landing-page chat ({primary_key})",
        )
        CfnOutput(
            self,
            "PrimaryAliasId",
            value=self.primary_alias_id,
            export_name=f"{construct_id}-primary-alias-id",
            description=f"Alias ID used by the UI's landing-page chat ({primary_key})",
        )
        CfnOutput(
            self,
            "PrimaryAgentName",
            value=self.primary_agent_name,
            export_name=f"{construct_id}-primary-agent-name",
        )

    # ────────────────────────────────────────────────────────────────────
    def _build_agent(
        self, agent_key: str, snap: Dict[str, Any], tools_lambda_arn: Optional[str]
    ) -> bedrock.CfnAgent:
        """Build a CfnAgent from a snapshot dict (specialist — no collaborators)."""
        action_groups = self._build_action_groups(snap.get("actionGroups", []), tools_lambda_arn)

        kwargs: Dict[str, Any] = {
            "agent_name": snap["agentName"],
            "foundation_model": self._model_override or snap["foundationModel"],
            "instruction": snap["instruction"],
            "agent_resource_role_arn": self.agent_role.role_arn,
            "idle_session_ttl_in_seconds": snap.get("idleSessionTTLInSeconds", 600),
            "description": snap.get("description") or f"CMS {agent_key}",
            "auto_prepare": True,
        }
        if action_groups:
            kwargs["action_groups"] = action_groups

        return bedrock.CfnAgent(self, self._pascal(agent_key), **kwargs)

    def _build_supervisor_agent(
        self, agent_key: str, snap: Dict[str, Any], tools_lambda_arn: Optional[str]
    ) -> bedrock.CfnAgent:
        """Build the supervisor agent with agent_collaborators wired to the 4 specialists.

        The supervisor delegates each question to one or more specialists based
        on the collaboration instructions. The alias ARNs it references must
        already be created by this stack — callers must build specialists first.
        """
        action_groups = self._build_action_groups(snap.get("actionGroups", []), tools_lambda_arn)

        # Wire collaborators. Each references a specialist built earlier in this stack.
        collaborators: List[bedrock.CfnAgent.AgentCollaboratorProperty] = []
        for c in snap.get("collaborators", []):
            specialist_key = c["specialistAgentKey"]
            specialist_alias = self.aliases.get(specialist_key)
            if specialist_alias is None:
                raise ValueError(
                    f"Supervisor references unknown specialist '{specialist_key}'. "
                    f"Known agents: {list(self.aliases.keys())}"
                )
            # Build the alias ARN. CfnAgentAlias.attr_agent_alias_arn is the full ARN.
            collaborators.append(
                bedrock.CfnAgent.AgentCollaboratorProperty(
                    agent_descriptor=bedrock.CfnAgent.AgentDescriptorProperty(
                        alias_arn=specialist_alias.attr_agent_alias_arn,
                    ),
                    collaboration_instruction=c["collaborationInstruction"],
                    collaborator_name=c["collaboratorName"],
                    relay_conversation_history=c.get("relayConversationHistory", "TO_COLLABORATOR"),
                )
            )

        kwargs: Dict[str, Any] = {
            "agent_name": snap["agentName"],
            "foundation_model": self._model_override or snap["foundationModel"],
            "instruction": snap["instruction"],
            "agent_resource_role_arn": self.agent_role.role_arn,
            "idle_session_ttl_in_seconds": snap.get("idleSessionTTLInSeconds", 600),
            "description": snap.get("description") or f"CMS {agent_key}",
            "auto_prepare": True,
            "agent_collaboration": snap.get("agentCollaboration", "SUPERVISOR"),
        }
        if action_groups:
            kwargs["action_groups"] = action_groups
        if collaborators:
            kwargs["agent_collaborators"] = collaborators

        supervisor = bedrock.CfnAgent(self, self._pascal(agent_key), **kwargs)
        # Explicit dependencies on specialist aliases (CDK usually infers, but
        # make it obvious in the template).
        for alias in self.aliases.values():
            supervisor.add_dependency(alias)

        # Supervisor agent needs permission to invoke its collaborator aliases.
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeAgent"],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:agent-alias/*",
                ],
            )
        )

        return supervisor

    def _build_action_groups(
        self, snapshots: List[Dict[str, Any]], tools_lambda_arn: Optional[str]
    ) -> List[bedrock.CfnAgent.AgentActionGroupProperty]:
        """Translate snapshot action groups to CfnAgent action group properties.

        If tools_lambda_arn is None, returns an empty list — the agent will
        still deploy and answer questions, it just can't call tools.
        """
        if not tools_lambda_arn:
            return []

        out: List[bedrock.CfnAgent.AgentActionGroupProperty] = []
        for ag in snapshots:
            # Convert the snapshot functionSchema into CDK types.
            schema = ag.get("functionSchema") or {}
            functions_raw = schema.get("functions", [])
            functions: List[bedrock.CfnAgent.FunctionProperty] = []
            for fn in functions_raw:
                params_raw = fn.get("parameters") or {}
                params: Dict[str, bedrock.CfnAgent.ParameterDetailProperty] = {}
                for pname, pdef in params_raw.items():
                    params[pname] = bedrock.CfnAgent.ParameterDetailProperty(
                        type=pdef.get("type", "string"),
                        description=pdef.get("description"),
                        required=bool(pdef.get("required", False)),
                    )
                functions.append(
                    bedrock.CfnAgent.FunctionProperty(
                        name=fn["name"],
                        description=fn.get("description"),
                        parameters=params,
                        require_confirmation=fn.get("requireConfirmation", "DISABLED"),
                    )
                )

            out.append(
                bedrock.CfnAgent.AgentActionGroupProperty(
                    action_group_name=ag["actionGroupName"],
                    action_group_state=ag.get("actionGroupState", "ENABLED"),
                    description=ag.get("description") or f"{ag['actionGroupName']} functions",
                    action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                        lambda_=tools_lambda_arn,
                    ),
                    function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                        functions=functions,
                    ),
                )
            )
        return out

    @staticmethod
    def _pascal(s: str) -> str:
        return "".join(p.capitalize() for p in s.replace("_", "-").split("-"))

    def _create_tools_lambda(
        self, deployment_stage: str, kb_bucket_name: Optional[str]
    ) -> lambda_.Function:
        """Deploy cms-<stage>-vfo-tools from ``services/vfo-pipeline/vfo_tools.py``.

        The code lives in the repo (region-agnostic: uses AWS_REGION from the
        runtime, env-var-driven table names). Invoked by all 4 specialist agents
        via their action groups.
        """
        lambda_src = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..",
            "services",
            "vfo-pipeline",
        )

        role = iam.Role(
            self,
            "VfoToolsLambdaRole",
            role_name=f"cms-{deployment_stage}-vfo-tools-role-{self.region}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description=f"Execution role for cms-{deployment_stage}-vfo-tools Lambda ({self.region})",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonDynamoDBReadOnlyAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3ReadOnlyAccess"),
            ],
        )

        env = {
            # Table names match what storage_stack.py creates. If storage_stack
            # names drift these need to be updated in both places; a future
            # improvement is to cross-reference via Fn::ImportValue.
            "FLEETS_TABLE": f"cms-{deployment_stage}-storage-fleets",
            "VEHICLES_TABLE": f"cms-{deployment_stage}-storage-vehicles",
            "SAFETY_EVENTS_TABLE": f"cms-{deployment_stage}-storage-safety-events",
            "MAINTENANCE_ALERTS_TABLE": f"cms-{deployment_stage}-storage-maintenance-alerts",
            "TRIPS_TABLE": f"cms-{deployment_stage}-storage-trips",
            "DRIVERS_TABLE": f"cms-{deployment_stage}-storage-drivers",
            "WARRANTY_CLAIMS_TABLE": f"cms-{deployment_stage}-storage-warranty-claims",
            "SERVICE_HISTORY_TABLE": f"cms-{deployment_stage}-storage-service-history",
            "DTC_HISTORY_TABLE": f"cms-{deployment_stage}-storage-dtc-history",
            "TELEMETRY_TABLE": f"cms-{deployment_stage}-storage-telemetry",
            "KB_BUCKET": kb_bucket_name or f"cms-{deployment_stage}-vfo-knowledge-base",
        }

        fn = lambda_.Function(
            self,
            "VfoToolsLambda",
            function_name=f"cms-{deployment_stage}-vfo-tools",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="vfo_tools.lambda_handler",
            code=lambda_.Code.from_asset(lambda_src),
            role=role,
            timeout=Duration.seconds(30),
            memory_size=256,
            environment=env,
            description=f"Bedrock Agent tool functions for CMS {deployment_stage} ({self.region})",
        )

        CfnOutput(
            self,
            "VfoToolsLambdaArn",
            value=fn.function_arn,
            export_name=f"cms-{deployment_stage}-bedrock-agents-vfo-tools-arn",
            description="ARN of the deployed cms-<stage>-vfo-tools Lambda",
        )

        return fn
