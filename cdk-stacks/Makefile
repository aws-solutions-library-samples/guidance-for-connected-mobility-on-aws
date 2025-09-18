# CMS Modular CDK Deployment Makefile
.PHONY: help install bootstrap deploy-all deploy-storage deploy-iot deploy-msk deploy-flink deploy-ui destroy-all clean check-java11

# Default target
.DEFAULT_GOAL := help

# Colors for output
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[0;33m
BLUE := \033[0;34m
NC := \033[0m # No Color

# Configuration
AWS_REGION := us-east-1
DEPLOYMENT_STAGE := dev

# Java 11 detection for Flink builds
JAVA_11_HOME := /usr/libexec/java_home -v 11

# AWS Profile Selection
AWS_PROFILES := $(shell aws configure list-profiles 2>/dev/null || echo "default")
PROFILE_COUNT := $(shell echo $(AWS_PROFILES) | wc -w)

# Auto-select profile if only one exists, otherwise use default for interactive targets
ifeq ($(PROFILE_COUNT),1)
    AWS_PROFILE := $(AWS_PROFILES)
else
    # For interactive deployment, we'll set this dynamically
    # For manual commands, require AWS_PROFILE to be specified
    ifndef AWS_PROFILE
        ifneq ($(MAKECMDGOALS),deploy)
        ifneq ($(MAKECMDGOALS),deploy-interactive)
        ifneq ($(MAKECMDGOALS),help)
            $(info $(YELLOW)Multiple AWS profiles found: $(AWS_PROFILES)$(NC))
            $(info $(BLUE)Please specify a profile: make deploy-msk AWS_PROFILE=<profile-name>$(NC))
            $(info $(BLUE)Or set it as default: export AWS_PROFILE=<profile-name>$(NC))
            $(error No AWS_PROFILE specified)
        endif
        endif
        endif
        AWS_PROFILE := default
    endif
endif

deploy-interactive: ## Interactive deployment with profile and component selection
	@echo "$(BLUE)🚀 Interactive CMS Deployment$(NC)"
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
	echo "$(BLUE)Stack names will be: cms-$$selected_stage-*$(NC)"; \
	echo ""; \
	echo "$(GREEN)Deployment Options:$(NC)"; \
	echo "1. Deploy All (Parallel: MSK + VPC async + Storage + IoT + Flink + UI + Integration)"; \
	echo "2. Deploy MSK only"; \
	echo "3. Deploy Flink only"; \
	echo "4. Deploy UI only"; \
	echo "5. Deploy MSK + Flink"; \
	echo "6. Deploy MSK + Flink + UI"; \
	echo "7. Full sequential deployment (MSK + Flink + UI + Complete Integration)"; \
	echo "8. Complete integration only (IoT rules + Flink config + Start apps)"; \
	echo "9. Start Flink applications only"; \
	echo ""; \
	read -p "Select deployment option (1-9): " deploy_choice; \
	echo ""; \
	case $$deploy_choice in \
		1) echo "$(BLUE)🚀 Deploying All Stacks...$(NC)"; \
		   $(MAKE) deploy-all AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		2) echo "$(BLUE)🚀 Deploying MSK stack...$(NC)"; \
		   $(MAKE) deploy-msk AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		3) echo "$(BLUE)🚀 Deploying Flink stack...$(NC)"; \
		   $(MAKE) deploy-flink AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		4) echo "$(BLUE)🚀 Deploying UI stack...$(NC)"; \
		   $(MAKE) deploy-ui AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		5) echo "$(BLUE)🚀 Deploying MSK + Flink...$(NC)"; \
		   $(MAKE) deploy-msk AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage && \
		   $(MAKE) deploy-flink AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		6) echo "$(BLUE)🚀 Deploying MSK + Flink + UI...$(NC)"; \
		   $(MAKE) deploy-msk AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage && \
		   $(MAKE) deploy-flink AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage && \
		   $(MAKE) deploy-ui AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		7) echo "$(BLUE)🚀 Full sequential deployment...$(NC)"; \
		   $(MAKE) deploy-sequential AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		8) echo "$(BLUE)🔗 Running complete integration...$(NC)"; \
		   $(MAKE) integrate-complete AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		9) echo "$(BLUE)🚀 Starting Flink applications...$(NC)"; \
		   $(MAKE) start-flink-apps AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage ;; \
		*) echo "$(RED)❌ Invalid deployment option$(NC)"; exit 1 ;; \
	esac; \
	echo ""; \
	echo "$(GREEN)🎉 Deployment completed!$(NC)"; \
	$(MAKE) status AWS_PROFILE=$$selected_profile DEPLOYMENT_STAGE=$$selected_stage

