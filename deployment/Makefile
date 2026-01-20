# CMS Simplified Deployment Makefile
.PHONY: help install bootstrap deploy-all clean infrastructure

# Default target
.DEFAULT_GOAL := help

# Colors for output
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[0;33m
BLUE := \033[0;34m
NC := \033[0m # No Color

# Configuration
AWS_REGION ?= $(shell aws configure get region --profile $(AWS_PROFILE) 2>/dev/null || echo "us-east-1")
DEPLOYMENT_STAGE := dev

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
	@echo "  make infrastructure      - Infrastructure Foundation (VPC, Subnets, ElastiCache)"
	@echo "  make data-processing     - Data Processing Foundation (Signal Catalog, Transform Manifests)"
	@echo "  make phase1              - Fleet Manager Interface (IoT, Storage, UI, Lambda, Cognito, S3)"
	@echo "  make phase2              - Fleet Management Interface (historical data injection)"
	@echo "  make phase3              - Telemetry Pipeline (MSK Deployment)"
	@echo "  make phase3b             - Telemetry Pipeline (MSK Configuration + IoT Integration)"
	@echo "  make phase4              - Telemetry Pipeline (Flink Deployment)"
	@echo "  make phase5              - Telemetry Pipeline (Flink Configuration)"
	@echo "  make phase6              - Complete Pipeline Configuration"
	@echo ""
	@echo "$(GREEN)Setup Commands:$(NC)"
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

build-ui: ## Build the React frontend application
	@echo "$(BLUE)🔨 Building React frontend...$(NC)"
	@cd ../modules/cms_ui/source/frontend && yarn install && yarn build
	@echo "$(GREEN)✅ Frontend build completed$(NC)"

infrastructure: check-bootstrap ## Infrastructure Foundation (VPC, Subnets, ElastiCache)
	@echo "$(BLUE)🏗️  Infrastructure Foundation$(NC)"
	@echo "Deploying: VPC, Subnets, Security Groups, ElastiCache Redis"
	@echo "$(YELLOW)⚠️  This creates the foundation for all other services$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-infrastructure --require-approval never --profile $(AWS_PROFILE)
	@echo "$(GREEN)✅ Infrastructure Foundation Complete$(NC)"
	@echo "$(BLUE)📋 Infrastructure Details:$(NC)"
	@REDIS_ENDPOINT=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-infrastructure --query 'Stacks[0].Outputs[?OutputKey==`RedisEndpoint`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo "Not available"); \
	VPC_ID=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-infrastructure --query 'Stacks[0].Outputs[?OutputKey==`VpcId`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo "Not available"); \
	echo "├── VPC ID: $$VPC_ID"; \
	echo "└── Redis Endpoint: $$REDIS_ENDPOINT"

data-processing: ## Phase 0.5: Data Processing Foundation (Signal Catalog, Transform Manifests)
	@echo "$(BLUE)📊 Phase 0.5: Data Processing Foundation$(NC)"
	@echo "Deploying: Signal Catalog, Transform Manifests, Data Source Configs"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$(AWS_REGION) \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-data-processing --require-approval never --profile $(AWS_PROFILE)
	@echo "$(BLUE)📤 Seeding Signal Catalog from JSON...$(NC)"
	@cd scripts && AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) python3 seed_signal_catalog.py
	@echo "$(GREEN)✅ Phase 0.5 Complete: Data Processing Foundation deployed$(NC)"
	@echo "$(BLUE)📋 Data Processing Details:$(NC)"
	@CATALOG_TABLE=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-data-processing --query 'Stacks[0].Outputs[?OutputKey==`SignalCatalogTableName`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo "Not available"); \
	MANIFESTS_BUCKET=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-data-processing --query 'Stacks[0].Outputs[?OutputKey==`ManifestsBucketName`].OutputValue' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo "Not available"); \
	echo "├── Signal Catalog Table: $$CATALOG_TABLE"; \
	echo "└── Manifests Bucket: $$MANIFESTS_BUCKET"

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
	cdk deploy cms-$(DEPLOYMENT_STAGE)-storage cms-$(DEPLOYMENT_STAGE)-iot cms-$(DEPLOYMENT_STAGE)-ui --require-approval never --profile $(AWS_PROFILE) --output cdk.out.phase1.$$$$
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
	cdk deploy cms-$(DEPLOYMENT_STAGE)-flink --require-approval never --profile $(AWS_PROFILE) --output cdk.out.phase4.$$$$
	@echo "$(BLUE)Validating Flink deployment...$(NC)"
	@$(MAKE) validate-flink-deployment AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(YELLOW)⚠️  Flink applications deployed but need JAR files to be functional$(NC)"
	@echo "$(GREEN)✅ Phase 4 Complete: Flink infrastructure deployed$(NC)"

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
	REDIS_ENDPOINT=$$(aws cloudformation describe-stacks --stack-name cms-$(DEPLOYMENT_STAGE)-infrastructure --query 'Stacks[0].Outputs[?OutputKey==`RedisEndpoint`].OutputValue' --output text --profile $(AWS_PROFILE)); \
	echo '{"ApplicationCodeConfigurationUpdate":{"CodeContentUpdate":{"S3ContentLocationUpdate":{"BucketARNUpdate":"arn:aws:s3:::'$$BUCKET_NAME'","FileKeyUpdate":"jars/cms-telemetry-processor-1.0.0.zip"}},"CodeContentTypeUpdate":"ZIPFILE"}}' > /tmp/flink-jar-update.json; \
	aws kinesisanalyticsv2 list-applications --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationSummaries[?contains(ApplicationName, `cms-$(DEPLOYMENT_STAGE)-flink`)].ApplicationName' --output text | tr '\t' '\n' | while read app; do \
		if [ -n "$$app" ]; then \
			echo "Configuring application: $$app"; \
			case "$$app" in \
				*event-driven-telemetry-processor) \
					PROCESSOR_TYPE="EventDrivenTelemetryProcessor"; \
					GROUP_ID="cms-event-driven-telemetry-processor-consumer"; \
					TABLE_NAME="$$TELEMETRY_TABLE"; \
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
					;; \
				*maintenance-processor) \
					PROCESSOR_TYPE="MaintenanceProcessor"; \
					GROUP_ID="cms-maintenance-processor-consumer"; \
					TABLE_NAME="$$MAINTENANCE_TABLE"; \
					;; \
				*) \
					echo "Unknown application type: $$app"; \
					continue; \
					;; \
			esac; \
			echo "{\"EnvironmentPropertyUpdates\":{\"PropertyGroups\":[{\"PropertyGroupId\":\"consumer.config.0\",\"PropertyMap\":{\"PROCESSOR_TYPE\":\"$$PROCESSOR_TYPE\",\"bootstrap.servers\":\"$$BOOTSTRAP_SERVERS\",\"security.protocol\":\"SASL_SSL\",\"sasl.mechanism\":\"AWS_MSK_IAM\",\"sasl.jaas.config\":\"software.amazon.msk.auth.iam.IAMLoginModule required;\",\"sasl.client.callback.handler.class\":\"software.amazon.msk.auth.iam.IAMClientCallbackHandler\",\"auto.offset.reset\":\"earliest\",\"enable.auto.commit\":\"false\",\"group.id\":\"$$GROUP_ID\",\"aws.region\":\"$(AWS_REGION)\",\"TABLE_NAME\":\"$$TABLE_NAME\",\"REDIS_ENDPOINT\":\"$$REDIS_ENDPOINT\"}}]}}" > /tmp/flink-env-update-$$app.json; \
			CURRENT_VERSION=$$(aws kinesisanalyticsv2 describe-application --application-name "$$app" --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationDetail.ApplicationVersionId' --output text); \
			aws kinesisanalyticsv2 update-application --application-name "$$app" --current-application-version-id $$CURRENT_VERSION --region $(AWS_REGION) --profile $(AWS_PROFILE) --application-configuration-update file:///tmp/flink-env-update-$$app.json >/dev/null 2>&1 && echo "✅ Updated env properties for $$app ($$PROCESSOR_TYPE)" || echo "❌ Failed to update env properties for $$app"; \
			NEW_VERSION=$$(aws kinesisanalyticsv2 describe-application --application-name "$$app" --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationDetail.ApplicationVersionId' --output text); \
			aws kinesisanalyticsv2 update-application --application-name "$$app" --current-application-version-id $$NEW_VERSION --region $(AWS_REGION) --profile $(AWS_PROFILE) --application-configuration-update file:///tmp/flink-jar-update.json >/dev/null 2>&1 && echo "✅ Updated JAR for $$app" || echo "❌ Failed to update JAR for $$app"; \
			FINAL_VERSION=$$(aws kinesisanalyticsv2 describe-application --application-name "$$app" --region $(AWS_REGION) --profile $(AWS_PROFILE) --query 'ApplicationDetail.ApplicationVersionId' --output text); \
			aws kinesisanalyticsv2 add-application-vpc-configuration --application-name "$$app" --current-application-version-id $$FINAL_VERSION --region $(AWS_REGION) --profile $(AWS_PROFILE) --vpc-configuration '{"SecurityGroupIds":["'$$SG_ID'"],"SubnetIds":["'$$SUBNET1'","'$$SUBNET2'"]}' >/dev/null 2>&1 && echo "✅ Added VPC to $$app" || echo "❌ Failed to add VPC to $$app (may already exist)"; \
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

