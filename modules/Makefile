# CMS UI Comprehensive Deployment Makefile
.PHONY: help build deploy deploy-ui deploy-telemetry deploy-both clean status frontend-dev frontend-build logs list info build-flink deploy-flink build-deploy-flink

# Default target
.DEFAULT_GOAL := help

# Colors for output
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[0;33m
BLUE := \033[0;34m
NC := \033[0m # No Color

# AWS Configuration
AWS_PROFILE := target-account
AWS_REGION := us-east-1
S3_BUCKET := cdk-hnb659fds-assets-470296731304-us-east-1
MSK_ENDPOINT := b-1.cmstelemetryclustersas.7v7vwf.c7.kafka.us-east-1.amazonaws.com:9096,b-2.cmstelemetryclustersas.7v7vwf.c7.kafka.us-east-1.amazonaws.com:9096

help: ## Show this help message
	@echo "$(BLUE)🚀 CMS UI Deployment Commands$(NC)"
	@echo "================================"
	@echo ""
	@echo "$(GREEN)Main Commands:$(NC)"
	@echo "  make build      - Build the application"
	@echo "  make deploy     - Interactive deployment (choose account & components)"
	@echo "  make info       - Show deployment URLs and test credentials"
	@echo "  make clean      - Clean build artifacts"
	@echo "  make status     - Show deployment status"
	@echo ""
	@echo "$(GREEN)Specific Deployments:$(NC)"
	@echo "  make deploy-ui        - Deploy UI only to current account"
	@echo "  make deploy-ui-target - Deploy UI only to target account"
	@echo "  make deploy-both      - Deploy UI + telemetry (interactive)"
	@echo ""
	@echo "$(GREEN)Flink Deployments:$(NC)"
	@echo "  make build-flink                           - Build Flink JAR"
	@echo "  make build-deploy-flink APP=cms-trip-processor - Build and deploy Flink app"
	@echo "  make deploy-all-flink                      - Deploy all Flink applications"
	@echo ""
	@echo "$(GREEN)IoT Management:$(NC)"
	@echo "  make deploy-ui-with-iot - Deploy UI + IoT management capabilities"
	@echo "  make deploy-iot-only    - Deploy IoT infrastructure only"
	@echo "  make destroy-iot        - Destroy IoT management infrastructure"
	@echo ""
	@echo "$(GREEN)Kafka Management:$(NC)"
	@echo "  make create-kafka-topics - Create required Kafka topics in MSK cluster"
	@echo ""
	@echo "$(GREEN)Development:$(NC)"
	@echo "  make frontend-dev     - Start frontend development server"
	@echo "  make frontend-build   - Build frontend only"
	@echo "  make logs            - Show recent deployment logs"

build: ## Build the application
	@echo "$(BLUE)🔨 Building CMS UI Application$(NC)"
	@$(MAKE) _build-frontend
	@cd . && source ../.venv/bin/activate && cdk synth
	@echo "$(GREEN)✅ Build completed$(NC)"

_build-frontend: ## Build the frontend if build directory doesn't exist
	@if [ ! -d "cms_ui/source/frontend/build" ]; then \
		echo "$(YELLOW)📦 Frontend build directory not found, building frontend...$(NC)"; \
		yarn --cwd cms_ui/source/frontend install && yarn --cwd cms_ui/source/frontend build; \
		echo "$(GREEN)✅ Frontend build completed$(NC)"; \
	else \
		echo "$(GREEN)✅ Frontend build directory exists$(NC)"; \
	fi

frontend-build: ## Force build the frontend
	@echo "$(BLUE)🔨 Building Frontend Application$(NC)"
	@yarn --cwd cms_ui/source/frontend install && yarn --cwd cms_ui/source/frontend build
	@echo "$(GREEN)✅ Frontend build completed$(NC)"

deploy: ## Interactive deployment - choose account and components
	@echo "$(BLUE)🚀 Interactive CMS Deployment$(NC)"
	@echo "=============================="
	@echo ""
	@if [ ! -f ~/.aws/credentials ]; then \
		echo "$(YELLOW)⚠️  No AWS credentials file found$(NC)"; \
		echo "$(BLUE)🔧 Setting up AWS credentials$(NC)"; \
		echo ""; \
		read -p "Enter AWS Access Key ID: " access_key; \
		read -s -p "Enter AWS Secret Access Key: " secret_key; \
		echo ""; \
		read -p "Enter AWS Region (default: us-east-1): " region; \
		region=$${region:-us-east-1}; \
		echo ""; \
		mkdir -p ~/.aws; \
		echo "[default]" > ~/.aws/credentials; \
		echo "aws_access_key_id = $$access_key" >> ~/.aws/credentials; \
		echo "aws_secret_access_key = $$secret_key" >> ~/.aws/credentials; \
		echo "[default]" > ~/.aws/config; \
		echo "region = $$region" >> ~/.aws/config; \
		echo "$(GREEN)✅ AWS credentials configured$(NC)"; \
		echo ""; \
	fi; \
	echo "$(YELLOW)Available AWS Profiles:$(NC)"; \
	profiles=$$(grep '^\[' ~/.aws/credentials 2>/dev/null | sed 's/\[//g' | sed 's/\]//g' | nl -w2 -s') '); \
	if [ -z "$$profiles" ]; then \
		echo "$(RED)❌ No profiles found in credentials file$(NC)"; \
		exit 1; \
	fi; \
	echo "$$profiles"; \
	echo ""; \
	read -p "Choose profile number (or press Enter for default): " profile_num; \
	if [ -z "$$profile_num" ]; then \
		selected_profile=""; \
		echo "$(GREEN)✅ Using default AWS profile$(NC)"; \
	else \
		selected_profile=$$(echo "$$profiles" | sed -n "$${profile_num}p" | awk '{print $$2}'); \
		if [ -z "$$selected_profile" ]; then \
			echo "$(RED)❌ Invalid profile number$(NC)"; \
			exit 1; \
		fi; \
		echo "$(GREEN)✅ Using profile: $$selected_profile$(NC)"; \
	fi; \
	echo ""; \
	echo "$(YELLOW)Select components:$(NC)"; \
	echo "1) Full CMS Stack (UI + Telemetry)"; \
	echo "2) CMS UI Stack (only)"; \
	echo "3) CMS Telemetry Stack (only)"; \
	read -p "Choose [1-3]: " components; \
	echo ""; \
	case $$components in \
		1) \
			echo "$(YELLOW)Deployment type:$(NC)"; \
			echo "1) Update existing stack (recommended)"; \
			echo "2) Create new stack"; \
			read -p "Choose [1-2]: " deploy_type; \
			$(MAKE) _deploy-both PROFILE="$$selected_profile" DEPLOY_TYPE="$$deploy_type"; \
			;; \
		2) \
			echo "$(YELLOW)Deployment type:$(NC)"; \
			echo "1) Update existing stack (recommended)"; \
			echo "2) Create new stack"; \
			read -p "Choose [1-2]: " deploy_type; \
			$(MAKE) _deploy-ui PROFILE="$$selected_profile" DEPLOY_TYPE="$$deploy_type"; \
			;; \
		3) \
			echo "$(YELLOW)MSK Cluster Options:$(NC)"; \
			echo "1) Use existing MSK cluster (recommended if available)"; \
			echo "2) Deploy new MSK Serverless cluster (cost-effective)"; \
			echo "3) Deploy new MSK Express cluster (development/testing)"; \
			read -p "Choose MSK option [1-3]: " msk_option; \
			case $$msk_option in \
				1) \
					$(MAKE) _deploy-telemetry-existing PROFILE="$$selected_profile"; \
					;; \
				2) \
					$(MAKE) _deploy-telemetry-serverless PROFILE="$$selected_profile"; \
					;; \
				3) \
					$(MAKE) _deploy-telemetry-express PROFILE="$$selected_profile"; \
					;; \
				*) \
					echo "$(RED)❌ Invalid choice. Exiting.$(NC)"; \
					exit 1; \
					;; \
			esac; \
			;; \
		*) \
			echo "$(RED)❌ Invalid choice. Exiting.$(NC)"; \
			exit 1; \
			;; \
	esac