deploy: deploy-interactive ## Alias for interactive deployment

help: ## Show this help message
	@echo "$(BLUE)🚀 CMS Modular CDK Deployment$(NC)"
	@echo "================================"
	@echo ""
	@echo "$(GREEN)🎯 Quick Start:$(NC)"
	@echo "  make deploy              - Interactive deployment (RECOMMENDED)"
	@echo ""
	@echo "$(GREEN)Setup Commands:$(NC)"
	@echo "  make install        - Install dependencies and setup environment"
	@echo "  make bootstrap      - Bootstrap CDK in the target account"
	@echo ""
	@echo "$(GREEN)Manual Deployment Commands:$(NC)"
	@echo "  make build-jar                   - Build Flink JAR file (requires Java 11)"
	@echo "  make deploy-sequential           - Deploy all stacks sequentially with integrations"
	@echo "  make deploy-msk                  - Deploy MSK stack"
	@echo "  make deploy-flink                - Deploy Flink stack (builds JAR first)"
	@echo "  make integrate-iot-msk           - Integrate IoT rules with MSK"
	@echo "  make integrate-flink-msk         - Update Flink with MSK configuration"
	@echo ""
	@echo "$(GREEN)Profile Selection (for manual commands):$(NC)"
	@echo "  Current: AWS_PROFILE=$(AWS_PROFILE)"
	@echo "  Available: $(AWS_PROFILES)"
	@echo "  Usage: make deploy-msk AWS_PROFILE=my-profile"
	@echo ""
	@echo "$(GREEN)Management Commands:$(NC)"
	@echo "  make status      - Show deployment status"
	@echo "  make clean       - Clean build artifacts"
	@echo "  make destroy-all - Destroy all stacks (CAREFUL!)"

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

deploy-storage: ## Deploy storage stack
	@echo "$(BLUE)🗄️  Deploying storage stack...$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$$(aws configure get region --profile $(AWS_PROFILE) || echo "us-east-1") \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-storage --require-approval never --output cdk.out.storage.$$$$
	@echo "$(GREEN)✅ Storage stack deployed$(NC)"

deploy-iot: ## Deploy IoT stack
	@echo "$(BLUE)📡 Deploying IoT stack...$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$$(aws configure get region --profile $(AWS_PROFILE) || echo "us-east-1") \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-iot --require-approval never --output cdk.out.iot.$$$$
	@echo "$(GREEN)✅ IoT stack deployed$(NC)"

deploy-msk-only: ## Deploy MSK stack without VPC connectivity (for parallel deployment)
	@echo "$(BLUE)📨 Deploying MSK stack with profile: $(AWS_PROFILE)$(NC)"
	@source .venv/bin/activate && \
	export AWS_PROFILE=$(AWS_PROFILE) && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-msk --require-approval never --profile $(AWS_PROFILE) --output cdk.out.msk.$$$$
	@echo "$(GREEN)✅ MSK stack deployed$(NC)"

deploy-msk: deploy-msk-only ## Deploy MSK stack with VPC connectivity (sequential)
	@echo "$(BLUE)🔧 Enabling VPC connectivity for SCRAM (IoT Core only)...$(NC)"
	@$(MAKE) enable-msk-vpc-connectivity AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(GREEN)✅ VPC connectivity SCRAM enabled for IoT Core$(NC)"

