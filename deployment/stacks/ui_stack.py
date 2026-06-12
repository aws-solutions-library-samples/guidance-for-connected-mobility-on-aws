"""
UI Stack - Frontend, API Gateway, and Cognito authentication
"""

import os
from aws_cdk import (
    Stack,
    aws_cognito as cognito,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_apigateway as apigateway,
    aws_apigatewayv2 as apigatewayv2,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_location as location,
    aws_certificatemanager as acm,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
    custom_resources as custom_resource,
    CfnOutput,
    Duration,
    Fn,
    Size
)
from constructs import Construct
from typing import Dict
import json
import boto3
import botocore


def _lookup_stack_output(stack_name: str, output_key: str, region: str) -> str:
    """Resolve a sibling stack's output at synth time.
    Returns empty string if the stack or output doesn't exist yet -
    this lets the UI stack be deployed standalone before siblings."""
    try:
        cfn = boto3.client('cloudformation', region_name=region)
        resp = cfn.describe_stacks(StackName=stack_name)
        for output in resp['Stacks'][0].get('Outputs', []):
            if output['OutputKey'] == output_key:
                return output['OutputValue']
    except (botocore.exceptions.ClientError, botocore.exceptions.NoCredentialsError, Exception):
        pass
    return ""


def _lookup_cognito_domain(user_pool_id: str, region: str) -> str:
    """Return the user pool's Hosted UI domain (auth.<region>.amazoncognito.com)
    or empty string if none configured yet."""
    try:
        cidp = boto3.client('cognito-idp', region_name=region)
        resp = cidp.describe_user_pool(UserPoolId=user_pool_id)
        domain = resp.get('UserPool', {}).get('Domain')
        if domain:
            return f"{domain}.auth.{region}.amazoncognito.com"
    except Exception:
        pass
    return ""


def _build_bedrock_agents_dict(stack, import_stack_name: str = "") -> dict:
    """Build the optional agents dict for runtimeConfig.json.

    Returns ``{agent_name: {agentId, agentAliasId}}`` for each of the 5 CMS
    agents. Resolves IDs with this priority:

      1. Explicit CDK context flag (e.g. ``-c bedrockAgentIdCostAgent=...``)
      2. ``Fn::ImportValue`` from the sibling bedrock-agents stack, when
         ``import_stack_name`` is provided (e.g. ``cms-prod-bedrock-agents``).
         The export names match what ``bedrock_agents_stack.py`` emits:
         ``{stack_name}-{snapshot_name}-agent-id`` and
         ``{stack_name}-{snapshot_name}-prod-alias-id``.
      3. Entry is omitted.

    Keys match the snapshot filenames in
    ``deployment/scripts/bedrock_agents_snapshot/``.

    Context keys (all optional, all via ``-c KEY=VALUE``):
        bedrockAgentIdCostAgent, bedrockAgentAliasIdCostAgent
        bedrockAgentIdMaintenanceAgent, bedrockAgentAliasIdMaintenanceAgent
        bedrockAgentIdRebalancingAgent, bedrockAgentAliasIdRebalancingAgent
        bedrockAgentIdRecallWarrantyAgent, bedrockAgentAliasIdRecallWarrantyAgent
        bedrockAgentIdVirtualFleetOperator, bedrockAgentAliasIdVirtualFleetOperator

    If nothing is set and no import stack is provided, returns {}. The frontend
    only falls back to ``agents[key]`` for specialist-specific flows;
    landing-page chat uses ``bedrockAgent`` top-level.
    """
    specs = [
        ("cms-cost-agent", "CostAgent"),
        ("cms-maintenance-agent", "MaintenanceAgent"),
        ("cms-rebalancing-agent", "RebalancingAgent"),
        ("cms-recall-warranty-agent", "RecallWarrantyAgent"),
        ("cms-virtual-fleet-operator", "VirtualFleetOperator"),
    ]
    out = {}
    for snapshot_name, ctx_suffix in specs:
        agent_id = stack.node.try_get_context(f"bedrockAgentId{ctx_suffix}")
        alias_id = stack.node.try_get_context(f"bedrockAgentAliasId{ctx_suffix}")
        # Fall back to Fn::ImportValue when the caller has opted in.
        if not agent_id and import_stack_name:
            agent_id = Fn.import_value(f"{import_stack_name}-{snapshot_name}-agent-id")
        if not alias_id and import_stack_name:
            alias_id = Fn.import_value(f"{import_stack_name}-{snapshot_name}-prod-alias-id")
        if agent_id and alias_id:
            out[snapshot_name] = {"agentId": agent_id, "agentAliasId": alias_id}
    return out


class UIStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, 
                 storage_tables: Dict[str, dynamodb.Table] = None,
                 redis_endpoint: str = None,
                 msk_stack=None,
                 data_processing_api_endpoint: str = None,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        self._data_processing_api_endpoint = data_processing_api_endpoint or ""
        
        # Resolve VPC and Redis from MSK stack (single VPC architecture)
        if msk_stack:
            redis_endpoint = msk_stack.redis_endpoint
            self._data_vpc = msk_stack.vpc
            self._data_sg = msk_stack.msk_security_group
        else:
            self._data_vpc = None
            self._data_sg = None
        
        # Amazon Location Services resources
        # Provider switched from Esri → HERE on 2026-05-29 for parity with
        # prod's manually-created `cvs_location_map_test2` HERE map (see
        # issues/2026-05-29-staging-map-provider-esri-not-here/report.md).
        # Prod additionally has a legacy hand-created HERE map outside CDK;
        # the IAM unauthenticated role still allowlists that legacy map ARN
        # below for backward compatibility, but new deploys (staging) get
        # HERE tiles directly from the CDK-managed map.
        # NOTE: AWS::Location::Map, PlaceIndex, RouteCalculator do NOT support
        # in-place update of Style/DataSource AND require non-empty *_name
        # properties at the CFN schema level (CFN early validation rejects
        # change sets where these are absent — confirmed 2026-05-29 attempt).
        # Auto-generated names are therefore not an option. Approach taken
        # instead: deterministic rename with a `-here` provider-suffix so the
        # new physical names differ from the old. Different physical names →
        # CFN performs CREATE-new + DELETE-old (no name-conflict failure).
        self.map = location.CfnMap(
            self, "CMSVehicleMap",
            map_name=f"{construct_id}-vehicle-map-here",
            configuration=location.CfnMap.MapConfigurationProperty(
                style="VectorHereExplore"
            ),
            description="Map for Connected Mobility Solution vehicle tracking",
            pricing_plan="RequestBasedUsage"
        )
        
        self.place_index = location.CfnPlaceIndex(
            self, "CMSPlaceIndex",
            index_name=f"{construct_id}-place-index-here",
            data_source="Here",
            description="Place index for Connected Mobility Solution",
            pricing_plan="RequestBasedUsage"
        )
        
        # Route calculator for simulation routing
        self.route_calculator = location.CfnRouteCalculator(
            self, "CMSRouteCalculator",
            calculator_name=f"{construct_id}-route-calculator-here",
            data_source="Here",
            description="Route calculator for Connected Mobility Solution simulation"
        )
        self.route_calculator_name = self.route_calculator.calculator_name
        
        # Use actual table names from storage stack (with suffixes)
        # Table names — hardcoded to avoid cross-stack CloudFormation exports
        # (storage stack tables were created outside CDK for this deployment)
        storage_prefix = construct_id.replace('-ui', '-storage')
        table_names = {
            'fleets': f"{storage_prefix}-fleets",
            'vehicles': f"{storage_prefix}-vehicles",
            'trips': f"{storage_prefix}-trips",
            'telemetry': f"{storage_prefix}-telemetry",
            'safety_events': f"{storage_prefix}-safety-events",
            'maintenance_events': f"{storage_prefix}-maintenance-alerts",
            'user_preferences': f"{storage_prefix}-user-preferences",
            'dashboard_metrics_cache': f"{storage_prefix}-dashboard-metrics-cache",
            'vehicle_certificates': f"{storage_prefix}-vehicle-certificates",
            'drivers': f"{storage_prefix}-drivers",
            'service_history': f"{storage_prefix}-service-history",
            'fleet_enrollment': f"{storage_prefix}-fleet-enrollment",
            # Written by MaintenanceProcessor (threshold path) AND
            # FWTelemetryProcessor (authentic UDS path). Read by the Fleet
            # API Lambda's /api/v1/vehicles/{vehicleId}/dtcs route.
            'dtc_history': f"{storage_prefix}-dtc-history",
        }
        
        ws_connections_table_name = f"{storage_prefix}-ws-connections"
        vehicle_link_codes_table_name = f"{storage_prefix}-vehicle-link-codes"
        
        # Cognito User Pool
        self.user_pool = cognito.UserPool(
            self, "CMSUserPool",
            user_pool_name=f"{construct_id}-users",
            self_sign_up_enabled=self.node.try_get_context('cms.allow_self_signup') in (True, 'true', '1'),
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True
            ),
            custom_attributes={
                "fleetIds": cognito.StringAttribute(mutable=True),
                # vehicleIds already exists in the pool (added post-creation)
                # ── VSA / iOS-app schema (piggyback) ────────────────────────
                # The iOS demo personas (Samantha / Marcus / Priya) need these
                # attributes per `seed_driver_users.py`. Schema mirrors the
                # legacy CVX VSA pool
                # so this pool can replace it: one
                # CMS-deployed pool serves both Fleet Manager web users AND
                # iOS app users in any region. Closes the cross-region
                # defect surfaced by clean-deploy run 14
                # (`InvalidParameterException: Type for attribute
                # {custom:tenantId} could not be determined`).
                # Issue: issues/2026-06-04-cms-vsa-pool-id-region-aware-fallback/
                "tenantId": cognito.StringAttribute(mutable=False),
                "driverId": cognito.StringAttribute(mutable=False),
                "role": cognito.StringAttribute(mutable=True),
                "vehicleId": cognito.StringAttribute(mutable=True),
            }
        )
        
        # Cognito User Pool Groups — role-based access
        cognito.CfnUserPoolGroup(
            self, "PlatformAdminGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="platform-admin",
            description="Full access to all fleets, system config, OEM connectors"
        )
        
        cognito.CfnUserPoolGroup(
            self, "FleetOperatorGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="fleet-operator",
            description="Manage own fleet vehicles, trips, telemetry, drivers"
        )
        
        cognito.CfnUserPoolGroup(
            self, "FleetViewerGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="fleet-viewer",
            description="Read-only access to own fleet dashboards"
        )
        
        # User Pool Client
        # Build the identity provider list dynamically so Federate is only
        # wired in when the env vars to create the IdP above are set.
        idp_list = [cognito.UserPoolClientIdentityProvider.COGNITO]
        if os.environ.get("FEDERATE_CLIENT_ID", "").strip() and os.environ.get("FEDERATE_CLIENT_SECRET", "").strip():
            idp_list.append(cognito.UserPoolClientIdentityProvider.custom("AmazonFederate"))

        self.user_pool_client = cognito.UserPoolClient(
            self, "CMSUserPoolClient",
            user_pool=self.user_pool,
            generate_secret=False,
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True
            ),
            supported_identity_providers=idp_list,
        )

        CfnOutput(self, 'UserPoolArnExport',
                  value=self.user_pool.user_pool_arn,
                  export_name=f'{construct_id}-user-pool-arn',
                  description='User Pool ARN for cross-stack import by simulation/commands/predictive-agent stacks')

        # Hosted UI domain (Cognito-provided subdomain under amazoncognito.com).
        # OPT-IN — only created when COGNITO_DOMAIN_PREFIX is explicitly set
        # (or when Federate IdP env vars are set, since Federate needs the
        # Hosted UI domain to redirect to).
        #
        # The name is globally unique per region. Set COGNITO_DOMAIN_PREFIX to
        # a value that's unlikely to collide (e.g. <org>-<stage>-<region>-cms).
        # If another account in the same region already registered this prefix,
        # deploy will fail at early validation. Change the prefix and retry.
        #
        # NOTE: The Federate OIDC IdP (if used) only redirects back to URIs
        # that are on its allowlist. If you change the prefix after Federate
        # is wired up, request the new URI be added to the Federate allowlist
        # before users try to SSO.
        #
        # Why this isn't created by default: this CDK ships to customers who
        # deploy under their own accounts. Baking a specific prefix in means
        # the first customer to deploy "wins" the name and all subsequent
        # deploys collide. Customers opt in by setting the env var.
        cognito_domain_prefix = os.environ.get("COGNITO_DOMAIN_PREFIX", "").strip()

        # Amazon Federate OIDC IdP (opt-in via env vars).
        # When the Federate team provisions a Cognito-integrated client in
        # their self-service portal, they give you:
        #   - client_id (e.g. 'cms-demo-cognito')
        #   - client_secret
        #   - issuer (e.g. 'https://idp-integ.federate.amazon.com' for integ,
        #              or https://idp.federate.amazon.com for prod)
        # Set FEDERATE_CLIENT_ID, FEDERATE_CLIENT_SECRET, FEDERATE_ISSUER to
        # enable the 'Sign in with Amazon Federate' button. The Federate team
        # must also allowlist {cognito_domain}/oauth2/idpresponse.
        federate_client_id = os.environ.get("FEDERATE_CLIENT_ID", "").strip()
        federate_client_secret = os.environ.get("FEDERATE_CLIENT_SECRET", "").strip()
        federate_issuer = os.environ.get(
            "FEDERATE_ISSUER", "https://idp-integ.federate.amazon.com"
        ).strip()
        federate_enabled = bool(federate_client_id and federate_client_secret)

        # Federate requires the Hosted UI domain for its OAuth2 redirect, so
        # if the operator enabled Federate without setting a domain prefix,
        # that's a config error — fail loudly rather than silently.
        if federate_enabled and not cognito_domain_prefix:
            raise ValueError(
                "FEDERATE_CLIENT_ID/FEDERATE_CLIENT_SECRET are set but "
                "COGNITO_DOMAIN_PREFIX is not. Federate requires a Cognito "
                "Hosted UI domain for its OAuth2 redirect. Set "
                "COGNITO_DOMAIN_PREFIX to a unique prefix (e.g. "
                "'<org>-<stage>-cms') and redeploy."
            )

        if cognito_domain_prefix:
            self.user_pool_domain = cognito.UserPoolDomain(
                self, "CMSUserPoolDomain",
                user_pool=self.user_pool,
                cognito_domain=cognito.CognitoDomainOptions(
                    domain_prefix=cognito_domain_prefix
                ),
            )
            CfnOutput(
                self, "CognitoHostedUIDomain",
                value=f"{cognito_domain_prefix}.auth.{self.region}.amazoncognito.com",
                description="Cognito Hosted UI domain - used for Federate / OAuth2 flows",
                export_name=f"{construct_id}-cognito-domain"
            )

        if federate_enabled:
            self.federate_idp = cognito.CfnUserPoolIdentityProvider(
                self, "AmazonFederateIdP",
                user_pool_id=self.user_pool.user_pool_id,
                provider_name="AmazonFederate",
                provider_type="OIDC",
                provider_details={
                    "client_id": federate_client_id,
                    "client_secret": federate_client_secret,
                    "oidc_issuer": federate_issuer,
                    "authorize_scopes": "openid email profile",
                    "attributes_request_method": "GET",
                    "attributes_url_add_attributes": "false",
                },
                attribute_mapping={
                    "email": "EMAIL",
                    "name": "GIVEN_NAME",
                    "username": "sub",
                },
            )
            # User pool client must depend on the IdP so CFN creates it first
            self.user_pool_client.node.add_dependency(self.federate_idp)

        # Identity Pool
        self.identity_pool = cognito.CfnIdentityPool(
            self, "CMSIdentityPool",
            identity_pool_name=f"{construct_id}-identity",
            allow_unauthenticated_identities=self.node.try_get_context('cms.allow_unauth_map_auth') in (True, 'true', '1'),
            cognito_identity_providers=[
                cognito.CfnIdentityPool.CognitoIdentityProviderProperty(
                    client_id=self.user_pool_client.user_pool_client_id,
                    provider_name=self.user_pool.user_pool_provider_name
                )
            ]
        )
        
        # IAM role for unauthenticated users to access Location Services
        allow_unauth_map_auth = self.node.try_get_context('cms.allow_unauth_map_auth') in (True, 'true', '1')
        if allow_unauth_map_auth:
            unauthenticated_role = iam.Role(
                self, "CognitoUnauthenticatedRole",
                assumed_by=iam.FederatedPrincipal(
                    "cognito-identity.amazonaws.com",
                    {
                        "StringEquals": {
                            "cognito-identity.amazonaws.com:aud": self.identity_pool.ref
                        },
                        "ForAnyValue:StringLike": {
                            "cognito-identity.amazonaws.com:amr": "unauthenticated"
                        }
                    },
                    "sts:AssumeRoleWithWebIdentity"
                ),
                inline_policies={
                    "LocationServicesPolicy": iam.PolicyDocument(
                        statements=[
                            # New geo-maps actions for maps
                            iam.PolicyStatement(
                                effect=iam.Effect.ALLOW,
                                actions=[
                                    "geo-maps:GetTile",
                                    "geo-maps:GetStaticMap"
                                ],
                                resources=[
                                    f"arn:aws:geo-maps:{self.region}::provider/default",
                                    f"arn:aws:geo-maps:{self.region}::provider/default/*"
                                ]
                            ),
                            # Legacy geo actions for backward compatibility.
                            # Allow both the canonical UI-provisioned map
                            # (`cms-prod-ui-vehicle-map`, VectorEsriStreets)
                            # AND the legacy Here-source demo map
                            # (`cvs_location_map_test2`, VectorHereExplore).
                            # The CDK creates the canonical Esri map, but
                            # the demo's visual branding has historically
                            # used the Here vector style — operators flip
                            # the runtime config's `mapAuth.mapName` between
                            # the two depending on the look they want, so
                            # the IAM policy needs to permit either map for
                            # signed-out (pre-auth) tile loads.
                            #
                            # See `regenerate-runtime-config` Makefile
                            # target: it auto-prefers the Here map when
                            # present so the runtime config and IAM stay
                            # in sync without manual edits.
                            iam.PolicyStatement(
                                effect=iam.Effect.ALLOW,
                                actions=[
                                    "geo:GetMap*",
                                    "geo:DescribeMap"
                                ],
                                resources=[
                                    self.map.attr_arn,
                                    f"arn:aws:geo:{self.region}:{self.account}:map/cvs_location_map_test2",
                                ]
                            )
                        ]
                    )
                }
            )
        
        # IAM role for authenticated users to access Location Services
        authenticated_role = iam.Role(
            self, "CognitoAuthenticatedRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                {
                    "StringEquals": {
                        "cognito-identity.amazonaws.com:aud": self.identity_pool.ref
                    },
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": "authenticated"
                    }
                },
                "sts:AssumeRoleWithWebIdentity"
            ),
            inline_policies={
                "LocationServicesPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "geo:GetMap*",
                                "geo:DescribeMap",
                                "geo:SearchPlaceIndex*",
                                "geo:GetPlace",
                                "geo:CalculateRoute*"
                            ],
                            resources=[
                                f"arn:aws:geo:{self.region}:{self.account}:map/*",
                                f"arn:aws:geo:{self.region}:{self.account}:place-index/*",
                                f"arn:aws:geo:{self.region}:{self.account}:route-calculator/*"
                            ]
                        )
                    ]
                ),
                # Bedrock Agent invocation for the CMS UI's chat/assistant features.
                # Scoped to agent-aliases in this account/region only.
                "BedrockAgentInvokePolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:InvokeAgent",
                            ],
                            resources=[
                                f"arn:aws:bedrock:{self.region}:{self.account}:agent-alias/*",
                            ]
                        )
                    ]
                )
            }
        )
        
        # Attach roles to identity pool
        auth_roles: dict = {"authenticated": authenticated_role.role_arn}
        if allow_unauth_map_auth:
            auth_roles["unauthenticated"] = unauthenticated_role.role_arn
        cognito.CfnIdentityPoolRoleAttachment(
            self, "IdentityPoolRoleAttachment",
            identity_pool_id=self.identity_pool.ref,
            roles=auth_roles,
        )
        
        # Private S3 bucket (secure)
        # Bucket name is suffixed with -{account}-{region} per spec
        # `2026-06-03-cms-ui-frontend-bucket-region-suffix` — S3 bucket
        # names are GLOBAL; the (account, region) tuple prevents
        # cross-region collisions when the same construct is deployed
        # in more than one region (e.g. clean-deploy harness validation
        # in ap-northeast-1 against a live us-west-2 staging stack).
        # Mirrors the storage_stack.py + data_processing_stack.py pattern.
        self.frontend_bucket = s3.Bucket(
            self, "FrontendBucket",
            bucket_name=f"{construct_id}-frontend-{self.account}-{self.region}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            public_read_access=False
        )
        
        # Origin Access Control for secure CloudFront access
        # Generate unique OAC name with stack ID suffix
        import hashlib
        stack_hash = hashlib.md5(self.stack_id.encode() if hasattr(self, 'stack_id') else construct_id.encode()).hexdigest()[:8]
        oac = cloudfront.CfnOriginAccessControl(
            self, "FrontendOAC",
            origin_access_control_config=cloudfront.CfnOriginAccessControl.OriginAccessControlConfigProperty(
                name=f"{construct_id}-oac-{stack_hash}",
                origin_access_control_origin_type="s3",
                signing_behavior="always",
                signing_protocol="sigv4"
            )
        )

        # Optional custom domain for the CloudFront distribution.
        #
        # Opt in by setting BOTH:
        #   -c uiCustomDomain=cms.example.com
        #   -c uiCustomDomainCertArn=arn:aws:acm:us-east-1:<account>:certificate/<id>
        #
        # The cert MUST be in us-east-1 (CloudFront requirement) and must already
        # be ISSUED. The hosted zone for the domain must exist; if it lives in
        # the same AWS account we'll also upsert the A-record alias. If it
        # doesn't (e.g., delegated zones outside the account), set
        # -c uiCustomDomainManageDns=false and the stack will just attach
        # the cert+alias to CloudFront, leaving DNS to whatever manages it.
        #
        # Without this opt-in, the distribution behaves exactly as before:
        # CloudFront default cert, default <dist-id>.cloudfront.net URL only.
        ui_custom_domain = (self.node.try_get_context("uiCustomDomain") or "").strip()
        ui_custom_domain_cert_arn = (
            self.node.try_get_context("uiCustomDomainCertArn") or ""
        ).strip()
        ui_custom_domain_manage_dns = str(
            self.node.try_get_context("uiCustomDomainManageDns") or "true"
        ).lower() not in ("false", "0", "no")

        distribution_kwargs = {}
        if ui_custom_domain and ui_custom_domain_cert_arn:
            cert = acm.Certificate.from_certificate_arn(
                self, "UiCustomDomainCert", ui_custom_domain_cert_arn
            )
            distribution_kwargs["domain_names"] = [ui_custom_domain]
            distribution_kwargs["certificate"] = cert
        elif ui_custom_domain or ui_custom_domain_cert_arn:
            # Both must be set together to avoid surprising partial configs.
            raise ValueError(
                "uiCustomDomain and uiCustomDomainCertArn must both be set "
                "(or both unset). Got domain={!r} cert_arn={!r}".format(
                    ui_custom_domain, ui_custom_domain_cert_arn
                )
            )

        # ── Edge auth gate — staging-only ────────────────────────────────────
        # Reference an externally-created CloudFront Key Group by ID. The Key
        # Group itself, the underlying RSA key pair, the KMS encryption key,
        # the Secrets Manager secret, and the external SSO gate's onboard
        # call are all configured out-of-band by the operator per the
        # internal staging-gate runbook. CDK ONLY references the existing
        # Key Group ID via cdk.context.json.
        #
        # Gating: construct_id == "cms-staging-ui" AND a non-empty
        # stagingGateKeyGroupId context. Prod (cms-prod-ui) and dev
        # (cms-dev-ui) never enter this branch — verified at synth time.
        staging_gate_key_group_id = (
            self.node.try_get_context("stagingGateKeyGroupId") or ""
        ).strip()
        is_staging_ui = construct_id == "cms-staging-ui"
        staging_gate_trusted_key_groups = []
        if is_staging_ui and staging_gate_key_group_id:
            staging_gate_key_group = cloudfront.KeyGroup.from_key_group_id(
                self, "StagingGateKeyGroup", staging_gate_key_group_id
            )
            staging_gate_trusted_key_groups = [staging_gate_key_group]
            print(
                f"  [ui_stack] staging edge auth gate ENABLED for "
                f"{construct_id} via Key Group ID {staging_gate_key_group_id}"
            )
        elif is_staging_ui and not staging_gate_key_group_id:
            print(
                "  [ui_stack] stagingGateKeyGroupId context not set; "
                "cms-staging-ui distribution will deploy WITHOUT the staging "
                "edge auth gate. Set -c stagingGateKeyGroupId=<id> in "
                "cdk.context.json once gate onboarding is complete (see the "
                "internal staging-gate runbook)."
            )

        # CloudFront distribution with OAC.
        #
        # The default cache behavior gets `trusted_key_groups` ONLY when the
        # staging edge auth gate is active (staging-only — see context-read
        # block above). The /js/cfs-handler.js path-pattern gets its own
        # additional behavior with NO trusted-key-group binding so the
        # handler script can load while the user is unauthenticated.
        #
        # Custom error responses:
        # - 404 → /index.html → 200  (preserved unchanged for SPA routing)
        # - 403 → /error/403.html → 403 when gate active (gate template)
        # - 403 → /index.html     → 200 when gate inactive (legacy SPA fallback
        #                                                  for missing keys)
        default_behavior_kwargs = dict(
            origin=origins.S3BucketOrigin(
                self.frontend_bucket,
                origin_access_control_id=oac.attr_id
            ),
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
        )
        if staging_gate_trusted_key_groups:
            default_behavior_kwargs["trusted_key_groups"] = staging_gate_trusted_key_groups

        staging_gate_additional_behaviors = None
        if staging_gate_trusted_key_groups:
            # CloudFront Function: rewrite SPA deep-link paths to /index.html so
            # S3+OAC serves a real object. Without this, paths like
            # /vehicles/management/VEH-xxx have no S3 key — S3 returns 403 (not
            # 404) under OAC, which re-triggers the auth gate even with valid
            # signed cookies.
            spa_rewrite_fn = cloudfront.Function(
                self, "SpaRewriteFunction",
                code=cloudfront.FunctionCode.from_inline("""
function handler(event) {
    var request = event.request;
    var uri = request.uri;
    // Rewrite paths with no file extension (SPA routes) to /index.html.
    // Exclude known static asset prefixes.
    if (!uri.includes('.') && uri !== '/') {
        request.uri = '/index.html';
    }
    return request;
}
"""),
                runtime=cloudfront.FunctionRuntime.JS_2_0,
            )

            # /js/cfs-handler.js MUST load BEFORE auth — explicit no-gate
            # path-pattern behavior so the handler script is fetchable
            # before any trusted-key-group check runs.
            staging_gate_additional_behaviors = {
                "/js/cfs-handler.js": cloudfront.BehaviorOptions(
                    origin=origins.S3BucketOrigin(
                        self.frontend_bucket,
                        origin_access_control_id=oac.attr_id
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                    # No trusted_key_groups — explicitly public.
                ),
            }
            default_behavior_kwargs["function_associations"] = [
                cloudfront.FunctionAssociation(
                    function=spa_rewrite_fn,
                    event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                )
            ]

        staging_gate_error_responses = [
            cloudfront.ErrorResponse(
                http_status=404,
                response_http_status=200,
                response_page_path="/index.html"
            ),
        ]
        if staging_gate_trusted_key_groups:
            # Gate active: 403 must serve the gate template AND keep the
            # 403 status code (cfs-handler.js uses the response to drive its
            # redirect logic). ttl=0 prevents CloudFront from caching the 403
            # error response — without this, after signing succeeds and cookies
            # are set, the next request to the same path still gets the cached
            # 403 for up to 5 minutes (CloudFront's default error TTL).
            staging_gate_error_responses.append(cloudfront.ErrorResponse(
                http_status=403,
                response_http_status=403,
                response_page_path="/error/403.html",
                ttl=Duration.seconds(0)
            ))
        else:
            # Gate inactive: preserve the existing SPA fallback for missing
            # S3 keys (matches prod behavior).
            staging_gate_error_responses.append(cloudfront.ErrorResponse(
                http_status=403,
                response_http_status=200,
                response_page_path="/index.html"
            ))

        self.distribution = cloudfront.Distribution(
            self, "FrontendDistribution",
            default_behavior=cloudfront.BehaviorOptions(**default_behavior_kwargs),
            additional_behaviors=staging_gate_additional_behaviors,
            default_root_object="index.html",
            error_responses=staging_gate_error_responses,
            **distribution_kwargs
        )

        # Upsert the A-record alias when the hosted zone is in this account
        # AND the operator opted in to DNS management. The lookup is best-
        # effort — if the zone isn't here, we skip silently and trust the
        # operator to manage DNS externally (this is the common path for
        # delegated zones managed outside this account).
        if ui_custom_domain and ui_custom_domain_cert_arn and ui_custom_domain_manage_dns:
            # Infer the zone name by stripping the leading subdomain (e.g.
            # "cms.example.com" → zone "example.com").
            # Fall back to the full domain if there's no subdomain.
            zone_name = ".".join(ui_custom_domain.split(".")[1:]) or ui_custom_domain
            try:
                r53 = boto3.client('route53', region_name=self.region)
                zones = r53.list_hosted_zones_by_name(DNSName=zone_name, MaxItems="5")
                matching = [
                    z for z in zones.get('HostedZones', [])
                    if z['Name'].rstrip('.') == zone_name and not z.get('Config', {}).get('PrivateZone')
                ]
                # Also accept a zone named after the full custom domain (e.g.
                # Supernova delegations create a zone for the full subdomain).
                if not matching:
                    zones = r53.list_hosted_zones_by_name(DNSName=ui_custom_domain, MaxItems="5")
                    matching = [
                        z for z in zones.get('HostedZones', [])
                        if z['Name'].rstrip('.') == ui_custom_domain and not z.get('Config', {}).get('PrivateZone')
                    ]
                    if matching:
                        zone_name = ui_custom_domain
                if matching:
                    zone = route53.HostedZone.from_hosted_zone_attributes(
                        self, "UiCustomDomainZone",
                        hosted_zone_id=matching[0]['Id'].split('/')[-1],
                        zone_name=zone_name,
                    )
                    # When zone_name == ui_custom_domain the record_name is the
                    # apex (empty string); otherwise it's the subdomain label.
                    record_name = None if zone_name == ui_custom_domain else ui_custom_domain
                    route53.ARecord(
                        self, "UiCustomDomainAlias",
                        zone=zone,
                        record_name=record_name,
                        target=route53.RecordTarget.from_alias(
                            route53_targets.CloudFrontTarget(self.distribution)
                        ),
                    )
            except Exception as e:
                # Don't fail the synth if the DNS lookup hits permissions or
                # network issues — the cert+alias on CloudFront is still correct.
                print(f"  [ui_stack] Route53 zone lookup for '{zone_name}' "
                      f"failed; skipping A-record: {e}")
        
        # Bucket policy to allow CloudFront OAC access
        bucket_policy = iam.PolicyDocument(
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                    actions=["s3:GetObject"],
                    resources=[f"{self.frontend_bucket.bucket_arn}/*"],
                    conditions={
                        "StringEquals": {
                            "AWS:SourceArn": f"arn:aws:cloudfront::{self.account}:distribution/{self.distribution.distribution_id}"
                        }
                    }
                )
            ]
        )
        
        s3.CfnBucketPolicy(
            self, "FrontendBucketPolicy",
            bucket=self.frontend_bucket.bucket_name,
            policy_document=bucket_policy
        )
        
        # Lambda execution role with minimal permissions
        lambda_role = iam.Role(
            self, "LambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
            ],
            inline_policies={
                "AppAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                                "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"
                            ],
                            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/*"]
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "iot:CreateThing", "iot:CreateKeysAndCertificate", "iot:CreatePolicy",
                                "iot:AttachThingPrincipal", "iot:AttachPrincipalPolicy"
                            ],
                            resources=["*"]
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "cognito-idp:ListUsers", "cognito-idp:AdminCreateUser",
                                "cognito-idp:AdminDeleteUser", "cognito-idp:AdminUpdateUserAttributes",
                                "cognito-idp:AdminAddUserToGroup", "cognito-idp:AdminRemoveUserFromGroup",
                                "cognito-idp:AdminListGroupsForUser", "cognito-idp:AdminDisableUser",
                                "cognito-idp:AdminEnableUser", "cognito-idp:AdminResetUserPassword",
                                "cognito-idp:AdminSetUserPassword",
                            ],
                            resources=[self.user_pool.user_pool_arn]
                        ),
                        # Read + lock/unlock the driver-facing (VSA) pool from
                        # the CMS operator UI. Scoped down to Get + enable/disable
                        # + attribute update only — no create/delete here (the
                        # seed script owns provisioning via admin_create_user
                        # in the seed-side boto3 session).
                        #
                        # AdminUpdateUserAttributes is required for /api/v1/driver-users/*
                        # PATCH routes (e.g., editing a driver's name/phone from
                        # the operator UI) — ported from a previous inline hotfix
                        # on the live role that wasn't in CDK.
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "cognito-idp:AdminGetUser",
                                "cognito-idp:AdminEnableUser",
                                "cognito-idp:AdminDisableUser",
                                "cognito-idp:AdminUpdateUserAttributes",
                            ],
                            resources=[
                                f"arn:aws:cognito-idp:{self.region}:{self.account}:userpool/"
                                + (self.node.try_get_context('vsaUserPoolId') or '*')
                            ]
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "s3:ListBucket", "s3:GetObject"
                            ],
                            resources=[
                                # Bucket name is suffixed with -{region}-{account} per
                                # spec `2026-06-04-cms-vfo-kb-bucket-region-suffix`
                                # (S3 names are global; suffix prevents cross-region
                                # collision). Bucket is now declared in
                                # `bedrock_agents_stack.py` as `VfoKnowledgeBaseBucket`.
                                f"arn:aws:s3:::cms-{construct_id.split('-')[1]}-vfo-knowledge-base-{self.region}-{self.account}",
                                f"arn:aws:s3:::cms-{construct_id.split('-')[1]}-vfo-knowledge-base-{self.region}-{self.account}/*",
                            ]
                        )
                    ]
                )
            }
        )
        
        # API Lambda functions
        self.api_functions = {}
        
        # Fleet management API
        fleet_api_kwargs = {
            'runtime': lambda_.Runtime.PYTHON_3_9,
            'handler': 'index.handler',
            'role': lambda_role,
            'code': lambda_.Code.from_asset("../modules/cms_ui/source/handlers/main_api"),
            'environment': {
                'FLEETS_TABLE_NAME': table_names['fleets'],
                'VEHICLES_TABLE_NAME': table_names['vehicles'],
                'TRIPS_TABLE_NAME': table_names['trips'],
                'TELEMETRY_TABLE_NAME': table_names['telemetry'],
                'SAFETY_EVENTS_TABLE_NAME': table_names['safety_events'],
                'MAINTENANCE_ALERTS_TABLE_NAME': table_names['maintenance_events'],
                'DTC_HISTORY_TABLE_NAME': table_names['dtc_history'],
                'USER_PREFERENCES_TABLE_NAME': table_names['user_preferences'],
                'DASHBOARD_METRICS_CACHE_TABLE': table_names['dashboard_metrics_cache'],
                'VEHICLE_CERTIFICATES_TABLE_NAME': table_names['vehicle_certificates'],
                'DRIVERS_TABLE_NAME': table_names['drivers'],
                'SERVICE_HISTORY_TABLE_NAME': table_names['service_history'],
                'FLEET_ENROLLMENT_TABLE_NAME': table_names['fleet_enrollment'],
                'VEHICLE_LINK_CODES_TABLE_NAME': vehicle_link_codes_table_name,
                'SUBSCRIPTIONS_TABLE_NAME': f"{storage_prefix}-subscriptions",
                'USER_POOL_ID': self.user_pool.user_pool_id,
                'CLIENT_ID': self.user_pool_client.user_pool_client_id,
                # VSA (driver-facing) Cognito pool. Used by /api/v1/driver-users/*
                # routes to show/manage driver Cognito accounts from the CMS UI.
                # Separate from the CMS USER_POOL_ID above (operator pool).
                # Override via `-c vsaUserPoolId=...` when deploying to a new env.
                # No hardcoded default — leaving it empty means handler code
                # must check for empty and fail-closed (rather than silently
                # connecting to a foreign tenant's Cognito pool).
                'VSA_USER_POOL_ID': self.node.try_get_context('vsaUserPoolId') or '',
                'REDIS_ENDPOINT': redis_endpoint if redis_endpoint else '',
                'SIGNAL_CATALOG_TABLE': f'cms-{construct_id.replace("-ui", "").split("-", 1)[-1]}-signal-catalog',
                'MODEL_MANIFEST_TABLE_NAME': f'cms-{construct_id.replace("-ui", "").split("-", 1)[-1]}-model-manifest',
            },
            'timeout': Duration.seconds(60),
            'memory_size': 1024,
        }

        # Add VPC config if MSK stack provides it (needed for Redis access)
        if self._data_vpc:
            fleet_api_kwargs['vpc'] = self._data_vpc
            fleet_api_kwargs['vpc_subnets'] = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
            if self._data_sg:
                fleet_api_kwargs['security_groups'] = [self._data_sg]

        self.api_functions['fleet'] = lambda_.Function(
            self, "FleetAPIFunction",
            **fleet_api_kwargs
        )
        
        # API Gateway
        self.api = apigateway.RestApi(
            self, "CMSAPI",
            rest_api_name=f"{construct_id}-api",
            description="CMS API Gateway",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"]
            )
        )

        # Gateway Responses — return CORS headers on 4XX/5XX so browsers
        # don't mask auth errors as opaque CORS failures.
        for resp_type in [
            apigateway.ResponseType.DEFAULT_4_XX,
            apigateway.ResponseType.DEFAULT_5_XX,
        ]:
            self.api.add_gateway_response(
                f"GatewayResponse{resp_type.response_type}",
                type=resp_type,
                response_headers={
                    "Access-Control-Allow-Origin": "'*'",
                    "Access-Control-Allow-Headers": "'Content-Type,Authorization'",
                    "Access-Control-Allow-Methods": "'GET,POST,PUT,DELETE,OPTIONS'",
                },
            )
        
        # Cognito Authorizer
        cognito_authorizer = apigateway.CognitoUserPoolsAuthorizer(
            self, "CMSCognitoAuthorizer",
            cognito_user_pools=[self.user_pool],
            authorizer_name=f"{construct_id}-cognito-auth"
        )
        
        # API resources - match target-account structure
        api_resource = self.api.root.add_resource("api")
        v1_resource = api_resource.add_resource("v1")
        
        # Use allow_test_invoke=False to prevent per-route Lambda::Permission bloat.
        # A single wildcard permission is added below instead.
        fleet_integration = apigateway.LambdaIntegration(
            self.api_functions['fleet'],
            allow_test_invoke=False
        )
        
        # Single wildcard permission — covers all routes/stages/methods
        self.api_functions['fleet'].add_permission(
            "APIGatewayInvokeAll",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=self.api.arn_for_execute_api()
        )
        
        # Realtime endpoints
        realtime_resource = self.api.root.add_resource("realtime")
        realtime_vehicles = realtime_resource.add_resource("vehicles")
        realtime_vehicles.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        realtime_trips = realtime_resource.add_resource("trips")
        realtime_trips.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        
        # IoT endpoint
        iot_endpoint = self.api.root.add_resource("discover-iot-endpoint")
        iot_endpoint.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        
        # Core v1 endpoints — kept explicit to stay within Lambda policy size limit
        # Additional routes (subscriptions, users, drivers, etc.) are handled by
        # the /api/v1/{proxy+} resource created outside CDK via API Gateway API.
        fleets_resource = v1_resource.add_resource("fleets")
        fleets_resource.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        fleets_resource.add_method("POST", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        fleet_id_resource = fleets_resource.add_resource("{fleetId}")
        fleet_id_resource.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        fleet_vehicles = fleet_id_resource.add_resource("vehicles")
        fleet_vehicles.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)

        vehicles_resource = v1_resource.add_resource("vehicles")
        vehicles_resource.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        vehicles_resource.add_method("POST", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        vehicle_locations = vehicles_resource.add_resource("locations")
        vehicle_locations.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        vehicle_id_resource = vehicles_resource.add_resource("{vehicleId}")
        vehicle_id_resource.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        vehicle_trips = vehicle_id_resource.add_resource("trips")
        vehicle_trips.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        trip_id_resource = vehicle_trips.add_resource("{tripId}")
        trip_id_resource.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        vehicle_safety = vehicle_id_resource.add_resource("safety-alerts")
        vehicle_safety.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        vehicle_maintenance = vehicle_id_resource.add_resource("maintenance-alerts")
        vehicle_maintenance.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)

        trips_resource = v1_resource.add_resource("trips")
        trips_resource.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        safety_alerts = v1_resource.add_resource("safety-alerts")
        safety_alerts.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        maintenance_alerts = v1_resource.add_resource("maintenance-alerts")
        maintenance_alerts.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)

        dashboard_resource = v1_resource.add_resource("dashboard")
        dashboard_metrics = dashboard_resource.add_resource("metrics")
        dashboard_metrics.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        dashboard_comparison = dashboard_resource.add_resource("fleet-comparison")
        dashboard_comparison.add_method("GET", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)

        # /api/v1/{proxy+} created manually via API Gateway API (not managed by CDK)
        # to avoid Lambda policy size limit. Covered by existing prod/*/* permission.

        # Root-level proxy for any other endpoints
        proxy_resource = self.api.root.add_resource("{proxy+}")
        proxy_resource.add_method("ANY", fleet_integration, authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO)
        
        # ── WebSocket API for real-time fleet telemetry ──────────────────
        ws_handler = lambda_.Function(
            self, "WSHandler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="websocket_handler.handler",
            code=lambda_.Code.from_asset("../services/websocket/lambda"),
            environment={
                'WS_CONNECTIONS_TABLE': ws_connections_table_name,
            },
            timeout=Duration.seconds(30),
            memory_size=256,
        )
        
        self.ws_api = apigatewayv2.CfnApi(
            self, "CMSWebSocketAPI",
            name=f"{construct_id}-ws",
            protocol_type="WEBSOCKET",
            route_selection_expression="$request.body.action",
        )
        
        ws_integration = apigatewayv2.CfnIntegration(
            self, "WSIntegration",
            api_id=self.ws_api.ref,
            integration_type="AWS_PROXY",
            integration_uri=f"arn:aws:apigateway:{self.region}:lambda:path/2015-03-31/functions/{ws_handler.function_arn}/invocations",
        )
        
        for route_key in ["$connect", "$disconnect", "$default"]:
            safe_name = route_key.replace("$", "")
            apigatewayv2.CfnRoute(
                self, f"WSRoute{safe_name}",
                api_id=self.ws_api.ref,
                route_key=route_key,
                target=f"integrations/{ws_integration.ref}",
            )
        
        apigatewayv2.CfnStage(
            self, "WSStage",
            api_id=self.ws_api.ref,
            stage_name="live",
            auto_deploy=True,
        )
        
        ws_handler.add_permission(
            "WSInvokePermission",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=f"arn:aws:execute-api:{self.region}:{self.account}:{self.ws_api.ref}/*",
        )
        
        ws_handler.add_to_role_policy(
            iam.PolicyStatement(
                actions=["execute-api:ManageConnections"],
                resources=[f"arn:aws:execute-api:{self.region}:{self.account}:{self.ws_api.ref}/live/*"],
            )
        )

        # WS connections table grants. The handler:
        #   $connect    → PutItem  (registers connectionId + fleetId)
        #   $disconnect → DeleteItem
        #   $default    → GetItem  (looks up the caller's fleet)
        # Plus Query on the fleetId-index GSI so ws-fanout can list a fleet's
        # active connections. The table lives in storage_stack; we build the
        # ARN from the same name-string both stacks share (line 187 here,
        # line 394 in storage_stack.py) instead of taking a cross-stack
        # construct dependency. Was missing entirely — caused HTTP 502 on
        # every $connect (issue 2026-05-28-cms-staging-ws-502-iam-gap).
        ws_handler.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:PutItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                ],
                resources=[
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/{ws_connections_table_name}",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/{ws_connections_table_name}/index/*",
                ],
            )
        )

        ws_endpoint = f"wss://{self.ws_api.ref}.execute-api.{self.region}.amazonaws.com/live"
        CfnOutput(self, "WebSocketEndpoint", value=ws_endpoint, export_name=f"{construct_id}-ws-endpoint")
        
        # Resolve sibling stack outputs at synth time so the runtime config is
        # populated even when env vars aren't exported. Priority:
        #   1. Explicit env var (lets users override without redeploying siblings)
        #   2. Synth-time CloudFormation describe-stacks lookup
        #   3. Empty string (downstream code handles missing endpoints)
        stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
        region = self.region

        def _resolve_endpoint(env_var: str, stack_suffix: str, output_keys: list) -> str:
            val = os.environ.get(env_var, "").strip()
            if val:
                return val
            stack_name = f"cms-{stage}-{stack_suffix}"
            for key in output_keys:
                v = _lookup_stack_output(stack_name, key, region)
                if v:
                    return v
            return ""

        data_processing_endpoint = (
            self._data_processing_api_endpoint
            or _resolve_endpoint("DATA_PROCESSING_API_ENDPOINT", "data-processing", ["APIEndpoint"])
        )
        simulation_endpoint = _resolve_endpoint("SIMULATION_API_ENDPOINT", "simulation", ["SimulationApiUrl"])
        commands_endpoint = _resolve_endpoint("COMMANDS_API_ENDPOINT", "commands", ["CommandsApiUrl"])

        # Cognito domain: prefer env var, else look up what's attached to this
        # stack's user pool (only resolvable on re-deploys, not first deploy).
        cognito_domain = os.environ.get("COGNITO_DOMAIN", "").strip()
        if not cognito_domain:
            existing_pool_id = _lookup_stack_output(f"cms-{stage}-ui", "UserPoolId", region)
            if existing_pool_id:
                cognito_domain = _lookup_cognito_domain(existing_pool_id, region)

        # Bedrock-agents sibling stack name — opt-in. When set, UI pulls agent
        # IDs/alias IDs via Fn::ImportValue from that stack's exports instead
        # of requiring `-c bedrockAgentId=XXX` on every deploy. Context flags
        # still take precedence when set, so legacy deploys keep working.
        bedrock_agents_stack_name = (
            self.node.try_get_context("bedrockAgentsStackName") or ""
        )

        # Create dynamic runtime config after API is created
        runtime_config = {
            "awsRegion": self.region,
            "mapAuth": {
                "identityPoolClient": f"cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}",
                "mapName": self.map.ref,
                "identityPoolId": self.identity_pool.ref
            },
            "locationServices": {
                "mapName": self.map.ref,
                "placeIndexName": self.place_index.ref, 
                "routeCalculatorName": self.route_calculator_name,
                "region": self.region,
                "enabled": True
            },
            "isDemoMode": "false",
            # Show quick-fill login buttons on non-prod stages for UAT.
            # Never true in prod — gated by DEPLOYMENT_STAGE env var.
            "showDemoButtons": os.environ.get('DEPLOYMENT_STAGE', 'dev') != 'prod',
            "apiEndpoint": self.api.url,
            "wsEndpoint": ws_endpoint,
            "userPreferencesApiEndpoint": self.api.url,
            "awsCredentials": {
                "region": self.region,
                "identityPoolId": self.identity_pool.ref,
                "userPoolId": self.user_pool.user_pool_id,
                "userPoolWebClientId": self.user_pool_client.user_pool_client_id
            },
            "dataProcessingApiEndpoint": data_processing_endpoint,
            "simulationApiEndpoint": simulation_endpoint,
            "commandsApiEndpoint": commands_endpoint,
            "cognitoDomain": cognito_domain,
            # Bedrock agents for the in-app chat/assistant. Read by ChatAgent.tsx.
            #
            # The primary agent is what the landing-page chat talks to. In the
            # CMS default deployment this is the supervisor (cms-virtual-fleet-
            # operator) which delegates to 4 specialists via Bedrock multi-agent
            # collaboration. The UI only needs the supervisor's ID + alias.
            #
            # Resolution order (each field independent):
            #   1. CDK context (`-c bedrockAgentId=...`, `-c bedrockAgentAliasId=...`)
            #   2. `Fn::ImportValue` from a sibling bedrock-agents stack, when
            #      `-c bedrockAgentsStackName=cms-<stage>-bedrock-agents` is set.
            #      Uses the `{stack}-primary-agent-id` / `{stack}-primary-alias-id`
            #      exports emitted by bedrock_agents_stack.py.
            #   3. Empty string — ChatAgent.tsx no-ops with a helpful message
            #      rather than throwing ResourceNotFoundException.
            #
            # The full agents dict (all 5 agent_name -> {agentId, aliasId} pairs)
            # is optional and only useful if the UI wants to bypass the
            # supervisor and talk directly to a specialist for domain-specific
            # flows. Not required for the landing-page chat.
            "bedrockAgent": {
                "agentId": (
                    self.node.try_get_context("bedrockAgentId")
                    or (Fn.import_value(f"{bedrock_agents_stack_name}-primary-agent-id")
                        if bedrock_agents_stack_name else "")
                ),
                "agentAliasId": (
                    self.node.try_get_context("bedrockAgentAliasId")
                    or (Fn.import_value(f"{bedrock_agents_stack_name}-primary-alias-id")
                        if bedrock_agents_stack_name else "")
                ),
                "region": self.node.try_get_context("bedrockAgentRegion") or self.region,
                "agents": _build_bedrock_agents_dict(self, bedrock_agents_stack_name),
            },
        }
        
        # Deploy frontend assets from the built React app
        frontend_deployment = s3deploy.BucketDeployment(
            self, "FrontendDeployment",
            sources=[
                s3deploy.Source.asset("../modules/cms_ui/source/frontend/build"),
                s3deploy.Source.json_data("runtimeConfig.json", runtime_config)
            ],
            destination_bucket=self.frontend_bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
            memory_limit=512,
            ephemeral_storage_size=Size.mebibytes(1024)
        )
        
        # Create default user with custom resource
        default_user_email = "FleetManager@example.com"
        # CMS_DEMO_DEFAULT_PASSWORD must be set at deploy time — never hardcode here.
        # Set via: export CMS_DEMO_DEFAULT_PASSWORD=... or in deployment/config/staging.env
        # NOTE: `os` is imported at module level (line 5). DO NOT re-import inside
        # this method — Python treats it as a local var, breaking the line ~236
        # `os.environ.get` reads earlier in __init__.
        default_password = os.environ.get("CMS_DEMO_DEFAULT_PASSWORD")
        if not default_password:
            raise ValueError(
                "CMS_DEMO_DEFAULT_PASSWORD environment variable must be set when seeding demo users. "
                "Set ad-hoc with `export CMS_DEMO_DEFAULT_PASSWORD=...` or via deployment/config/staging.env"
            )
        
        default_user_resource = custom_resource.AwsCustomResource(
            self, "DefaultUserResource",
            on_create=custom_resource.AwsSdkCall(
                service="CognitoIdentityServiceProvider",
                action="adminCreateUser",
                parameters={
                    "UserPoolId": self.user_pool.user_pool_id,
                    "Username": default_user_email,
                    "MessageAction": "SUPPRESS",
                    "TemporaryPassword": default_password,
                    "UserAttributes": [
                        {"Name": "email", "Value": default_user_email},
                        {"Name": "email_verified", "Value": "true"}
                    ]
                },
                physical_resource_id=custom_resource.PhysicalResourceId.of("default-user-create")
            ),
            policy=custom_resource.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=[
                        "cognito-idp:AdminCreateUser",
                        "cognito-idp:AdminSetUserPassword"
                    ],
                    resources=[self.user_pool.user_pool_arn]
                )
            ])
        )
        
        # Set permanent password after user creation
        set_password_resource = custom_resource.AwsCustomResource(
            self, "SetPermanentPasswordResource",
            on_create=custom_resource.AwsSdkCall(
                service="CognitoIdentityServiceProvider",
                action="adminSetUserPassword",
                parameters={
                    "UserPoolId": self.user_pool.user_pool_id,
                    "Username": default_user_email,
                    "Password": default_password,
                    "Permanent": True
                },
                physical_resource_id=custom_resource.PhysicalResourceId.of("default-user-password")
            ),
            policy=custom_resource.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=["cognito-idp:AdminSetUserPassword"],
                    resources=[self.user_pool.user_pool_arn]
                )
            ])
        )
        
        # Ensure password is set after user creation
        set_password_resource.node.add_dependency(default_user_resource)

        # Add default user to platform-admin group so the UI shows admin routes
        # (Data Processing, Documents, IoT device views, etc.) out of the box.
        add_to_admin_group_resource = custom_resource.AwsCustomResource(
            self, "DefaultUserAdminGroupResource",
            on_create=custom_resource.AwsSdkCall(
                service="CognitoIdentityServiceProvider",
                action="adminAddUserToGroup",
                parameters={
                    "UserPoolId": self.user_pool.user_pool_id,
                    "Username": default_user_email,
                    "GroupName": "platform-admin",
                },
                physical_resource_id=custom_resource.PhysicalResourceId.of("default-user-admin-group")
            ),
            policy=custom_resource.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=["cognito-idp:AdminAddUserToGroup"],
                    resources=[self.user_pool.user_pool_arn]
                )
            ])
        )
        add_to_admin_group_resource.node.add_dependency(set_password_resource)

        # Location Services resources are created above
        
        CfnOutput(
            self, "UserPoolId",
            value=self.user_pool.user_pool_id,
            export_name=f"{construct_id}-user-pool-id"
        )
        
        CfnOutput(
            self, "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
            export_name=f"{construct_id}-user-pool-client-id"
        )
        
        CfnOutput(
            self, "IdentityPoolId",
            value=self.identity_pool.ref,
            export_name=f"{construct_id}-identity-pool-id"
        )
        
        CfnOutput(
            self, "CloudFrontURL",
            value=f"https://{self.distribution.distribution_domain_name}",
            export_name=f"{construct_id}-cloudfront-url"
        )

        # Surface the friendly URL when a custom domain is configured.
        # Operators typically share this URL with users instead of the
        # CloudFront default domain.
        if ui_custom_domain and ui_custom_domain_cert_arn:
            CfnOutput(
                self, "CustomDomainURL",
                value=f"https://{ui_custom_domain}",
                export_name=f"{construct_id}-custom-domain-url"
            )
        
        CfnOutput(
            self, "APIEndpoint",
            value=self.api.url,
            export_name=f"{construct_id}-api-endpoint"
        )
        
        # Default user credentials
        CfnOutput(
            self, "DefaultUserEmail",
            value=default_user_email,
            description="Default user email for Fleet Manager login"
        )
        
        CfnOutput(
            self, "DefaultUserPassword", 
            value=default_password,
            description="Default user password for Fleet Manager login"
        )
        
        CfnOutput(
            self, "RouteCalculatorName",
            value=self.route_calculator_name,
            description="Location Services route calculator name for telemetry simulation"
        )
        
        # Location Services outputs
        CfnOutput(
            self, "LocationServicesMapName",
            value=self.map.ref,
            description="Amazon Location Services Map name",
            export_name=f"{construct_id}-map-name"
        )
        
        CfnOutput(
            self, "LocationServicesPlaceIndexName", 
            value=self.place_index.ref,
            description="Amazon Location Services Place Index name",
            export_name=f"{construct_id}-place-index-name"
        )
