# CMS Simplified Deployment Makefile
.PHONY: help install bootstrap deploy deploy-all clean clean-logs infrastructure check-docker check-bootstrap \
	build-ui build-commands data-processing \
	phase1 phase2 phase3 phase3b phase4 phase5 phase-foundation phase-streaming phase-seeds phase-services \
	deploy-simulation deploy-commands deploy-ws-fanout deploy-tco deploy-fleetwise deploy-flink deploy-bedrock-agents deploy-connector \
	configure-msk configure-flink configure-pipeline validate-flink-deployment \
	regenerate-runtime-config status \
	seed-all-demo-data seed-drivers seed-driver-users seed-fleet-enrollment seed-signal-catalog seed-event-catalog \
	seed-vehicle-lifecycle seed-trip-history seed-service-warranty seed-nhtsa-recalls seed-fleetwise seed-manifest \
	seed-vfo-actions seed-decision-journal upload-decoder-manifest sync-manifests \
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

# Configuration
AWS_REGION ?= $(shell aws configure get region --profile $(AWS_PROFILE) 2>/dev/null || echo "us-west-2")
DEPLOYMENT_STAGE ?= $(shell echo $${DEPLOYMENT_STAGE:-dev})

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
	@echo "    make phase-services    - simulation + commands + ws-fanout + tco"
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
	@echo "    make deploy-simulation - Simulation Service (Docker)"
	@echo "    make deploy-commands   - Remote Commands (Lambda + API GW + IoT Rule)"
	@echo "    make deploy-ws-fanout  - WebSocket Fanout (Docker, Kafka → WS bridge)"
	@echo "    make deploy-tco        - TCO Optimization (fleet cost intelligence)"
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
	@echo "  make seed-vfo-actions    - VFO action queue (cross-domain recommendations)"
	@echo "  make seed-decision-journal - VFO decision journal (autonomous decisions)"
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

check-docker: ## Verify Docker daemon is running (required for ECS image builds)
	@if ! docker info >/dev/null 2>&1; then \
		echo "$(RED)❌ Docker daemon is not running.$(NC)"; \
		echo "$(YELLOW)Start Docker Desktop (open -a Docker) or your container runtime, wait for it to be ready, then retry.$(NC)"; \
		exit 1; \
	fi
	@echo "$(GREEN)✅ Docker daemon is running$(NC)"

build-ui: ## Build the React frontend application
	@echo "$(BLUE)🔨 Building React frontend...$(NC)"
	@cd ../modules/cms_ui/source/frontend && yarn install && yarn build
	@echo "$(GREEN)✅ Frontend build completed$(NC)"

ui-quick-deploy: ## Build UI + sync to S3 + invalidate CloudFront (~30s loop, no CDK)
	@# Fast path for UI-only changes. Bypasses CDK entirely:
	@#   1. yarn build the frontend  (~20-30s)
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
	@cd ../modules/cms_ui/source/frontend && yarn install --silent && yarn build
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
	echo "  $(BLUE)$$CF_URL$(NC)"

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
	cdk deploy cms-$(DEPLOYMENT_STAGE)-data-processing --require-approval never --profile $(AWS_PROFILE)
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

phase1: build-ui ## Phase 1: Fleet Manager Interface (IoT, Storage, UI, Lambda, Cognito, S3)
	@echo "$(BLUE)🚀 Phase 1: Fleet Manager Interface$(NC)"
	@echo "Deploying: IoT Core, DynamoDB, S3, Lambda, Cognito, CloudFront UI"
	@echo "$(YELLOW)⚠️  Checking for existing resources...$(NC)"
	# Tables are now managed by CloudFormation - skip cleanup
	@echo "$(GREEN)✅ Using CloudFormation-managed tables$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-storage cms-$(DEPLOYMENT_STAGE)-iot cms-$(DEPLOYMENT_STAGE)-ui --require-approval never --profile $(AWS_PROFILE)
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
	cdk deploy cms-$(DEPLOYMENT_STAGE)-msk --require-approval never --profile $(AWS_PROFILE)
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
	cdk deploy cms-$(DEPLOYMENT_STAGE)-telemetry-integration --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Phase 3b Complete: MSK configured with telemetry integration stack$(NC)"