enable-msk-vpc-connectivity: ## Enable VPC connectivity SCRAM auth for IoT Core (IAM disabled)
	@echo "$(BLUE)🔧 Enabling MSK VPC connectivity for SCRAM authentication (IoT Core)...$(NC)"
	@CLUSTER_ARN=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKClusterArn`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	CURRENT_VERSION=$$(aws kafka describe-cluster --cluster-arn $$CLUSTER_ARN --query 'ClusterInfo.CurrentVersion' --output text --profile $(AWS_PROFILE)); \
	if [ -n "$$CLUSTER_ARN" ] && [ -n "$$CURRENT_VERSION" ]; then \
		echo "Updating cluster: $$CLUSTER_ARN (version: $$CURRENT_VERSION)"; \
		aws kafka update-connectivity \
			--cluster-arn $$CLUSTER_ARN \
			--current-version $$CURRENT_VERSION \
			--connectivity-info '{"VpcConnectivity":{"ClientAuthentication":{"Sasl":{"Iam":{"Enabled":false},"Scram":{"Enabled":true}}}}}' \
			--profile $(AWS_PROFILE); \
		echo "$(GREEN)✅ VPC connectivity update initiated$(NC)"; \
		echo "$(YELLOW)⏳ Update will take 10-15 minutes to complete$(NC)"; \
		echo "$(BLUE)ℹ️  SCRAM enabled for IoT Core, IAM disabled (Flink uses same-VPC)$(NC)"; \
	else \
		echo "$(RED)❌ Could not find MSK cluster ARN or version$(NC)"; \
		exit 1; \
	fi

build-jar: check-java11 ## Build and upload Flink JAR directly to S3
	@echo "$(BLUE)🔨 Building and uploading Flink JAR...$(NC)"
	@export JAVA_HOME=$$($(JAVA_11_HOME)) && \
	cd ../modules/flink && ./build.sh && \
	BUCKET_NAME=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-flink --query 'Stacks[0].Outputs[?OutputKey==`FlinkJarBucketOutput`].OutputValue' --output text --profile $(AWS_PROFILE)) && \
	aws s3 cp target/cms-telemetry-processor-1.0.0.jar s3://$$BUCKET_NAME/jars/ --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ JAR built and uploaded to S3$(NC)"

clean-flink-logs: ## Clean existing Flink log groups to avoid conflicts
	@echo "$(BLUE)🧹 Cleaning existing Flink log groups...$(NC)"
	@for log_group in "/aws/kinesis-analytics/cms-$(DEPLOYMENT_STAGE)-flink-event-driven-telemetry-processor" \
	                  "/aws/kinesis-analytics/cms-$(DEPLOYMENT_STAGE)-flink-telemetry-enhanced-final" \
	                  "/aws/kinesis-analytics/cms-$(DEPLOYMENT_STAGE)-flink-trip-processor" \
	                  "/aws/kinesis-analytics/cms-$(DEPLOYMENT_STAGE)-flink-safety-processor" \
	                  "/aws/kinesis-analytics/cms-$(DEPLOYMENT_STAGE)-flink-maintenance-processor"; do \
		echo "Checking log group: $$log_group"; \
		aws logs delete-log-group --region $(AWS_REGION) --log-group-name "$$log_group" --profile $(AWS_PROFILE) 2>/dev/null && echo "  ✅ Deleted $$log_group" || echo "  ℹ️  $$log_group not found or already deleted"; \
	done
	@echo "$(GREEN)✅ Log group cleanup completed$(NC)"

deploy-flink: deploy-storage clean-flink-logs ## Deploy Flink stack (depends on storage)
	@echo "$(BLUE)⚡ Deploying Flink stack with profile: $(AWS_PROFILE)$(NC)"
	@MSK_CLUSTER_ARN=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKClusterArn`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	MSK_VPC_ID=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKVpcId`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	MSK_SECURITY_GROUP_ID=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKSecurityGroupId`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	MSK_SUBNET_IDS=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-msk --query 'Stacks[0].Outputs[?OutputKey==`MSKPrivateSubnetIds`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo ""); \
	if [ -n "$$MSK_CLUSTER_ARN" ] && [ -n "$$MSK_VPC_ID" ] && [ -n "$$MSK_SECURITY_GROUP_ID" ] && [ -n "$$MSK_SUBNET_IDS" ]; then \
		echo "$(GREEN)✅ Found MSK configuration:$(NC)"; \
		echo "  VPC: $$MSK_VPC_ID"; \
		echo "  Security Group: $$MSK_SECURITY_GROUP_ID"; \
		echo "  Subnets: $$MSK_SUBNET_IDS"; \
		export MSK_CLUSTER_ARN=$$MSK_CLUSTER_ARN; \
		export MSK_VPC_ID=$$MSK_VPC_ID; \
		export MSK_SECURITY_GROUP_ID=$$MSK_SECURITY_GROUP_ID; \
		export MSK_SUBNET_IDS=$$MSK_SUBNET_IDS; \
	else \
		echo "$(YELLOW)⚠️  MSK configuration not found, deploying without VPC configuration$(NC)"; \
	fi; \
	source .venv/bin/activate && \
	export AWS_PROFILE=$(AWS_PROFILE) && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-flink --require-approval never --profile $(AWS_PROFILE) --output cdk.out.flink.$$$$
	@echo "$(GREEN)✅ Flink stack deployed$(NC)"
	@$(MAKE) build-jar

