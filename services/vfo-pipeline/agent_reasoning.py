"""
Agent Reasoning — Invokes Bedrock to analyze enriched alerts and produce decisions.
"""
import boto3
import json
import os

REGION = os.environ.get("AWS_REGION", "us-east-1")
bedrock = boto3.client("bedrock-runtime", region_name=REGION)

def handler(event, context):
    ctx = event.get("context", {})
    enriched = ctx.get("enriched_alerts", [])
    summary = ctx.get("summary", "No alerts")

    if not enriched:
        return {"decisions": []}

    # Build compact context for the model
    alert_details = []
    for e in enriched[:10]:  # Cap at 10 to stay within token limits
        a = e.get("alert", {})
        v = e.get("vehicle", {})
        alert_details.append(
            f"- {a.get('alertType','?')} on {a.get('vehicleId','?')} "
            f"({v.get('make','')} {v.get('model','')}, {v.get('mileage','?')} mi, "
            f"warranty={'yes' if v.get('warrantyActive') else 'no'})"
        )

    prompt = f"""You are an autonomous fleet maintenance agent. Analyze these alerts and decide actions.

Summary: {summary}

Alerts:
{chr(10).join(alert_details)}

For each alert, produce a decision with:
- vehicleId, alertId, alertType
- action: SCHEDULE_SERVICE or REASSIGN_VEHICLE or MONITOR
- severity: HIGH, MEDIUM, or LOW
- reasoning: brief explanation
- estimatedCost: number
- serviceType: e.g. "Tire Replacement"
- description: what needs to be done
- scheduledDate: ISO date within next 7 days

Respond ONLY with valid JSON: {{"decisions": [...]}}"""

    resp = bedrock.invoke_model(
        modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}]
        })
    )

    body = json.loads(resp["body"].read())
    text = body["content"][0]["text"]

    # Parse JSON from response
    try:
        # Handle markdown code blocks
        if "```" in text:
            text = text.split("```json")[-1].split("```")[0] if "```json" in text else text.split("```")[1].split("```")[0]
        result = json.loads(text.strip())
        return result
    except json.JSONDecodeError:
        return {"decisions": [], "raw": text}
