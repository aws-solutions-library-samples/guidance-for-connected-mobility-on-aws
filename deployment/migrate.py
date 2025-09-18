#!/usr/bin/env python3
"""
Migration script to help transition from Makefile-based deployment to modular CDK
"""

import os
import sys
import subprocess
import json
import boto3
from typing import Dict, List

class CMSMigrationHelper:
    def __init__(self, aws_profile: str = "target-account", region: str = "us-east-1"):
        self.aws_profile = aws_profile
        self.region = region
        self.session = boto3.Session(profile_name=aws_profile, region_name=region)
        
    def check_existing_resources(self) -> Dict:
        """Check what resources already exist in the account"""
        print("🔍 Checking existing resources...")
        
        resources = {
            "dynamodb_tables": [],
            "msk_clusters": [],
            "flink_applications": [],
            "cloudformation_stacks": []
        }
        
        # Check DynamoDB tables
        try:
            dynamodb = self.session.client('dynamodb')
            tables = dynamodb.list_tables()['TableNames']
            cms_tables = [t for t in tables if 'cms' in t.lower()]
            resources["dynamodb_tables"] = cms_tables
            print(f"  📊 Found {len(cms_tables)} CMS DynamoDB tables")
        except Exception as e:
            print(f"  ❌ Error checking DynamoDB: {e}")
            
        # Check MSK clusters
        try:
            msk = self.session.client('kafka')
            clusters = msk.list_clusters_v2()['ClusterInfoList']
            cms_clusters = [c for c in clusters if 'cms' in c['ClusterName'].lower()]
            resources["msk_clusters"] = cms_clusters
            print(f"  📨 Found {len(cms_clusters)} CMS MSK clusters")
        except Exception as e:
            print(f"  ❌ Error checking MSK: {e}")
            
        # Check Flink applications
        try:
            flink = self.session.client('kinesisanalyticsv2')
            apps = flink.list_applications()['ApplicationSummaries']
            cms_apps = [a for a in apps if 'cms' in a['ApplicationName'].lower()]
            resources["flink_applications"] = cms_apps
            print(f"  ⚡ Found {len(cms_apps)} CMS Flink applications")
        except Exception as e:
            print(f"  ❌ Error checking Flink: {e}")
            
        # Check CloudFormation stacks
        try:
            cf = self.session.client('cloudformation')
            stacks = cf.list_stacks(StackStatusFilter=['CREATE_COMPLETE', 'UPDATE_COMPLETE'])['StackSummaries']
            cms_stacks = [s for s in stacks if 'cms' in s['StackName'].lower()]
            resources["cloudformation_stacks"] = cms_stacks
            print(f"  📚 Found {len(cms_stacks)} CMS CloudFormation stacks")
        except Exception as e:
            print(f"  ❌ Error checking CloudFormation: {e}")
            
        return resources
    
    def generate_migration_plan(self, resources: Dict) -> List[str]:
        """Generate a migration plan based on existing resources"""
        print("\n📋 Generating migration plan...")
        
        plan = []
        
        # Check if we should preserve existing tables
        if resources["dynamodb_tables"]:
            existing_suffix = self.extract_table_suffix(resources["dynamodb_tables"])
            if existing_suffix:
                plan.append(f"export USE_EXISTING_TABLES=true")
                plan.append(f"export EXISTING_TABLE_SUFFIX={existing_suffix}")
                print(f"  📊 Will preserve existing tables with suffix: {existing_suffix}")
        
        # Check MSK cluster configuration
        if resources["msk_clusters"]:
            cluster = resources["msk_clusters"][0]  # Use first cluster
            cluster_arn = cluster['ClusterArn']
            plan.append(f"# Use existing MSK cluster: {cluster_arn}")
            print(f"  📨 Will integrate with existing MSK cluster")
        
        # Migration steps
        plan.extend([
            "",
            "# Migration Steps:",
            "# 1. Deploy storage stack (will use existing tables if configured)",
            "make deploy-storage",
            "",
            "# 2. Deploy IoT stack",
            "make deploy-iot", 
            "",
            "# 3. Deploy MSK stack (will create new serverless cluster)",
            "make deploy-msk",
            "",
            "# 4. Deploy Flink stack",
            "make deploy-flink",
            "",
            "# 5. Deploy UI stack", 
            "make deploy-ui"
        ])
        
        return plan
    
    def extract_table_suffix(self, table_names: List[str]) -> str:
        """Extract common suffix from existing table names"""
        cms_tables = [t for t in table_names if t.startswith('cms-')]
        if not cms_tables:
            return ""
            
        # Look for pattern like cms-631ca2-591631-tablename
        for table in cms_tables:
            parts = table.split('-')
            if len(parts) >= 4:  # cms-hash-timestamp-tablename
                return f"{parts[1]}-{parts[2]}"
        
        return ""
    
    def create_migration_script(self, plan: List[str]):
        """Create a migration script file"""
        script_path = "migrate.sh"
        
        with open(script_path, 'w') as f:
            f.write("#!/bin/bash\n")
            f.write("# CMS Migration Script - Generated automatically\n\n")
            f.write("set -e\n\n")
            
            for line in plan:
                if line.startswith('#') or line.startswith('export') or line.startswith('make'):
                    f.write(f"{line}\n")
                elif line.strip() == "":
                    f.write("\n")
        
        os.chmod(script_path, 0o755)
        print(f"📝 Created migration script: {script_path}")
    
    def validate_prerequisites(self) -> bool:
        """Validate that prerequisites are met"""
        print("✅ Validating prerequisites...")
        
        # Check AWS CLI
        try:
            result = subprocess.run(['aws', '--version'], capture_output=True, text=True)
            print(f"  ✅ AWS CLI: {result.stdout.strip()}")
        except FileNotFoundError:
            print("  ❌ AWS CLI not found")
            return False
            
        # Check CDK
        try:
            result = subprocess.run(['cdk', '--version'], capture_output=True, text=True)
            print(f"  ✅ CDK: {result.stdout.strip()}")
        except FileNotFoundError:
            print("  ❌ CDK not found")
            return False
            
        # Check Python
        try:
            result = subprocess.run([sys.executable, '--version'], capture_output=True, text=True)
            print(f"  ✅ Python: {result.stdout.strip()}")
        except:
            print("  ❌ Python not found")
            return False
            
        # Check AWS credentials
        try:
            sts = self.session.client('sts')
            identity = sts.get_caller_identity()
            print(f"  ✅ AWS Account: {identity['Account']}")
            print(f"  ✅ AWS Profile: {self.aws_profile}")
        except Exception as e:
            print(f"  ❌ AWS credentials error: {e}")
            return False
            
        return True

def main():
    print("🚀 CMS Migration Helper")
    print("=" * 50)
    
    # Initialize helper
    helper = CMSMigrationHelper()
    
    # Validate prerequisites
    if not helper.validate_prerequisites():
        print("\n❌ Prerequisites not met. Please install missing components.")
        sys.exit(1)
    
    # Check existing resources
    resources = helper.check_existing_resources()
    
    # Generate migration plan
    plan = helper.generate_migration_plan(resources)
    
    # Create migration script
    helper.create_migration_script(plan)
    
    print("\n🎉 Migration analysis complete!")
    print("\nNext steps:")
    print("1. Review the generated migrate.sh script")
    print("2. Run: chmod +x migrate.sh && ./migrate.sh")
    print("3. Monitor deployment progress with: make status")
    print("\n💡 Tip: Start with 'make deploy-storage' to test the foundation layer")

if __name__ == "__main__":
    main()
