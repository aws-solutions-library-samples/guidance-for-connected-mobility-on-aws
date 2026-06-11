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
- Foundation model: ``us.anthropic.claude-sonnet-4-6`` inference
  profile. Must be enabled in the target region via Bedrock console (model
  access grants).
- Tools Lambda (optional): pass via context
  ``-c bedrockAgentToolsLambdaArn=arn:aws:lambda:...:function:cms-prod-vfo-tools``.
  If not provided, action groups are skipped and the agent can still answer
  questions but cannot call tools.

Inference-profile resolution (Group 3.3 of the clean-deploy harness)
--------------------------------------------------------------------
The pinned inference-profile ID can be overridden per-region via the
``BEDROCK_INFERENCE_PROFILE_ID`` environment variable (set by
``deployment/scripts/preflight_per_region.py --emit-env`` at clean-deploy
time). Resolution order:

  1. ``BEDROCK_INFERENCE_PROFILE_ID`` env var (if non-empty after strip).
  2. ``foundation_model_override`` constructor arg (CDK context flag
     ``-c bedrockAgentModel=<id>`` — the path Makefile uses).
  3. Snapshot ``foundationModel`` field.

Resolution applies BOTH to the agent's ``foundation_model`` property AND
to the IAM ``PolicyStatement`` resource ARNs (per the
``amazon-bedrock`` skill's inference-profile-vs-foundation-model ARN
duality: inference-profile ARN includes the account ID and embeds the
geo-prefixed profile ID; foundation-model ARN has no account ID and uses
the geo-stripped model ID).

When the env var is unset, the resolved profile equals the snapshot's
``us.anthropic.claude-sonnet-4-6`` (or whatever was configured via
``-c bedrockAgentModel=...``), so existing us-west-2 staging deploys
keep working unchanged.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_bedrock as bedrock,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
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


# Inference-profile validation. Allow the geo-prefix + body separated by
# at least one dot (e.g. "us.anthropic.claude-sonnet-4-6",
# "jp.anthropic.claude-sonnet-4-6", "apac.anthropic.claude-sonnet-4-test").
# Whitespace/control chars and missing-prefix forms are rejected — we want
# a synth-time error, not a silent fallback (Group 3.3 constraint).
_INFERENCE_PROFILE_RE = re.compile(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9_.\-]+$")


def _validate_inference_profile_id(profile_id: str, source: str) -> str:
    """Validate a resolved inference-profile ID; raise if malformed.

    `source` names where the value came from (env var name, kwarg name)
    so the error message points the operator at the right knob.
    """
    if profile_id != profile_id.strip() or any(c.isspace() for c in profile_id):
        raise ValueError(
            f"BedrockAgentsStack: {source}={profile_id!r} contains "
            "whitespace/control chars. Expected a value like "
            "'jp.anthropic.claude-sonnet-4-6'."
        )
    if not _INFERENCE_PROFILE_RE.match(profile_id):
        raise ValueError(
            f"BedrockAgentsStack: {source}={profile_id!r} does not match "
            "'<geo-prefix>.<model-id>' (e.g. 'jp.anthropic.claude-sonnet-4-6'). "
            "Set BEDROCK_INFERENCE_PROFILE_ID to a valid SYSTEM_DEFINED "
            "inference-profile ID for the target region (resolve via "
            "deployment/scripts/preflight_per_region.py --emit-env)."
        )
    return profile_id