phase4: ## Phase 4: Telemetry Pipeline 3 (Flink Deployment)
	@echo "$(BLUE)🚀 Phase 4: Telemetry Pipeline 3 (Flink Deployment)$(NC)"
	@echo "Deploying Flink applications for real-time telemetry processing"
	@echo "$(BLUE)Building Flink JAR...$(NC)"
	@cd ../modules/flink && ./build.sh
	@echo "$(BLUE)Deploying Flink stack...$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-flink --require-approval never --profile $(AWS_PROFILE)
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
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) DEPLOY_FLEETWISE=true AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-fleetwise --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ FleetWise integration deployed$(NC)"

deploy-connect: ## Deploy Amazon Connect contact center (queues, flows, Customer Profiles)
	@echo "$(BLUE)📞 Deploying Amazon Connect Stack$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) DEPLOY_CONNECT=true AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-connect --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Amazon Connect deployed$(NC)"
	@echo "$(YELLOW)Next: run 'python3 scripts/wire-connect-to-tenant.py' in the CVX repo to wire outputs into tenant config$(NC)"

deploy-tco: ## Deploy TCO Optimization stack (Fleet cost intelligence + agentic optimization)
	@echo "$(BLUE)💰 Deploying TCO Optimization Stack$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) DEPLOY_TCO=true AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-tco --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ TCO Optimization deployed$(NC)"

deploy-simulation: check-docker ## Deploy Simulation Service (Lambda + API Gateway + ECS Fargate)
	@echo "$(BLUE)🎮 Deploying Simulation Service$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) DEPLOY_SIMULATION=true AWS_PROFILE=$(AWS_PROFILE) \
	AWS_DEFAULT_REGION=$(AWS_REGION) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-simulation --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Simulation service deployed$(NC)"

build-commands: ## Build commands Lambda package (.build/)
	@echo "$(BLUE)🔨 Building commands Lambda package$(NC)"
	@bash ../services/commands/build.sh
	@echo "$(GREEN)✅ Commands Lambda built$(NC)"

deploy-commands: ## Deploy Remote Commands (Lambda + API Gateway + IoT Rule)
	@echo "$(BLUE)🎮 Deploying Remote Commands$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	AWS_DEFAULT_REGION=$(AWS_REGION) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-commands --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Remote commands deployed$(NC)"
	@echo "$(BLUE)📋 Commands API:$(NC)"
	@CMD_URL=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-commands --query 'Stacks[0].Outputs[?OutputKey==`CommandsApiUrl`].OutputValue' --output text --profile $(AWS_PROFILE) --region $(AWS_REGION) 2>/dev/null || echo "Not available"); \
	echo "└── API URL: $$CMD_URL"

# Bedrock agents config. Override via `make deploy-bedrock-agents BEDROCK_AGENT_MODEL=...`.
BEDROCK_AGENT_MODEL ?= us.anthropic.claude-sonnet-4-6
BEDROCK_AGENT_KB_BUCKET ?= cms-$(DEPLOYMENT_STAGE)-vfo-knowledge-base

deploy-bedrock-agents: ## Deploy Bedrock multi-agent stack (4 specialists + supervisor + vfo-tools Lambda)
	@echo "$(BLUE)🤖 Deploying Bedrock Agents Stack$(NC)"
	@echo "   Model:     $(BEDROCK_AGENT_MODEL)"
	@echo "   KB bucket: $(BEDROCK_AGENT_KB_BUCKET)"
	@echo "   Region:    $(AWS_REGION)"
	@echo "   Stage:     $(DEPLOYMENT_STAGE)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	DEPLOY_BEDROCK_AGENTS=true DEPLOY_BEDROCK_TOOLS=true \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-bedrock-agents \
		-c bedrockAgentModel=$(BEDROCK_AGENT_MODEL) \
		-c bedrockAgentKbBucket=$(BEDROCK_AGENT_KB_BUCKET) \
		-c bedrockAgentDeployTools=true \
		--require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Bedrock agents deployed$(NC)"
	@echo "$(BLUE)📋 Primary agent (supervisor):$(NC)"
	@AGENT_ID=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-bedrock-agents --query 'Stacks[0].Outputs[?OutputKey==`PrimaryAgentId`].OutputValue' --output text --profile $(AWS_PROFILE) --region $(AWS_REGION) 2>/dev/null || echo "Not available"); \
	ALIAS_ID=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-bedrock-agents --query 'Stacks[0].Outputs[?OutputKey==`PrimaryAliasId`].OutputValue' --output text --profile $(AWS_PROFILE) --region $(AWS_REGION) 2>/dev/null || echo "Not available"); \
	echo "├── Agent ID:  $$AGENT_ID"; \
	echo "└── Alias ID:  $$ALIAS_ID"
	@echo ""
	@echo "$(YELLOW)➡  Next: redeploy UI with \`-c bedrockAgentsStackName=cms-$(DEPLOYMENT_STAGE)-bedrock-agents\` to wire agent IDs via Fn::ImportValue.$(NC)"

