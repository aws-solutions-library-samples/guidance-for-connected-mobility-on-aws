"""Bedrock Agent tool functions for the CMS Virtual Fleet Operator + 4 specialists.

Invoked by each agent's action group. Reads per-vehicle/fleet state from the
DynamoDB storage tables and the KB S3 bucket. Intentionally small and
stateless — no external dependencies beyond boto3.

Deployed by ``deployment/stacks/vfo_stack.py`` as ``cms-<stage>-vfo-tools`` in
whichever region the stack is deployed to. Region-agnostic: uses the Lambda
runtime's AWS_REGION for DynamoDB/S3 clients, so copying this stack to a new
region just works as long as the referenced tables + S3 bucket exist there.

Environment variables (populated by the CDK):
    FLEETS_TABLE, VEHICLES_TABLE, SAFETY_EVENTS_TABLE, MAINTENANCE_ALERTS_TABLE,
    TRIPS_TABLE, DRIVERS_TABLE, WARRANTY_CLAIMS_TABLE, SERVICE_HISTORY_TABLE,
    DTC_HISTORY_TABLE, TELEMETRY_TABLE  — DynamoDB table names
    KB_BUCKET — S3 bucket containing knowledge-base documents

If any env var is missing the code falls back to the historical hard-coded
name (``cms-prod-storage-<entity>``) so existing us-east-2 deployments keep
working during migration.
"""
import json
import os

import boto3
from boto3.dynamodb.conditions import Attr
from decimal import Decimal


# Clients pick up AWS_REGION from the Lambda runtime — no hard-coded region.
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, set):
            return list(o)
        return super().default(o)


# Env-driven table name config with backward-compatible defaults.
TABLES = {
    "fleets": os.environ.get("FLEETS_TABLE", "cms-prod-storage-fleets"),
    "vehicles": os.environ.get("VEHICLES_TABLE", "cms-prod-storage-vehicles"),
    "safety_events": os.environ.get("SAFETY_EVENTS_TABLE", "cms-prod-storage-safety-events"),
    "maintenance_alerts": os.environ.get("MAINTENANCE_ALERTS_TABLE", "cms-prod-storage-maintenance-alerts"),
    "trips": os.environ.get("TRIPS_TABLE", "cms-prod-storage-trips"),
    "drivers": os.environ.get("DRIVERS_TABLE", "cms-prod-storage-drivers"),
    "warranty_claims": os.environ.get("WARRANTY_CLAIMS_TABLE", "cms-prod-storage-warranty-claims"),
    "service_history": os.environ.get("SERVICE_HISTORY_TABLE", "cms-prod-storage-service-history"),
    "dtc_history": os.environ.get("DTC_HISTORY_TABLE", "cms-prod-storage-dtc-history"),
    "telemetry": os.environ.get("TELEMETRY_TABLE", "cms-prod-storage-telemetry"),
}
KB_BUCKET = os.environ.get("KB_BUCKET", "cms-prod-vfo-knowledge-base")


def lambda_handler(event, context):
    action = event.get("actionGroup", "")
    function = event.get("function", "")
    params = {p["name"]: p["value"] for p in event.get("parameters", [])}
    try:
        result = dispatch(function, params)
    except Exception as e:  # noqa: BLE001
        result = {"error": str(e)}
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action,
            "function": function,
            "functionResponse": {
                "responseBody": {"TEXT": {"body": json.dumps(result, cls=DecimalEncoder, default=str)}}
            },
        },
    }