def _split_inference_profile(profile_id: str) -> Tuple[str, str]:
    """Split a profile ID into (geo_prefix, foundation_model_id).

    Example: ``"jp.anthropic.claude-sonnet-4-6"`` →
    ``("jp", "anthropic.claude-sonnet-4-6")``.
    """
    geo, fm_id = profile_id.split(".", 1)
    return geo, fm_id


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

        # ── Resolve the inference profile (Group 3.3) ─────────────────────
        # Order: env var → constructor override → first-snapshot fallback.
        # Validates whitespace and shape; raises at synth time on malformed
        # values (no silent fallback). The resolved values feed BOTH the
        # IAM PolicyStatement resource ARNs below AND each agent's
        # `foundation_model` property in `_build_agent` /
        # `_build_supervisor_agent`.
        env_profile = os.environ.get("BEDROCK_INFERENCE_PROFILE_ID", "")
        env_profile_stripped = env_profile.strip()
        if env_profile_stripped:
            # Validate the env var BEFORE comparing to override — the
            # constraint says malformed env values must raise, not silently
            # fall through to the override.
            self._resolved_profile_id = _validate_inference_profile_id(
                env_profile, "BEDROCK_INFERENCE_PROFILE_ID"
            )
        elif self._model_override:
            self._resolved_profile_id = _validate_inference_profile_id(
                self._model_override, "foundation_model_override"
            )
        else:
            # Last resort: peek at the first snapshot's `foundationModel`
            # to seed IAM. The same value is reused per-agent in
            # `_build_agent` so the IAM and agent property stay aligned.
            baseline = self._read_baseline_from_snapshot()
            self._resolved_profile_id = _validate_inference_profile_id(
                baseline, "snapshot foundationModel"
            )

        _, self._resolved_fm_id = _split_inference_profile(self._resolved_profile_id)

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
        # Per the amazon-bedrock skill: TWO ARN forms are required.
        #   - inference-profile ARN includes the account ID and embeds the
        #     resolved profile ID (e.g.
        #     `arn:aws:bedrock:*:<account>:inference-profile/jp.anthropic.claude-sonnet-4-6`).
        #     Wildcard region permits cross-region routing within the
        #     account.
        #   - foundation-model ARN has NO account ID and uses the
        #     geo-stripped model ID (e.g.
        #     `arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6`).
        #     Wildcard region matches the underlying foundation model in
        #     whichever region the inference profile dispatches to.
        # Strands/Bedrock Converse uses InvokeModelWithResponseStream, so
        # both actions must be granted.
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    # Foundation model (no account ID; wildcard region).
                    f"arn:aws:bedrock:*::foundation-model/{self._resolved_fm_id}",
                    # Inference profile (with account ID; wildcard region for
                    # cross-region routing within the account).
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/{self._resolved_profile_id}",
                    # Operator-created application inference profiles
                    # (region-scoped; wildcard suffix is intentional — these
                    # are minted at runtime, not pinned to a single ID).
                    f"arn:aws:bedrock:{self.region}:{self.account}:application-inference-profile/*",
                ],
            )
        )
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:GetInferenceProfile"],
                resources=[
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/{self._resolved_profile_id}",
                ],
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

        # ── VFO knowledge-base bucket ────────────────────────────────────
        # Globally-named S3 resource. Brought under CDK lifecycle by spec
        # `2026-06-04-cms-vfo-kb-bucket-region-suffix` after specs
        # `2026-06-03-cms-storage-bucket-region-suffix` and
        # `2026-06-03-cms-ui-frontend-bucket-region-suffix` codified the
        # `-{region}-{account}` discipline (see
        # `~/.kiro/steering/cross-region-namespace.md`). The deterministic
        # `(stage, account, region)` name prevents cross-region collision on
        # the global S3 namespace.
        #
        # Worst-case length: staging+ap-northeast-1+12-digit-account = 58
        # chars (≤63). Regression assertion at
        # `deployment/scripts/test_bucket_name_lengths.py`.
        #
        # The legacy `kb_bucket_name` constructor param + `bedrockAgentKbBucket`
        # context flag are preserved as no-op pass-throughs for backwards
        # compatibility — see `decisions.md` D3.
        self.kb_bucket = s3.Bucket(
            self,
            "VfoKnowledgeBaseBucket",
            bucket_name=f"cms-{deployment_stage}-vfo-knowledge-base-{self.region}-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        CfnOutput(
            self,
            "VfoKnowledgeBaseBucketName",
            value=self.kb_bucket.bucket_name,
            export_name=f"{construct_id}-vfo-kb-bucket",
            description="VFO knowledge-base bucket (region+account suffixed)",
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
            self.tools_lambda = self._create_tools_lambda(deployment_stage, self.kb_bucket.bucket_name)
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
    def _read_baseline_from_snapshot(self) -> str:
        """Peek at the first specialist snapshot's `foundationModel`.

        Used as the IAM-resolution fallback when neither the
        `BEDROCK_INFERENCE_PROFILE_ID` env var nor the
        `foundation_model_override` constructor arg is set. All snapshots
        share the same `foundationModel` today (Sonnet 4.6 per Path C);
        if a future change diverges, the operator must explicitly resolve
        via env var or override and the validator above will catch the
        mismatch on synth.
        """
        for name in AGENT_SNAPSHOTS:
            path = os.path.join(SNAPSHOT_DIR, f"{name}.json")
            if not os.path.exists(path):
                continue
            with open(path) as f:
                snap = json.load(f)
            fm = snap.get("foundationModel")
            if fm:
                return fm
        raise FileNotFoundError(
            "BedrockAgentsStack: no specialist snapshot has a "
            "foundationModel field — cannot determine baseline. "
            "Set BEDROCK_INFERENCE_PROFILE_ID or pass "
            "foundation_model_override."
        )

    def _build_agent(
        self, agent_key: str, snap: Dict[str, Any], tools_lambda_arn: Optional[str]
    ) -> bedrock.CfnAgent:
        """Build a CfnAgent from a snapshot dict (specialist — no collaborators)."""
        action_groups = self._build_action_groups(snap.get("actionGroups", []), tools_lambda_arn)

        kwargs: Dict[str, Any] = {
            "agent_name": snap["agentName"],
            # Use the resolved inference-profile ID (env > override >
            # snapshot baseline). This keeps the agent property and the
            # IAM ARNs in lock-step.
            "foundation_model": self._resolved_profile_id,
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
            # Use the resolved inference-profile ID (env > override >
            # snapshot baseline). Same value as specialists, so all 5
            # agents address the same model.
            "foundation_model": self._resolved_profile_id,
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
            "KB_BUCKET": kb_bucket_name,  # CDK-owned VfoKnowledgeBaseBucket; always non-None per spec 2026-06-04-cms-vfo-kb-bucket-region-suffix
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