deploy-ui: ## Deploy UI only with account selection
	@echo "$(BLUE)🚀 CMS UI Deployment$(NC)"
	@echo "===================="
	@echo ""
	@echo "$(YELLOW)Available AWS Profiles:$(NC)"
	@profiles=$$(grep '^\[' ~/.aws/credentials 2>/dev/null | sed 's/\[//g' | sed 's/\]//g' | nl -w2 -s') '); \
	if [ -z "$$profiles" ]; then \
		echo "$(RED)❌ No profiles found in credentials file$(NC)"; \
		exit 1; \
	fi; \
	echo "$$profiles"; \
	echo ""; \
	read -p "Choose profile number (or press Enter for default): " profile_num; \
	if [ -z "$$profile_num" ]; then \
		selected_profile=""; \
		echo "$(GREEN)✅ Using default AWS profile$(NC)"; \
	else \
		selected_profile=$$(echo "$$profiles" | sed -n "$${profile_num}p" | awk '{print $$2}'); \
		if [ -z "$$selected_profile" ]; then \
			echo "$(RED)❌ Invalid profile number$(NC)"; \
			exit 1; \
		fi; \
		echo "$(GREEN)✅ Using profile: $$selected_profile$(NC)"; \
	fi; \
	echo ""; \
	echo "$(YELLOW)Deployment type:$(NC)"; \
	echo "1) Update existing stack (recommended)"; \
	echo "2) Create new stack"; \
	read -p "Choose [1-2]: " deploy_type; \
	$(MAKE) _deploy-ui PROFILE="$$selected_profile" DEPLOY_TYPE="$$deploy_type"

