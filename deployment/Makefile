# CMS Simplified Deployment Makefile
.PHONY: help install bootstrap deploy deploy-all clean clean-logs infrastructure check-docker check-bootstrap \
	build-ui build-commands data-processing \
	phase1 phase2 phase3 phase3b phase4 phase5 phase-foundation phase-streaming phase-seeds phase-services phase-staging-demo-data \
	deploy-simulation deploy-commands deploy-ws-fanout deploy-fleetwise deploy-flink deploy-bedrock-agents deploy-connector \
	drain-stale-fwe-agents publish-public-ecr \
	configure-msk configure-flink configure-pipeline validate-flink-deployment \
	regenerate-runtime-config status \
	seed-all-demo-data seed-drivers seed-driver-users seed-demo-personas seed-fleet-enrollment seed-generic-fleets seed-meridian-fleet seed-signal-catalog seed-event-catalog \
	seed-vehicle-lifecycle seed-trip-history seed-service-warranty seed-nhtsa-recalls seed-fleetwise seed-manifest \
	seed-vfo-actions seed-decision-journal generate-and-upload-decoder-manifest sync-manifests \
	inject-everything inject-derived-only inject-warranty-only inject-recalls-only \
	preflight-demo verify-demo-data bootstrap-demo refresh-recalls-ui

# Default target
.DEFAULT_GOAL := help

# Colors for output
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[0;33m
BLUE := \033[0;34m
NC := \033[0m # No Color

# --- cdk-shim PATH prepend ---------------------------------------------------
# Routes all `cdk` invocations made via `make` through deployment/scripts/cdk,
# a wrapper that refuses to run when the clean-deploy harness holds the
# .cdk-harness-lock and the caller is NOT a descendant of the lock holder.
#
# Closes the bidirectional gap left by run_clean_deploy_test.sh's lock
# (which only catches at orchestrator startup): now any concurrent cdk
# invocation FROM another shell, IDE, or automation that goes through
# `make` is also blocked while the harness is in flight.
#
# Surfaced by clean-deploy run 19 (2026-06-05T19-16-42Z): a separate
# cdk process started mid-run and CDK CLI's internal "Another CLI is
# currently synthing to cdk.out" lock killed the harness.
#
# Limitation: raw `cdk` invocations from another shell session that don't
# go through `make` bypass this shim unless the user prepends scripts/
# to their shell PATH (or uses direnv). P4 follow-up.
MAKEFILE_DIR := $(dir $(realpath $(firstword $(MAKEFILE_LIST))))
export PATH := $(MAKEFILE_DIR)scripts:$(PATH)

# Configuration
AWS_REGION ?= $(shell aws configure get region --profile $(AWS_PROFILE) 2>/dev/null || echo "us-west-2")
# Public ECR registry for published simulation images. TEMPORARY self-hosted
# namespace; override to publish/pull elsewhere, e.g. PUBLIC_ECR_REGISTRY=public.ecr.aws/yourns
PUBLIC_ECR_REGISTRY ?= public.ecr.aws/o0q5e8r2
# Images published by `make publish-public-ecr`. Both by default; override to
# narrow (see the rationale block on that target).
PUBLISH_IMAGES ?= cms-sim-service cms-fwe-agent
DEPLOYMENT_STAGE ?= $(shell echo $${DEPLOYMENT_STAGE:-dev})

# COGNITO_DOMAIN for `regenerate-runtime-config`, resolved once here.
#
# Environment wins (the `source config/<stage>.env && make ...` path), else read
# it out of the stage env file directly — the same thing `phase1` already does
# for UI_CUSTOM_DOMAIN, WAF_WEB_ACL_ARN and the rest of that block.
#
# Why this exists: five targets chain `$(MAKE) regenerate-runtime-config` and NONE
# passed COGNITO_DOMAIN, so the target's fail-closed guard fired every single time
# and each call site swallowed the non-zero exit with `|| echo`. The refresh step
# had a 100% failure rate from every chained caller while the parents still printed
# a green success line. Measured across all three staging deploys on 2026-09-23.
# See issues/2026-09-22-regenerate-runtime-config-never-runs-from-chained-callers/.
#
# Recursive (`=`) not immediate (`:=`) so DEPLOYMENT_STAGE is whatever the invoking
# target has, not whatever it was at parse time.
#
# This does NOT weaken the guard. clean-deploy.env and smoke-test-email.env carry
# `COGNITO_DOMAIN=` (empty), so those resolve to empty and the target still refuses
# rather than writing `"cognitoDomain": ""` and silently removing the Federate
# login button — the defect that guard was added for.
COGNITO_DOMAIN_RESOLVED = $(strip $(if $(strip $(COGNITO_DOMAIN)),$(strip $(COGNITO_DOMAIN)),$(shell sed -n 's/^COGNITO_DOMAIN=//p' config/$(DEPLOYMENT_STAGE).env 2>/dev/null | head -1)))

# DMS console origin for `regenerate-runtime-config` → runtimeConfig.dmsUiOrigin,
# which CMS uses to deep-link a dispatched session to DMS's repair-order page
# (utils/dmsLinks.ts). Resolved in the RECIPE'S SHELL, not by make: environment
# DMS_UI_CALLBACK_ORIGIN first, then the stage's config file, where the key
# already exists for the Cognito callback allowlist. make must never expand the
# value: an environment variable referenced as $(VAR) is recursively expanded,
# so a value containing $(shell …) would run at parse time, even under make -n
# (FG5 security review cycle 2, W2). In the shell the value is only data.
# Empty on stages without DMS; the key is then omitted and CMS shows the
# repair-order id without a link. Before FG5 the target never wrote the key, so
# CMS built a relative link that opened its own 404 page
# (issues/2026-09-25-diagnostics-view-ro-link-404/).

# VSA Cognito pool — sourced from deployment/cdk.json's `context` block so
# the same value is used by both CDK synth (`self.node.try_get_context('vsaUserPoolId')`
# at deployment/stacks/ui_stack.py:778) and the seed-driver-users target.
# Single source of truth. Empty string if absent — seed_driver_users.py
# fail-closes with a clear error in that case.
VSA_USER_POOL_ID := $(shell python3 -c "import json,os; p=os.path.join(os.path.dirname('$(MAKEFILE_LIST)'),'cdk.json'); ctx=json.load(open('cdk.json')).get('context',{}) if os.path.exists('cdk.json') else {}; print(ctx.get('vsaUserPoolId',''))")

# AWS Profile Selection
AWS_PROFILES := $(shell aws configure list-profiles 2>/dev/null || echo "default")
PROFILE_COUNT := $(shell echo $(AWS_PROFILES) | wc -w)

# Auto-select profile if only one exists
ifeq ($(PROFILE_COUNT),1)
    AWS_PROFILE := $(AWS_PROFILES)
else
    ifndef AWS_PROFILE
        AWS_PROFILE := default
    endif
endif

# ── Alias-guard context flags (issue 2026-06-19-cms-nonphase1-targets-missing-domain-context) ──
# The prod/staging UI custom-domain alias guard (app.py -> aspects/domain_alias_guard.py)
# runs on EVERY cdk synth, app-wide, regardless of which stack is the deploy target.
# So every `cdk deploy`/`cdk diff` must carry the UI custom-domain context (+ the
# prod/staging demo map-auth flag), or synth aborts with RuntimeError. phase1 builds
# these inline; this variable supplies the SAME flags to all other phase targets,
# sourced from config/<stage>.env (single source of truth). Empty for stages with no
# UI custom domain (e.g. dev). prod => map-auth on, self-signup OFF (admin-create seeding).
GUARD_CTX_FLAGS := $(shell cfg=config/$(DEPLOYMENT_STAGE).env; flags=""; if [ -f "$$cfg" ]; then d=$$(sed -n 's/^UI_CUSTOM_DOMAIN=//p' "$$cfg"); c=$$(sed -n 's/^UI_CUSTOM_DOMAIN_CERT_ARN=//p' "$$cfg"); r=$$(sed -n 's/^UI_CUSTOM_DOMAIN_REGION=//p' "$$cfg"); n=$$(sed -n 's/^UI_CUSTOM_DOMAIN_MANAGE_DNS=//p' "$$cfg"); if [ -n "$$d" ] && [ -n "$$c" ]; then flags="-c uiCustomDomain=$$d -c uiCustomDomainCertArn=$$c -c uiCustomDomainRegion=$${r:-$(AWS_REGION)} -c uiCustomDomainManageDns=$${n:-false}"; fi; fi; db=$$(sed -n 's/^DMS_EVENT_BUS_NAME=//p' "$$cfg"); da=$$(sed -n 's/^DMS_API_ENDPOINT=//p' "$$cfg"); dco=$$(sed -n 's/^DMS_UI_CALLBACK_ORIGIN=//p' "$$cfg"); csco=$$(sed -n 's/^CONNECTED_SERVICES_UI_CALLBACK_ORIGIN=//p' "$$cfg"); if [ -n "$$db" ]; then flags="$$flags -c dmsEventBusName=$$db"; fi; if [ -n "$$da" ]; then flags="$$flags -c dmsApiEndpoint=$$da"; fi; if [ -n "$$dco" ]; then flags="$$flags -c dmsUiCallbackOrigin=$$dco"; fi; if [ -n "$$csco" ]; then flags="$$flags -c connectedServicesUiCallbackOrigin=$$csco"; fi; if [ "$(DEPLOYMENT_STAGE)" = "staging" ]; then flags="$$flags -c cms.allow_unauth_map_auth=true"; elif [ "$(DEPLOYMENT_STAGE)" = "prod" ]; then flags="$$flags -c cms.allow_unauth_map_auth=true"; fi; echo $$flags)

help: ## Show this help message
	@echo "$(BLUE)🚀 CMS Simplified Deployment$(NC)"
	@echo "============================="
	@echo ""
	@echo "$(GREEN)🎯 Deployment Phases:$(NC)"
	@echo "  make deploy-all          - Deploy everything (excludes demo data seeding)"
	@echo ""
	@echo "$(GREEN)  Grouped phases (deploy-all runs these in order):$(NC)"
	@echo "    make phase-foundation  - data-processing + storage + iot + ui + msk + telemetry-integration"
	@echo "    make phase-streaming   - flink + fleetwise (order matters)"
	@echo "    make phase-seeds       - signal/event catalog + fleet-enrollment + fleetwise decoder"
	@echo "    make phase5            - Configure + start Flink apps"
	@echo "    make phase-services    - simulation + commands + ws-fanout"
	@echo "    make phase-staging-demo-data - drivers (staging-deploy only; not prod)"
	@echo ""
	@echo "$(GREEN)  Individual stacks:$(NC)"
	@echo "    make data-processing   - Signal Catalog + Transform Manifests"
	@echo "    make phase1            - Storage + IoT + UI (pulls in MSK transitively)"
	@echo "    make phase3            - VPC + MSK + Redis (subset of phase1)"
	@echo "    make phase3b           - Telemetry Integration (IoT → MSK rule + VPC destination)"
	@echo "    make phase4            - Flink Apps (build JAR + deploy)"
	@echo "    make phase5            - Flink Configuration (MSK bootstrap + IAM auth + start)"
	@echo "    make phase6            - Pipeline Configuration"
	@echo "    make deploy-fleetwise  - FleetWise Integration (FWE rules + VPC endpoints)"
	@echo "    make deploy-simulation - Simulation Service (published image — no builder; SIM_IMAGE_MODE=asset builds locally)"
	@echo "    make publish-public-ecr - [release] Build+push sim images to public ECR (VERSION=vX.Y.Z)"
	@echo "    make deploy-commands   - Remote Commands (Lambda + API GW + IoT Rule)"
	@echo "    make deploy-ws-fanout  - WebSocket Fanout (Docker, Kafka → WS bridge)"
	@echo ""
	@echo "$(GREEN)📊 Data Seeding:$(NC)"
	@echo "  $(BOLD)make bootstrap-demo$(NC)        - $(BOLD)ONE COMMAND$(NC): preflight + signal/event catalogs + FWE decoder/campaign + enhanced injector (trips/safety/maint/warranty/DTC/recalls/TCO/charging/snapshots) + verification"
	@echo "                              Override: INJECT_DAYS=730, NUM_FLEETS=5, VEHICLES_PER_FLEET=10"
	@echo "  make preflight-demo        - Just run the preflight checks (doesn't seed anything)"
	@echo "  make verify-demo-data      - Just run the post-seed verification"
	@echo ""
	@echo "  make inject-everything     - Enhanced injector + FWE seeds (no preflight, no verify)"
	@echo "  make inject-derived-only   - Regenerate only charging + TCO + location snapshots"
	@echo "  make inject-warranty-only  - Regenerate only service-history + warranty + DTC"
	@echo "  make inject-recalls-only   - Fetch NHTSA recalls and match against existing vehicles"
	@echo ""
	@echo "  make seed-all-demo-data  - Granular (pre-injector workflow)"
	@echo "  make seed-drivers        - 75 drivers (CDL classes, safety scores, experience)"
	@echo "  make seed-fleet-enrollment - Fleet-vehicle assignments"
	@echo "  make seed-signal-catalog - CAN signals, GPS, ADAS metadata"
	@echo "  make seed-event-catalog  - Safety + maintenance event definitions"
	@echo "  make seed-vehicle-lifecycle - Purchase, depreciation, insurance, warranty, cost/mile"
	@echo "  make seed-trip-history   - Trip history from purchase date to now"
	@echo "  make seed-service-warranty - Service records + warranty claims + DTCs + PDFs"
	@echo "  make seed-nhtsa-recalls  - NHTSA recall matching"
	@echo ""
	@echo "  Options: HISTORY_YEARS=N (default: 2), DEPLOYMENT_STAGE=dev|prod, AWS_REGION=us-west-2"
	@echo ""
	@echo "$(GREEN)Optional:$(NC)"
	@echo "  make phase2              - Interactive historical data injection (trips + telemetry)"
	@echo "  make data-processing     - Signal Catalog + Transform Manifests"
	@echo ""
	@echo "$(GREEN)Setup Commands:$(NC)"
	@echo "  make preflight-demo      - Check all prerequisites (Docker, AWS creds, tools)"
	@echo "  make install             - Install dependencies and setup environment"
	@echo "  make bootstrap           - Bootstrap CDK in the target account"
	@echo ""
	@echo "$(GREEN)🧪 Clean-deploy integration test:$(NC)"
	@echo "  make clean-deploy-test                  - Run end-to-end clean-deploy harness (default REGION=ap-northeast-1)"
	@echo "  make clean-deploy-test REGION=<code>    - Run against a specific AWS region"
	@echo ""
	@echo "$(GREEN)Profile Selection:$(NC)"
	@echo "  Current: AWS_PROFILE=$(AWS_PROFILE)"
	@echo "  Available: $(AWS_PROFILES)"
	@echo "  Usage: make phase1 AWS_PROFILE=my-profile"

deploy: ## Interactive deployment with profile and phase selection
	@echo "$(BLUE)🚀 CMS Interactive Deployment$(NC)"
	@echo "=============================="
	@echo ""
	@echo "$(GREEN)Available AWS Profiles:$(NC)"
	@echo "$(AWS_PROFILES)" | tr ' ' '\n' | nl -v0
	@echo ""
	@read -p "Select profile number (0 for default): " profile_choice; \
	if [ "$$profile_choice" = "0" ] || [ -z "$$profile_choice" ]; then \
		selected_profile="default"; \
	else \
		profile_index=$$(($$profile_choice + 1)); \
		selected_profile=$$(echo "$(AWS_PROFILES)" | tr ' ' '\n' | sed -n "$${profile_index}p"); \
	fi; \
	if [ -z "$$selected_profile" ]; then \
		echo "$(RED)❌ Invalid profile selection$(NC)"; \
		exit 1; \
	fi; \
	echo "$(GREEN)✅ Selected profile: $$selected_profile$(NC)"; \
	echo ""; \
	echo "$(GREEN)Deployment Stage Options:$(NC)"; \
	echo "1. dev (cms-dev-*)"; \
	echo "2. prod (cms-prod-*)"; \
	echo "3. $$selected_profile (cms-$$selected_profile-*)"; \
	echo "4. Custom stage name"; \
	echo ""; \
	read -p "Select deployment stage (1-4): " stage_choice; \
	case $$stage_choice in \
		1) selected_stage="dev" ;; \
		2) selected_stage="prod" ;; \
		3) selected_stage="$$selected_profile" ;; \
		4) read -p "Enter custom stage name: " selected_stage ;; \
		*) echo "$(RED)❌ Invalid stage selection$(NC)"; exit 1 ;; \
	esac; \
	echo "$(GREEN)✅ Selected stage: $$selected_stage$(NC)"; \
	echo ""; \
	echo "$(GREEN)Deployment Options:$(NC)"; \
	echo "0. Infrastructure Foundation (VPC, Subnets, ElastiCache)"; \
	echo "1. Fleet Manager Interface (IoT, Storage, UI, Lambda, Cognito, S3)"; \
	echo "2. Fleet Management Interface (historical data injection)"; \
	echo "3. Telemetry Pipeline (MSK Deployment)"; \
	echo "4. Telemetry Pipeline (MSK Configuration + IoT Integration)"; \
	echo "5. Telemetry Pipeline (Flink Deployment)"; \
	echo "6. Telemetry Pipeline (Flink Configuration)"; \
	echo "7. Complete Pipeline Configuration"; \
	echo "8. Deploy All Phases"; \
	echo ""; \
	read -p "Select deployment option (0-8): " deploy_choice; \
	echo ""; \
	case $$deploy_choice in \
		0) echo "$(BLUE)🚀 Infrastructure Foundation$(NC)"; \
		   $(MAKE) infrastructure AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		1) echo "$(BLUE)🚀 Phase 1: Fleet Manager Interface$(NC)"; \
		   $(MAKE) phase1 AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		2) echo "$(BLUE)🚀 Phase 2: Fleet Management Interface$(NC)"; \
		   $(MAKE) phase2 AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		3) echo "$(BLUE)🚀 Phase 3: Telemetry Pipeline (MSK Deployment)$(NC)"; \
		   $(MAKE) phase3 AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		4) echo "$(BLUE)🚀 Phase 3b: Telemetry Pipeline (MSK Configuration + IoT Integration)$(NC)"; \
		   $(MAKE) phase3b AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		5) echo "$(BLUE)🚀 Phase 4: Telemetry Pipeline (Flink Deployment)$(NC)"; \
		   $(MAKE) phase4 AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		6) echo "$(BLUE)🚀 Phase 5: Telemetry Pipeline (Flink Configuration)$(NC)"; \
		   $(MAKE) phase5 AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		7) echo "$(BLUE)🚀 Phase 6: Complete Pipeline Configuration$(NC)"; \
		   $(MAKE) phase6 AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		8) echo "$(BLUE)🚀 Deploying All Phases$(NC)"; \
		   $(MAKE) deploy-all AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		*) echo "$(RED)❌ Invalid deployment option$(NC)"; exit 1 ;; \
	esac; \
	echo ""; \
	echo "$(GREEN)🎉 Deployment completed!$(NC)"; \
	$(MAKE) status AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage

install: ## Install dependencies and setup environment
	@echo "$(BLUE)📦 Installing dependencies...$(NC)"
	@python3 -m venv .venv
	@source .venv/bin/activate && pip install -r requirements.txt
	@echo "$(GREEN)✅ Dependencies installed$(NC)"

bootstrap: ## Bootstrap CDK in current account
	@echo "$(BLUE)🔧 Bootstrapping CDK...$(NC)"
	@source .venv/bin/activate && \
	cdk bootstrap aws://$(shell aws sts get-caller-identity --query Account --output text)/$(AWS_REGION)
	@echo "$(GREEN)✅ CDK bootstrapped$(NC)"

check-bootstrap: ## Check if CDK is bootstrapped
	@echo "$(BLUE)🔍 Checking CDK bootstrap status in $(AWS_REGION)...$(NC)"
	@if ! aws ssm get-parameter --name /cdk-bootstrap/hnb659fds/version --region $(AWS_REGION) --profile $(AWS_PROFILE) >/dev/null 2>&1; then \
		echo "$(YELLOW)⚠️  CDK not bootstrapped in $(AWS_REGION). Running bootstrap...$(NC)"; \
		source .venv/bin/activate && \
		cdk bootstrap aws://$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text)/$(AWS_REGION) --profile $(AWS_PROFILE); \
	else \
		echo "$(GREEN)✅ CDK already bootstrapped in $(AWS_REGION)$(NC)"; \
	fi

check-docker: ## Verify a container builder is available (docker, finch, or podman)
	@# CDK uses the executable named by $$CDK_DOCKER (default: 'docker') for image-asset
	@# builds. Accepts AWS-supported `finch` and community-tested `podman` as drop-ins.
	@# Reference: https://docs.aws.amazon.com/cdk/v2/guide/build-containers.html
	@# (verified against aws-cdk-lib v2.258.1 aws_ecr_assets README, 2026-06-15)
	@#
	@# Behavior:
	@#  1. If CDK_DOCKER is set explicitly → validate that exact executable is on PATH
	@#     and `<exe> info` succeeds. Hard-fail otherwise.
	@#  2. Else if `docker info` succeeds → use docker (CDK default; no env var needed).
	@#  3. Else if finch or podman is ready → fail with an explicit "set CDK_DOCKER=<x>"
	@#     message (CDK won't auto-pick a non-docker builder; opt-in by design).
	@#  4. Else → fail with install hints for all three.
	@if [ -n "$${CDK_DOCKER:-}" ]; then \
		if ! command -v "$$CDK_DOCKER" >/dev/null 2>&1; then \
			echo "$(RED)❌ CDK_DOCKER='$$CDK_DOCKER' but executable not on PATH.$(NC)"; \
			exit 1; \
		fi; \
		if ! "$$CDK_DOCKER" info >/dev/null 2>&1; then \
			echo "$(RED)❌ CDK_DOCKER='$$CDK_DOCKER' set, but '$$CDK_DOCKER info' failed.$(NC)"; \
			echo "$(YELLOW)   Start it (e.g. 'finch vm start', 'podman machine start', or 'open -a Docker') and retry.$(NC)"; \
			exit 1; \
		fi; \
		echo "$(GREEN)✅ Container builder '$$CDK_DOCKER' is ready (CDK_DOCKER override)$(NC)"; \
		exit 0; \
	fi; \
	if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then \
		echo "$(GREEN)✅ docker daemon is running (CDK default)$(NC)"; \
		exit 0; \
	fi; \
	for alt in finch podman; do \
		if command -v $$alt >/dev/null 2>&1 && $$alt info >/dev/null 2>&1; then \
			echo "$(YELLOW)⚠️  docker not available, but '$$alt' is ready.$(NC)"; \
			echo "$(YELLOW)   CDK defaults to 'docker' — opt in to '$$alt' explicitly:$(NC)"; \
			echo "    CDK_DOCKER=$$alt make $(MAKECMDGOALS)"; \
			if [ "$$alt" = "podman" ]; then \
				echo "$(YELLOW)   podman also requires DOCKER_HOST (see docs/DEPLOYMENT.md):$(NC)"; \
				echo "    export DOCKER_HOST=\$$(podman machine inspect --format 'unix://{{.ConnectionInfo.PodmanSocket.Path}}')"; \
			fi; \
			exit 1; \
		fi; \
	done; \
	echo "$(RED)❌ No container builder available (docker, finch, or podman).$(NC)"; \
	echo "$(YELLOW)Install one of:$(NC)"; \
	echo "  • Docker Desktop:        open -a Docker  (or 'colima start --vm-type vz' on Apple Silicon)"; \
	echo "  • Finch (rootless, AWS-supported, no Docker Desktop license):"; \
	echo "      brew install --cask finch && finch vm init && finch vm start"; \
	echo "      then: export CDK_DOCKER=finch"; \
	echo "  • Podman (rootless):"; \
	echo "      brew install podman && podman machine init && podman machine start"; \
	echo "      then: export CDK_DOCKER=podman"; \
	echo "      and:  export DOCKER_HOST=\$$(podman machine inspect --format 'unix://{{.ConnectionInfo.PodmanSocket.Path}}')"; \
	exit 1

