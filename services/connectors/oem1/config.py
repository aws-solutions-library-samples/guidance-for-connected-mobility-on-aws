"""
OEM1 connector configuration constants and env-var readers.
"""
import os


def get_auto_register() -> bool:
    """Return OEM1_AUTO_REGISTER flag; default true (staging). Use a function for testability."""
    return os.environ.get("OEM1_AUTO_REGISTER", "true").lower() == "true"


DEFAULT_FLEET_ID = "oem1-staging-fleet"
STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
VEHICLES_TABLE = f"cms-{STAGE}-storage-vehicles"
FLEET_ENROLLMENT_TABLE = f"cms-{STAGE}-storage-fleet-enrollment"
CLOUDWATCH_NAMESPACE = "CMS/OEM1Connector"

# Checkpoint DynamoDB table — multi-connector shared, scoped per row prefix; matches deployment/stacks/connector_stack.py:97 IAM grant.
CHECKPOINT_TABLE = f"cms-{STAGE}-connector-checkpoints"

# Maximum consecutive retry attempts before circuit-breaker calls sys.exit(1)
MAX_RETRIES = 5

# Feed host — set via env var in production; placeholder for local/CI use
OEM1_FEED_HOST = os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local")