deploy-ui-target: ## Deploy UI only using AWS_PROFILE environment variable
	@$(MAKE) _deploy-ui PROFILE="$(AWS_PROFILE)"

deploy-both: ## Deploy UI + telemetry with interactive MSK config
	@echo "$(BLUE)🚀 Deploying UI + Telemetry$(NC)"
	@echo "============================"
	@read -p "Enter MSK Cluster ARN (or press Enter to skip): " msk_arn; \
	read -p "Enter Bootstrap Servers (or press Enter to skip): " bootstrap; \
	$(MAKE) _deploy-both PROFILE="" MSK_ARN="$$msk_arn" BOOTSTRAP="$$bootstrap"

# Internal deployment targets
_deploy-ui:
	@$(MAKE) frontend-build
	@if [ -z "$(PROFILE)" ]; then \
		echo "$(GREEN)🎯 Deploying UI using default profile$(NC)"; \
	else \
		echo "$(GREEN)🎯 Deploying UI using profile: $(PROFILE)$(NC)"; \
	fi; \
	echo ""; \
	cd . && source ../.venv/bin/activate && \
	if [ -z "$(PROFILE)" ]; then \
		cdk deploy cms-ui-enhanced-single-stack --app "python3 deployment/scripts/app_single_stack_enhanced.py" --require-approval never \
			--parameters MSKClusterArn="" --parameters BootstrapServers=""; \
	else \
		if [ "$(PROFILE)" = "target-account" ]; then \
			USE_EXISTING_TABLES=true EXISTING_TABLE_SUFFIX=cms-631ca2-591631 AWS_PROFILE=$(PROFILE) cdk deploy cms-ui-enhanced-single-stack --app "python3 deployment/scripts/app_single_stack_enhanced.py" --require-approval never \
				--parameters MSKClusterArn="" --parameters BootstrapServers=""; \
		else \
			AWS_PROFILE=$(PROFILE) cdk deploy cms-ui-enhanced-single-stack --app "python3 deployment/scripts/app_single_stack_enhanced.py" --require-approval never \
				--parameters MSKClusterArn="" --parameters BootstrapServers=""; \
		fi; \
	fi
	@$(MAKE) _post-deploy

_deploy-both:
	@$(MAKE) frontend-build
	@if [ -z "$(PROFILE)" ]; then \
		echo "$(GREEN)🎯 Deploying UI + Telemetry using default profile$(NC)"; \
		cd . && source ../.venv/bin/activate && \
		cdk deploy cms-ui-enhanced-single-stack --app "python3 deployment/scripts/app_single_stack_enhanced.py" --require-approval never \
			--parameters MSKClusterArn="$(MSK_ARN)" --parameters BootstrapServers="$(BOOTSTRAP)"; \
	else \
		echo "$(GREEN)🎯 Deploying UI + Telemetry using profile: $(PROFILE)$(NC)"; \
		cd . && source ../.venv/bin/activate && \
		AWS_PROFILE=$(PROFILE) cdk deploy cms-ui-enhanced-single-stack --app "python3 deployment/scripts/app_single_stack_enhanced.py" --require-approval never \
			--parameters MSKClusterArn="$(MSK_ARN)" --parameters BootstrapServers="$(BOOTSTRAP)"; \
	fi
	@$(MAKE) _post-deploy