seed-fleetwise: ## Seed decoder manifest + default CAN campaign + click-to-assign templates (GPS, safety, UDS-DTC)
	@echo "$(BLUE)📦 Seeding decoder manifest and campaign templates$(NC)"
	@source .venv/bin/activate && \
	AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) \
	python3 scripts/seed_decoder_and_campaign.py
	@echo "$(GREEN)✅ Decoder manifest and campaign templates seeded (includes uds-dtc-polling)$(NC)"

seed-vehicle-models: ## Seed FleetWise Vehicle Models (CMS-FLEET-MODEL default + Acme Motors BE6/BE07)
	@echo "$(BLUE)📦 Seeding vehicle model manifests$(NC)"
	@source .venv/bin/activate && \
	AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) \
	python3 scripts/seed_model_manifests.py
	@echo "$(GREEN)✅ Vehicle models seeded (idempotent — safe to re-run)$(NC)"

upload-decoder-manifest: ## Upload DecoderManifest.bin to Flink config bucket
	@echo "$(BLUE)📦 Uploading DecoderManifest.bin to Flink config bucket$(NC)"
	@BUCKET=$$(aws s3 ls --profile $(AWS_PROFILE) | grep "flink.*jar" | awk '{print $$3}' | grep $(AWS_REGION) | head -1); \
	if [ -z "$$BUCKET" ]; then \
		BUCKET=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-flink \
			--query 'Stacks[0].Outputs[?OutputKey==`FlinkJarBucketOutput`].OutputValue' \
			--output text --profile $(AWS_PROFILE) --region $(AWS_REGION)); \
	fi; \
	echo "  Bucket: $$BUCKET"; \
	aws s3 cp ../services/simulation/fwe_config/persistency/DecoderManifest.bin \
		s3://$$BUCKET/fwe-config/DecoderManifest.bin \
		--profile $(AWS_PROFILE) --region $(AWS_REGION); \
	echo "$(GREEN)✅ DecoderManifest.bin uploaded to s3://$$BUCKET/fwe-config/$(NC)"

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

seed-vfo-actions: ## Seed VFO unified action queue (cross-domain recommendations for Fleet Command Center)
	@echo "$(BLUE)🎯 Seeding VFO action queue$(NC)"
	@AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
		python3 scripts/seed_vfo_actions.py
	@echo "$(GREEN)✅ VFO action queue seeded$(NC)"

seed-decision-journal: ## Seed VFO decision journal (autonomous decision records)
	@echo "$(BLUE)📓 Seeding VFO decision journal$(NC)"
	@AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
		python3 scripts/seed_decision_journal.py
	@echo "$(GREEN)✅ VFO decision journal seeded$(NC)"