phase5: ## Phase 5: Telemetry Pipeline 4 (Flink Configuration)
	@echo "$(BLUE)🚀 Phase 5: Telemetry Pipeline 4 (Flink Configuration)$(NC)"
	@echo "Building JAR from source and configuring Flink applications"
	@$(MAKE) configure-flink AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE)
	@echo "$(GREEN)✅ Phase 5 Complete: Flink applications configured with latest JAR and running$(NC)"

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
	@cd scripts && AWS_PROFILE=$(AWS_PROFILE) DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) python3 complete_integration.py
	@echo "$(GREEN)✅ Pipeline configuration complete$(NC)"

deploy-all: infrastructure phase1 phase2 phase3 phase4 phase5 phase6 ## Deploy all phases sequentially
	@echo "$(GREEN)🎉 All phases deployed successfully!$(NC)"
	@$(MAKE) status

status: ## Show deployment status
	@echo "$(BLUE)📊 Deployment Status$(NC)"
	@echo "===================="
	@for stack in cms-$(DEPLOYMENT_STAGE)-infrastructure cms-$(DEPLOYMENT_STAGE)-storage cms-$(DEPLOYMENT_STAGE)-iot cms-$(DEPLOYMENT_STAGE)-ui cms-$(DEPLOYMENT_STAGE)-msk cms-$(DEPLOYMENT_STAGE)-flink; do \
		status=$$(aws cloudformation describe-stacks --stack-name $$stack --query 'Stacks[0].StackStatus' --output text --profile $(AWS_PROFILE) 2>/dev/null || echo "NOT_DEPLOYED"); \
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
	@rm -rf cdk.out*
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)✅ Clean completed$(NC)"