_post-deploy:
	@echo ""
	@echo "$(BLUE)🔄 Invalidating CloudFront cache...$(NC)"
	@if [ -z "$(PROFILE)" ]; then \
		distribution_id=$$(aws cloudformation describe-stacks \
			--stack-name cms-ui-enhanced-single-stack \
			--query 'Stacks[0].Outputs[?OutputKey==`CloudFrontDistributionId`].OutputValue' \
			--output text 2>/dev/null); \
	else \
		distribution_id=$$(AWS_PROFILE=$(PROFILE) aws cloudformation describe-stacks \
			--stack-name cms-ui-enhanced-single-stack \
			--query 'Stacks[0].Outputs[?OutputKey==`CloudFrontDistributionId`].OutputValue' \
			--output text 2>/dev/null); \
	fi; \
	if [ ! -z "$$distribution_id" ] && [ "$$distribution_id" != "None" ]; then \
		echo "$(YELLOW)📡 Invalidating distribution: $$distribution_id$(NC)"; \
		if [ -z "$(PROFILE)" ]; then \
			aws cloudfront create-invalidation \
				--distribution-id $$distribution_id \
				--paths "/*" >/dev/null 2>&1 || echo "$(YELLOW)⚠️  CloudFront invalidation failed (non-critical)$(NC)"; \
		else \
			AWS_PROFILE=$(PROFILE) aws cloudfront create-invalidation \
				--distribution-id $$distribution_id \
				--paths "/*" >/dev/null 2>&1 || echo "$(YELLOW)⚠️  CloudFront invalidation failed (non-critical)$(NC)"; \
		fi; \
		echo "$(GREEN)✅ CloudFront cache invalidated$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  CloudFront distribution ID not found, skipping cache invalidation$(NC)"; \
	fi
	@echo ""
	@echo "$(GREEN)✅ Deployment completed successfully!$(NC)"
	@echo ""
	@echo "$(BLUE)🌐 Access Your CMS Application:$(NC)"
	@echo "   🔗 CloudFront URL: [Run 'make info' to get URLs]"
	@echo "   🔗 API Endpoint: [Run 'make info' to get URLs]"
	@echo ""
	@echo "$(BLUE)🔐 Test Credentials:$(NC)"
	@echo "   📧 Email: test@example.com"
	@echo "   🔑 Password: FleetManager123!"