fix-iot-msk: ## Fix IoT-MSK integration (create VPC destination and rule)
	@echo "$(BLUE)🔧 Fixing IoT-MSK integration with profile: $(AWS_PROFILE)$(NC)"
	@cd scripts && AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) python3 fix_iot_msk.py
	@echo "$(GREEN)✅ IoT-MSK integration fixed$(NC)"

create-iot-rule: ## Create IoT rule (after VPC destination is ready)
	@echo "$(BLUE)📡 Creating IoT rule with profile: $(AWS_PROFILE)$(NC)"
	@cd scripts && AWS_PROFILE=$(AWS_PROFILE) python3 create_iot_rule.py
	@echo "$(GREEN)✅ IoT rule created$(NC)"

deploy-sequential: ## Deploy core stacks (MSK + Flink + UI) with integrations
	@echo "$(BLUE)🚀 Sequential deployment starting...$(NC)"
	@echo "$(BLUE)📨 Step 1: Deploying MSK stack...$(NC)"
	$(MAKE) deploy-msk AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(BLUE)📦 Step 2: Deploying Flink stack...$(NC)"
	$(MAKE) deploy-flink AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(BLUE)🌐 Step 3: Deploying UI stack...$(NC)"
	$(MAKE) deploy-ui AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(BLUE)🔗 Step 4: Running complete integration...$(NC)"
	$(MAKE) integrate-complete AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(GREEN)✅ Sequential deployment completed!$(NC)"

integrate-complete: ## Complete integration: IoT rules + Flink config + Start applications
	@echo "$(BLUE)🔗 Running complete integration with profile: $(AWS_PROFILE)$(NC)"
	@cd scripts && AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) python3 complete_integration.py
	@echo "$(GREEN)✅ Complete integration finished$(NC)"

update-flink-jars: ## Update Flink applications with JAR file
	@echo "$(BLUE)📦 Updating Flink applications with JAR file...$(NC)"
	@cd scripts && AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) python3 update_flink_jar.py
	@echo "$(GREEN)✅ Flink applications updated with JAR$(NC)"

