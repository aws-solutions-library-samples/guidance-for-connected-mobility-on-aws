"""
test_self_signup_blocked.py — Verify that self-signup is disabled on the deployed User Pool.

Usage:
    python3 deployment/scripts/test_self_signup_blocked.py [--stage STAGE]

Reads the User Pool Client ID from the CloudFormation stack output
`{stage}-user-pool-client-id` on the `cms-{stage}-ui` stack.

With default-off configuration (post-fix), `cognito-idp:SignUp` must return
`NotAuthorizedException` (or a similar client error indicating admin-create-only).
Exit 0 on expected failure. Exit 1 if signup unexpectedly succeeds.

Region is read from DEFAULT_REGION or AWS_REGION environment variables,
defaulting to us-west-2.
"""

import argparse
import sys
import uuid

import boto3
from botocore.exceptions import ClientError

# Errors Cognito raises when self-signup is disabled (admin-create-only pool).
EXPECTED_ERROR_CODES = {
    "NotAuthorizedException",
    "UserLambdaValidationException",
    "InvalidParameterException",
}


def get_client_id(stage: str, region: str) -> str:
    cfn = boto3.client("cloudformation", region_name=region)
    stack_name = f"cms-{stage}-ui"
    resp = cfn.describe_stacks(StackName=stack_name)
    outputs = resp["Stacks"][0].get("Outputs", [])
    key = f"cms-{stage}-ui-user-pool-client-id"
    for o in outputs:
        if o.get("ExportName") == key or o.get("OutputKey") == "UserPoolClientId":
            return o["OutputValue"]
    raise RuntimeError(
        f"Could not find User Pool Client ID output in stack {stack_name}. "
        f"Looked for ExportName={key} or OutputKey=UserPoolClientId."
    )


def run(stage: str, region: str) -> int:
    client_id = get_client_id(stage, region)
    synthetic_email = f"test-signup-probe-{uuid.uuid4().hex[:8]}@example-cms-test.invalid"
    password = "Test@" + uuid.uuid4().hex[:12] + "Aa1!"

    cidp = boto3.client("cognito-idp", region_name=region)
    try:
        cidp.sign_up(
            ClientId=client_id,
            Username=synthetic_email,
            Password=password,
            UserAttributes=[{"Name": "email", "Value": synthetic_email}],
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in EXPECTED_ERROR_CODES:
            print(f"PASS — SignUp rejected with {code} (self-signup is OFF).")
            return 0
        print(f"FAIL — Unexpected Cognito error: {code}: {exc.response['Error']['Message']}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL — Unexpected exception: {exc}")
        return 1

    print(
        "FAIL — SignUp SUCCEEDED. Self-signup is ON. "
        "Set cms.allow_self_signup=false in cdk.json and redeploy."
    )
    return 1


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", default=os.environ.get("DEPLOYMENT_STAGE", "staging"))
    args = parser.parse_args()

    region = os.environ.get("DEFAULT_REGION") or os.environ.get("AWS_REGION", "us-west-2")
    sys.exit(run(args.stage, region))


if __name__ == "__main__":
    main()