info: ## Show deployment information and URLs
	@echo "$(BLUE)📊 CMS Deployment Information$(NC)"
	@echo "=============================="
	@echo ""
	@echo "$(YELLOW)📋 Select AWS profile for deployment info...$(NC)"
	@echo ""
	@profiles=$$(aws configure list-profiles); \
	echo "Available AWS profiles:"; \
	i=1; \
	for profile in $$profiles; do \
		echo "  $$i) $$profile"; \
		i=$$((i+1)); \
	done; \
	echo ""; \
	read -p "Select profile number (default: 1): " choice; \
	choice=$${choice:-1}; \
	profile=$$(echo "$$profiles" | sed -n "$${choice}p"); \
	if [ -z "$$profile" ]; then \
		echo "$(RED)Invalid selection. Using default profile.$(NC)"; \
		profile="default"; \
	fi; \
	echo "$(BLUE)🔧 Using profile: $$profile$(NC)"; \
	echo ""; \
	cloudfront_url=$$(aws cloudformation describe-stacks \
		--profile $$profile \
		--stack-name cms-ui-enhanced-single-stack \
		--query 'Stacks[0].Outputs[?OutputKey==`CloudFrontUrl`].OutputValue' \
		--output text 2>/dev/null); \
	api_endpoint=$$(aws cloudformation describe-stacks \
		--profile $$profile \
		--stack-name cms-ui-enhanced-single-stack \
		--query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' \
		--output text 2>/dev/null); \
	if [ -n "$$cloudfront_url" ] && [ "$$cloudfront_url" != "None" ]; then \
		echo "🔗 CloudFront URL: $$cloudfront_url"; \
	else \
		echo "$(RED)❌ CloudFront URL not found$(NC)"; \
	fi; \
	if [ -n "$$api_endpoint" ] && [ "$$api_endpoint" != "None" ]; then \
		echo "🔗 API Endpoint: $$api_endpoint"; \
	else \
		echo "$(RED)❌ API Endpoint not found$(NC)"; \
	fi
	@echo ""
	@echo "$(BLUE)🔐 Test Credentials:$(NC)"
	@echo "   📧 Email: test@example.com"
	@echo "   🔑 Password: FleetManager123!"

frontend-dev: ## Start frontend development server
	@echo "$(BLUE)🖥️  Starting frontend development server$(NC)"
	@echo "$(YELLOW)📋 Select AWS profile for configuration...$(NC)"
	@echo ""
	@profiles=$$(aws configure list-profiles); \
	echo "Available AWS profiles:"; \
	i=1; \
	for profile in $$profiles; do \
		echo "  $$i) $$profile"; \
		i=$$((i+1)); \
	done; \
	echo ""; \
	read -p "Select profile number (default: 1): " choice; \
	choice=$${choice:-1}; \
	profile=$$(echo "$$profiles" | sed -n "$${choice}p"); \
	if [ -z "$$profile" ]; then \
		echo "$(RED)Invalid selection. Using default profile.$(NC)"; \
		profile="default"; \
	fi; \
	echo "$(BLUE)🔧 Using profile: $$profile$(NC)"; \
	$(MAKE) update-localhost-config AWS_PROFILE=$$profile
	@echo ""
	@echo "$(BLUE)🚀 Starting development server...$(NC)"
	@yarn --cwd cms_ui/source/frontend start

update-localhost-config: ## Update localhost runtimeConfig.json with latest API endpoint
	@echo "$(BLUE)🔧 Updating localhost configuration...$(NC)"
	@profile=$${AWS_PROFILE:-target-account}; \
	echo "$(YELLOW)Using AWS profile: $$profile$(NC)"; \
	api_endpoint=$$(aws cloudformation describe-stacks \
		--profile $$profile \
		--stack-name cms-ui-enhanced-single-stack \
		--query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' \
		--output text 2>/dev/null); \
	user_pool_id=$$(aws cloudformation describe-stacks \
		--profile $$profile \
		--stack-name cms-ui-enhanced-single-stack \
		--query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' \
		--output text 2>/dev/null); \
	user_pool_client_id=$$(aws cloudformation describe-stacks \
		--profile $$profile \
		--stack-name cms-ui-enhanced-single-stack \
		--query 'Stacks[0].Outputs[?OutputKey==`UserPoolClientId`].OutputValue' \
		--output text 2>/dev/null); \
	identity_pool_id=$$(aws cloudformation describe-stacks \
		--profile $$profile \
		--stack-name cms-ui-enhanced-single-stack \
		--query 'Stacks[0].Outputs[?OutputKey==`IdentityPoolId`].OutputValue' \
		--output text 2>/dev/null); \
	if [ -z "$$api_endpoint" ] || [ "$$api_endpoint" = "None" ]; then \
		echo "$(RED)❌ Could not retrieve API endpoint from stack$(NC)"; \
		exit 1; \
	fi; \
	runtime_config="cms_ui/source/frontend/public/runtimeConfig.json"; \
	if [ -f "$$runtime_config" ]; then \
		cp "$$runtime_config" "$${runtime_config}.backup"; \
		echo "$(YELLOW)📋 Backed up existing config$(NC)"; \
	fi; \
	echo "{" > "$$runtime_config"; \
	echo "  \"awsRegion\": \"us-east-1\"," >> "$$runtime_config"; \
	echo "  \"mapAuth\": {" >> "$$runtime_config"; \
	echo "    \"identityPoolClient\": \"cognito-idp.us-east-1.amazonaws.com/$$user_pool_id\"," >> "$$runtime_config"; \
	echo "    \"mapName\": \"cms-map\"," >> "$$runtime_config"; \
	echo "    \"identityPoolId\": \"$$identity_pool_id\"" >> "$$runtime_config"; \
	echo "  }," >> "$$runtime_config"; \
	echo "  \"isDemoMode\": \"false\"," >> "$$runtime_config"; \
	echo "  \"apiEndpoint\": \"$$api_endpoint\"," >> "$$runtime_config"; \
	echo "  \"userPreferencesApiEndpoint\": \"$$api_endpoint\"," >> "$$runtime_config"; \
	echo "  \"awsCredentials\": {" >> "$$runtime_config"; \
	echo "    \"region\": \"us-east-1\"," >> "$$runtime_config"; \
	echo "    \"identityPoolId\": \"$$identity_pool_id\"," >> "$$runtime_config"; \
	echo "    \"userPoolId\": \"$$user_pool_id\"," >> "$$runtime_config"; \
	echo "    \"userPoolWebClientId\": \"$$user_pool_client_id\"" >> "$$runtime_config"; \
	echo "  }" >> "$$runtime_config"; \
	echo "}" >> "$$runtime_config"; \
	echo "$(GREEN)✅ Updated localhost config: $$api_endpoint$(NC)"

# Configuration
S3_BUCKET := cdk-hnb659fds-assets-470296731304-us-east-1

# Flink JAR Build and Deployment Targets
build-flink-jar: ## Build and upload Flink JAR with correct configuration
	@echo "$(BLUE)🔨 Building Flink JAR for telemetry processing$(NC)"
	@if [ ! -d "flink" ]; then \
		echo "$(RED)❌ Flink source directory not found$(NC)"; \
		echo "$(YELLOW)💡 Skipping Flink JAR build - directory not available$(NC)"; \
		exit 0; \
	fi; \
	cd flink && mvn clean package -q; \
	jar_hash=$$(shasum -a 256 target/telemetry-processor-1.0.0.jar | cut -d' ' -f1); \
	echo "$(GREEN)✅ JAR built with hash: $$jar_hash$(NC)"; \
	if [ -n "$(PROFILE)" ]; then \
		AWS_PROFILE=$(PROFILE) aws s3 cp target/telemetry-processor-1.0.0.jar s3://$(S3_BUCKET)/$${jar_hash}.jar; \
	else \
		aws s3 cp target/telemetry-processor-1.0.0.jar s3://$(S3_BUCKET)/$${jar_hash}.jar; \
	fi; \
	echo "$$jar_hash" > current-jar-hash.txt

update-flink-app: ## Update Kinesis Analytics application with new JAR
	@if [ ! -f "flink/current-jar-hash.txt" ]; then \
		echo "$(RED)❌ No JAR hash found. Run 'make build-flink-jar' first$(NC)"; \
		exit 1; \
	fi; \
	jar_hash=$$(cat flink/current-jar-hash.txt); \
	echo "$(BLUE)🔄 Updating Flink application with JAR: $$jar_hash$(NC)"; \
	cd deployment/scripts && python3 update_flink_application.py "$$jar_hash" "$(PROFILE)"

deploy-telemetry-with-flink: ## Deploy telemetry stack with proper Flink JAR
	@$(MAKE) build-flink-jar PROFILE="$(PROFILE)"
	@$(MAKE) _deploy-telemetry PROFILE="$(PROFILE)"
	@$(MAKE) update-flink-app PROFILE="$(PROFILE)"

rebuild-flink: ## Rebuild and redeploy Flink JAR only
	@$(MAKE) build-flink-jar PROFILE="$(PROFILE)"
	@$(MAKE) update-flink-app PROFILE="$(PROFILE)"

clean: ## Clean build artifacts
	@echo "$(BLUE)🧹 Cleaning build artifacts$(NC)"
	@rm -rf cms_ui/source/frontend/build
	@rm -rf cms_ui/source/frontend/node_modules/.cache
	@find . -name "cdk.out" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)✅ Clean completed$(NC)"

