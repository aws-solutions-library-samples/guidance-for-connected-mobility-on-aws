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

help: ## Show this help message
	@echo "$(BLUE)🚀 CMS Modular CDK Deployment$(NC)"
	@echo "================================"
	@echo ""
	@echo "$(GREEN)Setup Commands:$(NC)"
	@echo "  make install     - Install dependencies and setup environment"
	@echo "  make bootstrap   - Bootstrap CDK in the target account"
	@echo ""
	@echo "$(GREEN)Deployment Commands:$(NC)"
	@echo "  make deploy-all     - Deploy all stacks in order"
	@echo "  make deploy-storage - Deploy storage stack (DynamoDB)"
	@echo "  make deploy-iot     - Deploy IoT stack"
	@echo "  make deploy-msk     - Deploy MSK stack"
	@echo "  make deploy-flink   - Deploy Flink stack"
	@echo "  make deploy-ui      - Deploy UI stack"
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
	cdk deploy cms-$(DEPLOYMENT_STAGE)-storage --require-approval never
	@echo "$(GREEN)✅ Storage stack deployed$(NC)"

deploy-iot: ## Deploy IoT stack
	@echo "$(BLUE)📡 Deploying IoT stack...$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$$(aws configure get region --profile $(AWS_PROFILE) || echo "us-east-1") \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-iot --require-approval never
	@echo "$(GREEN)✅ IoT stack deployed$(NC)"

deploy-msk: deploy-iot ## Deploy MSK stack (depends on IoT)
	@echo "$(BLUE)📨 Deploying MSK stack...$(NC)"
	@source .venv/bin/activate && \
	CDK_DEFAULT_ACCOUNT=$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text) \
	CDK_DEFAULT_REGION=$$(aws configure get region --profile $(AWS_PROFILE) || echo "us-east-1") \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) \
	cdk deploy cms-$(DEPLOYMENT_STAGE)-msk --require-approval never
	@echo "$(GREEN)✅ MSK stack deployed$(NC)"

deploy-flink: deploy-storage deploy-msk check-java11 ## Deploy Flink stack (depends on storage and MSK)
	@echo "$(BLUE)⚡ Deploying Flink stack...$(NC)"
	@source .venv/bin/activate && \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) cdk deploy cms-$(DEPLOYMENT_STAGE)-flink --require-approval never
	@echo "$(GREEN)✅ Flink stack deployed$(NC)"

deploy-ui: deploy-storage ## Deploy UI stack (depends on storage)
	@echo "$(BLUE)🌐 Deploying UI stack...$(NC)"
	@source .venv/bin/activate && \
	DEPLOYMENT_STAGE=$(DEPLOYMENT_STAGE) AWS_PROFILE=$(AWS_PROFILE) cdk deploy cms-$(DEPLOYMENT_STAGE)-ui --require-approval never
	@echo "$(GREEN)✅ UI stack deployed$(NC)"

deploy-all: deploy-storage deploy-iot deploy-msk deploy-flink deploy-ui ## Deploy all stacks in correct order
	@echo "$(GREEN)🎉 All stacks deployed successfully!$(NC)"
	@$(MAKE) status

# Utility targets
check-java11: ## Check if Java 11 is available
	@echo "$(BLUE)☕ Checking Java 11 availability...$(NC)"
	@if command -v java >/dev/null 2>&1; then \
		JAVA_VERSION=$$(java -version 2>&1 | head -n 1 | cut -d'"' -f2 | cut -d'.' -f1-2); \
		if [ "$$JAVA_VERSION" = "11" ] || [ "$$JAVA_VERSION" = "1.8" ] || [ "$$(echo $$JAVA_VERSION | cut -d'.' -f1)" = "11" ]; then \
			echo "$(GREEN)✅ Java 11 found: $$(java -version 2>&1 | head -n 1)$(NC)"; \
		else \
			echo "$(RED)❌ Java 11 required but found: $$JAVA_VERSION$(NC)"; \
			echo "$(YELLOW)Please install Java 11 or set JAVA_HOME to Java 11 installation$(NC)"; \
			echo "$(YELLOW)Flink requires Java 11 for compilation and runtime compatibility$(NC)"; \
			exit 1; \
		fi; \
	else \
		echo "$(RED)❌ Java not found in PATH$(NC)"; \
		echo "$(YELLOW)Please install Java 11 and ensure it's in your PATH$(NC)"; \
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