start-flink-apps: update-flink-jars ## Start all Flink applications (updates JARs first)
	@echo "$(BLUE)🚀 Starting Flink applications with profile: $(AWS_PROFILE)$(NC)"
	@for app in cms-$(DEPLOYMENT_STAGE)-flink-event-driven-telemetry-processor \
	           cms-$(DEPLOYMENT_STAGE)-flink-telemetry-enhanced-final \
	           cms-$(DEPLOYMENT_STAGE)-flink-trip-processor \
	           cms-$(DEPLOYMENT_STAGE)-flink-safety-processor \
	           cms-$(DEPLOYMENT_STAGE)-flink-maintenance-processor; do \
		echo "Starting $$app..."; \
		aws kinesisanalyticsv2 start-application --application-name $$app --profile $(AWS_PROFILE) 2>/dev/null || echo "  $$app already running or not ready"; \
	done
	@echo "$(GREEN)✅ Flink applications started$(NC)"

deploy-serverless: ## Deploy MSK Serverless + Flink (FAST - ~3 minutes)
	@echo "$(BLUE)⚡ Fast Serverless Deployment$(NC)"
	@echo "MSK Serverless provisions in ~2 minutes vs 8-12 for regular MSK"
	@echo ""
	@read -p "Select AWS profile: " profile; \
	read -p "Deployment stage [dev]: " stage; \
	stage=$${stage:-dev}; \
	echo "$(BLUE)🚀 Deploying MSK Serverless...$(NC)"; \
	$(MAKE) deploy-msk-serverless AWS_PROFILE=$$profile DEPLOYMENT_STAGE=$$stage && \
	$(MAKE) deploy-flink AWS_PROFILE=$$profile DEPLOYMENT_STAGE=$$stage && \
	$(MAKE) integrate-serverless AWS_PROFILE=$$profile DEPLOYMENT_STAGE=$$stage

deploy-msk-serverless: ## Deploy MSK Serverless stack
	@echo "$(BLUE)⚡ Deploying MSK Serverless with profile: $(AWS_PROFILE)$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-msk-serverless --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ MSK Serverless deployed$(NC)"

integrate-serverless: ## Integrate IoT + Flink with MSK Serverless
	@echo "$(BLUE)🔗 Integrating with MSK Serverless$(NC)"
	@cd scripts && AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) python3 serverless_integration.py
	@echo "$(GREEN)✅ Serverless integration completed$(NC)"

deploy-fast: deploy-serverless ## Alias for serverless deployment

integrate-parallel: ## Run IoT and Flink integrations in parallel
	@echo "$(BLUE)🔄 Running parallel integrations with profile: $(AWS_PROFILE)$(NC)"
	@cd scripts && AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) python3 parallel_integration.py
	@echo "$(GREEN)✅ Parallel integrations completed$(NC)"

	@echo "$(GREEN)🎉 Sequential deployment with integration completed successfully!$(NC)"
	@$(MAKE) status

update-ui-stack: ## Update UI stack with OAC fix
	@echo "$(BLUE)🔄 Updating UI stack with OAC fix...$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-ui --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ UI stack updated$(NC)"

deploy-real-ui: ## Build and deploy the actual React frontend
	@echo "$(BLUE)🏗️  Building React frontend...$(NC)"
	@cd ../modules/cms_ui/source/frontend && npm run build
	@echo "$(BLUE)🚀 Deploying real UI to S3...$(NC)"
	@cd ../modules/cms_ui/source/frontend && aws s3 sync build/ s3://cms-$(DEPLOYMENT_STAGE)-ui-frontend-195026230833/ --delete --profile $(AWS_PROFILE)
	@echo "$(BLUE)🔄 Invalidating CloudFront cache...$(NC)"
	@DISTRIBUTION_ID=$$(aws cloudfront list-distributions --query 'DistributionList.Items[?contains(DomainName, `d13d2q5vq7u93s`)].Id' --output text --profile $(AWS_PROFILE)); \
	aws cloudfront create-invalidation --distribution-id $$DISTRIBUTION_ID --paths "/*" --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Real UI deployed! Wait 2-3 minutes for CloudFront propagation$(NC)"
	@echo "$(GREEN)🌐 CloudFront URL: https://d13d2q5vq7u93s.cloudfront.net$(NC)"