status: ## Show deployment status
	@echo "$(BLUE)📊 Deployment Status$(NC)"
	@echo "===================="
	@echo "├── UI Build: $(shell [ -d cms_ui/source/frontend/build ] && echo '✅ Built' || echo '❌ Not built')"
	@echo "├── CDK Synth: $(shell [ -d deployment/cdk.out ] && echo '✅ Synthesized' || echo '❌ Not synthesized')"
	@echo "└── Virtual Env: $(shell [ -d ../.venv ] && echo '✅ Active' || echo '❌ Not setup')"

logs: ## Show recent deployment logs
	@echo "$(BLUE)📋 Recent Deployment Logs$(NC)"
	@echo "=========================="
	@echo "$(YELLOW)CloudFormation events for cms-ui-enhanced-single-stack:$(NC)"
	@aws cloudformation describe-stack-events --stack-name cms-ui-enhanced-single-stack --max-items 10 --query 'StackEvents[].{Time:Timestamp,Status:ResourceStatus,Type:ResourceType,Reason:ResourceStatusReason}' --output table 2>/dev/null || echo "No stack events found"
# ================================
# Flink Application Deployment
# ================================

build-flink: ## Build Flink JAR
	@echo "🏗️ Building Flink application..."
	cd flink && mvn clean package -DskipTests
	@echo "✅ Flink JAR built: flink/target/*.jar"

upload-flink: build-flink ## Upload Flink JAR to S3
	@echo "📤 Uploading Flink JAR to S3..."
	@JAR_FILE=$$(ls flink/target/telemetry-processor-*.jar | grep -v original | head -1); \
	if [ -z "$$JAR_FILE" ]; then echo "❌ No shaded JAR found!"; exit 1; fi; \
	JAR_NAME=$$(basename $$JAR_FILE); \
	TIMESTAMPED_JAR="$${JAR_NAME%.*}-$$(date +%Y%m%d-%H%M%S).jar"; \
	echo "📦 Uploading: $$JAR_FILE -> $$TIMESTAMPED_JAR"; \
	aws s3 cp $$JAR_FILE s3://$(S3_BUCKET)/$$TIMESTAMPED_JAR --profile $(AWS_PROFILE); \
	echo "✅ JAR uploaded: $$TIMESTAMPED_JAR"