build-ui: ## Build the React frontend application
	@echo "$(BLUE)🔨 Building React frontend...$(NC)"
	@# Gate substitution is tied to a SINGLE flag — `stagingGateKeyGroupId` — the
	@# same context key that controls the CloudFront TrustedKeyGroups. When the
	@# gate is off, `/error/403.html` is never routed to (CloudFront's 403 error
	@# response maps to /index.html) and the file serves no purpose. Shipping a
	@# substituted copy to the public bucket in that state exposes the real
	@# account ID + Secrets Manager ARN with no consumer — see
	@# issues/2026-09-15-gate-403-template-publishes-account-id-and-secret-arn/.
	@#
	@# One-flag re-imposition: set `stagingGateKeyGroupId` → substitution happens
	@# AND the file ships. Unset → no substitution, file is DELETED from build/
	@# so the CDK BucketDeployment prunes it from S3 on the next deploy.
	@STAGING_GATE_KEY_GROUP_ID=$$(python3 -c "import json,os; d=json.load(open('cdk.context.json')) if os.path.exists('cdk.context.json') else {}; print(d.get('stagingGateKeyGroupId',''))"); \
	if [ -n "$$STAGING_GATE_KEY_GROUP_ID" ]; then \
		CMS_GATE_BINDLE_ID=$$(python3 -c "import json,os; d=json.load(open('cdk.context.json')) if os.path.exists('cdk.context.json') else {}; print(d.get('cmsGateBindleId',''))"); \
		CMS_GATE_SECRET_ARN=$$(python3 -c "import json,os; d=json.load(open('cdk.context.json')) if os.path.exists('cdk.context.json') else {}; print(d.get('cmsGateSecretArn',''))"); \
	else \
		CMS_GATE_BINDLE_ID=""; \
		CMS_GATE_SECRET_ARN=""; \
		echo "$(YELLOW)  ↳ stagingGateKeyGroupId unset — gate substitution SKIPPED and build/error/403.html will be removed post-build.$(NC)"; \
	fi; \
	cd ../modules/cms_ui/source/frontend && yarn install && \
		if [ "$(DEPLOYMENT_STAGE)" = "staging" ] && [ -n "$${CMS_DEMO_DEFAULT_PASSWORD:-}" ]; then \
			echo "  ✱ Demo login buttons enabled (DEPLOYMENT_STAGE=staging, CMS_DEMO_DEFAULT_PASSWORD set)"; \
			CMS_GATE_BINDLE_ID="$$CMS_GATE_BINDLE_ID" \
			CMS_GATE_SECRET_ARN="$$CMS_GATE_SECRET_ARN" \
			VITE_SHOW_DEMO_BUTTONS=true \
			yarn build; \
		else \
			CMS_GATE_BINDLE_ID="$$CMS_GATE_BINDLE_ID" \
			CMS_GATE_SECRET_ARN="$$CMS_GATE_SECRET_ARN" \
			yarn build; \
		fi
	@echo "$(GREEN)✅ Frontend build completed$(NC)"
	@# Post-build gate-template disposition — three cases, all keyed on
	@# stagingGateKeyGroupId (the one-flag gate control):
	@#
	@#   1. gate ON  + bindleId present  → assert substitution worked (existing behavior)
	@#   2. gate ON  + bindleId absent   → operator misconfiguration; fail loud
	@#   3. gate OFF                     → DELETE build/error/403.html so it
	@#                                     cannot reach S3 via any deploy path.
	@#                                     Also mutation-verifies the guard's
	@#                                     complementary invariant: no file, no leak.
	@STAGING_GATE_KEY_GROUP_ID=$$(python3 -c "import json,os; d=json.load(open('cdk.context.json')) if os.path.exists('cdk.context.json') else {}; print(d.get('stagingGateKeyGroupId',''))"); \
	CMS_GATE_BINDLE_ID=$$(python3 -c "import json,os; d=json.load(open('cdk.context.json')) if os.path.exists('cdk.context.json') else {}; print(d.get('cmsGateBindleId',''))"); \
	BUILD_403=../modules/cms_ui/source/frontend/build/error/403.html; \
	if [ -z "$$STAGING_GATE_KEY_GROUP_ID" ]; then \
		if [ -f "$$BUILD_403" ]; then \
			rm -f "$$BUILD_403"; \
			echo "$(GREEN)✅ Removed $$BUILD_403 (stagingGateKeyGroupId unset — file has no consumer and must not ship).$(NC)"; \
		else \
			echo "$(GREEN)✅ $$BUILD_403 absent (stagingGateKeyGroupId unset — nothing to remove).$(NC)"; \
		fi; \
	elif [ -z "$$CMS_GATE_BINDLE_ID" ]; then \
		echo "$(RED)❌ FAIL: stagingGateKeyGroupId is set but cmsGateBindleId is absent from cdk.context.json.$(NC)"; \
		echo "   Gate is enabled at synth time but the 403 template will not carry the correct bindleId."; \
		echo "   Set cmsGateBindleId (and cmsGateSecretArn) in cdk.context.json, or unset stagingGateKeyGroupId to disable the gate."; \
		exit 1; \
	elif [ ! -f "$$BUILD_403" ]; then \
		echo "$(RED)❌ FAIL: build artifact $$BUILD_403 not found — cannot assert gate template.$(NC)"; \
		exit 1; \
	else \
		echo "$(BLUE)🔍 Asserting gate template in build artifact...$(NC)"; \
		if ! grep -qF "$$CMS_GATE_BINDLE_ID" "$$BUILD_403"; then \
			echo "$(RED)❌ FAIL: gate template does not contain the expected bindleId (redacted).$(NC)"; \
			echo "   Substitution may have failed. Check postbuild output above."; \
			exit 1; \
		fi; \
		echo "$(GREEN)✅ Gate template assertion PASSED (local build) — placeholder absent, bindleId present (redacted).$(NC)"; \
	fi

# GATE BINDLE ASSERTION — why there is no placeholder sentinel (2026-08-25)
# The negative check for '4nh4dosbuejyi3ljt6oa' was removed from both gate assertions.
# That string was believed to be a dummy: the 2026-08-04 spec's security-review calls it
# "the deliberately-fake source placeholder". It is not fake. It is the real bindleId of
# Aws-automfg-iss-connected, the SoftwareApp bindle that owns the
# staging CMS CloudFront Domain resource. Asserting it ABSENT
# forced substitution away from the only correct value, so CFS evaluated a bindle carrying
# no CloudFront Domain Permissions and denied every authorized colleague.
# The positive check — built artifact must contain $CMS_GATE_BINDLE_ID — is the real
# invariant and is sufficient, because it validates against configuration rather than
# against a guess about which literals are real.