configure-flink: ## Configure Flink applications
	@echo "$(BLUE)🔧 Configuring Flink applications...$(NC)"
	@echo "Building JAR from source and updating applications with JAR file, IAM authentication, and VPC configuration..."
	@echo "$(BLUE)🔨 Building Flink JAR from source...$(NC)"
	@cd ../modules/flink && \
	export JAVA_HOME=/opt/homebrew/opt/openjdk@11 && \
	mvn clean package -DskipTests -q
	@echo "$(GREEN)✅ JAR built successfully$(NC)"
	@echo "$(BLUE)📤 Uploading JAR to S3...$(NC)"
	@BUCKET_NAME=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-flink --query 'Stacks[0].Outputs[?OutputKey==`FlinkJarBucketOutput`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	cd ../modules/flink && \
	aws s3 cp target/cms-telemetry-processor-1.0.0.jar s3://$$BUCKET_NAME/jars/cms-telemetry-processor-1.0.0.zip --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ JAR uploaded to S3$(NC)"
	@BUCKET_NAME=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-flink --query 'Stacks[0].Outputs[?OutputKey==`FlinkJarBucketOutput`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	MSK_CLUSTER_ARN=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKClusterArn`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	VPC_ID=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKVpcId`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	SUBNET_IDS=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKPrivateSubnetIds`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	SG_ID=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKSecurityGroupId`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	BOOTSTRAP_SERVERS=$$(aws kafka get-bootstrap-brokers --cluster-arn $$MSK_CLUSTER_ARN --query 'BootstrapBrokerStringSaslIam' --output text --profile $(AWS_PROFILE)); \
	SUBNET1=$$(echo $$SUBNET_IDS | cut -d',' -f1); \
	SUBNET2=$$(echo $$SUBNET_IDS | cut -d',' -f2); \
	TELEMETRY_TABLE=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-storage --query 'Stacks[0].Outputs[?OutputKey==`TelemetryTableName`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	TRIPS_TABLE=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-storage --query 'Stacks[0].Outputs[?OutputKey==`TripsTableName`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	SAFETY_TABLE=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-storage --query 'Stacks[0].Outputs[?OutputKey==`SafetyEventsTableName`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	MAINTENANCE_TABLE=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-storage --query 'Stacks[0].Outputs[?OutputKey==`MaintenanceEventsTableName`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	VEHICLES_TABLE=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-storage --query 'Stacks[0].Outputs[?OutputKey==`VehiclesTableName`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	REDIS_ENDPOINT=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`RedisEndpoint`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	echo '{"ApplicationCodeConfigurationUpdate":{"CodeContentUpdate":{"S3ContentLocationUpdate":{"BucketARNUpdate":"arn:aws:s3:::'$$BUCKET_NAME'","FileKeyUpdate":"jars/cms-telemetry-processor-1.0.0.zip"}},"CodeContentTypeUpdate":"ZIPFILE"}}' > /tmp/flink-jar-update.json; \
	aws kinesisanalyticsv2 list-applications --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationSummaries[?contains(ApplicationName, `cms-$(DEPLOYMENT_STAGE)-flink`)].ApplicationName' --output text | tr '\t' '\n' | while read app; do \
		if [ -n "$$app" ]; then \
			echo "Configuring application: $$app"; \
			EXTRA_PROPS=""; \
			case "$$app" in \
				*simulator-preprocessor) \
					PROCESSOR_TYPE="SimulatorPreprocessor"; \
					GROUP_ID="simulator-preprocessor"; \
					TABLE_NAME="$$TELEMETRY_TABLE"; \
					;; \
				*event-driven-telemetry-processor) \
					PROCESSOR_TYPE="EventDrivenTelemetryProcessor"; \
					GROUP_ID="cms-event-driven-telemetry-processor-consumer"; \
					TABLE_NAME="$$VEHICLES_TABLE"; \
					EXTRA_PROPS=",\"FLEET_ENROLLMENT_TABLE\":\"cms-$(DEPLOYMENT_STAGE)-storage-fleet-enrollment\""; \
					;; \
				*telemetry-enhanced-final) \
					PROCESSOR_TYPE="TelemetryDataProcessor"; \
					GROUP_ID="cms-telemetry-enhanced-processor-consumer"; \
					TABLE_NAME="$$TELEMETRY_TABLE"; \
					;; \
				*trip-processor) \
					PROCESSOR_TYPE="TripProcessor"; \
					GROUP_ID="cms-trip-processor-consumer"; \
					TABLE_NAME="$$TRIPS_TABLE"; \
					;; \
				*safety-processor) \
					PROCESSOR_TYPE="SafetyProcessor"; \
					GROUP_ID="cms-safety-processor-consumer"; \
					TABLE_NAME="$$SAFETY_TABLE"; \
					EXTRA_PROPS=",\"safety.table.name\":\"$$SAFETY_TABLE\",\"event.catalog.table\":\"cms-$(DEPLOYMENT_STAGE)-event-catalog\",\"signal.catalog.table\":\"cms-$(DEPLOYMENT_STAGE)-signal-catalog\""; \
					;; \
				*maintenance-processor) \
					PROCESSOR_TYPE="MaintenanceProcessor"; \
					GROUP_ID="cms-maintenance-processor-consumer"; \
					TABLE_NAME="$$MAINTENANCE_TABLE"; \
					;; \
				*fw-telemetry-processor) \
					PROCESSOR_TYPE="FWTelemetryProcessor"; \
					GROUP_ID="fw-telemetry-processor"; \
					TABLE_NAME="$$VEHICLES_TABLE"; \
					;; \
				*oem-telemetry-processor) \
					PROCESSOR_TYPE="OEMTelemetryProcessor"; \
					GROUP_ID="oem-telemetry-processor"; \
					TABLE_NAME="$$VEHICLES_TABLE"; \
					;; \
				*campaign-sync-processor) \
					PROCESSOR_TYPE="CampaignSyncProcessor"; \
					GROUP_ID="campaign-sync-processor"; \
					TABLE_NAME="$$VEHICLES_TABLE"; \
					IOT_ENDPOINT=$$(aws iot describe-endpoint --endpoint-type iot:Data-ATS --query 'endpointAddress' --output text --profile $(AWS_PROFILE) --region $(AWS_REGION)); \
					EXTRA_PROPS=",\"campaigns.table\":\"cms-$(DEPLOYMENT_STAGE)-campaigns\",\"fwe.config.bucket\":\"$$BUCKET_NAME\",\"iot.endpoint\":\"$$IOT_ENDPOINT\",\"input.topic\":\"fw-checkin\""; \
					;; \
				*geofence-processor) \
					PROCESSOR_TYPE="GeofenceProcessor"; \
					GROUP_ID="geofence-processor-group"; \
					TABLE_NAME="$$VEHICLES_TABLE"; \
					EXTRA_PROPS=",\"GEOFENCE_TABLE\":\"cms-$(DEPLOYMENT_STAGE)-storage-geofences\",\"safety.table.name\":\"$$SAFETY_TABLE\""; \
					;; \
				*) \
					echo "Unknown application type: $$app"; \
					continue; \
					;; \
			esac; \
			echo "{\"EnvironmentPropertyUpdates\":{\"PropertyGroups\":[{\"PropertyGroupId\":\"consumer.config.0\",\"PropertyMap\":{\"PROCESSOR_TYPE\":\"$$PROCESSOR_TYPE\",\"bootstrap.servers\":\"$$BOOTSTRAP_SERVERS\",\"security.protocol\":\"SASL_SSL\",\"sasl.mechanism\":\"AWS_MSK_IAM\",\"sasl.jaas.config\":\"software.amazon.msk.auth.iam.IAMLoginModule required;\",\"sasl.client.callback.handler.class\":\"software.amazon.msk.auth.iam.IAMClientCallbackHandler\",\"auto.offset.reset\":\"earliest\",\"enable.auto.commit\":\"false\",\"group.id\":\"$$GROUP_ID\",\"aws.region\":\"$(AWS_REGION)\",\"TABLE_NAME\":\"$$TABLE_NAME\",\"REDIS_ENDPOINT\":\"$$REDIS_ENDPOINT\"$$EXTRA_PROPS}}]}}" > /tmp/flink-env-update-$$app.json; \
			CURRENT_VERSION=$$(aws kinesisanalyticsv2 describe-application --application-name "$$app" --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationDetail.ApplicationVersionId' --output text); \
			aws kinesisanalyticsv2 update-application --application-name "$$app" --current-application-version-id $$CURRENT_VERSION --region $(AWS_REGION) --profile $(AWS_PROFILE) --application-configuration-update file:///tmp/flink-env-update-$$app.json >/dev/null 2>&1 && echo "✅ Updated env properties for $$app ($$PROCESSOR_TYPE)" || echo "❌ Failed to update env properties for $$app"; \
			NEW_VERSION=$$(aws kinesisanalyticsv2 describe-application --application-name "$$app" --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationDetail.ApplicationVersionId' --output text); \
			aws kinesisanalyticsv2 update-application --application-name "$$app" --current-application-version-id $$NEW_VERSION --region $(AWS_REGION) --profile $(AWS_PROFILE) --application-configuration-update file:///tmp/flink-jar-update.json >/dev/null 2>&1 && echo "✅ Updated JAR for $$app" || echo "❌ Failed to update JAR for $$app"; \
			FINAL_VERSION=$$(aws kinesisanalyticsv2 describe-application --application-name "$$app" --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationDetail.ApplicationVersionId' --output text); \
			aws kinesisanalyticsv2 add-application-vpc-configuration --application-name "$$app" --current-application-version-id $$FINAL_VERSION --region $(AWS_REGION) --profile $(AWS_PROFILE) --vpc-configuration '{"SecurityGroupIds":["'$$SG_ID'"],"SubnetIds":["'$$SUBNET1'","'$$SUBNET2'"]}' >/dev/null 2>&1 && echo "✅ Added VPC to $$app" || echo "❌ Failed to add VPC to $$app (may already exist)"; \
			MONITOR_VERSION=$$(aws kinesisanalyticsv2 describe-application --application-name "$$app" --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationDetail.ApplicationVersionId' --output text); \
			aws kinesisanalyticsv2 update-application --application-name "$$app" --current-application-version-id $$MONITOR_VERSION --region $(AWS_REGION) --profile $(AWS_PROFILE) --application-configuration-update '{"FlinkApplicationConfigurationUpdate":{"MonitoringConfigurationUpdate":{"ConfigurationTypeUpdate":"CUSTOM","MetricsLevelUpdate":"APPLICATION","LogLevelUpdate":"INFO"}}}' >/dev/null 2>&1 && echo "✅ Enabled CloudWatch logging for $$app" || echo "❌ Failed to enable logging for $$app"; \
			rm -f /tmp/flink-env-update-$$app.json; \
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
	@echo "$(GREEN)✅ Flink applications configured with IAM authentication and VPC$(NC)"

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


phase5: ## Phase 5: Telemetry Pipeline 4 (Flink Configuration)
	@echo "$(BLUE)🚀 Phase 5: Telemetry Pipeline 4 (Flink Configuration)$(NC)"
	@echo "Building JAR from source and configuring Flink applications"
	@$(MAKE) configure-flink AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(GREEN)✅ Phase 5 Complete: Flink applications configured with latest JAR and running$(NC)"

deploy-connector: ## Deploy OEM connector (ECS Fargate). Usage: make deploy-connector CONNECTOR_NAME=ford-feed CONNECTOR_TYPE=grpc_streaming
	@if [ -z "$(CONNECTOR_NAME)" ]; then echo "$(RED)❌ CONNECTOR_NAME required. Usage: make deploy-connector CONNECTOR_NAME=ford-feed CONNECTOR_TYPE=grpc_streaming$(NC)"; exit 1; fi
	@echo "$(BLUE)🔌 Deploying OEM Connector: $(CONNECTOR_NAME) ($(CONNECTOR_TYPE))$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	CONNECTOR_NAME=$(CONNECTOR_NAME) CONNECTOR_TYPE=$(CONNECTOR_TYPE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-connector-$(CONNECTOR_NAME) --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Connector $(CONNECTOR_NAME) deployed$(NC)"

seed-manifest: ## Upload transform manifest to S3. Usage: make seed-manifest CONNECTOR_NAME=ford-feed
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
	cdk deploy cms-$(DEPLOYMENT_STAGE)-ws-fanout --require-approval never --profile $(AWS_PROFILE)
	@echo "$(YELLOW)⚠️  Set BOOTSTRAP_SERVERS on the Fargate task:$(NC)"
	@MSK_CLUSTER_ARN=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query "Stacks[0].Outputs[?OutputKey=='MSKClusterArn'].OutputValue" --output text --profile $(AWS_PROFILE)); \
	BOOTSTRAP_SERVERS=$$(aws kafka get-bootstrap-brokers --cluster-arn $$MSK_CLUSTER_ARN --query 'BootstrapBrokerStringSaslIam' --output text --profile $(AWS_PROFILE)); \
	echo "$(GREEN)Bootstrap servers: $$BOOTSTRAP_SERVERS$(NC)"
	@echo "$(GREEN)✅ WebSocket Fanout Service deployed$(NC)"

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

deploy-flink: ## Deploy Flink stack with MSK integration
	@echo "$(BLUE)⚡ Deploying Flink stack...$(NC)"
	@MSK_CLUSTER_ARN=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKClusterArn`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	MSK_VPC_ID=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKVpcId`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	MSK_SECURITY_GROUP_ID=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKSecurityGroupId`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	MSK_SUBNET_IDS=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKPrivateSubnetIds`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	export MSK_CLUSTER_ARN=$$MSK_CLUSTER_ARN; \
	export MSK_VPC_ID=$$MSK_VPC_ID; \
	export MSK_SECURITY_GROUP_ID=$$MSK_SECURITY_GROUP_ID; \
	export MSK_SUBNET_IDS=$$MSK_SUBNET_IDS; \
	source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-flink --require-approval never --profile $(AWS_PROFILE)

configure-pipeline: ## Configure IoT rules and Flink applications
	@echo "$(BLUE)🔗 Configuring telemetry pipeline...$(NC)"
	@cd scripts && AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_REGION=$(AWS_REGION) python3 complete_integration.py
	@echo "$(GREEN)✅ Pipeline configuration complete$(NC)"

# ── Grouped phases (for deploy-all ordering clarity) ─────────────────
phase-foundation: data-processing phase1 phase3b ## Storage + IoT + UI + MSK + telemetry-integration
	@echo "$(GREEN)✅ Foundation phase complete$(NC)"

phase-streaming: phase4 deploy-fleetwise ## Flink apps + FleetWise integration (order matters: fleetwise imports flink exports)
	@echo "$(GREEN)✅ Streaming phase complete$(NC)"

phase-seeds: seed-signal-catalog seed-event-catalog seed-fleet-enrollment seed-fleetwise ## Catalogs + fleet enrollment + decoder manifest
	@echo "$(GREEN)✅ Seed data phase complete$(NC)"

phase-services: deploy-simulation deploy-commands deploy-ws-fanout deploy-tco ## ECS/Lambda services (simulation, commands, ws-fanout, tco)
	@echo "$(GREEN)✅ Services phase complete$(NC)"

deploy-all: phase-foundation phase-streaming phase-seeds phase5 phase-services regenerate-runtime-config ## Deploy everything (excludes demo data seeding)
	@echo "$(GREEN)🎉 All phases deployed successfully!$(NC)"
	@echo "$(YELLOW)Optional next steps:$(NC)"
	@echo "$(YELLOW)  - make seed-all-demo-data    # Populate demo fleet data (drivers, trips, warranty)$(NC)"
	@echo "$(YELLOW)  - make phase2                # Interactive historical data injection$(NC)"
	@$(MAKE) status

regenerate-runtime-config: ## Regenerate runtimeConfig.json in the UI S3 bucket with current stack outputs and invalidate CloudFront
	@echo "$(BLUE)🔧 Regenerating runtimeConfig.json with outputs from all deployed stacks$(NC)"
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
	COG_DOMAIN=$${COGNITO_DOMAIN:-}; \
	BUCKET=$$(aws cloudformation list-stack-resources --stack-name $$PREFIX-ui --region $$REGION --profile $(AWS_PROFILE) \
		--query "StackResourceSummaries[?ResourceType=='AWS::S3::Bucket' && contains(LogicalResourceId, 'Frontend')].PhysicalResourceId" --output text); \
	CF_ID=$$(aws cloudformation list-stack-resources --stack-name $$PREFIX-ui --region $$REGION --profile $(AWS_PROFILE) \
		--query "StackResourceSummaries[?ResourceType=='AWS::CloudFront::Distribution'].PhysicalResourceId" --output text); \
	if [ -z "$$BUCKET" ] || [ -z "$$UI_POOL" ]; then \
		echo "$(RED)❌ Could not resolve UI stack outputs or bucket$(NC)"; exit 1; \
	fi; \
	echo "  Bucket: $$BUCKET"; \
	echo "  CF id: $$CF_ID"; \
	python3 -c "import json; print(json.dumps({ \
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
'cognitoDomain':'$$COG_DOMAIN' \
}, indent=2))" > /tmp/runtimeConfig.json; \
	aws s3 cp /tmp/runtimeConfig.json s3://$$BUCKET/runtimeConfig.json --region $$REGION --profile $(AWS_PROFILE) \
		--cache-control "max-age=0, no-cache" --content-type "application/json" >/dev/null; \
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