deploy-flink: ## Deploy Flink application (usage: make deploy-flink APP=cms-trip-processor JAR=myjar.jar)
	@if [ -z "$(APP)" ] || [ -z "$(JAR)" ]; then echo "❌ Error: APP and JAR parameters required"; exit 1; fi
	@echo "🚀 Deploying Flink application: $(APP)"
	@CONSUMER_GROUP="$(APP)-consumer-$$(date +%Y%m%d-%H%M%S)"; \
	echo "📋 Using consumer group: $$CONSUMER_GROUP"; \
	echo "⏹️ Stopping application..."; \
	aws kinesisanalyticsv2 stop-application --application-name $(APP) --profile $(AWS_PROFILE) --region $(AWS_REGION) || true; \
	echo "⏳ Waiting for application to stop..."; \
	while true; do \
		STATUS=$$(aws kinesisanalyticsv2 describe-application --application-name $(APP) --profile $(AWS_PROFILE) --region $(AWS_REGION) --query 'ApplicationDetail.ApplicationStatus' --output text 2>/dev/null || echo "READY"); \
		if [ "$$STATUS" = "READY" ]; then break; fi; \
		echo "  Status: $$STATUS - waiting..."; \
		sleep 5; \
	done; \
	echo "🔄 Getting current version..."; \
	CURRENT_VERSION=$$(aws kinesisanalyticsv2 describe-application --application-name $(APP) --profile $(AWS_PROFILE) --region $(AWS_REGION) --query 'ApplicationDetail.ApplicationVersionId' --output text); \
	PROCESSOR_TYPE="TripProcessor"; \
	if [ "$(APP)" = "cms-raw-telemetry-processor" ]; then PROCESSOR_TYPE="EventDrivenTelemetryProcessor"; fi; \
	if [ "$(APP)" = "cms-safety-processor" ]; then PROCESSOR_TYPE="SafetyProcessor"; fi; \
	if [ "$(APP)" = "cms-maintenance-processor" ]; then PROCESSOR_TYPE="MaintenanceProcessor"; fi; \
	echo "📝 Updating configuration for $$PROCESSOR_TYPE..."; \
	$(MAKE) update-flink-config APP=$(APP) CONSUMER_GROUP=$$CONSUMER_GROUP PROCESSOR_TYPE=$$PROCESSOR_TYPE JAR=$(JAR) CURRENT_VERSION=$$CURRENT_VERSION; \
	echo "▶️ Starting application..."; \
	aws kinesisanalyticsv2 start-application --application-name $(APP) --run-configuration '{"ApplicationRestoreConfiguration":{"ApplicationRestoreType":"SKIP_RESTORE_FROM_SNAPSHOT"}}' --profile $(AWS_PROFILE) --region $(AWS_REGION); \
	echo "✅ Deployment complete: $(APP) with $(JAR)"