deploy-ui-content: ## Upload UI content to S3 bucket
	@echo "$(BLUE)🌐 Uploading UI content...$(NC)"
	@BUCKET_NAME=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-ui --query 'Stacks[0].Outputs[?contains(OutputKey, `Bucket`) || contains(OutputValue, `s3://`)].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo "cms-$(DEPLOYMENT_STAGE)-ui-frontend-195026230833"); \
	if [ -f "index.html" ]; then \
		aws s3 cp index.html s3://$$BUCKET_NAME/ --content-type "text/html" --profile $(AWS_PROFILE); \
		echo "$(GREEN)✅ UI content uploaded to s3://$$BUCKET_NAME$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  No index.html found, creating basic one...$(NC)"; \
		echo '<!DOCTYPE html><html><head><title>CMS</title></head><body><h1>Connected Mobility Solution</h1><p>Deployment Complete!</p></body></html>' > index.html; \
		aws s3 cp index.html s3://$$BUCKET_NAME/ --content-type "text/html" --profile $(AWS_PROFILE); \
		echo "$(GREEN)✅ Basic UI content uploaded$(NC)"; \
	fi

fix-cloudfront-complete: update-ui-stack deploy-ui-content ## Complete CloudFront fix with website hosting
	@echo "$(BLUE)🔧 Enabling S3 website hosting...$(NC)"
	@BUCKET_NAME="cms-$(DEPLOYMENT_STAGE)-ui-frontend-195026230833"; \
	aws s3api delete-public-access-block --bucket $$BUCKET_NAME --profile $(AWS_PROFILE) 2>/dev/null || true; \
	aws s3 website s3://$$BUCKET_NAME --index-document index.html --error-document index.html --profile $(AWS_PROFILE); \
	echo "$(GREEN)✅ S3 website hosting enabled$(NC)"
	@echo "$(BLUE)🔄 Invalidating CloudFront cache...$(NC)"
	@DISTRIBUTION_ID=$$(aws cloudfront list-distributions --query 'DistributionList.Items[?contains(DomainName, `d13d2q5vq7u93s`)].Id' --output text --profile $(AWS_PROFILE)); \
	if [ -n "$$DISTRIBUTION_ID" ]; then \
		aws cloudfront create-invalidation --distribution-id $$DISTRIBUTION_ID --paths "/*" --profile $(AWS_PROFILE); \
		echo "$(GREEN)✅ CloudFront cache invalidated$(NC)"; \
		echo "$(BLUE)⏳ Wait 2-3 minutes for propagation$(NC)"; \
		echo "$(GREEN)🌐 CloudFront URL: https://d13d2q5vq7u93s.cloudfront.net$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  Could not find distribution ID$(NC)"; \
	fi
	@echo "$(BLUE)🌐 Uploading UI content...$(NC)"
	@BUCKET_NAME=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-ui --query 'Stacks[0].Outputs[?contains(OutputKey, `Bucket`) || contains(OutputValue, `s3://`)].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo "cms-$(DEPLOYMENT_STAGE)-ui-frontend-195026230833"); \
	if [ -f "index.html" ]; then \
		aws s3 cp index.html s3://$$BUCKET_NAME/ --content-type "text/html" --profile $(AWS_PROFILE); \
		echo "$(GREEN)✅ UI content uploaded to s3://$$BUCKET_NAME$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  No index.html found, creating basic one...$(NC)"; \
		echo '<!DOCTYPE html><html><head><title>CMS</title></head><body><h1>Connected Mobility Solution</h1><p>Deployment Complete!</p></body></html>' > index.html; \
		aws s3 cp index.html s3://$$BUCKET_NAME/ --content-type "text/html" --profile $(AWS_PROFILE); \
		echo "$(GREEN)✅ Basic UI content uploaded$(NC)"; \
	fi