refresh-recalls-ui: seed-nhtsa-recalls ## Regenerate NHTSA recall data + rebuild UI + redeploy cms-$(DEPLOYMENT_STAGE)-ui (so CloudFront sees fresh recalls)
	@echo "$(BLUE)🔁 Refreshing recall data in the UI bundle$(NC)"
	@echo "$(YELLOW)Step 2/3: Building frontend bundle...$(NC)"
	@cd $(CURDIR)/../modules/cms_ui/source/frontend && \
	yarn run vite build --mode development
	@echo "$(GREEN)✅ Frontend build complete$(NC)"
	@echo "$(YELLOW)Step 3/3: Looking up frontend bucket + deploying cms-$(DEPLOYMENT_STAGE)-ui...$(NC)"
	@# Resolve the existing frontend bucket from the deployed stack so we
	@# don't replace it (which would break the pinned CloudFront origin).
	@# If the stack hasn't been deployed yet, this will fail loudly rather
	@# than create a new bucket silently.
	@FRONTEND_BUCKET=$$(aws cloudformation list-stack-resources \
		--stack-name cms-$(DEPLOYMENT_STAGE)-ui \
		--profile $(AWS_PROFILE) --region $(AWS_REGION) \
		--query "StackResourceSummaries[?LogicalResourceId=='FrontendBucketEFE2E19C'].PhysicalResourceId" \
		--output text 2>/dev/null); \
	if [ -z "$$FRONTEND_BUCKET" ] || [ "$$FRONTEND_BUCKET" = "None" ]; then \
		echo "$(RED)❌ Could not find FrontendBucket in cms-$(DEPLOYMENT_STAGE)-ui stack.$(NC)"; \
		echo "   Deploy the UI stack first with \`cdk deploy cms-$(DEPLOYMENT_STAGE)-ui\`."; \
		exit 1; \
	fi; \
	echo "   Frontend bucket: $$FRONTEND_BUCKET"; \
	cd $(CURDIR) && source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	AWS_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-ui \
		-c frontendBucketName=$$FRONTEND_BUCKET \
		-c bedrockAgentsStackName=cms-$(DEPLOYMENT_STAGE)-bedrock-agents \
		--require-approval never --exclusively --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ UI redeployed with fresh NHTSA recall data$(NC)"
	@CF_URL=$$(aws cloudformation describe-stacks \
		--stack-name cms-$(DEPLOYMENT_STAGE)-ui \
		--profile $(AWS_PROFILE) --region $(AWS_REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' \
		--output text 2>/dev/null); \
	echo "   $(BLUE)$$CF_URL$(NC)"

seed-trip-history: ## Seed trip history from purchase date to now (matches odometer)
	@echo "$(YELLOW)Seeding trip history for all vehicles...$(NC)"
	@cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) \
	python3 deployment/scripts/seed_trip_history.py
	@echo "$(GREEN)✅ Trip history seeded$(NC)"

seed-drivers: ## Seed driver records (75 drivers with CDL, safety scores, experience)
	@echo "$(YELLOW)Seeding driver records...$(NC)"
	@cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	python3 deployment/scripts/seed_drivers.py
	@echo "$(GREEN)✅ Drivers seeded$(NC)"

seed-driver-users: ## Create/update a VSA Cognito user for each active driver (idempotent)
	@echo "$(YELLOW)Syncing drivers → VSA Cognito users...$(NC)"
	@cd $(CURDIR)/.. && AWS_REGION=$(AWS_REGION) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	python3 deployment/scripts/seed_driver_users.py
	@echo "$(GREEN)✅ Driver users synced$(NC)"

seed-all-demo-data: seed-drivers seed-driver-users seed-fleet-enrollment seed-signal-catalog seed-event-catalog seed-vehicle-lifecycle seed-trip-history seed-service-warranty seed-nhtsa-recalls seed-fleetwise upload-decoder-manifest seed-vfo-actions seed-decision-journal ## Seed all demo data (granular scripts)
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
	@. config/staging.env; \
	  AWS_REGION=$$AWS_REGION DEPLOYMENT_STAGE=$$DEPLOYMENT_STAGE \
	  $(MAKE) deploy-all

prod-deploy: ## Deploy CMS to prod (us-east-1) — requires confirmation unless CI=true
	@if [ ! -f config/prod.env ]; then echo 'config/prod.env missing'; exit 1; fi
	@if [ "$$CI" != "true" ]; then \
	  printf 'Confirm prod deploy [y/N]: '; \
	  read ans; \
	  if [ "$$ans" != "y" ]; then echo 'aborted'; exit 1; fi; \
	fi
	@. config/prod.env; \
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