def dispatch(fn: str, p: dict):
    limit = int(p.get("limit", "20"))
    vid = p.get("vehicle_id")
    fid = p.get("fleet_id")
    if fn == "get_fleet_summary":
        fleets = scan("fleets", 100)
        vehicles = scan("vehicles", 500)
        return {"total_fleets": len(fleets), "total_vehicles": len(vehicles), "fleets": fleets}
    if fn == "get_vehicles":
        return query_with_filter("vehicles", "fleetId", fid, limit)
    if fn == "get_safety_events":
        return scan("safety_events", limit)
    if fn == "get_maintenance_alerts":
        return scan("maintenance_alerts", limit)
    if fn == "get_trips":
        return query_with_filter("trips", "vehicleId", vid, limit)
    if fn == "get_warranty_claims":
        return scan("warranty_claims", limit)
    if fn == "get_service_history":
        return query_with_filter("service_history", "vehicleId", vid, limit)
    if fn == "get_dtc_codes":
        return query_with_filter("dtc_history", "vehicleId", vid, limit)
    if fn == "get_telemetry":
        return query_with_filter("telemetry", "vehicleId", vid, limit)
    if fn == "get_data_counts":
        counts = {}
        for name, tbl in TABLES.items():
            try:
                counts[name] = dynamodb.Table(tbl).scan(Select="COUNT").get("Count", 0)
            except Exception:  # noqa: BLE001
                counts[name] = "error"
        return counts
    if fn == "search_documents":
        return search_kb_docs(p.get("prefix", ""), vid, limit)
    return {"error": f"Unknown function: {fn}"}


def scan(key: str, limit: int):
    return dynamodb.Table(TABLES[key]).scan(Limit=limit).get("Items", [])


def query_with_filter(key: str, attr: str, value, limit: int):
    table = dynamodb.Table(TABLES[key])
    if value:
        return table.scan(FilterExpression=Attr(attr).eq(value), Limit=limit).get("Items", [])
    return table.scan(Limit=limit).get("Items", [])


def search_kb_docs(prefix: str = "", vehicle_id: str | None = None, limit: int = 5):
    """List + read documents from the VFO knowledge-base bucket.

    The bucket holds a mix of markdown context files (fleet-context/,
    fleet-operations/) and large numbers of PDFs (service-invoices/,
    work-orders/, parts-listings/, warranty-claims/). This function is
    the agent's only path into that corpus, so it has to handle both
    plain text and PDF binary content; an earlier version did
    ``obj['Body'].read().decode('utf-8')`` unconditionally and crashed
    on every PDF.

    Strategy:
      * For .md / .txt / .json: decode UTF-8 directly, return as-is.
      * For .pdf: extract text via pypdf when the dependency is
        available. If pypdf isn't bundled (older Lambda layers, local
        runs), fall back to a 'binary; pypdf unavailable' marker so
        the agent at least knows the file exists and can ask the
        operator to fetch it via a presigned URL instead.
      * Anything else: surface the key + size + skip note.
    """
    import io as _io
    resp = s3.list_objects_v2(Bucket=KB_BUCKET, Prefix=prefix, MaxKeys=1000)
    keys = [o["Key"] for o in resp.get("Contents", [])]
    if vehicle_id:
        keys = [k for k in keys if vehicle_id in k]
    docs = []
    for key in keys[:limit]:
        obj = s3.get_object(Bucket=KB_BUCKET, Key=key)
        body = obj["Body"].read()
        lower = key.lower()
        if lower.endswith((".md", ".txt", ".json", ".csv")):
            try:
                content = body.decode("utf-8", errors="replace")
            except Exception:
                content = "<undecodable>"
            docs.append({"key": key, "type": "text", "content": content})
            continue
        if lower.endswith(".pdf"):
            try:
                from pypdf import PdfReader  # type: ignore
                reader = PdfReader(_io.BytesIO(body))
                pages = []
                for page in reader.pages:
                    try:
                        pages.append(page.extract_text() or "")
                    except Exception:
                        pages.append("")
                content = "\n\n".join(p.strip() for p in pages if p.strip())
                docs.append({
                    "key": key,
                    "type": "pdf",
                    "pages": len(reader.pages),
                    "content": content,
                })
            except Exception as e:  # pypdf not installed or parse error
                docs.append({
                    "key": key,
                    "type": "pdf",
                    "content": f"<binary PDF; text extraction unavailable: {e}>",
                    "size": len(body),
                })
            continue
        docs.append({"key": key, "type": "binary", "size": len(body), "content": "<binary; not extracted>"})
    return {"total_matching": len(keys), "returned": len(docs), "documents": docs}