ui-quick-deploy: ## Build UI + sync to S3 + invalidate CloudFront (~30s loop, no CDK)
	@# Fast path for UI-only changes. Bypasses CDK entirely.
	@# Gate vars: reads cmsGateBindleId + cmsGateSecretArn from cdk.context.json.
	@#   1. yarn build the frontend with gate-var substitution (~20-30s)
	@#   2. aws s3 sync to the existing FrontendBucket
	@#   3. CloudFront invalidation for /*
	@# Use this for label changes, component edits, mock-data tweaks.
	@# DO NOT use when:
	@#   - You changed runtimeConfig inputs (Cognito IDs, API endpoints) —
	@#     run `make regenerate-runtime-config` instead.
	@#   - You changed CDK constructs (auth, infra, S3 bucket policies) —
	@#     run `make staging-deploy` (full CDK).
	@#
	@# Prereqs:
	@#   - cms-$(DEPLOYMENT_STAGE)-ui stack must already be deployed.
	@#   - node_modules installed (`yarn` runs `yarn install` automatically).
	@#   - AWS credentials configured for the staging account.
	@#
	@# Pre-sync secret scan:
	@#   The recipe greps build/ for internal hostnames (awsi.aws.dev,
	@#   <internal-corp-domains>) and aborts
	@#   BEFORE shipping to S3 if any are found. Typical cause is a
	@#   polluted .env.local file getting Vite-baked into the bundle —
	@#   move dev-only vars to .env.development.local (Vite skips it
	@#   for `yarn build`).
	@#
	@# S3 sync notes:
	@#   - Per-developer profile files (runtimeConfig.<anything>.json
	@#     except runtimeConfig.json itself) are find-deleted from build/
	@#     BEFORE sync. Single-token names like runtimeConfig.dev.json
	@#     and double-tagged names like runtimeConfig.<profile>-CMS.json
	@#     are both caught by the wildcard. Only runtimeConfig.json
	@#     (the live runtime config managed by regenerate-runtime-config)
	@#     remains in S3 between syncs.
	@#   - --delete removes files in S3 that aren't in the local build/ tree.
	@#     With the find-delete cleanup above, no profile files exist in
	@#     the local build/ at sync time, so they get deleted from S3 too.
	@#     The --exclude 'runtimeConfig.json' preserves the live config in
	@#     S3 (otherwise sync would also delete it since it's not in build/).
	@echo "$(BLUE)⚡ Quick UI deploy → cms-$(DEPLOYMENT_STAGE)-ui in $(AWS_REGION)$(NC)"
	@# 1. Build
	@# Demo quick-login buttons: only inject VITE_SHOW_DEMO_BUTTONS=true when
	@# DEPLOYMENT_STAGE=staging AND CMS_DEMO_DEFAULT_PASSWORD is set. Vite
	@# bakes these into the static bundle, so the gate must exclude prod.
	@# SimpleAuthProvider.tsx renders the buttons iff DEV is true OR the
	@# VITE_SHOW_DEMO_BUTTONS env value is the literal string 'true'.
	@# Read CFS gate values from gitignored cdk.context.json — but ONLY when
	@# the gate is enabled (stagingGateKeyGroupId set). See build-ui for the
	@# one-flag doctrine and issues/2026-09-15-gate-403-template-publishes-
	@# account-id-and-secret-arn/ for the exposure this closes.
	@STAGING_GATE_KEY_GROUP_ID=$$(python3 -c "import json,os; d=json.load(open('cdk.context.json')) if os.path.exists('cdk.context.json') else {}; print(d.get('stagingGateKeyGroupId',''))"); \
	if [ -n "$$STAGING_GATE_KEY_GROUP_ID" ]; then \
		CMS_GATE_BINDLE_ID=$$(python3 -c "import json,os; d=json.load(open('cdk.context.json')) if os.path.exists('cdk.context.json') else {}; print(d.get('cmsGateBindleId',''))"); \
		CMS_GATE_SECRET_ARN=$$(python3 -c "import json,os; d=json.load(open('cdk.context.json')) if os.path.exists('cdk.context.json') else {}; print(d.get('cmsGateSecretArn',''))"); \
	else \
		CMS_GATE_BINDLE_ID=""; \
		CMS_GATE_SECRET_ARN=""; \
	fi; \
	cd ../modules/cms_ui/source/frontend && yarn install --silent && \
		if [ "$(DEPLOYMENT_STAGE)" = "staging" ] && [ -n "$${CMS_DEMO_DEFAULT_PASSWORD:-}" ]; then \
			echo "  ✱ Demo login buttons enabled (DEPLOYMENT_STAGE=staging, CMS_DEMO_DEFAULT_PASSWORD set)"; \
			CMS_GATE_BINDLE_ID="$$CMS_GATE_BINDLE_ID" \
			CMS_GATE_SECRET_ARN="$$CMS_GATE_SECRET_ARN" \
			VITE_SHOW_DEMO_BUTTONS=true \
			yarn build; \
		else \
			CMS_GATE_BINDLE_ID="$$CMS_GATE_BINDLE_ID" \
			CMS_GATE_SECRET_ARN="$$CMS_GATE_SECRET_ARN" \
			yarn build; \
		fi
	@# Post-build disposition of the gate template — see build-ui for the
	@# doctrine. Gate OFF ⇒ delete build/error/403.html so `aws s3 sync --delete`
	@# removes any existing copy from the public bucket in the sync below.
	@STAGING_GATE_KEY_GROUP_ID=$$(python3 -c "import json,os; d=json.load(open('cdk.context.json')) if os.path.exists('cdk.context.json') else {}; print(d.get('stagingGateKeyGroupId',''))"); \
	BUILD_403=../modules/cms_ui/source/frontend/build/error/403.html; \
	if [ -z "$$STAGING_GATE_KEY_GROUP_ID" ]; then \
		if [ -f "$$BUILD_403" ]; then \
			rm -f "$$BUILD_403"; \
			echo "$(GREEN)✅ Removed $$BUILD_403 (stagingGateKeyGroupId unset — file has no consumer and must not ship).$(NC)"; \
		fi; \
	fi
	@# 2. Find the deployed FrontendBucket + CloudFront distribution from
	@#    the UI stack's resources (NOT outputs — outputs only expose URL,
	@#    not bucket name or distribution ID).
	@FRONTEND_BUCKET=$$(aws cloudformation list-stack-resources \
		--stack-name cms-$(DEPLOYMENT_STAGE)-ui \
		--profile $(AWS_PROFILE) --region $(AWS_REGION) \
		--query "StackResourceSummaries[?ResourceType=='AWS::S3::Bucket' && contains(LogicalResourceId, 'Frontend')].PhysicalResourceId" \
		--output text 2>/dev/null); \
	CF_DIST_ID=$$(aws cloudformation list-stack-resources \
		--stack-name cms-$(DEPLOYMENT_STAGE)-ui \
		--profile $(AWS_PROFILE) --region $(AWS_REGION) \
		--query "StackResourceSummaries[?ResourceType=='AWS::CloudFront::Distribution'].PhysicalResourceId" \
		--output text 2>/dev/null); \
	CF_URL=$$(aws cloudformation describe-stacks \
		--stack-name cms-$(DEPLOYMENT_STAGE)-ui \
		--profile $(AWS_PROFILE) --region $(AWS_REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' \
		--output text 2>/dev/null); \
	if [ -z "$$FRONTEND_BUCKET" ] || [ "$$FRONTEND_BUCKET" = "None" ]; then \
		echo "$(RED)❌ Could not find FrontendBucket in cms-$(DEPLOYMENT_STAGE)-ui stack.$(NC)"; \
		echo "   Deploy the UI stack first: $(BLUE)make staging-deploy$(NC)"; \
		exit 1; \
	fi; \
	if [ -z "$$CF_DIST_ID" ] || [ "$$CF_DIST_ID" = "None" ]; then \
		echo "$(RED)❌ Could not find CloudFront Distribution in cms-$(DEPLOYMENT_STAGE)-ui stack.$(NC)"; \
		exit 1; \
	fi; \
	echo "  Frontend bucket:  $$FRONTEND_BUCKET"; \
	echo "  CloudFront dist:  $$CF_DIST_ID"; \
	echo "  CloudFront URL:   $$CF_URL"; \
	echo ""; \
	if [ ! -d ../modules/cms_ui/source/frontend/build ]; then \
		echo "$(RED)❌ Build output not found at modules/cms_ui/source/frontend/build/$(NC)"; \
		echo "   The yarn build step above may have failed silently."; \
		exit 1; \
	fi; \
	echo "$(BLUE)🔍 Pre-sync secret scan of build/ ...$(NC)"; \
	LEAK=$$(grep -rE 'awsi\.aws\.dev|code\.amazon\.com|gitlab\.aws\.dev|slack\.com/archives' \
		../modules/cms_ui/source/frontend/build/ 2>/dev/null | head -3); \
	if [ -n "$$LEAK" ]; then \
		echo "$(RED)❌ Pre-sync scan found internal references in build/ — refusing to deploy.$(NC)"; \
		echo ""; \
		echo "$$LEAK" | head -3; \
		echo ""; \
		echo "$(YELLOW)Likely cause: a .env.local or .env.production.local file is baking$(NC)"; \
		echo "$(YELLOW)internal hostnames into the bundle. Move dev-only vars to$(NC)"; \
		echo "$(YELLOW).env.development.local (Vite skips it for production builds).$(NC)"; \
		exit 1; \
	fi; \
	echo "  (no internal-reference leaks found)"; \
	echo ""; \
	echo "$(BLUE)🔐 Running built-asset credential guard...$(NC)"; \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	.venv/bin/python scripts/assert_no_credential_in_assets.py \
		--asset-root ../modules/cms_ui/source/frontend/build \
		--stage "$(DEPLOYMENT_STAGE)" \
	|| { echo "$(RED)❌ Built-asset credential guard FAILED — aborting deploy$(NC)"; exit 1; }; \
	echo ""; \
	echo "$(BLUE)🧹 Cleaning per-developer profile files from build/ ...$(NC)"; \
	find ../modules/cms_ui/source/frontend/build/ -maxdepth 1 \
		-type f -name 'runtimeConfig.*.json' \
		! -name 'runtimeConfig.json' \
		-delete 2>/dev/null; \
	echo ""; \
	echo "$(BLUE)📤 Syncing build/ → s3://$$FRONTEND_BUCKET/$(NC)"; \
	aws s3 sync ../modules/cms_ui/source/frontend/build/ \
		s3://$$FRONTEND_BUCKET/ \
		--delete \
		--exclude 'runtimeConfig.json' \
		--profile $(AWS_PROFILE) --region $(AWS_REGION); \
	echo ""; \
	echo "$(BLUE)🔄 Creating CloudFront invalidation (/* )...$(NC)"; \
	INV_ID=$$(aws cloudfront create-invalidation \
		--distribution-id $$CF_DIST_ID \
		--paths '/*' \
		--profile $(AWS_PROFILE) \
		--query 'Invalidation.Id' --output text); \
	echo "  Invalidation ID: $$INV_ID"; \
	echo ""; \
	echo "$(GREEN)✅ Quick UI deploy complete$(NC)"; \
	echo ""; \
	echo "$(YELLOW)Note: CloudFront invalidations propagate in 60-120 seconds.$(NC)"; \
	echo "      Hard-reload (Cmd+Shift+R) once propagated to bypass browser cache."; \
	echo ""; \
	echo "  $(BLUE)$$CF_URL$(NC)"; \
	echo ""; \
	CMS_GATE_BINDLE_ID=$$(python3 -c "import json,os; d=json.load(open('cdk.context.json')) if os.path.exists('cdk.context.json') else {}; print(d.get('cmsGateBindleId',''))"); \
	STAGING_GATE_KEY_GROUP_ID=$$(python3 -c "import json,os; d=json.load(open('cdk.context.json')) if os.path.exists('cdk.context.json') else {}; print(d.get('stagingGateKeyGroupId',''))"); \
	if [ -z "$$STAGING_GATE_KEY_GROUP_ID" ]; then \
		echo "$(BLUE)ℹ️  Gate assertion SKIPPED — stagingGateKeyGroupId unset (gate disabled).$(NC)"; \
		echo "   /error/403.html is not part of the deploy while the gate is off."; \
		echo ""; \
		echo "$(BLUE)🔍 Verifying /error/403.html is absent from the deployed bucket...$(NC)"; \
		GATE_TMP=$$(mktemp -t cms-gate-check) || exit 1; \
		trap 'rm -f "$$GATE_TMP"' EXIT HUP INT TERM; \
		GATE_HOST=$${CF_URL#https://}; GATE_HOST=$${GATE_HOST#http://}; \
		GATE_URL="https://$${GATE_HOST}/error/403.html?ts=$$(date +%s)"; \
		curl -s --max-time 15 "$$GATE_URL" -o "$$GATE_TMP" 2>/dev/null || true; \
		if grep -qE 'arn:aws:secretsmanager|CFSSigningKey' "$$GATE_TMP" 2>/dev/null; then \
			echo "$(RED)❌ FAIL: /error/403.html still returns a substituted body (contains ARN or CFSSigningKey).$(NC)"; \
			echo "   CloudFront may still be serving a cached copy; if the invalidation has not propagated,"; \
			echo "   re-check in 60-120s. If persistent, the S3 object was not deleted — run:"; \
			echo "     aws s3 rm s3://<frontend-bucket>/error/403.html --profile $(AWS_PROFILE) --region $(AWS_REGION)"; \
			rm -f "$$GATE_TMP"; \
			exit 1; \
		fi; \
		rm -f "$$GATE_TMP"; \
		echo "$(GREEN)✅ /error/403.html carries no ARN / CFSSigningKey (gate-off state verified).$(NC)"; \
	elif [ -z "$$CMS_GATE_BINDLE_ID" ]; then \
		echo "$(YELLOW)┌─────────────────────────────────────────────────────────────────┐$(NC)"; \
		echo "$(YELLOW)│  ⚠️  SKIP: gate-template assertion not run                        │$(NC)"; \
		echo "$(YELLOW)│  stagingGateKeyGroupId is set but cmsGateBindleId is absent.     │$(NC)"; \
		echo "$(YELLOW)│  Add cmsGateBindleId to deployment/cdk.context.json.             │$(NC)"; \
		echo "$(YELLOW)│  See the staging auth-gate runbook in docs/.                      │$(NC)"; \
		echo "$(YELLOW)└─────────────────────────────────────────────────────────────────┘$(NC)"; \
	else \
		echo "$(BLUE)⏳ Waiting for CloudFront invalidation $$INV_ID to complete (max 150s)...$(NC)"; \
		CF_WAIT_SECS=0; \
		CF_STATUS="InProgress"; \
		while [ "$$CF_STATUS" != "Completed" ] && [ "$$CF_WAIT_SECS" -lt 150 ]; do \
			sleep 10; \
			CF_WAIT_SECS=$$((CF_WAIT_SECS + 10)); \
			CF_STATUS=$$(aws cloudfront get-invalidation \
				--distribution-id $$CF_DIST_ID \
				--id $$INV_ID \
				--profile $(AWS_PROFILE) \
				--query 'Invalidation.Status' --output text 2>/dev/null); \
			echo "  [$$CF_WAIT_SECS s] Status: $$CF_STATUS"; \
		done; \
		if [ "$$CF_STATUS" != "Completed" ]; then \
			echo "$(YELLOW)⚠️  Invalidation did not complete within 150s — proceeding to curl check with cache-busting query string.$(NC)"; \
		fi; \
		echo "$(BLUE)🔍 Asserting gate template on deployed artifact...$(NC)"; \
		GATE_TMP=$$(mktemp -t cms-gate-check) || exit 1; \
		trap 'rm -f "$$GATE_TMP"' EXIT HUP INT TERM; \
		GATE_HOST=$${CF_URL#https://}; GATE_HOST=$${GATE_HOST#http://}; \
		GATE_URL="https://$${GATE_HOST}/error/403.html?ts=$$(date +%s)"; \
		curl -s --max-time 15 "$$GATE_URL" -o "$$GATE_TMP" 2>/dev/null || true; \
		if [ ! -s "$$GATE_TMP" ]; then \
			echo "$(RED)❌ FAIL: curl could not fetch $$GATE_URL (HTTP error or timeout).$(NC)"; \
			rm -f "$$GATE_TMP"; \
			exit 1; \
		fi; \
		if ! grep -qF "$$CMS_GATE_BINDLE_ID" "$$GATE_TMP"; then \
			rm -f "$$GATE_TMP"; \
			echo "$(RED)❌ FAIL: gate template does not contain the expected bindleId (redacted). Substitution may have failed.$(NC)"; \
			exit 1; \
		fi; \
		rm -f "$$GATE_TMP"; \
		echo "$(GREEN)✅ Gate template assertion PASSED — placeholder absent, bindleId present (redacted).$(NC)"; \
	fi

infrastructure: check-bootstrap ## (Legacy) Redirects to MSK stack which now includes VPC + Redis
	@echo "$(YELLOW)⚠️  Infrastructure is now part of the MSK stack (single VPC architecture)$(NC)"
	@echo "$(BLUE)Run 'make phase3' to deploy VPC + MSK + Redis$(NC)"

data-processing: ## Phase 0.5: Data Processing Foundation (Signal Catalog, Transform Manifests)
	@echo "$(BLUE)📊 Phase 0.5: Data Processing Foundation$(NC)"
	@echo "Deploying: Signal Catalog, Transform Manifests, Data Source Configs"
	@# Auto-detect the UI stack's Cognito user pool so that re-runs after
	@# Phase 1 lock the data-processing API down. First-time deploys
	@# (UI stack doesn't exist yet) fall through to an open API; the
	@# next data-processing run after Phase 1 picks up the pool ID and
	@# the CDK stack flips on the Cognito authorizer.
	@CMS_USER_POOL_ID=$$(aws cloudformation describe-stacks \
		--stack-name cms-$(DEPLOYMENT_STAGE)-ui --region $(AWS_REGION) --profile $(AWS_PROFILE) \
		--query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text 2>/dev/null \
		| tr -d '\n'); \
	if [ -n "$$CMS_USER_POOL_ID" ]; then \
		echo "$(GREEN)🔒 UI Cognito pool found ($$CMS_USER_POOL_ID) — locking down data-processing API$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  UI Cognito pool not found yet — deploying with open API. Re-run this target after Phase 1 to enable auth.$(NC)"; \
	fi; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	CMS_USER_POOL_ID="$$CMS_USER_POOL_ID" \
	CMS_EXTRA_USER_POOL_IDS="$${CMS_EXTRA_USER_POOL_IDS:-}" \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-data-processing $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(BLUE)📤 Seeding Signal Catalog from JSON...$(NC)"
	@cd scripts && AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) python3 seed_signal_catalog.py
	@echo "$(GREEN)✅ Phase 0.5 Complete: Data Processing Foundation deployed$(NC)"
	@echo "$(BLUE)📋 Data Processing Details:$(NC)"
	@CATALOG_TABLE=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-data-processing --query 'Stacks[0].Outputs[?OutputKey==`SignalCatalogTableName`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo "Not available"); \
	MANIFESTS_BUCKET=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-data-processing --query 'Stacks[0].Outputs[?OutputKey==`ManifestsBucketName`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo "Not available"); \
	AUTH_MODE=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-data-processing --query 'Stacks[0].Outputs[?OutputKey==`AuthorizationMode`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo "Unknown"); \
	echo "├── Signal Catalog Table: $$CATALOG_TABLE"; \
	echo "├── Manifests Bucket: $$MANIFESTS_BUCKET"; \
	echo "└── API authorization: $$AUTH_MODE"
	@echo "$(BLUE)🔄 Refreshing runtimeConfig.json with updated dataProcessingApiEndpoint$(NC)"
	@# runtimeConfig endpoint-race hardening (issue
	@# 2026-06-15-runtimeconfig-endpoint-race-hardening): if the UI stack was
	@# previously synthed before the data-processing stack existed, the
	@# baked-in dataProcessingApiEndpoint is "". Now that the data-processing
	@# stack is deployed, regenerate runtimeConfig from the current CFN
	@# outputs. Soft-fail because the data-processing stack itself succeeded;
	@# operators can re-run regenerate-runtime-config manually.
	@$(MAKE) regenerate-runtime-config AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
		|| echo "$(YELLOW)⚠️  regenerate-runtime-config failed; run it manually to refresh dataProcessingApiEndpoint$(NC)"

phase1: build-ui ## Phase 1: Fleet Manager Interface (IoT, Storage, UI, Lambda, Cognito, S3)
	@echo "$(BLUE)🚀 Phase 1: Fleet Manager Interface$(NC)"
	@echo "Deploying: IoT Core, DynamoDB, S3, Lambda, Cognito, CloudFront UI"
	@echo "$(YELLOW)⚠️  Checking for existing resources...$(NC)"
	# Tables are now managed by CloudFormation - skip cleanup
	@echo "$(GREEN)✅ Using CloudFormation-managed tables$(NC)"
	@# Demo-mode CDK context flags for staging only — H1 3775026's Pattern B+C
	@# turned `cms.allow_unauth_map_auth` and `cms.allow_self_signup` to
	@# default-false (correct for production). Staging is the demo
	@# environment, where:
	@#   - HERE Maps need anonymous Cognito Identity Pool creds (geo:*),
	@#     i.e. `cms.allow_unauth_map_auth=true`.
	@# `cms.allow_self_signup` was ALSO passed here until 2026-09-08, on the
	@# stated grounds that "Demo personas come from Cognito self-signup, so
	@# cms.allow_self_signup=true keeps the seed flow working." That was FALSE:
	@# there are zero `.sign_up(` calls in deployment/scripts/ — every
	@# user-creating path uses admin_create_user (seed_driver_users.py:543,673),
	@# which AllowAdminCreateUserOnly does not govern. The prod branch's comment
	@# below had it right all along.
	@# Removed because 2026-09-02-env-collapse made this staging-named deployment
	@# the PUBLIC PRODUCTION surface and dropped the pre-env-collapse SSO gate, so
	@# the flag
	@# opened unauthenticated SignUp on an internet-facing pool with none of the
	@# Phase B controls deployed. See
	@# issues/2026-09-08-public-prod-pool-permits-external-self-signup/.
	@# Do not reintroduce without re-reading that report: the guard that should
	@# have caught this (deployment/aspects/provisioning_guards.py) exempts
	@# `staging` by name and therefore cannot fire on this deployment.
	@# Without these flags, every staging UI redeploy strips the
	@# CognitoUnauthenticatedRole → HERE Maps render-failure → repeated
	@# operator surprise. See issues/2026-06-11-h1-3775026-cms-template-auth-gaps
	@# § "Pattern B/C demo opt-ins" + the recurring symptom on 2026-06-12.
	@# Prod deploys (DEPLOYMENT_STAGE=prod) force allow_unauth_map_auth=true
	@# (PO decision 2026-06-19: HERE Maps must always render in the prod demo;
	@# otherwise the UI redeploy strips CognitoUnauthenticatedRole + geo grants).
	@# allow_self_signup stays default-false for prod — demo users are
	@# admin-created via the seed script, so self-signup is unneeded + less secure.
	@# ── Phase A account-provisioning gate (spec 2026-08-07-cms-account-provisioning-model) ──
	@# CMS_ENABLE_INTERNAL_AUTO_PROVISIONING is translated below into the dotted CDK context
	@# key `cms.enable_internal_auto_provisioning`. Both halves of that indirection are
	@# forced: a CDK context key cannot be read from the shell environment, and the dotted
	@# key cannot live in config/<stage>.env because that file is SOURCED as shell (a dotted
	@# assignment is parsed as a command and fails the target). Same shape as
	@# STAGING_GATE_KEY_GROUP_ID -> -c stagingGateKeyGroupId.
	@# Without this translation the provisioning Lambda is never created and Phase A is
	@# inert regardless of the env file — found by security review 2026-08-10 (Suggestion 3).
	@# ── Built-asset credential guard (spec 2026-08-05-cms-demo-identity-model) ──
	@# Runs BEFORE cdk deploy ships the build/ tree to S3/CloudFront.
	@# Reads the stage's demo persona secret(s) from Secrets Manager and
	@# rejects any build that contains those values verbatim, or that has a
	@# demo-field value from runtimeConfig.json leaking into a JS asset.
	@# Fails closed on an unrecognised DEPLOYMENT_STAGE.
	@# This is the control that would have stopped the 2026-08-05
	@# demo-password bundle reaching the public prod distribution.
	@# ── NOTE ON COMMENT PLACEMENT (do not undo) ─────────────────────────────
	@# Every comment for the shell block below lives HERE, above it, because the
	@# block is ONE backslash-continued shell command from `DEMO_CTX_FLAGS=""`
	@# through `cdk deploy`. A `@#` line placed inside that continuation is passed
	@# to sh verbatim and breaks the target — sh chokes on the first `(` in the
	@# comment text. It ALSO silently splits the command in two, which strands
	@# DEMO_CTX_FLAGS / UI_DOMAIN_CTX_FLAGS / _dsg in a shell that has already
	@# exited by the time cdk deploy runs, so the deploy gets EMPTY context flags
	@# and the domain-alias guard aborts synth.
	@# Both failures were live at HEAD on 2026-08-11 (introduced 2026-08-10 by the
	@# very fix whose own notes recorded this gotcha). `make -n phase1 | sh -n` is
	@# the check that catches it; scripts/test_makefile_recipe_syntax.py runs it.
	@#
	@# The block also EXPORTS the provisioning env vars that ui_stack.py reads via
	@# os.environ (INTERNAL_IDP_PROVIDER_NAME, INTERNAL_AUTO_ASSIGN_GROUP,
	@# INTERNAL_ALLOWED_EMAIL_DOMAINS, EXTERNAL_SELF_SIGNUP_*). Until 2026-08-11 it
	@# did not, so `make phase1` aborted at synth on any stage with the Phase A gate
	@# on — while _require_internal_provisioning_config's error message told the
	@# operator to "deploy via the canonical Make target, which exports these". The
	@# only path that worked was a bare `cdk deploy` with the env file sourced, i.e.
	@# exactly the path that drops regenerate-runtime-config's demoPasswords pass.
	@# Adding a new os.environ read to ui_stack.py means adding it here too;
	@# test_makefile_recipe_syntax.py fails if the two drift.
	@DEMO_CTX_FLAGS=""; \
	if [ "$(DEPLOYMENT_STAGE)" = "staging" ]; then \
	    DEMO_CTX_FLAGS="-c cms.allow_unauth_map_auth=true"; \
	    echo "$(YELLOW)📺 Staging: passing demo CDK context flags ($$DEMO_CTX_FLAGS)$(NC)"; \
	elif [ "$(DEPLOYMENT_STAGE)" = "prod" ]; then \
	    DEMO_CTX_FLAGS="-c cms.allow_unauth_map_auth=true"; \
	    echo "$(YELLOW)📺 Prod: always-on HERE-Maps anon flag ($$DEMO_CTX_FLAGS); self-signup stays default-off (admin-create seeding)$(NC)"; \
	fi; \
	UI_DOMAIN_CTX_FLAGS=""; \
	_cfg=config/$(DEPLOYMENT_STAGE).env; \
	if [ -f "$$_cfg" ]; then \
	  _dom=$$(sed -n 's/^UI_CUSTOM_DOMAIN=//p' "$$_cfg"); \
	  _cert=$$(sed -n 's/^UI_CUSTOM_DOMAIN_CERT_ARN=//p' "$$_cfg"); \
	  _creg=$$(sed -n 's/^UI_CUSTOM_DOMAIN_REGION=//p' "$$_cfg"); \
	  _dns=$$(sed -n 's/^UI_CUSTOM_DOMAIN_MANAGE_DNS=//p' "$$_cfg"); \
	  _kg=$$(sed -n 's/^STAGING_GATE_KEY_GROUP_ID=//p' "$$_cfg"); \
	  _dsg=$$(sed -n 's/^DRIVER_SELF_GUARD_ENABLED=//p' "$$_cfg"); \
	  _eiap=$$(sed -n 's/^CMS_ENABLE_INTERNAL_AUTO_PROVISIONING=//p' "$$_cfg"); \
	  _eess=$$(sed -n 's/^CMS_ENABLE_EXTERNAL_SELF_SIGNUP=//p' "$$_cfg"); \
	  _waf=$$(sed -n 's/^WAF_WEB_ACL_ARN=//p' "$$_cfg"); \
	  _iidp=$$(sed -n 's/^INTERNAL_IDP_PROVIDER_NAME=//p' "$$_cfg"); \
	  _iaag=$$(sed -n 's/^INTERNAL_AUTO_ASSIGN_GROUP=//p' "$$_cfg"); \
	  _iaed=$$(sed -n 's/^INTERNAL_ALLOWED_EMAIL_DOMAINS=//p' "$$_cfg"); \
	  _essg=$$(sed -n 's/^EXTERNAL_SELF_SIGNUP_GROUP=//p' "$$_cfg"); \
	  _essf=$$(sed -n 's/^EXTERNAL_SELF_SIGNUP_FLEET_IDS=//p' "$$_cfg"); \
	  _cxi=$$(sed -n 's/^CLIENT_EXTRA_IDPS=//p' "$$_cfg"); \
	  _cdp=$$(sed -n 's/^COGNITO_DOMAIN_PREFIX=//p' "$$_cfg"); \
	  _fed_secret=$$(sed -n 's/^FEDERATE_OIDC_SECRET_ID=//p' "$$_cfg"); \
	  _csep=$$(sed -n 's/^CS_PRODUCER_API_ENDPOINT=//p' "$$_cfg"); \
	  _cssid=$$(sed -n 's/^CS_SUBSCRIPTION_ID=//p' "$$_cfg"); \
	  if [ -n "$$_fed_secret" ]; then \
	    _fed_json=$$(aws secretsmanager get-secret-value --secret-id "$$_fed_secret" --region $(AWS_REGION) --profile $(AWS_PROFILE) --query SecretString --output text 2>/dev/null); \
	    if [ -n "$$_fed_json" ]; then \
	      _fcid=$$(printf '%s' "$$_fed_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('client_id',''))"); \
	      _fcs=$$(printf '%s' "$$_fed_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('client_secret',''))"); \
	      _fiss=$$(printf '%s' "$$_fed_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('oidc_issuer',''))"); \
	      echo "$(YELLOW)🔐 cms-$(DEPLOYMENT_STAGE)-ui: Federate OIDC client loaded from $$_fed_secret$(NC)"; \
	    else \
	      echo "$(RED)✗ cms-$(DEPLOYMENT_STAGE)-ui: FEDERATE_OIDC_SECRET_ID=$$_fed_secret set but unreadable.$(NC)"; \
	      echo "$(RED)  Refusing to deploy: ui_stack.py deletes the AmazonFederate IdP when the$(NC)"; \
	      echo "$(RED)  credentials are absent, which is issue 2026-08-11-prod-federate-client-config-reset.$(NC)"; \
	      exit 1; \
	    fi; \
	  fi; \
	  if [ -n "$$_dom" ] && [ -n "$$_cert" ]; then UI_DOMAIN_CTX_FLAGS="-c uiCustomDomain=$$_dom -c uiCustomDomainCertArn=$$_cert -c uiCustomDomainRegion=$${_creg:-$(AWS_REGION)} -c uiCustomDomainManageDns=$${_dns:-false}"; fi; \
	  if [ -n "$$_kg" ]; then UI_DOMAIN_CTX_FLAGS="$$UI_DOMAIN_CTX_FLAGS -c stagingGateKeyGroupId=$$_kg"; fi; \
	  if [ -n "$$_eiap" ]; then UI_DOMAIN_CTX_FLAGS="$$UI_DOMAIN_CTX_FLAGS -c cms.enable_internal_auto_provisioning=$$_eiap"; echo "$(YELLOW)🔑 cms-$(DEPLOYMENT_STAGE)-ui: internal auto-provisioning gate = $$_eiap$(NC)"; fi; \
	  if [ -n "$$_eess" ]; then UI_DOMAIN_CTX_FLAGS="$$UI_DOMAIN_CTX_FLAGS -c cms.enable_external_self_signup=$$_eess"; echo "$(YELLOW)🔑 cms-$(DEPLOYMENT_STAGE)-ui: EXTERNAL self-signup gate = $$_eess$(NC)"; fi; \
	  if [ -n "$$_waf" ]; then UI_DOMAIN_CTX_FLAGS="$$UI_DOMAIN_CTX_FLAGS -c wafWebAclArn=$$_waf"; echo "$(YELLOW)🛡  cms-$(DEPLOYMENT_STAGE)-ui: WAF WebACL will be attached$(NC)"; else echo "$(YELLOW)⚠  cms-$(DEPLOYMENT_STAGE)-ui: WAF_WEB_ACL_ARN unset — NO WAF will be attached$(NC)"; fi; \
	  if [ -n "$$UI_DOMAIN_CTX_FLAGS" ]; then echo "$(YELLOW)🔗 cms-$(DEPLOYMENT_STAGE)-ui: re-applying custom-domain/gate context from $$_cfg$(NC)"; fi; \
	  if [ -n "$$_csep" ]; then CS_CTX_FLAGS="$$CS_CTX_FLAGS -c csProducerApiEndpoint=$$_csep"; echo "$(YELLOW)ð¡ cms-$(DEPLOYMENT_STAGE)-ui: Connected Services producer endpoint threaded$(NC)"; else echo "$(YELLOW)â   cms-$(DEPLOYMENT_STAGE)-ui: CS_PRODUCER_API_ENDPOINT unset â the Connected Services routes will 502 config_missing$(NC)"; fi; \
	  if [ -n "$$_cssid" ]; then CS_CTX_FLAGS="$$CS_CTX_FLAGS -c csSubscriptionId=$$_cssid"; else echo "$(YELLOW)â   cms-$(DEPLOYMENT_STAGE)-ui: CS_SUBSCRIPTION_ID unset â the Connected Services routes will 502 config_missing$(NC)"; fi; \
	fi; \
	echo "$(BLUE)🔐 Running built-asset credential guard...$(NC)"; \
	BUILD_ROOT=../modules/cms_ui/source/frontend/build; \
	if [ -d "$$BUILD_ROOT" ]; then \
		DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
		.venv/bin/python scripts/assert_no_credential_in_assets.py \
			--asset-root "$$BUILD_ROOT" \
			--stage "$(DEPLOYMENT_STAGE)" \
		|| { echo "$(RED)❌ Built-asset credential guard FAILED — aborting deploy$(NC)"; exit 1; }; \
	else \
		echo "$(YELLOW)⚠️  build/ not found — skipping credential guard (build-ui may not have run)$(NC)"; \
	fi; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	DRIVER_SELF_GUARD_ENABLED=$${_dsg:-false} \
	INTERNAL_IDP_PROVIDER_NAME="$$_iidp" \
	INTERNAL_AUTO_ASSIGN_GROUP="$$_iaag" \
	INTERNAL_ALLOWED_EMAIL_DOMAINS="$$_iaed" \
	EXTERNAL_SELF_SIGNUP_GROUP="$$_essg" \
	EXTERNAL_SELF_SIGNUP_FLEET_IDS="$$_essf" \
	CLIENT_EXTRA_IDPS="$$_cxi" \
	COGNITO_DOMAIN_PREFIX="$$_cdp" \
	FEDERATE_CLIENT_ID="$$_fcid" \
	FEDERATE_CLIENT_SECRET="$$_fcs" \
	FEDERATE_ISSUER="$$_fiss" \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-storage cms-$(DEPLOYMENT_STAGE)-iot cms-$(DEPLOYMENT_STAGE)-ui $$DEMO_CTX_FLAGS $$UI_DOMAIN_CTX_FLAGS $$CS_CTX_FLAGS $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(BLUE)🔄 Refreshing runtimeConfig.json with latest sibling-stack endpoints$(NC)"
	@# runtimeConfig endpoint-race hardening (issue
	@# 2026-06-15-runtimeconfig-endpoint-race-hardening): the UI stack bakes
	@# runtimeConfig.json at synth time via _lookup_stack_output (synchronous
	@# describe-stacks). On the first deploy of a region, sibling stacks
	@# (data-processing, simulation, commands, bedrock-agents) usually do not
	@# exist yet, so each lookup returns "" and the empty value ships to S3.
	@# Re-running regenerate-runtime-config here closes the gap on subsequent
	@# phase1 invocations (when siblings exist) and is a no-op refresh on the
	@# very first deploy. Soft-fail because phase1 itself already succeeded.
	@$(MAKE) regenerate-runtime-config AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
		|| echo "$(YELLOW)⚠️  regenerate-runtime-config failed; run it manually after siblings deploy$(NC)"
	@echo "$(GREEN)✅ Phase 1 Complete: Fleet Manager Interface deployed$(NC)"

phase2: ## Phase 2: Fleet Management Interface (historical data injection)
	@echo "$(BLUE)🚀 Phase 2: Fleet Management Interface$(NC)"
	@echo "Setting up historical data injection with interactive configuration"
	@cd scripts && python3 setup_historical_data.py
	@echo "$(GREEN)✅ Phase 2 Complete: Historical data injection ready$(NC)"

phase3: ## Phase 3: Telemetry Pipeline 1 (MSK)
	@echo "$(BLUE)🚀 Phase 3: Telemetry Pipeline 1 (MSK)$(NC)"
	@echo "Deploying MSK cluster with VPC, security groups, and customer-managed KMS key for SCRAM secrets"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-msk $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Phase 3 Complete: MSK cluster deployed$(NC)"

phase3b: ## Phase 3b: Configure MSK for IoT Integration
	@echo "$(BLUE)🚀 Phase 3b: Configure MSK for IoT Integration$(NC)"
	@echo "Deploying telemetry integration stack (VPC destination + IoT rule)"
	@$(MAKE) configure-msk AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	DEPLOY_TELEMETRY_INTEGRATION=true \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-telemetry-integration $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Phase 3b Complete: MSK configured with telemetry integration stack$(NC)"

phase4: ## Phase 4: Telemetry Pipeline 3 (Flink Deployment)
	@echo "$(BLUE)🚀 Phase 4: Telemetry Pipeline 3 (Flink Deployment)$(NC)"
	@echo "Deploying Flink applications for real-time telemetry processing"
	@echo "$(BLUE)Building Flink JAR...$(NC)"
	@cd ../modules/flink && ./build.sh
	@echo "$(BLUE)Resolving MSK bootstrap servers (IAM auth, port 9098) + Redis endpoint...$(NC)"
	@set -a; [ -f config/$(DEPLOYMENT_STAGE).env ] && . config/$(DEPLOYMENT_STAGE).env; set +a; \
	MSK_CLUSTER_ARN=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKClusterArn`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null); \
	BOOTSTRAP_SERVERS=$$(aws kafka get-bootstrap-brokers --cluster-arn $$MSK_CLUSTER_ARN --query 'BootstrapBrokerStringSaslIam' --output text --profile $(AWS_PROFILE) 2>/dev/null); \
	REDIS_ENDPOINT=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`RedisEndpoint`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null); \
	export BOOTSTRAP_SERVERS=$$BOOTSTRAP_SERVERS; \
	export REDIS_ENDPOINT=$$REDIS_ENDPOINT; \
	echo "$(BLUE)Deploying Flink stack with BOOTSTRAP_SERVERS=$$BOOTSTRAP_SERVERS$(NC)"; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-flink $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(BLUE)Validating Flink deployment...$(NC)"
	@$(MAKE) validate-flink-deployment AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(YELLOW)⚠️  Flink applications deployed but need JAR files to be functional$(NC)"
	@echo "$(GREEN)✅ Phase 4 Complete: Flink infrastructure deployed$(NC)"

deploy-fleetwise: ## Deploy FleetWise integration (IoT rules, VPC endpoints, CampaignSyncProcessor)
	@echo "$(BLUE)🚗 Deploying FleetWise Integration Stack$(NC)"
	@echo "This creates IoT rules for FWE telemetry/checkin, VPC endpoints, and CampaignSyncProcessor"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-fleetwise $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ FleetWise integration deployed$(NC)"

deploy-connect: ## Deploy Amazon Connect contact center (queues, flows, Customer Profiles)
	@echo "$(BLUE)📞 Deploying Amazon Connect Stack$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) DEPLOY_CONNECT=true AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-connect $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Amazon Connect deployed$(NC)"
	@echo "$(YELLOW)Next: run 'python3 scripts/wire-connect-to-tenant.py' in the CVX repo to wire outputs into tenant config$(NC)"

deploy-simulation: ## Deploy Simulation Service (Lambda + API Gateway + ECS Fargate)
	@# Container images default to the published AWS Solutions public-ECR refs
	@# (SIM_IMAGE_MODE=published) — NO local container builder required. Only the
	@# asset-mode dev escape (SIM_IMAGE_MODE=asset) builds locally from
	@# services/simulation, so check-docker is gated on that mode alone.
	@if [ "$${SIM_IMAGE_MODE:-published}" = "asset" ]; then $(MAKE) check-docker; fi
	@echo "$(BLUE)🎮 Deploying Simulation Service$(NC)"
	@# Deploy is --exclusively (see cdk line below): ONLY the simulation stack,
	@# never its dependency stacks (ui/msk). Without it `cdk deploy` redeploys
	@# changed dependencies — which once began deploying an unrelated in-flight
	@# ui_stack change (issue 2026-06-16-cms-sim-deploy-pulled-dependency-stacks).
	@#
	@# `set -a; . config/<stage>.env` + load-federate-creds.sh are REQUIRED even
	@# though --exclusively means the ui stack never deploys: `cdk deploy X` still
	@# SYNTHS the whole app, and the ui-stack provisioning guards
	@# (aspects/provisioning_guards.py::_require_client_idp_config) run at synth.
	@# Without CLIENT_EXTRA_IDPS + FEDERATE_CLIENT_ID/SECRET in the environment
	@# they fail closed and this target cannot run at all — which is exactly what
	@# happened on 2026-09-01 (issue
	@# 2026-09-01-deploy-simulation-missing-stage-config-preamble). The values are
	@# in config/<stage>.env and Secrets Manager; they just never reached synth.
	@# Same pattern as phase-staging (see ~line 1937). load-federate-creds.sh is a
	@# no-op when FEDERATE_OIDC_SECRET_ID is unset, so cognito-only stages are
	@# unaffected. Each recipe line runs in its own shell, so the load must be on
	@# THIS line, joined to the cdk invocation.
	@set -a; [ -f config/$(DEPLOYMENT_STAGE).env ] && . config/$(DEPLOYMENT_STAGE).env; set +a; \
	. scripts/load-federate-creds.sh; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) DEPLOY_SIMULATION=true AWS_PROFILE=$(AWS_PROFILE) \
	SIM_IMAGE_MODE=$${SIM_IMAGE_MODE:-published} \
	AWS_DEFAULT_REGION=$(AWS_REGION) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-simulation --exclusively $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(BLUE)🧹 Post-deploy: draining stale fwe-agent task instances$(NC)"
	@# Lifecycle hardening Phase 2 (spec 2026-05-30-cms-sim-lifecycle-hardening):
	@# fwe-agent tasks are launched via one-shot run_task and ECS does NOT
	@# auto-replace them on a task-definition revision bump. The drain script
	@# stops any task whose revision is below the latest active revision so
	@# the new revision can claim the kernel ISO-TP socket pool.
	@# Non-zero drain rc fails the deploy.
	@STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) AWS_PROFILE=$(AWS_PROFILE) \
		bash scripts/drain_stale_fwe_agents.sh
	@echo "$(BLUE)🔄 Refreshing runtimeConfig.json with updated simulationApiEndpoint$(NC)"
	@# Without this step, partial / standalone re-deploys of the simulation stack
	@# leave runtimeConfig.json stale (Settings page shows "Cloud endpoint: Not
	@# configured" and the UI falls back to localhost:5001). The runtimeConfig
	@# was originally written by phase1's UI-stack BucketDeployment with an empty
	@# simulationApiEndpoint (sim stack didn't exist yet at synth time). Now that
	@# the sim stack is deployed, regenerate runtimeConfig from current CFN
	@# outputs (issue 2026-06-15-fresh-deploy-simulation-endpoint-stale).
	@# Soft-fail if regenerate hits a transient error — the sim stack is
	@# already deployed; operator can re-run `make regenerate-runtime-config`
	@# manually.
	@$(MAKE) regenerate-runtime-config AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
		|| echo "$(YELLOW)⚠️  regenerate-runtime-config failed; run it manually to refresh simulationApiEndpoint$(NC)"
	@echo "$(GREEN)✅ Simulation service deployed$(NC)"

publish-public-ecr: ## [release-only] Build+push sim images to public ECR (requires VERSION=vX.Y.Z)
	@# PUBLISH_IMAGES defaults to BOTH images — a release publishes both. Narrow it
	@# only when an image's build inputs are provably unchanged, e.g.
	@#   make publish-public-ecr VERSION=vX.Y.Z PUBLISH_IMAGES=cms-sim-service
	@# cms-fwe-agent's Dockerfile.fwe COPYs nothing from the build context (it clones
	@# aws-iot-fleetwise-edge at a pinned FWE_VERSION and copies only from its builder
	@# stage), so its content is independent of services/simulation. Rebuilding it
	@# needlessly also bumps its floating ubuntu:22.04 base inside an otherwise
	@# targeted release. When narrowing, retag the omitted image from the previous
	@# tag so every image exists at the new tag — resolve_sim_image_ref() resolves
	@# ALL images at SIM_IMAGE_VERSION, so a missing one breaks published mode.
	@# Maintainer/release target — NOT part of deploy-all / phase-*. Builds both
	@# simulation container images for linux/arm64 and pushes them to the AWS
	@# Solutions public ECR registry per the public-ECR convention. Gated on the
	@# publisher having created the repos (spec 2026-06-16-cms-sim-images-codebuild-ecr
	@# Open Q1). Non-interactive: docker login via --password-stdin, no prompts.
	@# Authoritative forbidden-pattern source: repo-root .publish-secrets-scan.yml.
	@if [ -z "$(VERSION)" ]; then \
		echo "$(RED)❌ VERSION is required, e.g. make publish-public-ecr VERSION=v0.2.6$(NC)"; exit 1; \
	fi
	@# Guard 1 (issues/2026-08-19-cms-vehicle-ecu-presence-not-resident): refuse to push a
	@# tag the stack will never pull. resolve_sim_image_ref() defaults to the
	@# SIM_IMAGE_VERSION constant, so publishing vX while the constant pins vY produces
	@# images nothing references — and leaves every published-mode deploy on the old tag.
	@# Runs before docker/scan/login so the failure costs nothing. The GitHub-mirror
	@# publish_preflight in scripts/lib/publish-config.sh gates a DIFFERENT flow (git
	@# release tag), which is why this check is needed here too.
	@pinned="$$(grep -E '^SIM_IMAGE_VERSION\s*[=:]' stacks/_sim_image_config.py \
		| grep -oE '"[^"]+"' | head -1 | tr -d '"')"; \
	if [ -z "$$pinned" ]; then \
		echo "$(RED)❌ could not read SIM_IMAGE_VERSION from stacks/_sim_image_config.py$(NC)"; exit 1; \
	fi; \
	if [ "$$pinned" != "$(VERSION)" ]; then \
		echo "$(RED)❌ VERSION=$(VERSION) does not match pinned SIM_IMAGE_VERSION=$$pinned$(NC)"; \
		echo "$(YELLOW)   Bump SIM_IMAGE_VERSION (and SIM_IMAGE_SOURCE_COMMIT) in$(NC)"; \
		echo "$(YELLOW)   deployment/stacks/_sim_image_config.py in the SAME commit as this publish,$(NC)"; \
		echo "$(YELLOW)   then re-run: make publish-public-ecr VERSION=$(VERSION)$(NC)"; \
		exit 1; \
	fi; \
	echo "$(GREEN)✅ VERSION matches pinned SIM_IMAGE_VERSION=$$pinned$(NC)"
	@if [ -z "$(strip $(PUBLISH_IMAGES))" ]; then \
		echo "$(RED)❌ PUBLISH_IMAGES is empty — nothing would be built or pushed.$(NC)"; \
		echo "$(YELLOW)   Unset it for both images, or name them explicitly, e.g.$(NC)"; \
		echo "$(YELLOW)   make publish-public-ecr VERSION=$(VERSION) PUBLISH_IMAGES=cms-sim-service$(NC)"; \
		exit 1; \
	fi
	@command -v docker >/dev/null 2>&1 || { echo "$(RED)❌ docker is required for publish-public-ecr$(NC)"; exit 1; }
	@echo "$(BLUE)📦 Staging container build contexts under deployment/ecr/$(NC)"
	@bash scripts/stage_ecr_resources.sh
	@echo "$(BLUE)🔎 Scanning staged contexts for secrets / internal canaries$(NC)"
	@# Use the canonical scanner with the single-source-of-truth pattern set
	@# (../.publish-secrets-scan.yml). It exits non-zero on any critical finding,
	@# so this fails closed before any build/push. We intentionally do NOT inline
	@# the forbidden literals here — this Makefile is itself public-mirror-bound
	@# (not in .publish-exclude), so embedding canary strings would leak them.
	@for name in $(PUBLISH_IMAGES); do \
		echo "  scanning ecr/$$name ..."; \
		.venv/bin/python ../scripts/lib/secret-scan.py \
			--config ../.publish-secrets-scan.yml --root "ecr/$$name" >/dev/null \
			|| { echo "$(RED)❌ secret/canary scan failed for ecr/$$name — aborting publish (no push)$(NC)"; exit 1; }; \
	done
	@echo "$(GREEN)✅ scan clean$(NC)"
	@echo "$(BLUE)🔐 Logging in to ECR Public (us-east-1)$(NC)"
	@aws ecr-public get-login-password --region us-east-1 --profile $(AWS_PROFILE) \
		| docker login --username AWS --password-stdin public.ecr.aws
	@for name in $(PUBLISH_IMAGES); do \
		ref="$(PUBLIC_ECR_REGISTRY)/$$name:$(VERSION)"; \
		echo "$(BLUE)🏗  Building $$ref (linux/arm64)$(NC)"; \
		docker build --platform linux/arm64 -t "$$ref" "ecr/$$name" || exit 1; \
		echo "$(BLUE)⬆  Pushing $$ref$(NC)"; \
		docker push "$$ref" || exit 1; \
	done
	@echo "$(BLUE)🧹 Pruning untagged (dangling) images on this builder$(NC)"
	@docker image prune -f >/dev/null 2>&1 || true
	@echo "$(GREEN)✅ Published sim images @ $(VERSION) to $(PUBLIC_ECR_REGISTRY)$(NC)"

drain-stale-fwe-agents: ## Stop fwe-agent tasks running on stale revisions (post-deploy hook + ad-hoc cleanup)
	@# Standalone wrapper for `deployment/scripts/drain_stale_fwe_agents.sh`.
	@# Operators can invoke this manually outside a deploy to clean up
	@# orphan / stale fwe-agent tasks. Pass `DRAIN_FLAGS=--dry-run` to
	@# preview without stopping anything.
	@echo "$(BLUE)🧹 Draining stale fwe-agent tasks (stage=$(DEPLOYMENT_STAGE) region=$(AWS_REGION))$(NC)"
	@STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) AWS_PROFILE=$(AWS_PROFILE) \
		bash scripts/drain_stale_fwe_agents.sh $(DRAIN_FLAGS)

build-commands: ## Build commands Lambda package (.build/)
	@echo "$(BLUE)🔨 Building commands Lambda package$(NC)"
	@bash ../services/commands/build.sh
	@echo "$(GREEN)✅ Commands Lambda built$(NC)"

deploy-commands: ## Deploy Remote Commands (Lambda + API Gateway + IoT Rule)
	@echo "$(BLUE)🎮 Deploying Remote Commands$(NC)"
	@# `set -a; . config/<stage>.env` + load-federate-creds.sh are REQUIRED even
	@# though this target deploys only the commands stack: `cdk deploy X` still
	@# SYNTHS the whole app, and the ui-stack provisioning guards
	@# (aspects/provisioning_guards.py::_require_client_idp_config) run at synth.
	@# Without CLIENT_EXTRA_IDPS + FEDERATE_CLIENT_ID/SECRET in the environment
	@# they fail closed and this target cannot run at all — same class as
	@# deploy-simulation (see ~line 875), fixed there in commit 69802d94 but never
	@# mirrored here (issue 2026-09-02-deploy-commands-missing-stage-config-preamble).
	@# load-federate-creds.sh is a no-op when FEDERATE_OIDC_SECRET_ID is unset, so
	@# cognito-only stages are unaffected.
	@#
	@# `--exclusively`: commands_stack.py imports the CMS user-pool ARN from
	@# ui_stack via a CloudFormation export (Fn.import_value), and app.py
	@# declares `commands_stack.add_dependency(ui_stack)` — so without this
	@# flag, `cdk deploy cms-{stage}-commands` also redeploys `cms-{stage}-ui`
	@# using ONLY this target's context (no CS_CTX_FLAGS, no UI_DOMAIN_CTX_FLAGS).
	@# `--exclusively` does not break the cross-stack reference: CFN exports
	@# are resolved from the ALREADY-DEPLOYED ui stack's live template output,
	@# independent of whether ui is redeployed in this same invocation. Fixed
	@# 2026-09-18 after this exact gap silently zeroed cms-staging-ui's
	@# CS_PRODUCER_API_ENDPOINT env var mid-session — see
	@# issues/2026-09-18-deploy-commands-drops-cs-producer-context/. Same
	@# pattern already applied to deploy-simulation (~line 946); the sibling
	@# gap across the other 11 single-stack targets lacking this flag is
	@# tracked as a follow-on in issues/2026-09-16-context-key-lint-covers-3-of-19-deploy-targets/.
	@set -a; [ -f config/$(DEPLOYMENT_STAGE).env ] && . config/$(DEPLOYMENT_STAGE).env; set +a; \
	. scripts/load-federate-creds.sh; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	AWS_DEFAULT_REGION=$(AWS_REGION) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-commands --exclusively $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Remote commands deployed$(NC)"
	@echo "$(BLUE)📋 Commands API:$(NC)"
	@CMD_URL=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-commands --query 'Stacks[0].Outputs[?OutputKey==`CommandsApiUrl`].OutputValue' --output text --profile $(AWS_PROFILE) --region $(AWS_REGION) 2>/dev/null || echo "Not available"); \
	echo "└── API URL: $$CMD_URL"
	@echo "$(BLUE)🔄 Refreshing runtimeConfig.json with updated commandsApiEndpoint$(NC)"
	@# runtimeConfig endpoint-race hardening (issue
	@# 2026-06-15-runtimeconfig-endpoint-race-hardening): the commands stack
	@# emits the CommandsApiUrl output that feeds runtimeConfig.commandsApiEndpoint.
	@# A standalone deploy-commands or a partial deploy-all that aborts before
	@# the trailing regenerate would leave commandsApiEndpoint stale ("").
	@# Soft-fail; the commands stack itself already succeeded.
	@$(MAKE) regenerate-runtime-config AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
		|| echo "$(YELLOW)⚠️  regenerate-runtime-config failed; run it manually to refresh commandsApiEndpoint$(NC)"

deploy-connected-services-consumer: ## Deploy the CMS-side Connected Services feed-cache table (opt-in)
	@echo "$(BLUE)📥 Deploying Connected Services consumer cache table$(NC)"
	@# Spec 2026-09-10-cms-connected-services-consumer T1.5/T2.0b.
	@#
	@# This stack is opt-in on DEPLOY_CONNECTED_SERVICES_CONSUMER=true (see
	@# app.py). The flag existed with NO make target and NO runbook entry from
	@# T1.5 onward, so the table T2.3's proxy routes read and write had never
	@# been created — a prerequisite that appeared in no task. This target is
	@# that missing step.
	@#
	@# Contents are ONE DynamoDB table (RETAIN + PITR + TTL on `ttl`). No
	@# Lambda, no container, no asset — so this target cannot bundle a
	@# concurrent session's uncommitted source, which matters in this repo.
	@#
	@# The stage-config + federate preamble is required for the same reason
	@# deploy-commands documents above: `cdk deploy X` synths the WHOLE app,
	@# and ui_stack's provisioning guards fail closed at synth without
	@# CLIENT_EXTRA_IDPS + FEDERATE_CLIENT_ID/SECRET in the environment.
	@set -a; [ -f config/$(DEPLOYMENT_STAGE).env ] && . config/$(DEPLOYMENT_STAGE).env; set +a; \
	. scripts/load-federate-creds.sh; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	AWS_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOY_CONNECTED_SERVICES_CONSUMER=true \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-connected-services-consumer $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(BLUE)🔎 Post-deploy check$(NC)"
	@# Per deploy-validation.md: a "successful deploy" that left the table
	@# unusable is not successful. Assert the three properties the spec's D3
	@# depends on, and exit non-zero if any is missing.
	@TBL=cms-$(DEPLOYMENT_STAGE)-storage-cs-feed-cache-$(AWS_REGION)-$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text); \
	DESC=$$(aws dynamodb describe-table --table-name $$TBL --profile $(AWS_PROFILE) --region $(AWS_REGION) --output json 2>/dev/null); \
	if [ -z "$$DESC" ]; then echo "$(RED)❌ table $$TBL not found after deploy$(NC)"; exit 1; fi; \
	STATUS=$$(echo "$$DESC" | python3 -c "import json,sys; print(json.load(sys.stdin)['Table']['TableStatus'])"); \
	TTL=$$(aws dynamodb describe-time-to-live --table-name $$TBL --profile $(AWS_PROFILE) --region $(AWS_REGION) --query 'TimeToLiveDescription.TimeToLiveStatus' --output text 2>/dev/null); \
	PITR=$$(aws dynamodb describe-continuous-backups --table-name $$TBL --profile $(AWS_PROFILE) --region $(AWS_REGION) --query 'ContinuousBackupsDescription.PointInTimeRecoveryDescription.PointInTimeRecoveryStatus' --output text 2>/dev/null); \
	if [ "$$STATUS" != "ACTIVE" ]; then echo "$(RED)❌ $$TBL status=$$STATUS (want ACTIVE)$(NC)"; exit 1; fi; \
	if [ "$$TTL" != "ENABLED" ] && [ "$$TTL" != "ENABLING" ]; then echo "$(RED)❌ $$TBL TTL=$$TTL (want ENABLED/ENABLING — D3 relies on TTL expiry)$(NC)"; exit 1; fi; \
	if [ "$$PITR" != "ENABLED" ]; then echo "$(RED)❌ $$TBL PITR=$$PITR (want ENABLED — docs/DEPLOYMENT.md claims it)$(NC)"; exit 1; fi; \
	echo "$(GREEN)✅ $$TBL ACTIVE, TTL=$$TTL, PITR=$$PITR$(NC)"
	@echo "$(YELLOW)ℹ️  Table only. The proxy routes that read/write it land in T2.3, which is blocked on the producer.$(NC)"

deploy-fleet-intelligence-analytics: ## Deploy the Fleet Intelligence Athena workgroup + results bucket (us-east-1)
	@echo "$(BLUE)📊 Deploying Fleet Intelligence analytics (Athena workgroup + results bucket, us-east-1)$(NC)"
	@# Spec 2026-09-10-cms-fleet-intelligence-adp-consumer § D4; unblocks T5.3/T5.4.
	@#
	@# THE MISSING STEP. fleet_intelligence_analytics_stack.py creates the
	@# workgroup (:131) and results bucket, and app.py:198 instantiates the stack
	@# as cms-<stage>-ui-analytics — but NO make target deployed it, so the stack
	@# had never existed in ANY Region while FleetIntelligenceFunction's env vars
	@# (ATHENA_WORKGROUP, ATHENA_OUTPUT_LOC) named both resources by name. The
	@# config therefore read as complete, the Lambda deployed clean, and only a
	@# live Athena query failed. Same class of gap that
	@# deploy-connected-services-consumer above documents for its own table, and
	@# the same shape as the DMS dms-<stage>-analytics workgroup finding.
	@#
	@# The stack pins region=us-east-1 inside its own __init__ regardless of the
	@# env passed (fleet_intelligence_analytics_stack.py:91) because Athena can
	@# only read a Glue catalog in its own Region and ADP's lake is us-east-1. On
	@# staging (us-west-2 primary) that makes this a CROSS-REGION deploy, which
	@# needs CDK bootstrap in us-east-1. On prod it is same-Region.
	@#
	@# Contents are ONE Athena workgroup + ONE S3 bucket. No Lambda, no
	@# container, no asset — so this target cannot bundle a concurrent session's
	@# uncommitted source, which matters in this repo.
	@#
	@# The stage-config + federate preamble is required for the reason
	@# deploy-commands documents: `cdk deploy X` synths the WHOLE app, and
	@# ui_stack's provisioning guards fail closed at synth without
	@# CLIENT_EXTRA_IDPS + FEDERATE_CLIENT_ID/SECRET and the uiCustomDomain
	@# context that GUARD_CTX_FLAGS supplies.
	@set -a; [ -f config/$(DEPLOYMENT_STAGE).env ] && . config/$(DEPLOYMENT_STAGE).env; set +a; \
	. scripts/load-federate-creds.sh; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	AWS_DEFAULT_REGION=$(AWS_REGION) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-ui-analytics $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(BLUE)🔎 Post-deploy check$(NC)"
	@# Per deploy-validation.md: assert that the two resources the FI Lambda's env
	@# vars NAME actually exist, in the Region Athena queries from, and exit
	@# non-zero otherwise. A deploy that left either absent reproduces the
	@# original bug, in which the names were present and the resources were not.
	@ACCT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text); \
	WG=cms-$(DEPLOYMENT_STAGE)-analytics; \
	BUCKET=cms-$(DEPLOYMENT_STAGE)-athena-results-$$ACCT-us-east-1; \
	WGSTATE=$$(aws athena get-work-group --work-group $$WG --region us-east-1 --profile $(AWS_PROFILE) --query 'WorkGroup.State' --output text 2>/dev/null); \
	if [ "$$WGSTATE" != "ENABLED" ]; then echo "$(RED)❌ workgroup $$WG state=$$WGSTATE (want ENABLED)$(NC)"; exit 1; fi; \
	if ! aws s3api head-bucket --bucket $$BUCKET --region us-east-1 --profile $(AWS_PROFILE) >/dev/null 2>&1; then echo "$(RED)❌ results bucket $$BUCKET not found$(NC)"; exit 1; fi; \
	echo "$(GREEN)✅ workgroup $$WG ENABLED, results bucket $$BUCKET present (us-east-1)$(NC)"

# Bedrock agents config — (Retired 2026-09-06). Spec 2026-09-05-cms-vfo-teardown
# retired the Bedrock Agents stack. Variables + validate-bedrock-model target
# remain because CVX (a separate accelerator) still uses Bedrock; validators
# are cheap and safe to keep. The deploy-bedrock-agents target below is a
# no-op stub that prints the retirement notice.
BEDROCK_AGENT_MODEL ?= us.anthropic.claude-sonnet-4-6
BEDROCK_AGENT_KB_BUCKET ?=

validate-bedrock-model: ## Pre-deploy guardrail — validate $(BEDROCK_AGENT_MODEL) is ACTIVE in $(AWS_REGION)
	@MODEL_ID="$(BEDROCK_AGENT_MODEL)" REGION="$(AWS_REGION)" PROFILE="$(AWS_PROFILE)" \
	  ./scripts/validate-bedrock-model.sh

deploy-bedrock-agents: ## (Retired 2026-09-06) VFO Bedrock Agents stack retired per spec 2026-09-05-cms-vfo-teardown; target is a no-op stub for CI back-compat.
	@echo "$(YELLOW)⚠️  deploy-bedrock-agents is a no-op — VFO Bedrock Agents stack retired 2026-09-06 (spec 2026-09-05-cms-vfo-teardown).$(NC)"
	@echo "$(YELLOW)   The fleet-view surface is now served by services/fleet_intelligence/ (Tier 1 deterministic renderer).$(NC)"

seed-fleetwise: generate-and-upload-decoder-manifest ## Seed decoder manifest + default CAN campaign + click-to-assign templates (GPS, safety, UDS-DTC). Always regenerates DecoderManifest.bin on S3.
	@echo "$(BLUE)📦 Seeding decoder manifest and campaign templates$(NC)"
	@source .venv/bin/activate && \
	AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) \
	python3 scripts/seed_decoder_and_campaign.py
	@echo "$(GREEN)✅ Decoder manifest and campaign templates seeded (includes uds-dtc-polling)$(NC)"

seed-vehicle-models: ## Seed FleetWise Vehicle Models (CMS-FLEET-MODEL default)
	@echo "$(BLUE)📦 Seeding vehicle model manifests$(NC)"
	@source .venv/bin/activate && \
	AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) \
	python3 scripts/seed_model_manifests.py
	@echo "$(GREEN)✅ Vehicle models seeded (idempotent — safe to re-run)$(NC)"

generate-and-upload-decoder-manifest: ## Build DecoderManifest.bin from canonical DDB and upload to Flink config bucket
	@echo "$(BLUE)📦 Generating DecoderManifest.bin from cms-$(DEPLOYMENT_STAGE)-decoder-manifest (DDB) and uploading to S3$(NC)"
	@source .venv/bin/activate && \
	AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) \
	python3 scripts/generate_decoder_manifest.py
	@echo "$(GREEN)✅ DecoderManifest.bin generated and uploaded to fwe-config/ in the Flink config bucket$(NC)"

seed-signal-catalog: ## Seed signal catalog (CAN signals, GPS, ADAS, metadata)
	@echo "$(BLUE)📦 Seeding signal catalog$(NC)"
	@source .venv/bin/activate && \
	AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) \
	python3 scripts/seed_signal_catalog.py
	@echo "$(GREEN)✅ Signal catalog seeded$(NC)"

seed-event-catalog: ## Seed event catalog (safety + maintenance event definitions)
	@echo "$(BLUE)📦 Seeding event catalog$(NC)"
	@source .venv/bin/activate && \
	AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) \
	python3 scripts/seed_event_catalog.py
	@echo "$(GREEN)✅ Event catalog seeded$(NC)"

seed-fleet-enrollment: ## Seed fleet enrollment table from existing vehicle-fleet assignments
	@echo "$(BLUE)📦 Seeding fleet enrollment table$(NC)"
	@source .venv/bin/activate && \
	AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	python3 scripts/seed_fleet_enrollment.py
	@echo "$(GREEN)✅ Fleet enrollment table seeded$(NC)"

seed-generic-fleets: ## Seed generic demo fleets/vehicles/enrollment (public-mirror default; non-branded synthetic content)
	@echo "$(BLUE)📦 Seeding generic demo fleets$(NC)"
	@cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	python3 deployment/scripts/seed_generic_fleets.py
	@echo "$(GREEN)✅ Generic demo fleets seeded$(NC)"

seed-meridian-fleet: ## Seed the Meridian range demo fleet (10 vehicles, model years 2022-2026)
	@echo "$(BLUE)🚗 Seeding Meridian range fleet$(NC)"
	@cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	python3 deployment/scripts/seed_meridian_fleet.py
	@echo "$(GREEN)✅ Meridian range fleet seeded$(NC)"

seed-vfo-actions: ## (Retired 2026-09-06) VFO action-queue seeding — spec 2026-09-05-cms-vfo-teardown retired the Bedrock Agents stack. The `vfo-action-queue` DDB table remains (Flink FW+Maintenance processors write it); its consumers moved to `services/fleet_intelligence/` (Tier 1). Target left as a no-op stub for backward compatibility of CI callers.
	@echo "$(YELLOW)⚠️  seed-vfo-actions is a no-op — VFO Tier 2 retired 2026-09-06 (spec 2026-09-05-cms-vfo-teardown)$(NC)"

seed-decision-journal: ## (Retired 2026-09-06) VFO decision-journal seeding — see seed-vfo-actions above; table remains, consumers moved to Tier 1.
	@echo "$(YELLOW)⚠️  seed-decision-journal is a no-op — VFO Tier 2 retired 2026-09-06 (spec 2026-09-05-cms-vfo-teardown)$(NC)"

configure-flink: ## Configure Flink applications - JAR rotation only (property maps now sourced from CDK; deploy via `cdk deploy cms-$(DEPLOYMENT_STAGE)-flink`)
	@echo "$(BLUE)🔧 Configuring Flink applications (JAR rotation only)...$(NC)"
	@echo "Building JAR from source and pointing applications at the new artifact."
	@echo "$(YELLOW)Note: property maps, VPC configuration, and monitoring are now sourced from CDK (spec 2026-06-08-cms-flink-cfn-config-keys-fix). Run \`cdk deploy cms-$(DEPLOYMENT_STAGE)-flink\` for those changes.$(NC)"
	@echo "$(BLUE)🔨 Building Flink JAR via build.sh (also stages the .zip asset for CDK BucketDeployment)...$(NC)"
	@cd ../modules/flink && bash build.sh
	@echo "$(GREEN)✅ JAR built successfully$(NC)"
	@echo "$(BLUE)📤 Uploading JAR to S3...$(NC)"
	@BUCKET_NAME=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-flink --query 'Stacks[0].Outputs[?OutputKey==`FlinkJarBucketOutput`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	cd ../modules/flink && \
	aws s3 cp target/cms-telemetry-processor-1.0.0.jar s3://$$BUCKET_NAME/jars/cms-telemetry-processor-1.0.0.zip --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ JAR uploaded to S3$(NC)"
	@BUCKET_NAME=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-flink --query 'Stacks[0].Outputs[?OutputKey==`FlinkJarBucketOutput`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	echo '{"ApplicationCodeConfigurationUpdate":{"CodeContentUpdate":{"S3ContentLocationUpdate":{"BucketARNUpdate":"arn:aws:s3:::'$$BUCKET_NAME'","FileKeyUpdate":"jars/cms-telemetry-processor-1.0.0.zip"}},"CodeContentTypeUpdate":"ZIPFILE"}}' > /tmp/flink-jar-update.json; \
	aws kinesisanalyticsv2 list-applications --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationSummaries[?contains(ApplicationName, `cms-$(DEPLOYMENT_STAGE)-flink`)].ApplicationName' --output text | tr '\t' '\n' | while read app; do \
		if [ -n "$$app" ]; then \
			echo "Rotating JAR for application: $$app"; \
			CURRENT_VERSION=$$(aws kinesisanalyticsv2 describe-application --application-name "$$app" --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationDetail.ApplicationVersionId' --output text); \
			aws kinesisanalyticsv2 update-application --application-name "$$app" --current-application-version-id $$CURRENT_VERSION --region $(AWS_REGION) --profile $(AWS_PROFILE) --application-configuration-update file:///tmp/flink-jar-update.json >/dev/null 2>&1 && echo "✅ Updated JAR for $$app" || echo "❌ Failed to update JAR for $$app"; \
		fi; \
	done; \
	rm -f /tmp/flink-jar-update.json
	@echo "Starting applications..."
	@aws kinesisanalyticsv2 list-applications --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationSummaries[?contains(ApplicationName, `cms-$(DEPLOYMENT_STAGE)-flink`)].ApplicationName' --output text | tr '\t' '\n' | while read app; do \
		if [ -n "$$app" ]; then \
			echo "Starting application: $$app"; \
			aws kinesisanalyticsv2 start-application --application-name "$$app" --region $(AWS_REGION) --profile $(AWS_PROFILE) --run-configuration '{"ApplicationRestoreConfiguration":{"ApplicationRestoreType":"SKIP_RESTORE_FROM_SNAPSHOT"}}' >/dev/null 2>&1 && echo "✅ Started $$app" || echo "❌ Failed to start $$app"; \
		fi; \
	done
	@echo "$(GREEN)✅ Flink JAR rotation complete (property maps sourced from CDK)$(NC)"

deploy-flink-fast: ## ⚡ Fast Flink redeploy (~7 min vs ~45 min): force-stop apps → phase4 → UpdateApplication → start with SKIP_RESTORE
	@# Why this exists: each `cdk deploy cms-{stage}-flink` triggers
	@# UpdateApplication on all Flink apps (typed-property re-serialization
	@# from spec 2026-06-08-cms-flink-cfn-config-keys-fix + BucketDeployment
	@# asset-hash propagation). KDA's pre-update snapshot on a RUNNING app
	@# takes 3-5 min; serialized via the DependsOn chain, that compounds to
	@# ~45 min for the Flink portion of any deploy. KDA UpdateApplication on
	@# a STOPPED/READY app skips the snapshot entirely (~30s). Force-stopping
	@# apps before the deploy turns 45 min into ~7 min.
	@#
	@# When to use: any cms-{stage}-flink redeploy that's not the very
	@# first deploy. Discovered 2026-06-12 during the v0.2.3 publish-gate
	@# validation cycle (issues/2026-06-11-flink-stack-deploy-blockers
	@# § "force-stop discipline").
	@#
	@# 2026-09-25 revisions closed two gaps found during the flink-guards
	@# fix deploy (issues/2026-06-22-flink-campaign-sync-processor-*):
	@#
	@#   (1) INCLUDES campaign-sync-processor. It lives in the FleetWise
	@#       stack (cms-{stage}-fwe-telemetry) but shares the same JAR
	@#       bucket via Fn.import_value(cms-{stage}-flink-jar-*). Excluding
	@#       it silently made the fast-deploy a no-op for any change to
	@#       CampaignSyncProcessor.class — operator would think the deploy
	@#       landed when only the S3 JAR was updated. The prior exclusion
	@#       was a scope self-imposition, not a technical constraint.
	@#
	@#   (2) Calls UpdateApplication with an explicit S3 ObjectVersion after
	@#       phase4. Empirically verified that `start-application` alone
	@#       does NOT force KDA to reload the JAR when the S3 object was
	@#       updated behind KDA's cached CodeMD5. The cdk deploy in phase4
	@#       does NOT call UpdateApplication either — CFN sees no drift on
	@#       the Application resource because CodeContent.S3ContentLocation
	@#       (bucket+key) is unchanged; only the underlying S3 object
	@#       changed. UpdateApplication with ObjectVersionUpdate is the
	@#       only deterministic way to force JAR reload.
	@#
	@# Trade-off: ~5 min Flink-processing interruption (up from ~3 min for
	@# the 9-app version) while apps are stopped. Fine for staging; if a
	@# genuine "flink-stack only" deploy ever needs to leave
	@# campaign-sync-processor untouched, factor out the exclusion behind an
	@# env-var toggle rather than reverting these changes.
	@echo "$(BLUE)⚡ Fast Flink deploy: force-stop → phase4 → UpdateApplication → start$(NC)"
	@echo "$(YELLOW)⏸  Step 1/4: force-stopping all cms-$(DEPLOYMENT_STAGE)-flink-* apps (including campaign-sync-processor)$(NC)"
	@aws kinesisanalyticsv2 list-applications \
		--region $(AWS_REGION) --profile $(AWS_PROFILE) \
		--query "ApplicationSummaries[?starts_with(ApplicationName,'cms-$(DEPLOYMENT_STAGE)-flink-')].ApplicationName" \
		--output text 2>/dev/null | tr '\t' '\n' | while read app; do \
		if [ -n "$$app" ]; then \
			status=$$(aws kinesisanalyticsv2 describe-application --application-name $$app \
				--region $(AWS_REGION) --profile $(AWS_PROFILE) \
				--query 'ApplicationDetail.ApplicationStatus' --output text 2>/dev/null); \
			if [ "$$status" = "RUNNING" ]; then \
				aws kinesisanalyticsv2 stop-application --application-name $$app --force \
					--region $(AWS_REGION) --profile $(AWS_PROFILE) >/dev/null 2>&1 && \
					echo "  ↓ $${app##cms-$(DEPLOYMENT_STAGE)-flink-} → FORCE_STOPPING" || \
					echo "  ⚠ $${app##cms-$(DEPLOYMENT_STAGE)-flink-} stop failed"; \
			else \
				echo "  · $${app##cms-$(DEPLOYMENT_STAGE)-flink-} already $$status (skip)"; \
			fi; \
		fi; \
	done
	@echo "$(YELLOW)⏳ Polling until all apps reach READY/STOPPED ...$(NC)"
	@for i in $$(seq 1 30); do \
		pending=$$(aws kinesisanalyticsv2 list-applications \
			--region $(AWS_REGION) --profile $(AWS_PROFILE) \
			--query "length(ApplicationSummaries[?starts_with(ApplicationName,'cms-$(DEPLOYMENT_STAGE)-flink-') && ApplicationStatus != 'READY' && ApplicationStatus != 'STOPPED'])" \
			--output text 2>/dev/null); \
		if [ "$$pending" = "0" ]; then \
			echo "$(GREEN)  ✅ All apps READY$(NC)"; \
			break; \
		fi; \
		echo "    pending=$$pending ..."; \
		sleep 15; \
	done
	@echo "$(BLUE)🚀 Step 2/4: phase4 (build.sh + cdk deploy cms-$(DEPLOYMENT_STAGE)-flink)$(NC)"
	@$(MAKE) phase4 AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION)
	@echo "$(BLUE)🔄 Step 3/4: UpdateApplication with new S3 ObjectVersion (force JAR reload — see 2026-06-22-flink-campaign-sync-processor-non-paginated-scan/summary.md § Deploy discovery)$(NC)"
	@JAR_BUCKET=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-flink \
		--region $(AWS_REGION) --profile $(AWS_PROFILE) \
		--query 'Stacks[0].Outputs[?OutputKey==`FlinkJarBucketOutput`].OutputValue' --output text 2>/dev/null); \
	JAR_KEY=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-flink \
		--region $(AWS_REGION) --profile $(AWS_PROFILE) \
		--query 'Stacks[0].Outputs[?OutputKey==`FlinkJarS3Key`].OutputValue' --output text 2>/dev/null); \
	NEW_VERSION=$$(aws s3api head-object --bucket $$JAR_BUCKET --key $$JAR_KEY \
		--region $(AWS_REGION) --profile $(AWS_PROFILE) \
		--query 'VersionId' --output text 2>/dev/null); \
	if [ -z "$$NEW_VERSION" ] || [ "$$NEW_VERSION" = "None" ] || [ "$$NEW_VERSION" = "null" ]; then \
		echo "$(RED)  ✗ Failed to resolve new JAR ObjectVersion (bucket=$$JAR_BUCKET key=$$JAR_KEY). Skipping UpdateApplication — apps may boot with stale JAR.$(NC)"; \
	else \
		echo "$(YELLOW)  → new S3 ObjectVersion: $$NEW_VERSION$(NC)"; \
		aws kinesisanalyticsv2 list-applications \
			--region $(AWS_REGION) --profile $(AWS_PROFILE) \
			--query "ApplicationSummaries[?starts_with(ApplicationName,'cms-$(DEPLOYMENT_STAGE)-flink-')].ApplicationName" \
			--output text 2>/dev/null | tr '\t' '\n' | while read app; do \
			if [ -n "$$app" ]; then \
				cur_ver=$$(aws kinesisanalyticsv2 describe-application --application-name $$app \
					--region $(AWS_REGION) --profile $(AWS_PROFILE) \
					--query 'ApplicationDetail.ApplicationVersionId' --output text 2>/dev/null); \
				if [ -z "$$cur_ver" ] || [ "$$cur_ver" = "None" ]; then \
					echo "  ⚠ $${app##cms-$(DEPLOYMENT_STAGE)-flink-} could not resolve current version, skipping"; \
				else \
					aws kinesisanalyticsv2 update-application \
						--application-name $$app \
						--current-application-version-id $$cur_ver \
						--region $(AWS_REGION) --profile $(AWS_PROFILE) \
						--application-configuration-update "{\"ApplicationCodeConfigurationUpdate\":{\"CodeContentUpdate\":{\"S3ContentLocationUpdate\":{\"ObjectVersionUpdate\":\"$$NEW_VERSION\"}}}}" \
						>/dev/null 2>&1 && \
						echo "  ✓ $${app##cms-$(DEPLOYMENT_STAGE)-flink-} v$$cur_ver → v$$((cur_ver+1)) (ObjectVersion=$$NEW_VERSION)" || \
						echo "  ⚠ $${app##cms-$(DEPLOYMENT_STAGE)-flink-} UpdateApplication failed (may already be at this ObjectVersion)"; \
				fi; \
			fi; \
		done; \
		echo "$(YELLOW)⏳ Polling until all apps reach READY after UpdateApplication ...$(NC)"; \
		for i in $$(seq 1 20); do \
			pending=$$(aws kinesisanalyticsv2 list-applications \
				--region $(AWS_REGION) --profile $(AWS_PROFILE) \
				--query "length(ApplicationSummaries[?starts_with(ApplicationName,'cms-$(DEPLOYMENT_STAGE)-flink-') && ApplicationStatus == 'UPDATING'])" \
				--output text 2>/dev/null); \
			if [ "$$pending" = "0" ]; then \
				echo "$(GREEN)  ✅ All apps READY after UpdateApplication$(NC)"; \
				break; \
			fi; \
			echo "    updating=$$pending ..."; \
			sleep 10; \
		done; \
	fi
	@echo "$(BLUE)▶️  Step 4/4: starting all apps (SKIP_RESTORE_FROM_SNAPSHOT, 5s spacing for KDA throttle)$(NC)"
	@aws kinesisanalyticsv2 list-applications \
		--region $(AWS_REGION) --profile $(AWS_PROFILE) \
		--query "ApplicationSummaries[?starts_with(ApplicationName,'cms-$(DEPLOYMENT_STAGE)-flink-')].ApplicationName" \
		--output text 2>/dev/null | tr '\t' '\n' | while read app; do \
		if [ -n "$$app" ]; then \
			status=$$(aws kinesisanalyticsv2 describe-application --application-name $$app \
				--region $(AWS_REGION) --profile $(AWS_PROFILE) \
				--query 'ApplicationDetail.ApplicationStatus' --output text 2>/dev/null); \
			if [ "$$status" = "READY" ]; then \
				aws kinesisanalyticsv2 start-application --application-name $$app \
					--run-configuration '{"ApplicationRestoreConfiguration":{"ApplicationRestoreType":"SKIP_RESTORE_FROM_SNAPSHOT"}}' \
					--region $(AWS_REGION) --profile $(AWS_PROFILE) >/dev/null 2>&1 && \
					echo "  ▶ $${app##cms-$(DEPLOYMENT_STAGE)-flink-} → STARTING" || \
					echo "  ⚠ $${app##cms-$(DEPLOYMENT_STAGE)-flink-} start failed"; \
				sleep 5; \
			else \
				echo "  · $${app##cms-$(DEPLOYMENT_STAGE)-flink-} status=$$status (skip start)"; \
			fi; \
		fi; \
	done
	@echo "$(GREEN)✅ Fast deploy complete. Apps starting async — ~3 min to RUNNING. Verify via:$(NC)"
	@echo "$(GREEN)   aws kinesisanalyticsv2 list-applications --query 'ApplicationSummaries[?starts_with(ApplicationName,\`cms-$(DEPLOYMENT_STAGE)-flink-\`)].[ApplicationName,ApplicationStatus,ApplicationVersionId]' --output table --region $(AWS_REGION) --profile $(AWS_PROFILE)$(NC)"

start-flink-apps: ## Idempotent: start ALL cms-{stage}-flink-* KDA apps that are READY (Flink stack + FleetWise stack)
	@# Why this exists: deploy-all → phase-streaming → phase4 (cdk deploy
	@# cms-{stage}-flink) deploys the flink-stack KDA apps but leaves them
	@# in `READY` status — KDA `CreateApplication` does not auto-start.
	@# Similarly `deploy-fleetwise` creates campaign-sync-processor in
	@# READY. Without this step, fresh-region deploys come up with all 10
	@# cms-{stage}-flink-* KDA apps READY (campaigns "Pending sync", no
	@# trips/telemetry until an operator manually `start-application`s
	@# each one). Hit on the 2026-06-15 Tokyo fresh deploy
	@# (issues/2026-06-16-staging-tokyo-campaign-sync-processor-not-started).
	@#
	@# Consolidated 2026-09-25 (spec 2026-09-25-cms-fleetwise-consolidation):
	@# previously two targets (`start-flink-stack-apps` starting 9 apps and
	@# `start-flink-campaign-sync-processor` starting the 10th). The split
	@# existed because campaign-sync-processor lives in the fwe-telemetry
	@# stack; but a single JMESPath prefix match `cms-{stage}-flink-*` covers
	@# ALL 10 apps regardless of which stack owns them. `phase-streaming`
	@# now runs this once, AFTER `deploy-fleetwise`, so all 10 exist.
	@#
	@# Idempotency: per-app `describe-application` first; only `READY`
	@# apps get `start-application`. Apps already RUNNING / STARTING /
	@# UPDATING / STOPPED are skipped with a status-tagged log line, so
	@# re-runs are safe (no double-start, no error). 5s spacing between
	@# starts to respect KDA throttle (matches deploy-flink-fast Step 4).
	@#
	@# `SKIP_RESTORE_FROM_SNAPSHOT` matches the deploy-flink-fast pattern
	@# — fresh-region deploys have no snapshots to restore from, and on
	@# subsequent re-runs we want fast cold-start over snapshot replay.
	@echo "$(BLUE)▶️  Starting cms-$(DEPLOYMENT_STAGE)-flink-* apps (READY → RUNNING; SKIP_RESTORE; 5s spacing)$(NC)"
	@aws kinesisanalyticsv2 list-applications \
		--region $(AWS_REGION) --profile $(AWS_PROFILE) \
		--query "ApplicationSummaries[?starts_with(ApplicationName,'cms-$(DEPLOYMENT_STAGE)-flink-')].ApplicationName" \
		--output text 2>/dev/null | tr '\t' '\n' | while read app; do \
		if [ -n "$$app" ]; then \
			status=$$(aws kinesisanalyticsv2 describe-application --application-name $$app \
				--region $(AWS_REGION) --profile $(AWS_PROFILE) \
				--query 'ApplicationDetail.ApplicationStatus' --output text 2>/dev/null); \
			if [ "$$status" = "READY" ]; then \
				aws kinesisanalyticsv2 start-application --application-name $$app \
					--run-configuration '{"ApplicationRestoreConfiguration":{"ApplicationRestoreType":"SKIP_RESTORE_FROM_SNAPSHOT"}}' \
					--region $(AWS_REGION) --profile $(AWS_PROFILE) >/dev/null 2>&1 && \
					echo "  ▶ $${app##cms-$(DEPLOYMENT_STAGE)-flink-} → STARTING" || \
					echo "  ⚠ $${app##cms-$(DEPLOYMENT_STAGE)-flink-} start failed"; \
				sleep 5; \
			else \
				echo "  · $${app##cms-$(DEPLOYMENT_STAGE)-flink-} status=$$status (skip start)"; \
			fi; \
		fi; \
	done
	@echo "$(GREEN)✅ Flink apps start step complete (apps starting async — ~3 min to RUNNING)$(NC)"

start-flink-campaign-sync-processor: start-flink-apps ## DEPRECATED alias — use start-flink-apps directly (2026-09-25-cms-fleetwise-consolidation)
	@echo "$(YELLOW)⚠  start-flink-campaign-sync-processor is deprecated; delegating to start-flink-apps.$(NC)"
	@echo "$(YELLOW)   The 2026-09-25 fleetwise consolidation merged this into start-flink-apps.$(NC)"

validate-flink-deployment: ## Validate Flink deployment
	@echo "$(BLUE)🔍 Validating Flink deployment...$(NC)"
	@echo "Checking Flink applications..."
	@aws kinesisanalyticsv2 list-applications --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationSummaries[?contains(ApplicationName, `cms-$(DEPLOYMENT_STAGE)`)].ApplicationName' --output text | grep -q . && echo "✅ Flink applications found" || echo "❌ No Flink applications found"
	@echo "Testing message publish..."
	@echo '{"test":"makefile-validation","timestamp":"'$$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' | base64 | xargs -I {} aws iot-data publish --topic "topic/telemetry" --payload {} --region $(AWS_REGION) --profile $(AWS_PROFILE) && echo "✅ Test message published" || echo "❌ Message publish failed"
	@echo "$(GREEN)✅ IoT-MSK integration validation complete$(NC)"
sync-manifests: ## Sync local manifests to S3 (deletes stale files like fleetwise-transform.json)
	@echo "$(BLUE)📤 Syncing transform manifests to S3$(NC)"
	@BUCKET=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-data-processing --query 'Stacks[0].Outputs[?OutputKey==`ManifestsBucketName`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	aws s3 sync ../services/data_processing/manifests/ s3://$$BUCKET/manifests/ --delete --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Manifests synced (stale files removed)$(NC)"

verify-manifests: ## Verify git-tracked transform manifests match the DEPLOYED ones (needs AWS creds; exit 2 = could not evaluate)
	@echo "$(BLUE)🔍 Verifying transform manifests: git vs deployed$(NC)"
	@BUCKET=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-data-processing --query 'Stacks[0].Outputs[?OutputKey==`ManifestsBucketName`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	python3 ../scripts/verify_manifest_sync.py --stage $(DEPLOYMENT_STAGE) --region $(AWS_REGION) --bucket "$$BUCKET"; \
	rc=$$?; \
	case $$rc in \
	  0) echo "$(GREEN)✅ Manifests in sync (exit 0)$(NC)" ;; \
	  1) echo "$(RED)❌ DRIFT — see script output above (exit 1)$(NC)" ;; \
	  2) echo "$(YELLOW)⊘ Could not evaluate (exit 2 — NOT a drift finding)$(NC)" ;; \
	  *) echo "$(RED)❌ Unexpected exit code $$rc$(NC)" ;; \
	esac; \
	exit $$rc
# NOTE: the bucket is resolved from the same CloudFormation output `sync-manifests` uses,
# and passed in explicitly. The script can derive the name itself, but that derivation is a
# fallback for ad-hoc use only — deriving it here would make this target a second source of
# truth for the bucket name, which is precisely the class of defect the guard exists to
# catch. Spec .kiro/specs/2026-09-20-transform-manifest-contract-guards/ T2.2.
#
# Deliberately NOT wired into .github/workflows/ — it requires AWS credentials, and a guard
# that fails in CI for want of credentials gets switched off. Exit 2 means "could not
# evaluate" and must never be read as drift.


phase5: ## Phase 5: Telemetry Pipeline 4 (Flink Configuration)
	@echo "$(BLUE)🚀 Phase 5: Telemetry Pipeline 4 (Flink Configuration)$(NC)"
	@echo "Building JAR from source and configuring Flink applications"
	@$(MAKE) configure-flink AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(GREEN)✅ Phase 5 Complete: Flink applications configured with latest JAR and running$(NC)"

deploy-connector: ## Deploy OEM connector (ECS Fargate). Usage: make deploy-connector CONNECTOR_NAME=oem1-feed CONNECTOR_TYPE=grpc_streaming
	@if [ -z "$(CONNECTOR_NAME)" ]; then echo "$(RED)❌ CONNECTOR_NAME required. Usage: make deploy-connector CONNECTOR_NAME=oem1-feed CONNECTOR_TYPE=grpc_streaming$(NC)"; exit 1; fi
	@echo "$(BLUE)🔌 Deploying OEM Connector: $(CONNECTOR_NAME) ($(CONNECTOR_TYPE))$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	CONNECTOR_NAME=$(CONNECTOR_NAME) CONNECTOR_TYPE=$(CONNECTOR_TYPE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-connector-$(CONNECTOR_NAME) $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Connector $(CONNECTOR_NAME) deployed$(NC)"

seed-manifest: ## Upload transform manifest to S3. Usage: make seed-manifest CONNECTOR_NAME=oem1-feed
	@if [ -z "$(CONNECTOR_NAME)" ]; then echo "$(RED)❌ CONNECTOR_NAME required$(NC)"; exit 1; fi
	@echo "$(BLUE)📤 Uploading transform manifest for $(CONNECTOR_NAME)$(NC)"
	@BUCKET=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-data-processing --query 'Stacks[0].Outputs[?OutputKey==`ManifestsBucketName`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	aws s3 cp ../services/data_processing/manifests/$(CONNECTOR_NAME)-transform.json s3://$$BUCKET/manifests/$(CONNECTOR_NAME)-transform.json --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Manifest uploaded for $(CONNECTOR_NAME)$(NC)"

deploy-ws-fanout: ## Deploy WebSocket fanout service (Kafka → WebSocket bridge)
	@echo "$(BLUE)🚀 Deploying WebSocket Fanout Service$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-ws-fanout $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(YELLOW)⚠️  Set BOOTSTRAP_SERVERS on the Fargate task:$(NC)"
	@MSK_CLUSTER_ARN=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query "Stacks[0].Outputs[?OutputKey=='MSKClusterArn'].OutputValue" --output text --profile $(AWS_PROFILE)); \
	BOOTSTRAP_SERVERS=$$(aws kafka get-bootstrap-brokers --cluster-arn $$MSK_CLUSTER_ARN --query 'BootstrapBrokerStringSaslIam' --output text --profile $(AWS_PROFILE)); \
	echo "$(GREEN)Bootstrap servers: $$BOOTSTRAP_SERVERS$(NC)"
	@echo "$(GREEN)✅ WebSocket Fanout Service deployed$(NC)"
	@echo "$(BLUE)🔄 Refreshing runtimeConfig.json (post-ws-fanout sibling-state hygiene)$(NC)"
	@# runtimeConfig endpoint-race hardening (issue
	@# 2026-06-15-runtimeconfig-endpoint-race-hardening): ws-fanout has no
	@# direct runtimeConfig endpoint key, but a partial deploy that includes
	@# deploy-ws-fanout may run AFTER siblings (data-processing/simulation/commands)
	@# came online while the UI stack's baked-in runtimeConfig still has them
	@# as "". Cheap idempotent refresh ensures every UI-touching phase leaves
	@# runtimeConfig consistent with deployed reality. Soft-fail.
	@$(MAKE) regenerate-runtime-config AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
		|| echo "$(YELLOW)⚠️  regenerate-runtime-config failed; run it manually to refresh runtimeConfig.json$(NC)"

phase6: ## Phase 6: Telemetry Pipeline 4 (Configuration)
	@echo "$(BLUE)🚀 Phase 6: Telemetry Pipeline 4 (Configuration)$(NC)"
	@echo "Configuring IoT rules, Flink applications, and starting services"
	@$(MAKE) configure-pipeline AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(GREEN)✅ Phase 6 Complete: Telemetry pipeline fully configured$(NC)"

configure-msk: ## Configure MSK with SCRAM secret and VPC destination
	@echo "$(BLUE)🔧 Configuring MSK cluster...$(NC)"
	@CLUSTER_ARN=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKClusterArn`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	SECRET_ARN=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`IoTUserSecretArn`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	STATE=$$(aws kafka describe-cluster --cluster-arn $$CLUSTER_ARN --query 'ClusterInfo.State' --output text --profile $(AWS_PROFILE)); \
	if [ "$$STATE" = "ACTIVE" ]; then \
		echo "$(BLUE)Associating SCRAM secret with MSK cluster...$(NC)"; \
		RESULT=$$(aws kafka batch-associate-scram-secret --cluster-arn $$CLUSTER_ARN --secret-arn-list $$SECRET_ARN --profile $(AWS_PROFILE) 2>&1); \
		if echo "$$RESULT" | grep -q "already associated"; then \
			echo "$(GREEN)✅ SCRAM secret already associated$(NC)"; \
		else \
			echo "$(GREEN)✅ SCRAM secret associated$(NC)"; \
		fi; \
		echo "$(GREEN)✅ MSK SCRAM configuration complete$(NC)"; \
	else \
		echo "$(YELLOW)⏳ Cluster state: $$STATE - waiting for ACTIVE state$(NC)"; \
	fi

configure-msk-topics: ## Create canonical MSK topics (idempotent; uses AWS MSK control-plane API — no VPC presence required)
	@echo "$(BLUE)📝 Creating canonical MSK topics via AWS MSK control-plane API...$(NC)"
	@AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
		python3 scripts/create_msk_topics.py
	@echo "$(GREEN)✅ Canonical MSK topics ensured$(NC)"

deploy-flink: ## Deploy Flink stack with MSK integration
	@echo "$(BLUE)⚡ Deploying Flink stack...$(NC)"
	@# JAR-prune-hazard precondition: the flink stack's BucketDeployment globs only
	@# `cms-telemetry-processor-1.0.0.zip` from `../modules/flink/target` with
	@# prune=True. If the .zip is absent (e.g. because something ran `mvn clean`
	@# without re-staging — see modules/flink/build.sh for the canonical staging
	@# step), CDK syncs an empty asset and prunes the live JAR from S3, breaking
	@# every Flink app on next restart with InvalidRequest "fileKey ... not found".
	@# Fail loud BEFORE invoking cdk deploy. Hit on 2026-06-12 11:30 EDT during
	@# the v0.2.3 publish-gate cycle (issues/2026-06-11-flink-stack-deploy-blockers
	@# § "JAR-prune trap").
	@if [ ! -f ../modules/flink/target/cms-telemetry-processor-1.0.0.zip ]; then \
		echo "$(RED)❌ Required asset ../modules/flink/target/cms-telemetry-processor-1.0.0.zip is missing.$(NC)"; \
		echo "$(YELLOW)   Stage it via:  cd ../modules/flink && bash build.sh$(NC)"; \
		echo "$(YELLOW)   (build.sh runs mvn package + cp .jar .zip atomically; do NOT run mvn clean directly)$(NC)"; \
		exit 1; \
	fi
	@set -a; [ -f config/$(DEPLOYMENT_STAGE).env ] && . config/$(DEPLOYMENT_STAGE).env; set +a; \
	MSK_CLUSTER_ARN=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKClusterArn`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	MSK_VPC_ID=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKVpcId`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	MSK_SECURITY_GROUP_ID=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKSecurityGroupId`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	MSK_SUBNET_IDS=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKPrivateSubnetIds`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	REDIS_ENDPOINT=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`RedisEndpoint`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	BOOTSTRAP_SERVERS=$$(aws kafka get-bootstrap-brokers --cluster-arn $$MSK_CLUSTER_ARN --query 'BootstrapBrokerStringSaslIam' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	export MSK_CLUSTER_ARN=$$MSK_CLUSTER_ARN; \
	export MSK_VPC_ID=$$MSK_VPC_ID; \
	export MSK_SECURITY_GROUP_ID=$$MSK_SECURITY_GROUP_ID; \
	export MSK_SUBNET_IDS=$$MSK_SUBNET_IDS; \
	export REDIS_ENDPOINT=$$REDIS_ENDPOINT; \
	export BOOTSTRAP_SERVERS=$$BOOTSTRAP_SERVERS; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-flink $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)

configure-pipeline: ## Configure IoT rules and Flink applications
	@echo "$(BLUE)🔗 Configuring telemetry pipeline...$(NC)"
	@cd scripts && AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) python3 complete_integration.py
	@echo "$(GREEN)✅ Pipeline configuration complete$(NC)"

# ── Grouped phases (for deploy-all ordering clarity) ─────────────────
# Order: phase3 (MSK) → phase1 (Storage/IoT/UI) → data-processing → phase3b
# (telemetry-integration) → configure-msk → configure-msk-topics.
#
# data-processing was previously FIRST in this list, but the H1 3775026
# auth-gaps fix (commit 2e8c200) made data_processing_stack import
# cms-{stage}-ui-user-pool-arn from ui_stack via Fn.import_value. That
# export only exists once phase1 (which deploys ui_stack) has run with
# the post-auth-fix template. Running data-processing first results in:
#
#   Resolution error: No export named cms-{stage}-ui-user-pool-arn found.
#
# CDK's add_dependency in app.py only enforces ordering for a single
# `cdk deploy --all` invocation; per-phase Makefile targets each call
# `cdk deploy <stack>` independently, so make-level ordering must mirror
# the import graph. See issues/2026-06-11-flink-stack-deploy-blockers
# § "phase-foundation ordering bug" for the staging-deploy repro.
phase-foundation: phase3 phase1 data-processing deploy-fleet-intelligence-analytics phase3b configure-msk configure-msk-topics ## MSK + Storage/IoT/UI + data-processing + FI analytics + telemetry-integration + canonical topics
	@echo "$(GREEN)✅ Foundation phase complete$(NC)"

phase-streaming: phase4 deploy-fleetwise start-flink-apps ## Flink apps + FleetWise integration + KDA app start (order matters: phase4 deploys flink stack → deploy-fleetwise creates campaign-sync-processor → start-flink-apps starts all 10)
	@echo "$(GREEN)✅ Streaming phase complete$(NC)"

phase-seeds: seed-signal-catalog seed-event-catalog seed-fleet-enrollment seed-fleetwise generate-and-upload-decoder-manifest seed-vehicle-models ## Catalogs + fleet enrollment + decoder manifest (DDB rows + S3 binary) + vehicle model manifests
	@echo "$(GREEN)✅ Seed data phase complete$(NC)"

phase-services: deploy-simulation deploy-commands deploy-ws-fanout ## ECS/Lambda services (simulation, commands, ws-fanout)
	@echo "$(GREEN)✅ Services phase complete$(NC)"

phase-staging-demo-data: seed-drivers ## Staging-only demo seeds the simulator hard-requires (NOT chained from prod-deploy)
	@# Why this exists: deploy-all produces a working CMS infra deployment but
	@# leaves cms-{stage}-storage-drivers EMPTY by design (so real OEM
	@# customers do not get 75 phantom CDL drivers seeded into their
	@# production accounts on first deploy). However, the simulator service
	@# (deploy-simulation in phase-services) hard-asserts at trip start that
	@# at least one driver row exists, failing fast with:
	@#   "❌ Error publishing telemetry for VEH-...: Drivers table is empty
	@#    (cms-{stage}-storage-drivers). Run 'make seed-drivers ...'"
	@# For staging fresh-region deploys (us-west-2 + Tokyo + any future
	@# region), this manifested as "deploy-all completes successfully but
	@# the Trip Simulator immediately crashes on first trip". Hit
	@# 2026-06-16 in Tokyo (VEH-1781613024 sim-start failure); same gap
	@# class as the 'Flink start gap' P2 backlog row (deploy-all leaves
	@# KDA apps READY but never started).
	@#
	@# Scope is intentionally narrow: ONLY the seeds the simulator hard-
	@# requires for startup. seed-driver-users is omitted — it cross-
	@# references the VSA Cognito pool (CVX repo) which is currently
	@# us-west-2-only and would fail Tokyo deploys. Other demo-data
	@# seeds (seed-vehicle-lifecycle, seed-trip-history, seed-warranty,
	@# seed-recalls, seed-vfo-actions, seed-decision-journal) are still
	@# operator-opt-in via 'make seed-all-demo-data' since they aren't
	@# gating the simulator and grow demo-table footprint considerably.
	@#
	@# Chained from staging-deploy ONLY (see staging-deploy target);
	@# prod-deploy intentionally does NOT include this — prod customers
	@# bring their own driver records.
	@echo "$(GREEN)✅ Staging demo-data phase complete (drivers seeded for simulator)$(NC)"

deploy-eval-user: ## Deploy cms-{stage}-eval-user stack (staging only) + promote eval-runner password to permanent
ifeq ($(DEPLOYMENT_STAGE),staging)
	@echo "$(BLUE)🔐 Deploying cms-$(DEPLOYMENT_STAGE)-eval-user (Tier-3 eval pipeline user)$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-eval-user $(GUARD_CTX_FLAGS) --require-approval never --profile $(AWS_PROFILE)
	@$(MAKE) set-eval-user-password AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION)
	@echo "$(GREEN)✅ Eval-user stack deployed and password promoted to permanent$(NC)"
else
	@echo "$(YELLOW)⏭  Skipping cms-$(DEPLOYMENT_STAGE)-eval-user (staging-only stack)$(NC)"
endif

set-eval-user-password: ## Promote eval-runner Cognito user password to permanent (idempotent; safe to re-run)
	@# Cognito does NOT permit setting permanent passwords at create time
	@# via CloudFormation. The EvalUserStack creates the user in
	@# FORCE_CHANGE_PASSWORD state with an auto-generated Secrets Manager
	@# password; this target promotes that password to permanent so
	@# admin_initiate_auth (test_S5) succeeds.
	@#
	@# Idempotent: admin-set-user-password has PUT semantics — re-running
	@# resets the password to the same Secrets Manager value, no error.
ifeq ($(DEPLOYMENT_STAGE),staging)
	@echo "$(YELLOW)Promoting cms-eval-runner password to permanent...$(NC)"
	@USER_POOL_ID=$$(aws cloudformation describe-stacks \
	    --stack-name cms-$(DEPLOYMENT_STAGE)-ui --region $(AWS_REGION) --no-cli-pager \
	    --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" \
	    --output text --profile $(AWS_PROFILE) 2>/dev/null); \
	  EVAL_USERNAME=$$(aws cloudformation describe-stacks \
	    --stack-name cms-$(DEPLOYMENT_STAGE)-eval-user --region $(AWS_REGION) --no-cli-pager \
	    --query "Stacks[0].Outputs[?OutputKey=='EvalUserName'].OutputValue" \
	    --output text --profile $(AWS_PROFILE) 2>/dev/null); \
	  SECRET_ARN=$$(aws cloudformation describe-stacks \
	    --stack-name cms-$(DEPLOYMENT_STAGE)-eval-user --region $(AWS_REGION) --no-cli-pager \
	    --query "Stacks[0].Outputs[?OutputKey=='EvalPasswordSecretArn'].OutputValue" \
	    --output text --profile $(AWS_PROFILE) 2>/dev/null); \
	  if [ -z "$$USER_POOL_ID" ] || [ "$$USER_POOL_ID" = "None" ]; then \
	    echo "$(RED)❌ Could not resolve UserPoolId from cms-$(DEPLOYMENT_STAGE)-ui$(NC)"; exit 1; fi; \
	  if [ -z "$$EVAL_USERNAME" ] || [ "$$EVAL_USERNAME" = "None" ]; then \
	    echo "$(RED)❌ Could not resolve EvalUserName from cms-$(DEPLOYMENT_STAGE)-eval-user$(NC)"; exit 1; fi; \
	  if [ -z "$$SECRET_ARN" ] || [ "$$SECRET_ARN" = "None" ]; then \
	    echo "$(RED)❌ Could not resolve EvalPasswordSecretArn from cms-$(DEPLOYMENT_STAGE)-eval-user$(NC)"; exit 1; fi; \
	  EVAL_PW=$$(aws secretsmanager get-secret-value \
	    --secret-id "$$SECRET_ARN" --region $(AWS_REGION) --no-cli-pager \
	    --query SecretString --output text --profile $(AWS_PROFILE) 2>/dev/null); \
	  if [ -z "$$EVAL_PW" ]; then \
	    echo "$(RED)❌ Secret $$SECRET_ARN has empty SecretString$(NC)"; exit 1; fi; \
	  aws cognito-idp admin-set-user-password \
	    --user-pool-id "$$USER_POOL_ID" \
	    --username "$$EVAL_USERNAME" \
	    --password "$$EVAL_PW" \
	    --permanent \
	    --region $(AWS_REGION) --no-cli-pager --profile $(AWS_PROFILE) >/dev/null
	@echo "$(GREEN)✅ Eval-user password promoted to permanent$(NC)"
else
	@echo "$(YELLOW)⏭  Skipping eval-user password (staging-only)$(NC)"
endif

deploy-all: phase-foundation phase-streaming phase-seeds phase-services deploy-eval-user regenerate-runtime-config seed-demo-personas ## Deploy everything (includes demo persona users + staging eval-user)
	@echo "$(GREEN)🎉 All phases deployed successfully!$(NC)"
	@echo "$(YELLOW)Optional next steps:$(NC)"
	@echo "$(YELLOW)  - make seed-all-demo-data    # Populate demo fleet data (drivers, trips, warranty)$(NC)"
	@echo "$(YELLOW)  - make phase2                # Interactive historical data injection$(NC)"
	@$(MAKE) status

regenerate-runtime-config: ## Regenerate runtimeConfig.json in the UI S3 bucket with current stack outputs and invalidate CloudFront
	@echo "$(BLUE)🔧 Regenerating runtimeConfig.json with outputs from all deployed stacks$(NC)"
	@# Fail-closed preamble: COGNITO_DOMAIN must be set (typically by sourcing
	@# `deployment/config/$(DEPLOYMENT_STAGE).env` before invocation). Without
	@# this, `$${COGNITO_DOMAIN:-}` silently expands to empty string and the
	@# generated runtimeConfig.json emits `"cognitoDomain": ""`, which hides
	@# the Federate login button on the deployed frontend
	@# (SimpleAuthProvider.tsx:197 gates rendering on truthiness).
	@# See issues/2026-09-14-regenerate-runtime-config-strips-cognito-domain/.
	@if [ -z "$(COGNITO_DOMAIN_RESOLVED)" ]; then \
		echo "$(RED)❌ COGNITO_DOMAIN is unset — refusing to regenerate runtimeConfig.json.$(NC)" >&2; \
		echo "$(YELLOW)   Without it, cognitoDomain would be written as empty string and the$(NC)" >&2; \
		echo "$(YELLOW)   Federate login button would silently disappear from the frontend.$(NC)" >&2; \
		echo "" >&2; \
		echo "$(YELLOW)   Resolution order is: environment, then config/$(DEPLOYMENT_STAGE).env.$(NC)" >&2; \
		echo "$(YELLOW)   Both are empty for stage '$(DEPLOYMENT_STAGE)'. Either set the key in$(NC)" >&2; \
		echo "$(YELLOW)   that file, or pass it explicitly:$(NC)" >&2; \
		echo "$(BLUE)     make regenerate-runtime-config DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) COGNITO_DOMAIN=<prefix>.auth.<region>.amazoncognito.com$(NC)" >&2; \
		echo "" >&2; \
		echo "$(YELLOW)   See issues/2026-09-14-regenerate-runtime-config-strips-cognito-domain/$(NC)" >&2; \
		echo "$(YELLOW)   and issues/2026-09-22-regenerate-runtime-config-never-runs-from-chained-callers/$(NC)" >&2; \
		exit 1; \
	fi
	@PREFIX=cms-$(DEPLOYMENT_STAGE); \
	get_out() { \
		aws cloudformation describe-stacks --stack-name "$$1" --region $(AWS_REGION) --profile $(AWS_PROFILE) \
			--query "Stacks[0].Outputs[?OutputKey==\`$$2\`].OutputValue" --output text 2>/dev/null | tr -d '\n'; \
	}; \
	REGION=$(AWS_REGION); \
	UI_POOL=$$(get_out $$PREFIX-ui UserPoolId); \
	UI_CLIENT=$$(get_out $$PREFIX-ui UserPoolClientId); \
	UI_IDPOOL=$$(get_out $$PREFIX-ui IdentityPoolId); \
	UI_API=$$(get_out $$PREFIX-ui APIEndpoint); \
	UI_WS=$$(get_out $$PREFIX-ui WebSocketEndpoint); \
	UI_MAP_DEFAULT=$$(get_out $$PREFIX-ui LocationServicesMapName); \
	UI_MAP_HERE=$$(aws location list-maps --region $$REGION --profile $(AWS_PROFILE) \
		--query "Entries[?DataSource=='Here'].MapName | [0]" --output text 2>/dev/null \
		| tr -d '\n' | grep -v '^None$$' || true); \
	if [ -n "$$UI_MAP_HERE" ]; then UI_MAP=$$UI_MAP_HERE; else UI_MAP=$$UI_MAP_DEFAULT; fi; \
	echo "  UI_MAP: $$UI_MAP (preferred Here-source: $$UI_MAP_HERE; UI-stack default: $$UI_MAP_DEFAULT)"; \
	UI_PLACE=$$(get_out $$PREFIX-ui LocationServicesPlaceIndexName); \
	UI_ROUTE=$$(get_out $$PREFIX-ui RouteCalculatorName); \
	DP_API=$$(get_out $$PREFIX-data-processing APIEndpoint); \
	SIM_API=$$(get_out $$PREFIX-simulation SimulationApiUrl); \
	CMD_API=$$(get_out $$PREFIX-commands CommandsApiUrl); \
	VSA_API=$$(aws cloudformation describe-stacks --stack-name vsa-$(DEPLOYMENT_STAGE)-api --region $(AWS_REGION) --profile $(AWS_PROFILE) \
		--query "Stacks[0].Outputs[?OutputKey=='RestApiUrl'].OutputValue" --output text 2>/dev/null | tr -d '\n'); \
	COG_DOMAIN=$(COGNITO_DOMAIN_RESOLVED); \
	DMS_API=$${DMS_API_ENDPOINT:-}; \
	DMS_UI_ORIGIN=$${DMS_UI_CALLBACK_ORIGIN:-}; \
	if [ -z "$$DMS_UI_ORIGIN" ]; then DMS_UI_ORIGIN=$$(sed -n 's/^DMS_UI_CALLBACK_ORIGIN=//p' config/$(DEPLOYMENT_STAGE).env 2>/dev/null | head -1); fi; \
	DMS_UI_ORIGIN=$${DMS_UI_ORIGIN%/}; \
	if [ -n "$$DMS_UI_ORIGIN" ] && { [ "$$(printf '%s' "$$DMS_UI_ORIGIN" | wc -l | tr -d ' ')" != 0 ] || ! printf '%s\n' "$$DMS_UI_ORIGIN" | grep -Exq 'https://[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*(:[0-9]{1,5})?'; }; then \
		echo "$(RED)❌ DMS_UI_CALLBACK_ORIGIN must be a bare https:// origin, no path (got '$$DMS_UI_ORIGIN').$(NC)" >&2; exit 1; \
	fi; \
	echo "  dmsUiOrigin: $${DMS_UI_ORIGIN:-(unset — CMS shows repair-order ids without a DMS link)}"; \
	export DMS_UI_ORIGIN; \
	stack_exists() { \
		aws cloudformation describe-stacks --stack-name "$$1" --region $$REGION --profile $(AWS_PROFILE) \
			--query "Stacks[0].StackStatus" --output text >/dev/null 2>&1; \
	}; \
	UNRESOLVED=""; \
	if [ -z "$$DP_API"  ] && stack_exists $$PREFIX-data-processing; then UNRESOLVED="$$UNRESOLVED dataProcessingApiEndpoint"; fi; \
	if [ -z "$$SIM_API" ] && stack_exists $$PREFIX-simulation;      then UNRESOLVED="$$UNRESOLVED simulationApiEndpoint";    fi; \
	if [ -z "$$CMD_API" ] && stack_exists $$PREFIX-commands;        then UNRESOLVED="$$UNRESOLVED commandsApiEndpoint";      fi; \
	if [ -z "$$VSA_API" ] && stack_exists vsa-$(DEPLOYMENT_STAGE)-api; then UNRESOLVED="$$UNRESOLVED vsaApiEndpoint";        fi; \
	if [ -n "$$UNRESOLVED" ]; then \
		echo "$(YELLOW)⚠️  Endpoints unresolved despite stack-exists: $$UNRESOLVED$(NC)"; \
	fi; \
	RESOLVED_AT=$$(date -u +"%Y-%m-%dT%H:%M:%SZ"); \
	BUCKET=$$(aws cloudformation list-stack-resources --stack-name $$PREFIX-ui --region $$REGION --profile $(AWS_PROFILE) \
		--query "StackResourceSummaries[?ResourceType=='AWS::S3::Bucket' && contains(LogicalResourceId, 'Frontend')].PhysicalResourceId" --output text); \
	CF_ID=$$(aws cloudformation list-stack-resources --stack-name $$PREFIX-ui --region $$REGION --profile $(AWS_PROFILE) \
		--query "StackResourceSummaries[?ResourceType=='AWS::CloudFront::Distribution'].PhysicalResourceId" --output text); \
	if [ -z "$$BUCKET" ] || [ -z "$$UI_POOL" ]; then \
		echo "$(RED)❌ Could not resolve UI stack outputs or bucket$(NC)"; exit 1; \
	fi; \
	echo "  Bucket: $$BUCKET"; \
	echo "  CF id: $$CF_ID"; \
	RC_FILE=$$(mktemp); trap 'rm -f "$$RC_FILE"' EXIT; \
	python3 -c "import json,os; print(json.dumps({ \
'awsRegion':'$$REGION', \
'mapAuth':{'identityPoolClient':f'cognito-idp.$$REGION.amazonaws.com/$$UI_POOL','mapName':'$$UI_MAP','identityPoolId':'$$UI_IDPOOL'}, \
'locationServices':{'mapName':'$$UI_MAP','placeIndexName':'$$UI_PLACE','routeCalculatorName':'$$UI_ROUTE','region':'$$REGION','enabled':True}, \
'isDemoMode':'false', \
'apiEndpoint':'$$UI_API', \
'wsEndpoint':'$$UI_WS', \
'userPreferencesApiEndpoint':'$$UI_API', \
'awsCredentials':{'region':'$$REGION','identityPoolId':'$$UI_IDPOOL','userPoolId':'$$UI_POOL','userPoolWebClientId':'$$UI_CLIENT'}, \
'dataProcessingApiEndpoint':'$$DP_API', \
'simulationApiEndpoint':'$$SIM_API', \
'commandsApiEndpoint':'$$CMD_API', \
'vsaApiEndpoint':'$$VSA_API', \
'cognitoDomain':'$$COG_DOMAIN', \
  **({'dmsApiEndpoint':'$$DMS_API'} if '$$DMS_API' else {}), \
  **({'dmsUiOrigin':os.environ['DMS_UI_ORIGIN']} if os.environ.get('DMS_UI_ORIGIN') else {}), \
'_resolved_at':'$$RESOLVED_AT', \
'_unresolved_endpoints':'$$UNRESOLVED'.split() \
}, indent=2))" > "$$RC_FILE" && python3 -m json.tool "$$RC_FILE" >/dev/null || { \
		echo "$(RED)❌ runtimeConfig.json generation failed — not uploading (the live file is left as it was).$(NC)" >&2; exit 1; }; \
	aws s3 cp "$$RC_FILE" s3://$$BUCKET/runtimeConfig.json --region $$REGION --profile $(AWS_PROFILE) \
		--cache-control "max-age=0, no-cache" --content-type "application/json" >/dev/null || { \
		echo "$(RED)❌ runtimeConfig.json upload failed.$(NC)" >&2; exit 1; }; \
	echo "$(GREEN)✅ runtimeConfig.json uploaded to s3://$$BUCKET/$(NC)"; \
	if [ -n "$$CF_ID" ]; then \
		INV_ID=$$(aws cloudfront create-invalidation --distribution-id $$CF_ID --paths "/runtimeConfig.json" "/index.html" --profile $(AWS_PROFILE) --query 'Invalidation.Id' --output text); \
		echo "$(GREEN)✅ CloudFront invalidation $$INV_ID created$(NC)"; \
	fi
	@echo "$(YELLOW)Set COGNITO_DOMAIN env var before running for Federate login support (e.g. COGNITO_DOMAIN=<your-cognito-prefix>.auth.<region>.amazoncognito.com)$(NC)"

status: ## Show deployment status
	@echo "$(BLUE)📊 Deployment Status$(NC)"
	@echo "===================="
	@for stack in \
		cms-$(DEPLOYMENT_STAGE)-data-processing \
		cms-$(DEPLOYMENT_STAGE)-storage \
		cms-$(DEPLOYMENT_STAGE)-iot \
		cms-$(DEPLOYMENT_STAGE)-ui \
		cms-$(DEPLOYMENT_STAGE)-msk \
		cms-$(DEPLOYMENT_STAGE)-telemetry-integration \
		cms-$(DEPLOYMENT_STAGE)-flink \
		cms-$(DEPLOYMENT_STAGE)-fleetwise \
		cms-$(DEPLOYMENT_STAGE)-simulation \
		cms-$(DEPLOYMENT_STAGE)-commands \
		cms-$(DEPLOYMENT_STAGE)-ws-fanout \
		cms-$(DEPLOYMENT_STAGE)-tco; do \
		status=$$(aws cloudformation describe-stacks --stack-name $$stack --query 'Stacks[0].StackStatus' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo "NOT_DEPLOYED"); \
		if [ "$$status" = "CREATE_COMPLETE" ] || [ "$$status" = "UPDATE_COMPLETE" ]; then \
			echo "├── $$stack: $(GREEN)✅ $$status$(NC)"; \
		elif [ "$$status" = "NOT_DEPLOYED" ]; then \
			echo "├── $$stack: $(YELLOW)⏳ NOT_DEPLOYED$(NC)"; \
		else \
			echo "├── $$stack: $(RED)❌ $$status$(NC)"; \
		fi; \
	done

clean-logs: ## Clean orphaned Flink log groups (run before deploy if CDK fails on existing log groups)
	@echo "$(BLUE)🧹 Cleaning orphaned Flink log groups...$(NC)"
	@for lg in $$(aws logs describe-log-groups --log-group-name-prefix "/aws/kinesis-analytics/cms-$(DEPLOYMENT_STAGE)-flink" --region $(AWS_REGION) --profile $(AWS_PROFILE) --query "logGroups[*].logGroupName" --output text 2>/dev/null); do \
		echo "  Deleting $$lg"; \
		aws logs delete-log-group --log-group-name "$$lg" --region $(AWS_REGION) --profile $(AWS_PROFILE) 2>/dev/null || true; \
	done
	@echo "$(GREEN)✅ Log groups cleaned$(NC)"

clean: ## Clean build artifacts
	@echo "$(BLUE)🧹 Cleaning build artifacts...$(NC)"
	@rm -rf cdk.out*
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)✅ Clean completed$(NC)"

seed-vehicle-lifecycle: ## Enrich vehicles with purchase data, depreciation, insurance, warranty, cost/mile
	@echo "$(YELLOW)Enriching vehicles with lifecycle data...$(NC)"
	@cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	python3 deployment/scripts/seed_vehicle_lifecycle.py
	@echo "$(GREEN)✅ Vehicle lifecycle data enriched$(NC)"

seed-service-warranty: ## Seed service history + warranty claims + DTCs with PDFs (invoices, work orders, claim docs)
	@echo "$(YELLOW)Generating service records, warranty claims, DTCs, and PDFs ($(or $(HISTORY_YEARS),2) years)...$(NC)"
	@cd $(CURDIR)/.. && DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	python3 services/simulation/generate_kb_data.py --region $(AWS_REGION)
	@echo "$(GREEN)✅ Service history, warranty claims, DTCs, and PDFs generated$(NC)"

seed-nhtsa-recalls: ## Fetch NHTSA recalls and match against fleet vehicles
	@echo "$(YELLOW)Fetching NHTSA recalls for fleet vehicles...$(NC)"
	@cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	python3 services/recall-integration/nhtsa_recall_poller.py
	@echo "$(GREEN)✅ NHTSA recalls fetched and matched$(NC)"

refresh-recalls-ui: seed-nhtsa-recalls ## Regenerate NHTSA recall data + rebuild/redeploy the UI bundle (delegates to ui-quick-deploy)
	@# WHY THIS DELEGATES INSTEAD OF DEPLOYING.
	@#
	@# `nhtsa_recall_poller.py` regenerates
	@# `modules/cms_ui/source/frontend/src/components/recall-warranty/nhtsaRecallData.ts`
	@# — an auto-generated FRONTEND SOURCE file, baked into the bundle at build
	@# time. So refreshing recalls is a pure asset change and needs a build plus an
	@# S3 sync plus a CloudFront invalidation. It does NOT need a CDK deploy, and
	@# until 2026-09-13 it did one anyway, which carried three defects:
	@#
	@#   1. `cdk deploy cms-{stage}-ui` with ONE of ui_stack.py's nineteen context
	@#      keys and no Federate creds in env. Verified by synth: that combination
	@#      succeeds with NO guard firing and emits
	@#      SupportedIdentityProviders: ['COGNITO'], no UserPoolIdentityProvider
	@#      resource, and absent Aliases / WebACLId / TrustedKeyGroups — i.e. it
	@#      removes federated sign-in, deletes the IdP, drops the custom domain and
	@#      its cert, detaches the WAF, and drops the edge auth gate. On staging,
	@#      which is the public production surface since 2026-09-02-env-collapse.
	@#      That is the 2026-08-11 outage mechanism verbatim (9 platform-admins
	@#      locked out, stack UPDATE_COMPLETE, no error anywhere).
	@#      `_require_client_idp_config` does not catch it: its condition is
	@#      `ui_custom_domain and not has_federated`, and this target passed
	@#      NEITHER, so the condition was False. The guard protects you when you
	@#      still have a domain but have lost the IdP — not when you have lost both.
	@#   2. `vite build --mode development` — a DEVELOPMENT-mode bundle shipped to
	@#      that same production surface.
	@#   3. bypassing `yarn build` also bypassed its postbuild
	@#      `substitute-gate-template.js`, so `build/error/403.html` shipped with
	@#      placeholder values — reintroducing exactly the drift that spec
	@#      2026-08-04-gate-403-substitution was written to fix.
	@#
	@# `ui-quick-deploy` fixes all three by construction: it runs the real
	@# `yarn build` (cleanup + vite build + gate substitution), scans build/ for
	@# internal hostnames BEFORE shipping, strips per-developer runtimeConfig
	@# files, syncs, invalidates CloudFront, and asserts the gate template landed.
	@# It touches no CloudFormation, so there is no context to lose.
	@#
	@# Delegating rather than duplicating is the point: this target went wrong
	@# because it copied a flag list that then drifted as phase1's grew. Copying
	@# ui-quick-deploy's body would set up the same failure again.
	@#
	@# See issues/2026-09-13-refresh-recalls-ui-deploys-ui-stack-context-less/.
	@echo "$(BLUE)🔁 Refreshing recall data in the UI bundle$(NC)"
	@echo "$(YELLOW)Recall data regenerated. Handing off to ui-quick-deploy for"
	@echo "         build + secret scan + S3 sync + CloudFront invalidation.$(NC)"
	$(MAKE) ui-quick-deploy DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) AWS_PROFILE=$(AWS_PROFILE)
	@echo "$(GREEN)✅ UI redeployed with fresh NHTSA recall data$(NC)"

seed-trip-history: ## Seed trip history from purchase date to now (matches odometer)
	@echo "$(YELLOW)Seeding trip history for all vehicles...$(NC)"
	@cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	python3 deployment/scripts/seed_trip_history.py
	@echo "$(GREEN)✅ Trip history seeded$(NC)"

seed-drivers: ## Seed driver records (75 drivers with CDL, safety scores, experience)
	@echo "$(YELLOW)Seeding driver records...$(NC)"
	@cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	python3 deployment/scripts/seed_drivers.py
	@echo "$(GREEN)✅ Drivers seeded$(NC)"

seed-driver-users: ## Create/update a VSA Cognito user for each active driver (idempotent)
	@echo "$(YELLOW)Syncing drivers → VSA Cognito users...$(NC)"
	@# Region-aware resolution: prefer the freshly-deployed cms-{stage}-ui
	@# stack's UserPoolId CFN output over the cdk.json hardcoded value.
	@# Closes the cross-region defect surfaced by clean-deploy run 17 (seed
	@# phase failed because the staging Cognito user pool did not exist
	@# in the secondary region).
	@# Issue: issues/2026-06-04-cms-vsa-pool-id-region-aware-fallback/
	@RESOLVED_POOL=$$(aws cloudformation describe-stacks \
	    --stack-name cms-$(DEPLOYMENT_STAGE)-ui \
	    --region $(AWS_REGION) --no-cli-pager \
	    --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" \
	    --output text 2>/dev/null) ; \
	  if [ -z "$$RESOLVED_POOL" ] || [ "$$RESOLVED_POOL" = "None" ]; then \
	    RESOLVED_POOL="$(VSA_USER_POOL_ID)" ; \
	    echo "  ⚠ ui stack not deployed; falling back to cdk.json vsaUserPoolId: $$RESOLVED_POOL" ; \
	  else \
	    echo "  ✓ resolved VSA pool from CFN: $$RESOLVED_POOL" ; \
	  fi ; \
	  cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	    VSA_USER_POOL_ID=$$RESOLVED_POOL \
	    python3 deployment/scripts/seed_driver_users.py
	@echo "$(GREEN)✅ Driver users synced$(NC)"

seed-demo-personas: ## Seed iOS demo persona users (Samantha Carter / Marcus Reyes / Priya Shah) into VSA Cognito (idempotent)
	@echo "$(YELLOW)Seeding iOS demo persona users...$(NC)"
	@cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	python3 deployment/scripts/seed_driver_users.py --demo-personas
	@echo "$(GREEN)✅ Demo persona users seeded$(NC)"

seed-all-demo-data: seed-generic-fleets seed-drivers seed-driver-users seed-fleet-enrollment seed-signal-catalog seed-event-catalog seed-vehicle-lifecycle seed-trip-history seed-service-warranty seed-nhtsa-recalls seed-fleetwise generate-and-upload-decoder-manifest seed-vehicle-models seed-vfo-actions seed-decision-journal ## Seed all demo data (granular scripts)
	@# seed-vehicle-models populates cms-{stage}-model-manifest with the canary-clean
	@# CMS-FLEET-MODEL default + engineer-persona BE6/BE07. The Fleet config "Default
	@# Vehicle Model" picker reads /api/v1/model-manifests; without this seed the
	@# picker errors / shows an empty Select on fresh deploys
	@# (issue 2026-06-15-fresh-deploy-model-manifest-empty).
	@echo "$(GREEN)✅ All demo data seeded$(NC)"

inject-everything: seed-fleetwise upload-decoder-manifest ## Run the enhanced injector + seed decoder manifest + campaigns in one pass. Writes: fleets/vehicles/drivers/trips/safety/maintenance/service-history/warranty/DTC/recalls/TCO/charging/snapshots/decoder-manifest/campaigns.
	@echo "$(BLUE)🚀 Running enhanced historical data injector (all-in-one)$(NC)"
	@echo "$(YELLOW)  Stage=$(DEPLOYMENT_STAGE) Region=$(AWS_REGION) Days=$(or $(INJECT_DAYS),730)$(NC)"
	@echo "$(YELLOW)  Flags (pre-reqs: drivers, fleet-enrollment, event-catalog, signal-catalog must already be seeded)$(NC)"
	@cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
		NUM_FLEETS=$(or $(NUM_FLEETS),5) VEHICLES_PER_FLEET=$(or $(VEHICLES_PER_FLEET),10) \
		USE_LOCATION_SERVICES=$(or $(USE_LOCATION_SERVICES),true) SELECTED_CITIES=$(or $(SELECTED_CITIES),1,2,3,4,5) \
		LOCATION_MAP_NAME=cms-$(DEPLOYMENT_STAGE)-ui-vehicle-map \
		LOCATION_ROUTE_CALCULATOR_NAME=cms-$(DEPLOYMENT_STAGE)-ui-route-calculator \
		LOCATION_PLACE_INDEX_NAME=cms-$(DEPLOYMENT_STAGE)-ui-place-index \
		PYTHONUNBUFFERED=1 \
		python3 -u services/simulation/enhanced_historical_data_injector.py \
			--days $(or $(INJECT_DAYS),730) \
			--region $(AWS_REGION) \
			$(INJECT_FLAGS)
	@echo "$(GREEN)✅ Injector completed$(NC)"

inject-derived-only: ## Quick rerun: refresh only derived aggregates (charging + TCO + snapshots) from existing DDB state.
	@echo "$(BLUE)🔄 Regenerating derived aggregates only$(NC)"
	@$(MAKE) inject-everything INJECT_FLAGS="--skip-warranty --skip-recalls"

inject-warranty-only: ## Quick rerun: refresh only service-history, warranty-claims, and DTC history.
	@echo "$(BLUE)🔄 Regenerating service/warranty/DTC only$(NC)"
	@$(MAKE) inject-everything INJECT_FLAGS="--skip-derived --skip-recalls"

inject-recalls-only: ## Quick rerun: refresh only NHTSA recalls.
	@echo "$(BLUE)🔄 Fetching NHTSA recalls only$(NC)"
	@$(MAKE) inject-everything INJECT_FLAGS="--skip-derived --skip-warranty"

# ─── bootstrap-demo: one command to seed a fresh environment end-to-end ───
preflight-demo: ## Validate prerequisites before seeding (stacks deployed, tables exist, deps installed)
	@cd $(CURDIR)/.. && AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
		python3 deployment/scripts/preflight_demo.py

verify-demo-data: ## Verify seeded data: DDB row counts, S3 PDFs, Fleet API endpoint health
	@cd $(CURDIR)/.. && AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
		python3 deployment/scripts/verify_demo_data.py

bootstrap-demo: preflight-demo ## ONE COMMAND: preflight + all catalogs + FWE seeds + enhanced injector + verification. Use after deploy-all.
	@echo ""
	@echo "$(BLUE)════════════════════════════════════════════════════════════════$(NC)"
	@echo "$(BLUE) CMS Demo Bootstrap$(NC)"
	@echo "$(BLUE)════════════════════════════════════════════════════════════════$(NC)"
	@echo ""
	@echo "$(YELLOW)Configuration:$(NC)"
	@echo "  DEPLOYMENT_STAGE      = $(DEPLOYMENT_STAGE)"
	@echo "  AWS_REGION            = $(AWS_REGION)"
	@echo "  AWS_PROFILE           = $(AWS_PROFILE)"
	@echo "  INJECT_DAYS           = $(or $(INJECT_DAYS),730)"
	@echo "  NUM_FLEETS            = $(or $(NUM_FLEETS),5)"
	@echo "  VEHICLES_PER_FLEET    = $(or $(VEHICLES_PER_FLEET),10)"
	@echo "  USE_LOCATION_SERVICES = $(or $(USE_LOCATION_SERVICES),true)"
	@echo ""
	@echo "$(BLUE)━━━ Step 1/6: Signal catalog ━━━$(NC)"
	@$(MAKE) seed-signal-catalog AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo ""
	@echo "$(BLUE)━━━ Step 2/6: Event catalog ━━━$(NC)"
	@$(MAKE) seed-event-catalog AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo ""
	@echo "$(BLUE)━━━ Step 3/6: Drivers ━━━$(NC)"
	@$(MAKE) seed-drivers AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo ""
	@echo "$(BLUE)━━━ Step 4/6: FWE decoder manifest + campaign ━━━$(NC)"
	@$(MAKE) upload-decoder-manifest AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@$(MAKE) seed-fleetwise AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo ""
	@echo "$(BLUE)━━━ Step 5/6: Enhanced historical injector ($(or $(INJECT_DAYS),730) days, $(or $(NUM_FLEETS),5) fleets x $(or $(VEHICLES_PER_FLEET),10) vehicles) ━━━$(NC)"
	@echo "$(YELLOW)  Writes: fleets, vehicles, trips, safety events, maintenance alerts,$(NC)"
	@echo "$(YELLOW)          service history, warranty claims, DTCs, NHTSA recalls,$(NC)"
	@echo "$(YELLOW)          IoT certificates, TCO rollups, charging sessions, location snapshots$(NC)"
	@echo "$(YELLOW)  ETA: 60-90 min with USE_LOCATION_SERVICES=true, 5-10 min without$(NC)"
	@cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
		NUM_FLEETS=$(or $(NUM_FLEETS),5) VEHICLES_PER_FLEET=$(or $(VEHICLES_PER_FLEET),10) \
		USE_LOCATION_SERVICES=$(or $(USE_LOCATION_SERVICES),true) SELECTED_CITIES=$(or $(SELECTED_CITIES),1,2,3,4,5) \
		LOCATION_MAP_NAME=cms-$(DEPLOYMENT_STAGE)-ui-vehicle-map \
		LOCATION_ROUTE_CALCULATOR_NAME=cms-$(DEPLOYMENT_STAGE)-ui-route-calculator \
		LOCATION_PLACE_INDEX_NAME=cms-$(DEPLOYMENT_STAGE)-ui-place-index \
		PYTHONUNBUFFERED=1 \
		python3 -u services/simulation/enhanced_historical_data_injector.py \
			--days $(or $(INJECT_DAYS),730) \
			--region $(AWS_REGION)
	@echo ""
	@echo "$(BLUE)━━━ Step 6/6: Fleet enrollment + PDF knowledge base ━━━$(NC)"
	@$(MAKE) seed-fleet-enrollment AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo ""
	@echo "$(YELLOW)  PDFs + fleet context markdown (required for VFO knowledge base RAG):$(NC)"
	@cd $(CURDIR)/.. && DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
		python3 services/simulation/generate_kb_data.py --region $(AWS_REGION) || \
		echo "$(YELLOW)  WARNING: PDF generation failed - dashboards still work, VFO RAG will be missing docs$(NC)"
	@echo ""
	@echo "$(BLUE)━━━ Post-seeding verification ━━━$(NC)"
	@$(MAKE) verify-demo-data AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo ""
	@echo "$(GREEN)════════════════════════════════════════════════════════════════$(NC)"
	@echo "$(GREEN) Demo bootstrap complete$(NC)"
	@echo "$(GREEN)════════════════════════════════════════════════════════════════$(NC)"
	@echo ""
	@CF_URL=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-ui --region $(AWS_REGION) --profile $(AWS_PROFILE) --query "Stacks[0].Outputs[?OutputKey=='CloudFrontURL'].OutputValue" --output text 2>/dev/null); \
	echo "$(YELLOW)Next steps:$(NC)"; \
	echo "  1. Visit the UI:   $$CF_URL"; \
	echo "  2. Sign in with:   FleetManager@example.com (password set via CMS_DEMO_DEFAULT_PASSWORD)"; \
	echo "  3. Every dashboard should populate with real data"; \
	echo ""; \
	echo "$(YELLOW)To re-seed portions later:$(NC)"; \
	echo "  make inject-derived-only    # TCO + charging + snapshots"; \
	echo "  make inject-warranty-only   # service-history + warranty + DTC"; \
	echo "  make inject-recalls-only    # NHTSA recalls"

# ===== Staging vs prod deploy abstraction (Group 3a) =====
#
# Wraps the existing phased-deploy targets (deploy-all etc.) with environment
# config sourced from deployment/config/{staging,prod}.env. Single AWS account
# (your-staging-account) two-region model: staging in us-west-2, prod in us-east-1.
# Region separation + IAM role separation + tag-based cost attribution provide
# the isolation layer.

.PHONY: staging-deploy prod-deploy tear-down-staging staging-status bootstrap-staging

staging-deploy: ## Deploy CMS to staging (us-west-2)
	@if [ ! -f config/staging.env ]; then echo 'config/staging.env missing'; exit 1; fi
	@# 2026-08-30 outage-repro fix: `set -a` auto-exports every var in
	@# config/staging.env, and load-federate-creds.sh fetches the Federate OIDC
	@# client from Secrets Manager and exports FEDERATE_CLIENT_ID/SECRET/ISSUER.
	@# Each recipe line runs in its own shell, so the load has to happen on
	@# EVERY line — otherwise a sub-make's cdk synth of cms-<stage>-ui produces
	@# a template WITHOUT the AmazonFederateIdP resource, CFN deletes the pool-
	@# level IdP, and every Federate user loses access. See
	@# issues/2026-08-30-federate-idp-guard-trust-without-cred/summary.md.
	@# The synth-time guard in aspects/provisioning_guards.py catches a missed
	@# load and fails synth loudly rather than silently deleting the IdP.
	@set -a; . config/staging.env; set +a; \
	  . scripts/load-federate-creds.sh; \
	  MODEL_ID=$(BEDROCK_AGENT_MODEL) REGION=$$AWS_REGION PROFILE=$(AWS_PROFILE) \
	  ./scripts/validate-bedrock-model.sh
	@set -a; . config/staging.env; set +a; \
	  . scripts/load-federate-creds.sh; \
	  AWS_REGION=$$AWS_REGION DEPLOYMENT_STAGE=$$DEPLOYMENT_STAGE \
	  $(MAKE) deploy-all
	@# phase-staging-demo-data: drivers (and any other simulator-hard-required seeds).
	@# Staging-only — prod-deploy intentionally does NOT chain this (real customers
	@# bring their own driver data). See phase-staging-demo-data target for rationale.
	@set -a; . config/staging.env; set +a; \
	  . scripts/load-federate-creds.sh; \
	  AWS_REGION=$$AWS_REGION DEPLOYMENT_STAGE=$$DEPLOYMENT_STAGE \
	  $(MAKE) phase-staging-demo-data

prod-deploy: ## Deploy CMS to prod (us-east-1) — requires confirmation unless CI=true
	@if [ ! -f config/prod.env ]; then echo 'config/prod.env missing'; exit 1; fi
	@if [ "$$CI" != "true" ]; then \
	  printf 'Confirm prod deploy [y/N]: '; \
	  read ans; \
	  if [ "$$ans" != "y" ]; then echo 'aborted'; exit 1; fi; \
	fi
	@set -a; . config/prod.env; set +a; \
	  . scripts/load-federate-creds.sh; \
	  MODEL_ID=$(BEDROCK_AGENT_MODEL) REGION=$$AWS_REGION PROFILE=$(AWS_PROFILE) \
	  ./scripts/validate-bedrock-model.sh
	@set -a; . config/prod.env; set +a; \
	  . scripts/load-federate-creds.sh; \
	  AWS_REGION=$$AWS_REGION DEPLOYMENT_STAGE=$$DEPLOYMENT_STAGE \
	  $(MAKE) deploy-all

tear-down-staging: ## Destroy ALL staging resources (irreversible) — requires explicit confirmation
	@if [ ! -f config/staging.env ]; then echo 'config/staging.env missing'; exit 1; fi
	@printf "Type 'destroy-staging' to confirm: "; \
	  read ans; \
	  if [ "$$ans" != "destroy-staging" ]; then echo 'aborted'; exit 1; fi
	@. config/staging.env; \
	  AWS_REGION=$$AWS_REGION DEPLOYMENT_STAGE=$$DEPLOYMENT_STAGE \
	  cdk destroy --all --force

staging-status: ## Show CDK stack status in staging us-west-2
	@. config/staging.env; \
	  AWS_REGION=$$AWS_REGION cdk list

bootstrap-staging: ## Idempotently bootstrap CDK Toolkit in staging us-west-2
	@. config/staging.env; \
	  AWS_REGION=$$AWS_REGION cdk bootstrap aws://$$(aws sts get-caller-identity --query Account --output text)/$$AWS_REGION

# OEM1 proto pipeline (delegates to services/connectors/oem1/Makefile)
.PHONY: protos-oem1
protos-oem1: ## Regenerate OEM1 gRPC Python bindings (run once per SDK version upgrade)
	$(MAKE) -C ../services/connectors/oem1 protos-oem1

# OEM1 seed + deploy targets (C1.1 of spec 2026-06-01-cms-oem1-transform-manifest-staging-e2e)
.PHONY: seed-vehicles-oem1 seed-manifest-oem1 deploy-connector-oem1 unseed-vehicles-oem1

seed-vehicles-oem1: ## Enumerate OEM1 fleet via Enrollment Status + vehicleData APIs; seed cms-staging-storage-vehicles + cms-staging-storage-fleet-enrollment
	@echo "✨ Seeding OEM1 vehicles for $(DEPLOYMENT_STAGE)..."
	@test -x .venv/bin/python3 || { echo "❌ deployment/.venv/bin/python3 missing — run 'python3 -m venv .venv && .venv/bin/pip install -r requirements.txt' first"; exit 1; }
	@AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) \
	.venv/bin/python3 ../services/connectors/oem1/seed_vehicles.py
	@echo "✅ OEM1 vehicles seeded"

unseed-vehicles-oem1: ## Backout: delete all OEM1 rows from vehicles + fleet-enrollment tables (safe to use if telemetry never materialises and you need to escalate to OEM1)
	@echo "🧹 Unseeding OEM1 vehicles for $(DEPLOYMENT_STAGE)..."
	@test -x .venv/bin/python3 || { echo "❌ deployment/.venv/bin/python3 missing"; exit 1; }
	@AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) \
	.venv/bin/python3 ../services/connectors/oem1/seed_vehicles.py --unseed
	@echo "✅ OEM1 vehicles unseeded"

seed-manifest-oem1: ## Upload validated oem1-transform.json to S3
	@echo "📦 Uploading OEM1 manifest..."
	@test -x .venv/bin/python3 || { echo "❌ deployment/.venv/bin/python3 missing — run 'python3 -m venv .venv && .venv/bin/pip install -r requirements.txt' first"; exit 1; }
	@AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) \
	.venv/bin/python3 ../services/connectors/oem1/seed_manifest.py
	@echo "✅ OEM1 manifest uploaded"

deploy-connector-oem1: ## CDK deploy ConnectorStack for OEM1 + post-deploy smoke test
	@echo "🚀 Deploying OEM1 connector..."
	@bash scripts/deploy_connector_oem1.sh
	@echo "✅ OEM1 connector deployed"

# Clean-deploy integration test (Group 3.2).
#
# Wraps `deployment/scripts/run_clean_deploy_test.sh`, which runs the
# full first-time-deployment harness: pre-flight → bootstrap → strict
# gate → deploy → seed → pytest e2e → teardown (trap) → audit (trap)
# → report.json. See `docs/DEPLOYMENT.md` (Group 5) for the full
# runbook.
#
# Default REGION = ap-northeast-1 (per `config/clean-deploy.env`).
# Override on the command line: `make clean-deploy-test REGION=eu-west-2`.
.PHONY: clean-deploy-test
clean-deploy-test: ## Clean-deploy integration test (default REGION=ap-northeast-1)
	@. config/clean-deploy.env; \
	  if [ -n "$(REGION)" ]; then \
	    AWS_REGION=$(REGION) ./scripts/run_clean_deploy_test.sh --region $(REGION); \
	  else \
	    ./scripts/run_clean_deploy_test.sh --region $$AWS_REGION; \
	  fi

# SOVD (Remote Diagnostics) post-deploy smoke test
# -----------------------------------------------------------------------
# smoke-sovd-staging: run the SOVD smoke test against the staging environment.
# smoke-sovd-prod:    run the SOVD smoke test against prod — requires ALLOW_PROD=1 guard.
#
# smoke-sovd-staging fires a synthetic SOVD read_dtcs command and asserts the
# DynamoDB commands row reaches SUCCEEDED within 15 s. Idempotent — each run
# uses a fresh correlation_id and cleans up the test row on exit.
#
# Usage:
#   make smoke-sovd-staging
#   ALLOW_PROD=1 make smoke-sovd-prod
.PHONY: smoke-sovd-staging smoke-sovd-prod

smoke-sovd-staging: ## SOVD post-deploy smoke test on staging — asserts read_dtcs SUCCEEDED within 15 s
	@bash scripts/smoke_sovd.sh staging

smoke-sovd-prod: ## SOVD post-deploy smoke test on prod — requires ALLOW_PROD=1
	@if [ "$${ALLOW_PROD:-0}" != "1" ]; then \
	  echo "ERROR: smoke-sovd-prod requires ALLOW_PROD=1 to prevent accidental prod invocations."; \
	  echo "  Run: ALLOW_PROD=1 make smoke-sovd-prod"; \
	  exit 1; \
	fi
	@bash scripts/smoke_sovd.sh prod


# Connected Services UI Stack — synth-only target
# -----------------------------------------------------------------------
# Synthesizes only the cms-{DEPLOYMENT_STAGE}-connected-services-ui stack
# in isolation so T1.5's test suite can verify the CDK template.
#
# GUARD_CTX_FLAGS is required: the domain-alias guard in app.py runs on
# every cdk synth regardless of target stack (Makefile:77-78), so the
# UI custom-domain context must always be carried.
#
# CLIENT_EXTRA_IDPS=cognito-only suppresses the Federate-credential guard in
# ui_stack.py (aspects/provisioning_guards.py) so this target can synth
# without AWS Secrets Manager access.  This is intentional for a synth-only
# target: the guard protects against a DEPLOY that drops the Federate IdP
# resource; it is not relevant when only synthesizing the
# connected-services-ui stack.  Modelled on the cognito-only guard note in
# the error message from aspects/provisioning_guards.py:791.
#
# The six connectedServicesUi* context keys below are either read from
# config/$(DEPLOYMENT_STAGE).env (if set) or from inline placeholder
# values. All placeholder values are RFC 2606 .invalid — they are never
# real AWS account IDs or internal hostnames.
#
# Usage:
#   make synth-connected-services DEPLOYMENT_STAGE=staging
#   make synth-connected-services DEPLOYMENT_STAGE=dev
.PHONY: synth-connected-services
synth-connected-services: ## Synth the Connected Services UI stack (T2.1)
	@echo "Building the Connected Services frontend bundle..."
	@# Included in synth, not just deploy: the bundle is a CDK asset, so a stale
	@# `dist/` also makes `cdk diff` misleading — it reports no asset-hash change
	@# while the source has in fact changed. Same rationale as the deploy target.
	@cd $(CURDIR)/../modules/connected_services_ui && npm run build
	@echo "Frontend build complete"
	@set -a; [ -f config/$(DEPLOYMENT_STAGE).env ] && . config/$(DEPLOYMENT_STAGE).env; set +a; \
	COGNITO_USER_POOL_ID=$${COGNITO_USER_POOL_ID:-us-west-2_SYNTH}; \
	COGNITO_CLIENT_ID=$${COGNITO_CLIENT_ID:-synthclientid0001}; \
	COGNITO_DOMAIN=$${COGNITO_DOMAIN:-placeholder.example.invalid}; \
	CONNECTED_SERVICES_API_ENDPOINT=$${CONNECTED_SERVICES_API_ENDPOINT:-https://api.example.invalid}; \
	CONNECTED_SERVICES_UI_CALLBACK_ORIGIN=$${CONNECTED_SERVICES_UI_CALLBACK_ORIGIN:-https://connected-services.example.invalid}; \
	CONNECTED_SERVICES_UI_CUSTOM_DOMAIN=$${CONNECTED_SERVICES_UI_CUSTOM_DOMAIN:-}; \
	CONNECTED_SERVICES_UI_CUSTOM_DOMAIN_CERT_ARN=$${CONNECTED_SERVICES_UI_CUSTOM_DOMAIN_CERT_ARN:-}; \
	CONNECTED_SERVICES_UI_HOSTED_ZONE_ID=$${CONNECTED_SERVICES_UI_HOSTED_ZONE_ID:-}; \
	WAF_WEB_ACL_ARN=$${WAF_WEB_ACL_ARN:-}; \
	CONNECTED_SERVICES_UI_ENABLED=$${CONNECTED_SERVICES_UI_ENABLED:-false}; \
	CONNECTED_SERVICES_UI_SUBSCRIPTIONS_API_ENDPOINT=$${CONNECTED_SERVICES_UI_SUBSCRIPTIONS_API_ENDPOINT:-}; \
	CONNECTED_SERVICES_UI_SIMULATION_API_ENDPOINT=$${CONNECTED_SERVICES_UI_SIMULATION_API_ENDPOINT:-}; \
	CONNECTED_SERVICES_UI_DATA_PROCESSING_API_ENDPOINT=$${CONNECTED_SERVICES_UI_DATA_PROCESSING_API_ENDPOINT:-}; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text 2>/dev/null || echo "123456789012") \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	CLIENT_EXTRA_IDPS=cognito-only \
	DEPLOY_CONNECTED_SERVICES_UI=true \
	cdk synth cms-$(DEPLOYMENT_STAGE)-connected-services-ui \
	  $(GUARD_CTX_FLAGS) \
	  -c connectedServicesUiCognitoUserPoolId=$$COGNITO_USER_POOL_ID \
	  -c connectedServicesUiCognitoClientId=$$COGNITO_CLIENT_ID \
	  -c connectedServicesUiCognitoDomain=$$COGNITO_DOMAIN \
	  -c connectedServicesUiApiEndpoint=$$CONNECTED_SERVICES_API_ENDPOINT \
	  -c connectedServicesUiCallbackOrigin=$$CONNECTED_SERVICES_UI_CALLBACK_ORIGIN \
	  -c connectedServicesUiCustomDomain=$$CONNECTED_SERVICES_UI_CUSTOM_DOMAIN \
	  -c connectedServicesUiCustomDomainCertArn=$$CONNECTED_SERVICES_UI_CUSTOM_DOMAIN_CERT_ARN \
	  -c connectedServicesUiHostedZoneId=$$CONNECTED_SERVICES_UI_HOSTED_ZONE_ID \
	  -c wafWebAclArn=$$WAF_WEB_ACL_ARN \
	  -c connectedServicesUiEnabled=$$CONNECTED_SERVICES_UI_ENABLED \
	  -c connectedServicesUiSubscriptionsApiEndpoint=$$CONNECTED_SERVICES_UI_SUBSCRIPTIONS_API_ENDPOINT \
	  -c connectedServicesUiSimulationApiEndpoint=$$CONNECTED_SERVICES_UI_SIMULATION_API_ENDPOINT \
	  -c connectedServicesUiDataProcessingApiEndpoint=$$CONNECTED_SERVICES_UI_DATA_PROCESSING_API_ENDPOINT \
	  --quiet

.PHONY: deploy-subscriptions
deploy-subscriptions: ## Deploy the Connected Services subscription plane (cms-{stage}-subscriptions).
	@# Added 2026-09-20. This stack had NO Makefile target despite being deployed
	@# and carrying the /simulate/vehicles + /simulate/start Lambdas, so every
	@# deploy of it was a bespoke `cdk deploy` incantation — the hand-waved path
	@# ~/.kiro/steering/deploy-validation.md § "Deploy Discovery" exists to stop.
	@#
	@# --exclusively is LOAD-BEARING, not tidiness. Without it CDK also deploys
	@# dependency stacks, and those DO read context keys this target does not
	@# thread. That is the mechanism behind the 2026-08-11 outage and its
	@# 2026-09-15 staging repeat: a narrow deploy silently reaching cms-{stage}-ui
	@# without its Federate/WAF/custom-domain context and dropping the pool-level
	@# IdP. Precedent: commit ae1561b9 added --exclusively to deploy-commands for
	@# exactly this. subscriptions_stack.py itself reads NO try_get_context keys
	@# (verified), so it needs none of its own — GUARD_CTX_FLAGS is passed only to
	@# satisfy app.py's synth-time provisioning guards.
	@#
	@# load-federate-creds.sh is likewise NOT optional: `cdk deploy X` still
	@# SYNTHS the whole app, so ui_stack's _require_client_idp_config runs even
	@# though this target deploys only the subscription plane. Without the creds
	@# it fails closed and the target cannot run at all — which is exactly what
	@# happened on the first attempt here. The helper is a no-op when
	@# FEDERATE_OIDC_SECRET_ID is unset, so cognito-only stages are unaffected.
	@# Do NOT 'fix' that failure with CLIENT_EXTRA_IDPS=cognito-only: staging DOES
	@# have Federate, and asserting otherwise is how the IdP gets deleted.
	@set -a; [ -f config/$(DEPLOYMENT_STAGE).env ] && . config/$(DEPLOYMENT_STAGE).env; set +a; \
	. scripts/load-federate-creds.sh; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	DEPLOY_SUBSCRIPTIONS=true \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-subscriptions \
	  --exclusively \
	  $(GUARD_CTX_FLAGS) \
	  --require-approval never
	@echo "$(GREEN)✅ Subscription plane deployed$(NC)"

.PHONY: diff-subscriptions
diff-subscriptions: ## Dry-run the subscription-plane deploy (same flags, cdk diff).
	@set -a; [ -f config/$(DEPLOYMENT_STAGE).env ] && . config/$(DEPLOYMENT_STAGE).env; set +a; \
	. scripts/load-federate-creds.sh; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	DEPLOY_SUBSCRIPTIONS=true \
	cdk diff cms-$(DEPLOYMENT_STAGE)-subscriptions \
	  --exclusively \
	  $(GUARD_CTX_FLAGS)

deploy-connected-services: ## Deploy the Connected Services UI stack (T4.1). Custom-domain triplet must be set in staging.env.
	@echo "Building the Connected Services frontend bundle..."
	@# The stack ships `Source.asset(".../connected_services_ui/dist")`, and `dist/`
	@# is gitignored — so without this step the deploy publishes whatever bundle
	@# happens to be on the operator's disk, corresponding to no particular commit.
	@# That is not hypothetical: the 2026-09-12 deploy shipped a 5-day-old bundle
	@# containing none of T3.5/T3.6, while `runtime-config.js` (Source.data,
	@# generated at synth) updated correctly and made the deploy look successful.
	@# See issues/2026-09-13-cs-ui-deploy-ships-stale-dist-no-build-step/.
	@# Precedent: `refresh-recalls-ui` above builds before redeploying for the
	@# same reason.
	@cd $(CURDIR)/../modules/connected_services_ui && npm run build
	@echo "Frontend build complete"
	@set -a; [ -f config/$(DEPLOYMENT_STAGE).env ] && . config/$(DEPLOYMENT_STAGE).env; set +a; \
	COGNITO_USER_POOL_ID=$${COGNITO_USER_POOL_ID:?COGNITO_USER_POOL_ID must be set for deploy}; \
	COGNITO_CLIENT_ID=$${COGNITO_CLIENT_ID:?COGNITO_CLIENT_ID must be set for deploy}; \
	COGNITO_DOMAIN=$${COGNITO_DOMAIN:?COGNITO_DOMAIN must be set for deploy}; \
	CONNECTED_SERVICES_API_ENDPOINT=$${CONNECTED_SERVICES_API_ENDPOINT:-https://api.example.invalid}; \
	CONNECTED_SERVICES_UI_CALLBACK_ORIGIN=$${CONNECTED_SERVICES_UI_CALLBACK_ORIGIN:?CONNECTED_SERVICES_UI_CALLBACK_ORIGIN must be set for deploy}; \
	CONNECTED_SERVICES_UI_CUSTOM_DOMAIN=$${CONNECTED_SERVICES_UI_CUSTOM_DOMAIN:-}; \
	CONNECTED_SERVICES_UI_CUSTOM_DOMAIN_CERT_ARN=$${CONNECTED_SERVICES_UI_CUSTOM_DOMAIN_CERT_ARN:-}; \
	CONNECTED_SERVICES_UI_HOSTED_ZONE_ID=$${CONNECTED_SERVICES_UI_HOSTED_ZONE_ID:-}; \
	WAF_WEB_ACL_ARN=$${WAF_WEB_ACL_ARN:-}; \
	CONNECTED_SERVICES_UI_ENABLED=$${CONNECTED_SERVICES_UI_ENABLED:-false}; \
	CONNECTED_SERVICES_UI_SUBSCRIPTIONS_API_ENDPOINT=$${CONNECTED_SERVICES_UI_SUBSCRIPTIONS_API_ENDPOINT:-}; \
	CONNECTED_SERVICES_UI_SIMULATION_API_ENDPOINT=$${CONNECTED_SERVICES_UI_SIMULATION_API_ENDPOINT:-}; \
	CONNECTED_SERVICES_UI_DATA_PROCESSING_API_ENDPOINT=$${CONNECTED_SERVICES_UI_DATA_PROCESSING_API_ENDPOINT:-}; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	CLIENT_EXTRA_IDPS=cognito-only \
	DEPLOY_CONNECTED_SERVICES_UI=true \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-connected-services-ui \
	  $(GUARD_CTX_FLAGS) \
	  -c connectedServicesUiCognitoUserPoolId=$$COGNITO_USER_POOL_ID \
	  -c connectedServicesUiCognitoClientId=$$COGNITO_CLIENT_ID \
	  -c connectedServicesUiCognitoDomain=$$COGNITO_DOMAIN \
	  -c connectedServicesUiApiEndpoint=$$CONNECTED_SERVICES_API_ENDPOINT \
	  -c connectedServicesUiCallbackOrigin=$$CONNECTED_SERVICES_UI_CALLBACK_ORIGIN \
	  -c connectedServicesUiCustomDomain=$$CONNECTED_SERVICES_UI_CUSTOM_DOMAIN \
	  -c connectedServicesUiCustomDomainCertArn=$$CONNECTED_SERVICES_UI_CUSTOM_DOMAIN_CERT_ARN \
	  -c connectedServicesUiHostedZoneId=$$CONNECTED_SERVICES_UI_HOSTED_ZONE_ID \
	  -c wafWebAclArn=$$WAF_WEB_ACL_ARN \
	  -c connectedServicesUiEnabled=$$CONNECTED_SERVICES_UI_ENABLED \
	  -c connectedServicesUiSubscriptionsApiEndpoint=$$CONNECTED_SERVICES_UI_SUBSCRIPTIONS_API_ENDPOINT \
	  -c connectedServicesUiSimulationApiEndpoint=$$CONNECTED_SERVICES_UI_SIMULATION_API_ENDPOINT \
	  -c connectedServicesUiDataProcessingApiEndpoint=$$CONNECTED_SERVICES_UI_DATA_PROCESSING_API_ENDPOINT \
	  --require-approval never --profile $(AWS_PROFILE)