update-flink-config: ## Internal: Update Flink configuration
	@case "$(PROCESSOR_TYPE)" in \
		"TripProcessor") \
			CONFIG='{"ApplicationCodeConfigurationUpdate":{"CodeContentUpdate":{"S3ContentLocationUpdate":{"BucketARNUpdate":"arn:aws:s3:::$(S3_BUCKET)","FileKeyUpdate":"$(JAR)"}}},"EnvironmentPropertyUpdates":{"PropertyGroups":[{"PropertyGroupId":"consumer.config.0","PropertyMap":{"PROCESSOR_TYPE":"TripProcessor","TRIPS_TABLE_NAME":"cms-631ca2-591631-trips-new","bootstrap.servers":"$(MSK_ENDPOINT)","group.id":"$(CONSUMER_GROUP)","security.protocol":"SASL_SSL","sasl.mechanism":"SCRAM-SHA-512","sasl.jaas.config":"org.apache.kafka.common.security.scram.ScramLoginModule required username=\"$(IOT_USERNAME)\" password=\"$(IOT_PASSWORD)\";","request.timeout.ms":"120000","session.timeout.ms":"60000","connections.max.idle.ms":"600000","metadata.max.age.ms":"300000","auto.offset.reset":"latest","enable.auto.commit":"false"}}]}}'; \
			;; \
		"EventDrivenTelemetryProcessor") \
			CONFIG='{"ApplicationCodeConfigurationUpdate":{"CodeContentUpdate":{"S3ContentLocationUpdate":{"BucketARNUpdate":"arn:aws:s3:::$(S3_BUCKET)","FileKeyUpdate":"$(JAR)"}}},"EnvironmentPropertyUpdates":{"PropertyGroups":[{"PropertyGroupId":"consumer.config.0","PropertyMap":{"PROCESSOR_TYPE":"EventDrivenTelemetryProcessor","bootstrap.servers":"$(MSK_ENDPOINT)","group.id":"$(CONSUMER_GROUP)","security.protocol":"SASL_SSL","sasl.mechanism":"SCRAM-SHA-512","sasl.jaas.config":"org.apache.kafka.common.security.scram.ScramLoginModule required username=\"$(IOT_USERNAME)\" password=\"$(IOT_PASSWORD)\";","request.timeout.ms":"120000","session.timeout.ms":"60000","connections.max.idle.ms":"600000","metadata.max.age.ms":"300000","auto.offset.reset":"latest","enable.auto.commit":"false"}}]}}'; \
			;; \
		"SafetyProcessor") \
			CONFIG='{"ApplicationCodeConfigurationUpdate":{"CodeContentUpdate":{"S3ContentLocationUpdate":{"BucketARNUpdate":"arn:aws:s3:::$(S3_BUCKET)","FileKeyUpdate":"$(JAR)"}}},"EnvironmentPropertyUpdates":{"PropertyGroups":[{"PropertyGroupId":"consumer.config.0","PropertyMap":{"PROCESSOR_TYPE":"SafetyProcessor","SAFETY_EVENTS_TABLE_NAME":"cms-631ca2-591631-safety-events-new","bootstrap.servers":"$(MSK_ENDPOINT)","group.id":"$(CONSUMER_GROUP)","security.protocol":"SASL_SSL","sasl.mechanism":"SCRAM-SHA-512","sasl.jaas.config":"org.apache.kafka.common.security.scram.ScramLoginModule required username=\"$(IOT_USERNAME)\" password=\"$(IOT_PASSWORD)\";","request.timeout.ms":"120000","session.timeout.ms":"60000","connections.max.idle.ms":"600000","metadata.max.age.ms":"300000","auto.offset.reset":"latest","enable.auto.commit":"false"}}]}}'; \
			;; \
		"MaintenanceProcessor") \
			CONFIG='{"ApplicationCodeConfigurationUpdate":{"CodeContentUpdate":{"S3ContentLocationUpdate":{"BucketARNUpdate":"arn:aws:s3:::$(S3_BUCKET)","FileKeyUpdate":"$(JAR)"}}},"EnvironmentPropertyUpdates":{"PropertyGroups":[{"PropertyGroupId":"consumer.config.0","PropertyMap":{"PROCESSOR_TYPE":"MaintenanceProcessor","MAINTENANCE_EVENTS_TABLE_NAME":"cms-631ca2-591631-maintenance-events-new","bootstrap.servers":"$(MSK_ENDPOINT)","group.id":"$(CONSUMER_GROUP)","security.protocol":"SASL_SSL","sasl.mechanism":"SCRAM-SHA-512","sasl.jaas.config":"org.apache.kafka.common.security.scram.ScramLoginModule required username=\"$(IOT_USERNAME)\" password=\"$(IOT_PASSWORD)\";","request.timeout.ms":"120000","session.timeout.ms":"60000","connections.max.idle.ms":"600000","metadata.max.age.ms":"300000","auto.offset.reset":"latest","enable.auto.commit":"false"}}]}}'; \
			;; \
	esac; \
	aws kinesisanalyticsv2 update-application --application-name $(APP) --current-application-version-id $(CURRENT_VERSION) --application-configuration-update "$$CONFIG" --profile $(AWS_PROFILE) --region $(AWS_REGION)

build-deploy-flink: upload-flink ## Build, upload, and deploy Flink application (usage: make build-deploy-flink APP=cms-trip-processor)
	@if [ -z "$(APP)" ]; then echo "❌ Error: APP parameter required. Usage: make build-deploy-flink APP=cms-trip-processor"; exit 1; fi
	@JAR_NAME=$$(aws s3 ls s3://$(S3_BUCKET)/ --profile $(AWS_PROFILE) | grep "telemetry-processor.*$$(date +%Y%m%d)" | tail -1 | awk '{print $$4}'); \
	$(MAKE) deploy-flink APP=$(APP) JAR=$$JAR_NAME

deploy-all-flink: upload-flink ## Build, upload, and deploy ALL Flink applications
	@echo "🚀 Deploying all Flink applications..."
	@JAR_NAME=$$(aws s3 ls s3://$(S3_BUCKET)/ --profile $(AWS_PROFILE) | grep "telemetry-processor.*$$(date +%Y%m%d)" | tail -1 | awk '{print $$4}'); \
	for APP in cms-raw-telemetry-processor cms-trip-processor cms-safety-processor cms-maintenance-processor; do \
		echo "📦 Deploying $$APP..."; \
		$(MAKE) deploy-flink APP=$$APP JAR=$$JAR_NAME; \
	done; \
	echo "✅ All Flink applications deployed"