build-frontend: ## Build React frontend with correct config
	@echo "$(BLUE)🏗️  Building React frontend...$(NC)"
	@cd scripts && ./build_frontend.sh
	@echo "$(GREEN)✅ Frontend built$(NC)"

deploy-ui: deploy-storage ## Deploy UI stack infrastructure first, then build frontend
	@echo "$(BLUE)🌐 Deploying UI stack infrastructure with profile: $(AWS_PROFILE)$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-ui --require-approval never --profile $(AWS_PROFILE) --output cdk.out.ui.$$$$
	@echo "$(GREEN)✅ UI infrastructure deployed$(NC)"
	@echo "$(BLUE)🏗️  Now building and deploying frontend...$(NC)"
	@$(MAKE) build-frontend AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(GREEN)✅ UI stack fully deployed$(NC)"

deploy-all-parallel: deploy-msk-only ## Deploy all stacks with parallel execution and async VPC connectivity
	@echo "$(BLUE)🚀 Starting parallel deployment with async VPC connectivity...$(NC)"
	@echo "$(BLUE)🔧 Starting VPC connectivity configuration (async)...$(NC)"
	@cd scripts && AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) python3 enable_vpc_connectivity.py $(DEPLOYMENT_STAGE) $(AWS_PROFILE) &
	@echo "$(BLUE)📦 Deploying other stacks in parallel...$(NC)"
	@$(MAKE) deploy-flink AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) & \
	$(MAKE) deploy-ui AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) & \
	$(MAKE) deploy-storage AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) & \
	$(MAKE) deploy-iot AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) & \
	wait
	@echo "$(BLUE)✅ All parallel deployments completed!$(NC)"
	@echo "$(BLUE)🔗 Running complete integration (will wait for VPC connectivity if needed)...$(NC)"
	$(MAKE) integrate-complete AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(GREEN)🎉 All stacks deployed with complete integration!$(NC)"
	@$(MAKE) status

deploy-all: deploy-all-parallel ## Alias for parallel deployment

# Utility targets
check-java11: ## Check if Java 11 is available
	@echo "$(BLUE)☕ Checking Java 11 availability...$(NC)"
	@if $(JAVA_11_HOME) >/dev/null 2>&1; then \
		JAVA_11_PATH=$$($(JAVA_11_HOME)); \
		echo "$(GREEN)✅ Java 11 found at: $$JAVA_11_PATH$(NC)"; \
	else \
		echo "$(RED)❌ Java 11 not found$(NC)"; \
		echo "$(YELLOW)Please install Java 11:$(NC)"; \
		echo "$(YELLOW)  brew install openjdk@11$(NC)"; \
		echo "$(YELLOW)Flink requires Java 11 for compilation and runtime compatibility$(NC)"; \
		exit 1; \
	fi

status: ## Show deployment status
	@echo "$(BLUE)📊 Deployment Status$(NC)"
	@echo "===================="
	@source .venv/bin/activate && \
	for stack in cms-$(DEPLOYMENT_STAGE)-storage cms-$(DEPLOYMENT_STAGE)-iot cms-$(DEPLOYMENT_STAGE)-msk cms-$(DEPLOYMENT_STAGE)-flink cms-$(DEPLOYMENT_STAGE)-ui; do \
		status=$$(aws cloudformation describe-stacks --stack-name $$stack --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "NOT_DEPLOYED"); \
		if [ "$$status" = "CREATE_COMPLETE" ] || [ "$$status" = "UPDATE_COMPLETE" ]; then \
			echo "├── $$stack: $(GREEN)✅ $$status$(NC)"; \
		elif [ "$$status" = "NOT_DEPLOYED" ]; then \
			echo "├── $$stack: $(YELLOW)⏳ NOT_DEPLOYED$(NC)"; \
		else \
			echo "├── $$stack: $(RED)❌ $$status$(NC)"; \
		fi; \
	done

