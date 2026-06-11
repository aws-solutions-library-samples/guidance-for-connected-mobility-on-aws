#!/usr/bin/env python3
"""Export the 4 CMS Bedrock agents into JSON snapshots for the CDK.

Usage
-----
Default (reads us-east-2, writes snapshots into the repo next to this script):
    python3 export_bedrock_agents.py

Custom source region:
    AWS_REGION=us-east-2 python3 export_bedrock_agents.py

The output files are consumed by ``deployment/stacks/bedrock_agents_stack.py``.
Re-run this whenever the source-of-truth region's agents are updated.
"""
import json
import os
import sys

import boto3


OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bedrock_agents_snapshot")

# Agent IDs in the reference (us-east-2) region.
# If these move, update the map here.
AGENT_IDS = {
    "cms-cost-agent": "RKS6ML45DU",
    "cms-maintenance-agent": "29XHZMJNHQ",
    "cms-rebalancing-agent": "PRWSXSJVL7",
    "cms-recall-warranty-agent": "NUS1AZQ256",
    # Supervisor — delegates to the 4 specialists above via Bedrock multi-agent collaboration.
    "cms-virtual-fleet-operator": "YSECV4SYI9",
}

SOURCE_REGION = os.environ.get("BEDROCK_AGENTS_SOURCE_REGION", "us-east-2")


def export_agent(ba, agent_id: str, agent_name: str) -> dict:
    agent = ba.get_agent(agentId=agent_id)["agent"]
    cfg = {
        "agentName": agent["agentName"],
        "foundationModel": agent["foundationModel"],
        "instruction": agent["instruction"],
        "idleSessionTTLInSeconds": agent.get("idleSessionTTLInSeconds", 600),
        "description": agent.get("description", ""),
        "orchestrationType": agent.get("orchestrationType", "DEFAULT"),
        "agentCollaboration": agent.get("agentCollaboration", "DISABLED"),
        "promptOverrideConfiguration": agent.get("promptOverrideConfiguration"),
    }

    # Action groups (DRAFT version has the editable definitions)
    ags = []
    for ag in ba.list_agent_action_groups(agentId=agent_id, agentVersion="DRAFT")["actionGroupSummaries"]:
        full = ba.get_agent_action_group(
            agentId=agent_id, agentVersion="DRAFT", actionGroupId=ag["actionGroupId"]
        )["agentActionGroup"]
        ags.append(
            {
                "actionGroupName": full["actionGroupName"],
                "actionGroupState": full["actionGroupState"],
                "description": full.get("description", ""),
                "parentActionGroupSignature": full.get("parentActionGroupSignature"),
                "actionGroupExecutor": full.get("actionGroupExecutor"),
                "functionSchema": full.get("functionSchema"),
                "apiSchema": full.get("apiSchema"),
            }
        )
    cfg["actionGroups"] = ags

    # Collaborators — only populated for SUPERVISOR agents. The referenced
    # specialist agents are captured by *logical key* (matching the keys in
    # AGENT_IDS above) so the CDK can resolve them at synth time to the IDs
    # it's building in the same stack.
    _reverse_agent_ids = {v: k for k, v in AGENT_IDS.items()}
    collabs = []
    try:
        for c in ba.list_agent_collaborators(agentId=agent_id, agentVersion="DRAFT").get(
            "agentCollaboratorSummaries", []
        ):
            alias_arn = c["agentDescriptor"]["aliasArn"]
            # alias ARN format: arn:aws:bedrock:<region>:<acct>:agent-alias/<AGENT_ID>/<ALIAS_ID>
            specialist_agent_id = alias_arn.split("/")[-2]
            collabs.append(
                {
                    "collaboratorName": c["collaboratorName"],
                    "collaborationInstruction": c["collaborationInstruction"],
                    "relayConversationHistory": c.get("relayConversationHistory", "TO_COLLABORATOR"),
                    "specialistAgentKey": _reverse_agent_ids.get(
                        specialist_agent_id, specialist_agent_id
                    ),
                    "specialistAliasName": "prod",
                }
            )
    except ba.exceptions.ValidationException:
        pass  # Not a supervisor — no collaborators endpoint available
    cfg["collaborators"] = collabs

    # Knowledge bases attached
    kbs = []
    for kb in ba.list_agent_knowledge_bases(agentId=agent_id, agentVersion="DRAFT").get(
        "agentKnowledgeBaseSummaries", []
    ):
        kbs.append(
            {
                "knowledgeBaseId": kb["knowledgeBaseId"],
                "description": kb.get("description", ""),
                "state": kb["knowledgeBaseState"],
            }
        )
    cfg["knowledgeBases"] = kbs

    # Aliases — name only; IDs are generated fresh on each deploy.
    aliases = []
    for a in ba.list_agent_aliases(agentId=agent_id)["agentAliasSummaries"]:
        if a["agentAliasName"] == "AgentTestAlias":
            continue
        aliases.append({"agentAliasName": a["agentAliasName"], "description": a.get("description", "")})
    cfg["aliases"] = aliases

    return cfg


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    ba = boto3.client("bedrock-agent", region_name=SOURCE_REGION)

    print(f"Exporting from region: {SOURCE_REGION}")
    for name, agent_id in AGENT_IDS.items():
        print(f"  {name} ({agent_id}) ...", end=" ", flush=True)
        try:
            cfg = export_agent(ba, agent_id, name)
            out = os.path.join(OUT_DIR, f"{name}.json")
            with open(out, "w") as f:
                json.dump(cfg, f, indent=2, default=str)
            print(f"ok ({len(cfg['instruction'])} chars instruction, {len(cfg['actionGroups'])} action groups)")
        except Exception as e:  # noqa: BLE001
            print(f"FAILED: {e}")
            return 1

    print(f"\n✅ Wrote {len(AGENT_IDS)} snapshots to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
