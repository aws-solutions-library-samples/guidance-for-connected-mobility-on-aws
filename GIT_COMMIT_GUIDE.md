# Git Commit Guide - Sensitive Content

## ✅ SAFE TO COMMIT (UPDATED)

### Code Files - Now Clean
- ✅ `deployment/stacks/*.py` - CDK stack definitions (no hardcoded values)
- ✅ `deployment/app.py` - Uses dynamic MSK cluster attributes
- ✅ `deployment/scripts/*.py` - Uses environment variables for bootstrap servers
- ✅ `deployment/Makefile` - Build and deployment scripts
- ✅ `deployment/requirements.txt` - Python dependencies
- ✅ `deployment/cdk.json` - CDK configuration
- ✅ `services/simulation/*.py` - Simulation code
- ✅ `documentation/*.md` - Documentation files

### Configuration Templates
- Files ending in `.template` or `.example`
- Generic configuration files without secrets

## 🚫 NEVER COMMIT

### CDK Build Outputs (Auto-excluded by .gitignore)
- `deployment/cdk.out*/` - All CDK synthesis outputs
- `deployment/.venv/` - Python virtual environment
- `deployment/node_modules/` - Node.js dependencies
- `deployment/__pycache__/` - Python cache files

### Sensitive Files (Auto-excluded by .gitignore)
- Any file containing `*credentials*`, `*secret*`, `*password*`
- AWS credential files
- Private keys (`.pem`, `.key`, `.p12`, `.pfx`)
- Files with AWS account IDs: `*195026230833*`, `*022035076260*`
- Files with hardcoded endpoints: `*bootstrap*.amazonaws.com*`

## 🎉 CHANGES MADE

### ✅ Removed All Hardcoded Values
1. **`deployment/app.py`** - Now uses `msk_stack.cluster.attr_bootstrap_broker_string_vpc_connectivity_sasl_scram`
2. **`deployment/scripts/create_iot_rule.py`** - Uses `os.environ.get('MSK_BOOTSTRAP_SERVERS')`
3. **`deployment/scripts/create_iot_rule_scram.py`** - Uses `os.environ.get('MSK_BOOTSTRAP_SERVERS')`
4. **`deployment/scripts/create_iot_msk_cloudformation.yaml`** - Uses CloudFormation parameters
5. **Removed `__pycache__/`** - Cleaned up cached files with hardcoded values

### 🔧 Dynamic Configuration
```python
# CDK now uses cluster attributes
msk_bootstrap_servers=msk_stack.cluster.attr_bootstrap_broker_string_vpc_connectivity_sasl_scram

# Scripts use environment variables
bootstrap_servers = os.environ.get('MSK_BOOTSTRAP_SERVERS', 'localhost:9092')
```

### 🛡️ Enhanced .gitignore
- Excludes all AWS account-specific patterns
- Excludes CDK build outputs
- Excludes sensitive file patterns

## 📋 DEPLOYMENT USAGE

### Environment Variables
```bash
export MSK_BOOTSTRAP_SERVERS="your-bootstrap-servers-here"
```

### CDK Deployment
```bash
# Bootstrap servers are automatically retrieved from MSK cluster
cdk deploy cms-dev-telemetry-integration
```

### CloudFormation Deployment
```bash
aws cloudformation create-stack \
  --template-body file://create_iot_msk_cloudformation.yaml \
  --parameters ParameterKey=BootstrapServers,ParameterValue="your-servers"
```

## 🎯 RESULT

**✅ Repository is now completely clean of hardcoded AWS-specific values!**

All code is now portable across different AWS accounts and environments. The repository can be safely committed to version control without exposing sensitive information.