clean: ## Clean build artifacts
	@echo "$(BLUE)🧹 Cleaning build artifacts...$(NC)"
	@rm -rf cdk.out
	@rm -rf .venv
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)✅ Clean completed$(NC)"

destroy-all: ## Destroy all stacks (CAREFUL!)
	@echo "$(RED)⚠️  WARNING: This will destroy ALL stacks!$(NC)"
	@read -p "Are you sure? Type 'yes' to continue: " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		echo "$(YELLOW)🗑️  Destroying stacks in reverse order...$(NC)"; \
		source .venv/bin/activate && \
		for stack in cms-$(DEPLOYMENT_STAGE)-ui cms-$(DEPLOYMENT_STAGE)-flink cms-$(DEPLOYMENT_STAGE)-msk cms-$(DEPLOYMENT_STAGE)-iot cms-$(DEPLOYMENT_STAGE)-storage; do \
			echo "Destroying $$stack..."; \
			DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) cdk destroy $$stack --force || true; \
		done; \
		echo "$(GREEN)✅ All stacks destroyed$(NC)"; \
	else \
		echo "$(BLUE)ℹ️  Destruction cancelled$(NC)"; \
	fi

# Individual stack destruction (for testing)
destroy-ui:
	@source .venv/bin/activate && DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) cdk destroy cms-$(DEPLOYMENT_STAGE)-ui --force

destroy-flink:
	@source .venv/bin/activate && DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) cdk destroy cms-$(DEPLOYMENT_STAGE)-flink --force

destroy-msk:
	@source .venv/bin/activate && DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) cdk destroy cms-$(DEPLOYMENT_STAGE)-msk --force

destroy-iot:
	@source .venv/bin/activate && DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) cdk destroy cms-$(DEPLOYMENT_STAGE)-iot --force

destroy-storage:
	@source .venv/bin/activate && DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) cdk destroy cms-$(DEPLOYMENT_STAGE)-storage --force
# Add parallel deployment targets to your existing Makefile

# Parallel deployment of MSK and Flink (they don't have hard dependencies)
deploy-msk-flink-parallel: deploy-storage ## Deploy MSK and Flink in parallel
	@echo "$(BLUE)🚀 Deploying MSK and Flink in parallel...$(NC)"
	@$(MAKE) deploy-msk AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) & \
	$(MAKE) deploy-flink-base AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) & \
	wait
	@echo "$(GREEN)✅ Parallel deployment completed$(NC)"
	@$(MAKE) integrate-msk-flink AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)

# Flink deployment without JAR build (JAR built separately)
deploy-flink-base: deploy-storage clean-flink-logs ## Deploy Flink stack without JAR build
	@echo "$(BLUE)⚡ Deploying Flink stack (base) with profile: $(AWS_PROFILE)$(NC)"
	@source .venv/bin/activate && \
	export AWS_PROFILE=$(AWS_PROFILE) && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-flink --require-approval never --profile $(AWS_PROFILE) --output cdk.out.flink
	@echo "$(GREEN)✅ Flink stack (base) deployed$(NC)"

# Combined integration step
integrate-msk-flink: ## Integrate MSK and Flink after parallel deployment
	@echo "$(BLUE)🔗 Running MSK-Flink integration...$(NC)"
	@$(MAKE) integrate-iot-msk AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) & \
	$(MAKE) integrate-flink-msk AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) & \
	$(MAKE) build-jar AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) & \
	wait
	@echo "$(GREEN)✅ All integrations completed$(NC)"

# Fast deployment option for interactive menu
deploy-fast: deploy-storage deploy-iot deploy-msk-flink-parallel deploy-ui ## Fast parallel deployment
	@echo "$(GREEN)🎉 Fast deployment completed!$(NC)"
	@$(MAKE) status
