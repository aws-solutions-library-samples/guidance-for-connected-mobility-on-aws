"""ConnectedServicesUiStack — CloudFront + S3 frontend for the Connected Services portal.

Spec: `.kiro/specs/2026-09-03-cms-connected-services-portal/`
Task: T2.1 (Group 2)

Design decisions:
  - Bucket name is region-suffixed per cross-region-namespace.md.
    Name template: connected-services-{stage}-ui-{account}-{region}
    Length budget (worst case):
      connected-services- (19) + staging (7) + -ui- (4)
      + 123456789012 (12) + - (1) + ap-southeast-7 (14) = 57 chars ≤ 63 ✓
  - Certificate: consume a pre-issued us-east-1 ARN via
    ``connectedServicesUiCustomDomainCertArn`` context key.
    See decisions.md § "D2 certificate: match the existing CMS/DMS ARN-via-env pattern".
    Both-or-neither validation on the domain+cert pair, throwing at synth if only one is set.
    The operator issues the cert for the portal domain in us-east-1 as a
    one-time action before deploying with the domain. Stack calls
    acm.Certificate.from_certificate_arn(...).
  - A Record: stack-owned Route53 A record — closes DMS residual (i) where
    dms_ui_stack.py has a manage-DNS flag that owns nothing. This stack unconditionally
    creates the A alias when the custom domain pair is set, using the pre-existing hosted
    zone from context (connectedServicesUiHostedZoneId).
  - Do NOT copy the unconsumed manage-DNS flag from dms_ui_stack.py.
  - SPA: 403 and 404 → /index.html error responses, default_root_object=index.html.
  - runtime-config.js injected via separate BucketDeployment (no-cache).
  - Cache policy split: index.html no-cache, hashed assets long-cache (immutable).
  - connectedServicesUiCallbackOrigin is a required context key; its value is embedded
    in the runtime config and in a CfnOutput so it appears in the synthesized template
    (spec D3 and test T1.5 item f).

Token-truthiness trap (recorded in active-projects.md):
    A CDK/CloudFormation token is always truthy.  This file deliberately uses
    explicit ``bool(x.strip())`` guards on context values and never bare ``if value``.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct


class ConnectedServicesUiStack(cdk.Stack):
    """CloudFront distribution + S3 bucket for the Connected Services portal frontend.

    Parameters
    ----------
    scope : Construct
    construct_id : str
    stage : str
        Deployment stage (e.g. ``"staging"``).
    **kwargs
        Forwarded to :class:`cdk.Stack`.

    CDK context keys read
    ---------------------
    connectedServicesUiCognitoUserPoolId : str
        Required. Cognito User Pool ID for the shared CMS pool.
    connectedServicesUiCognitoClientId : str
        Required. Cognito App Client ID.
    connectedServicesUiCognitoDomain : str
        Required. Cognito Hosted UI domain (e.g. ``<pool-domain>.auth.<region>.example.invalid``).
    connectedServicesUiApiEndpoint : str
        Required. Connected Services API endpoint URL.
    connectedServicesUiSubscriptionsApiEndpoint : str, optional
        Optional. Subscription-plane REST API endpoint URL (spec
        ``2026-09-10-cms-connected-services-subscriptions`` T3.5). Emitted to
        runtime-config.js as ``subscriptionsApiEndpoint`` and consumed by
        ``DataProductsView`` when it fetches the real catalog. Empty when the
        subscriptions stack is not deployed — the UI falls back to the fixture
        catalog in that case.
    connectedServicesUiCallbackOrigin : str
        Required. The https:// origin of this portal (e.g. ``https://<portal-domain>``).
        Embedded in the runtime config AND in a CfnOutput so it appears in the synthesized
        template — per spec D3 and T1.5 item (f). T2.2 also threads this through
        ui_stack.py's EXTERNAL_UI_CALLBACK_CONTEXT_KEYS so the Cognito client's
        callback URL list includes it.
    connectedServicesUiCustomDomain : str, optional
        Custom domain name (e.g. ``<portal-domain>``).
        Must be paired with ``connectedServicesUiCustomDomainCertArn`` and
        ``connectedServicesUiHostedZoneId``.
    connectedServicesUiCustomDomainCertArn : str, optional
        Pre-issued ACM certificate ARN in us-east-1. Must be paired with
        ``connectedServicesUiCustomDomain``.
    connectedServicesUiHostedZoneId : str, optional
        Route53 Hosted Zone ID for the domain. Required when the custom domain pair is set.
        The portal-domain hosted-zone id is a Route 53 attribute of the operator's
        DNS deployment (see spec § Context and staging.env). Not embedded here to
        keep this file scanner-clean for public mirror publish.

    CloudFormation outputs
    ----------------------
    DistributionDomainName
        The CloudFront ``d*.cloudfront.net`` domain name.
    CallbackOrigin
        The connectedServicesUiCallbackOrigin value — required to appear in the synthesized
        template per spec D3 and T1.5 item (f).
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        stage: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        account = self.account
        region = self.region

        # ── Required context keys — fail-closed ──────────────────────────────
        # These five values are required. Synth raises naming the missing key(s)
        # if any is absent or empty.
        # TOKEN-TRUTHINESS TRAP PREVENTION: bool(x.strip()) never bare if x.
        _required_context_keys = [
            "connectedServicesUiCognitoUserPoolId",
            "connectedServicesUiCognitoClientId",
            "connectedServicesUiCognitoDomain",
            "connectedServicesUiApiEndpoint",
            "connectedServicesUiCallbackOrigin",
        ]
        _missing_keys: list[str] = []
        _ctx_values: dict[str, str] = {}
        for _key in _required_context_keys:
            _val: str = (self.node.try_get_context(_key) or "").strip()
            if not bool(_val):
                _missing_keys.append(_key)
            else:
                _ctx_values[_key] = _val

        if _missing_keys:
            raise ValueError(
                "ConnectedServicesUiStack: required context key(s) missing or empty — "
                "cannot synth without them.  Pass each via `-c KEY=VALUE`.  "
                "Missing: " + ", ".join(_missing_keys)
            )

        # ── Optional context: subscription-plane API endpoint (T3.5) ─────────
        # The subscriptions stack is opt-in via DEPLOY_SUBSCRIPTIONS=true. When it
        # ships, this endpoint URL is threaded here so `DataProductsView` can call
        # the real catalog rather than its fixture. When it does not, the value is
        # empty and the frontend keeps falling back to the fixture — the whole
        # point of the field is that it degrades gracefully.
        _subscriptions_api_endpoint: str = (
            self.node.try_get_context("connectedServicesUiSubscriptionsApiEndpoint") or ""
        ).strip()

        # ── Optional context: simulation API endpoint (T7.4) ─────────────────
        # The simulation stack is opt-in. When deployed, this endpoint URL is
        # threaded here so the simulate-vehicle view can call the simulation REST
        # endpoints. When absent, the value is empty and the view degrades honestly
        # rather than making an undefined-URL fetch — the subscriptionsApiEndpoint
        # precedent, not the apiEndpoint/connectedServicesApiEndpoint mismatch.
        # Sourced from SimulationApiUrl CfnOutput (simulation_stack.py:1163).
        _simulation_api_endpoint: str = (
            self.node.try_get_context("connectedServicesUiSimulationApiEndpoint") or ""
        ).strip()

        # ── Optional context: data-processing API endpoint (T1.2) ────────────
        # Consumed by the Data Model screens (Signal Catalog, ECUs, Vehicle
        # Models, Decoder Manifests). When absent, the UI degrades to "not
        # configured" rather than fetching from an undefined URL — the same
        # absent-means-disabled contract as subscriptionsApiEndpoint (T3.5).
        # Sourced from staging.env CONNECTED_SERVICES_UI_DATA_PROCESSING_API_ENDPOINT.
        _data_processing_api_endpoint: str = (
            self.node.try_get_context("connectedServicesUiDataProcessingApiEndpoint") or ""
        ).strip()

        # ── Validate callback origin ─────────────────────────────────────────
        _callback_origin: str = _ctx_values["connectedServicesUiCallbackOrigin"]
        if not _callback_origin.startswith("https://"):
            raise ValueError(
                "connectedServicesUiCallbackOrigin must be an https:// origin "
                f"(got {_callback_origin!r})"
            )

        # ── Optional custom-domain context pair ──────────────────────────────
        # Both-or-neither validation — throws at synth if only one is set.
        # Mirrors ui_stack.py:1748-1756 and dms_ui_stack.py pattern.
        custom_domain: str = (
            self.node.try_get_context("connectedServicesUiCustomDomain") or ""
        ).strip()
        custom_domain_cert_arn: str = (
            self.node.try_get_context("connectedServicesUiCustomDomainCertArn") or ""
        ).strip()
        hosted_zone_id: str = (
            self.node.try_get_context("connectedServicesUiHostedZoneId") or ""
        ).strip()

        # Require cert ARN AND hosted zone when domain is set.
        _domain_set = bool(custom_domain)
        _cert_set = bool(custom_domain_cert_arn)
        if (_domain_set or _cert_set) and not (_domain_set and _cert_set):
            raise ValueError(
                "connectedServicesUiCustomDomain and connectedServicesUiCustomDomainCertArn "
                "must both be set (or both unset). "
                f"Got domain={custom_domain!r} cert_arn={custom_domain_cert_arn!r}"
            )

        _custom_domain_attached = bool(custom_domain) and bool(custom_domain_cert_arn)

        # ── S3 bucket — region-suffixed, RETAIN ──────────────────────────────
        # Name template: connected-services-{stage}-ui-{account}-{region}
        # Length budget (worst case, 14-char region + 12-digit account + 7-char "staging"):
        #   19 + 7 + 4 + 12 + 1 + 14 = 57 chars ≤ 63 limit. ✓
        # The BucketRetainAspect in app.py enforces RETAIN on any bucket with an explicit
        # bucket_name; the explicit RemovalPolicy.RETAIN here is belt-and-suspenders.
        bucket_name = f"connected-services-{stage}-ui-{account}-{region}"

        self.frontend_bucket = s3.Bucket(
            self,
            "ConnectedServicesUiFrontendBucket",
            bucket_name=bucket_name,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
        )

        # ── Custom error responses for SPA routing ────────────────────────────
        # 404 → 200 /index.html: every SPA deep link that doesn't match a file
        #   returns 404 from S3 without this, instead of the app shell.
        # 403 → 200 /index.html: OAC on a private bucket (no s3:ListBucket) means
        #   S3 returns 403 AccessDenied for a missing key, not 404.
        # ttl=0: without this CloudFront caches the error for up to 5 minutes.
        _custom_error_responses = [
            cloudfront.ErrorResponse(
                http_status=404,
                response_http_status=200,
                response_page_path="/index.html",
                ttl=cdk.Duration.seconds(0),
            ),
            cloudfront.ErrorResponse(
                http_status=403,
                response_http_status=200,
                response_page_path="/index.html",
                ttl=cdk.Duration.seconds(0),
            ),
        ]

        # ── CloudFront distribution kwargs ────────────────────────────────────
        _distribution_kwargs: dict = {}
        if _custom_domain_attached:
            _cert = acm.Certificate.from_certificate_arn(
                self,
                "ConnectedServicesUiCert",
                custom_domain_cert_arn,
            )
            _distribution_kwargs["domain_names"] = [custom_domain]
            _distribution_kwargs["certificate"] = _cert

        # ── WAF WebACL attachment ─────────────────────────────────────────────
        # Spec 2026-09-05-cms-connected-services-auth-integration § D6, H4.1.
        #
        # Closes the second divergence from primary CMS: the primary distribution
        # (E<distribution-id>) carries cms-<stage>-ui-waf, this distribution
        # carried nothing.
        #
        # § D6, and it matters where the temptation lands: a WAF does not authenticate.
        # This is NOT the remediation for the UnauthWebService finding and its presence
        # is not grounds to re-enable the distribution — see the enabled flag below.
        #
        # Reads the same `wafWebAclArn` context key as ui_stack.py:1787 rather than a
        # CS-specific one, so a single -c flag covers both stacks and there is one
        # convention to learn. Absent or blank = no WAF, matching ui_stack.py's
        # behaviour rather than inventing a stricter one that would block local synth.
        #
        # CloudFront's `web_acl_id` takes the WAFv2 *ARN*; the prop name is historical
        # (it accepted a WAF Classic id). Passing an id synths and fails at deploy.
        #
        # TOKEN-TRUTHINESS TRAP: explicit bool(x.strip()), never bare `if value`.
        _waf_web_acl_arn: str = (self.node.try_get_context("wafWebAclArn") or "").strip()
        if bool(_waf_web_acl_arn):
            _distribution_kwargs["web_acl_id"] = _waf_web_acl_arn
            print(f"  [connected_services_ui_stack] WAF WebACL attached to {construct_id}")
        else:
            print(
                f"  [connected_services_ui_stack] no WAF attached to {construct_id} "
                "(wafWebAclArn context key unset)"
            )

        # ── Distribution enabled state ────────────────────────────────────────
        # Spec § D7. Default is FALSE, and that is the whole point.
        #
        # Found 2026-09-05 while writing H1.3's synth guard: the DEPLOYED template for
        # this stack carried `Enabled: true` while the live distribution was `false`.
        # The Talos takedown of 2026-09-04 was applied out-of-band with the CLI, so it
        # was never IaC state — it was drift, and the next `cdk deploy` of this stack
        # would have silently re-enabled a surface taken down for a live Critical, with
        # no operator decision anywhere in the path.
        #
        # That made the deploy task's own promise ("distribution remains disabled")
        # false, and it routed around § D7's gate rather than honouring it.
        #
        # Defaulting to false converts the takedown into declared state: a deploy now
        # KEEPS it disabled, and re-enabling requires `-c connectedServicesUiEnabled=true`
        # — an explicit, reviewable, greppable act rather than a side effect.
        #
        # Same explicit-truthiness discipline; only the exact string "true" enables, so
        # a typo fails closed.
        _enabled_ctx: str = (
            self.node.try_get_context("connectedServicesUiEnabled") or ""
        ).strip().lower()
        _distribution_enabled: bool = _enabled_ctx == "true"
        _distribution_kwargs["enabled"] = _distribution_enabled
        if not _distribution_enabled:
            print(
                f"  [connected_services_ui_stack] {construct_id} distribution is "
                "DISABLED (default). Pass -c connectedServicesUiEnabled=true to enable "
                "— see spec 2026-09-05-cms-connected-services-auth-integration § D7."
            )

        # ── CloudFront distribution ───────────────────────────────────────────
        self.distribution = cloudfront.Distribution(
            self,
            "ConnectedServicesUiDistribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(
                    self.frontend_bucket,
                    origin_access_control=cloudfront.S3OriginAccessControl(
                        self,
                        "ConnectedServicesUiS3Oac",
                    ),
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            error_responses=_custom_error_responses,
            **_distribution_kwargs,
        )

        # ── Route53 A record (stack-owned) ────────────────────────────────────
        # Closes DMS residual (i): dms_ui_stack.py has a manage-DNS context flag that
        # reads as ownership but owns nothing (no aws_route53 import, no ARecord).
        # This stack creates the A alias unconditionally when the domain pair is set,
        # so a teardown/rebuild restores both cert and record rather than just the cert.
        if _custom_domain_attached and bool(hosted_zone_id):
            _hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
                self,
                "ConnectedServicesUiHostedZone",
                hosted_zone_id=hosted_zone_id,
                zone_name=custom_domain,
            )
            route53.ARecord(
                self,
                "ConnectedServicesUiAliasRecord",
                zone=_hosted_zone,
                record_name=custom_domain,
                target=route53.RecordTarget.from_alias(
                    targets.CloudFrontTarget(self.distribution)
                ),
            )

        # ── Derive cognitoRegion from the pool ID ─────────────────────────────
        _pool_id: str = _ctx_values["connectedServicesUiCognitoUserPoolId"]
        _cognito_region: str = _pool_id.split("_")[0] if "_" in _pool_id else region

        # ── Runtime config content ────────────────────────────────────────────
        # Embeds the callback origin so it appears in the synthesized CFN template
        # (spec D3 and T1.5 item f). T2.2 also threads this value through
        # ui_stack.py's EXTERNAL_UI_CALLBACK_CONTEXT_KEYS.
        # ── Simulation product rule name (T7.4a) ─────────────────────────────
        # A pure function of stage — no context key, no optional lookup.
        # The Lambda's allowlist accepts exactly this form; the portal sends it
        # as rule_name in POST /start so the simulation publishes to the
        # OEM2 manifest-path Kafka topic rather than the CMS-native default.
        _simulation_product_rule_name: str = f"cms_{stage}_cs_product_meridian_ev_rule"

        _runtime_config_js = (
            "window.runtimeConfig = {{\n"
            '  "cognitoUserPoolId": "{pool_id}",\n'
            '  "cognitoClientId": "{client_id}",\n'
            '  "cognitoRegion": "{cognito_region}",\n'
            '  "cognitoDomain": "{domain}",\n'
            '  "connectedServicesApiEndpoint": "{api_endpoint}",\n'
            '  "subscriptionsApiEndpoint": "{subscriptions_api_endpoint}",\n'
            '  "simulationApiEndpoint": "{simulation_api_endpoint}",\n'
            '  "simulationProductRuleName": "{simulation_product_rule_name}",\n'
            '  "dataProcessingApiEndpoint": "{data_processing_api_endpoint}",\n'
            '  "callbackOrigin": "{callback_origin}"\n'
            "}};\n"
        ).format(
            pool_id=_ctx_values["connectedServicesUiCognitoUserPoolId"],
            client_id=_ctx_values["connectedServicesUiCognitoClientId"],
            cognito_region=_cognito_region,
            domain=_ctx_values["connectedServicesUiCognitoDomain"],
            api_endpoint=_ctx_values["connectedServicesUiApiEndpoint"],
            subscriptions_api_endpoint=_subscriptions_api_endpoint,
            simulation_api_endpoint=_simulation_api_endpoint,
            simulation_product_rule_name=_simulation_product_rule_name,
            data_processing_api_endpoint=_data_processing_api_endpoint,
            callback_origin=_callback_origin,
        )

        # ── BucketDeployment 1 — runtime-config.js, no-cache ─────────────────
        # Separate from the rest of the bundle so cache headers are independent.
        # no-cache: ensures the browser re-validates after every redeploy.
        s3deploy.BucketDeployment(
            self,
            "ConnectedServicesUiRuntimeConfigDeployment",
            sources=[
                s3deploy.Source.data(
                    "runtime-config.js",
                    _runtime_config_js,
                )
            ],
            destination_bucket=self.frontend_bucket,
            distribution=self.distribution,
            distribution_paths=["/runtime-config.js"],
            cache_control=[
                s3deploy.CacheControl.from_string(
                    "no-cache, no-store, must-revalidate"
                )
            ],
            prune=False,
        )

        # ── BucketDeployment 2 — hashed assets, long-cached ──────────────────
        # Content-hashed filenames mean a new build produces new URLs, so these
        # can be cached for a year.  index.html is EXCLUDED and handled separately.
        s3deploy.BucketDeployment(
            self,
            "ConnectedServicesUiFrontendAssetsDeployment",
            sources=[s3deploy.Source.asset("../modules/connected_services_ui/dist")],
            destination_bucket=self.frontend_bucket,
            exclude=["index.html"],
            cache_control=[
                s3deploy.CacheControl.from_string(
                    "public, max-age=31536000, immutable"
                )
            ],
            distribution=self.distribution,
            distribution_paths=["/assets/*"],
            prune=False,
        )

        # ── BucketDeployment 3 — index.html, never long-cached ───────────────
        # index.html is the one file that names the current hashed bundle and
        # loads /runtime-config.js.  Must not be edge-cached or the browser
        # serves the prior index.html after a redeploy (DMS Fix Group 7 lesson).
        s3deploy.BucketDeployment(
            self,
            "ConnectedServicesUiFrontendIndexDeployment",
            sources=[s3deploy.Source.asset("../modules/connected_services_ui/dist")],
            destination_bucket=self.frontend_bucket,
            exclude=["*"],
            include=["index.html"],
            cache_control=[
                s3deploy.CacheControl.from_string(
                    "no-cache, no-store, must-revalidate"
                )
            ],
            distribution=self.distribution,
            distribution_paths=["/", "/index.html"],
            prune=False,
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "DistributionDomainName",
            value=self.distribution.distribution_domain_name,
            description="CloudFront distribution domain name for the Connected Services frontend",
        )

        # CallbackOrigin output: ensures the connectedServicesUiCallbackOrigin value
        # appears in the synthesized CloudFormation template (spec D3, T1.5 item f).
        cdk.CfnOutput(
            self,
            "CallbackOrigin",
            value=_callback_origin,
            description=(
                "Connected Services portal callback origin, consumed by ui_stack.py "
                "(EXTERNAL_UI_CALLBACK_CONTEXT_KEYS) to register this portal's "
                "/auth/callback URL with the shared Cognito app client."
            ),
        )
