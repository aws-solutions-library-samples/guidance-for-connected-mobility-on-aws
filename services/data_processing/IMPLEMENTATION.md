# Signal Catalog Implementation - Step 1 Complete

## What Was Created

### 1. CDK Stack (`deployment/stacks/data_processing_stack.py`)
Creates:
- **DynamoDB Table**: `cms-{stage}-signal-catalog`
  - Partition Key: `signal_group`
  - Sort Key: `signal_name`
  - GSI: `signal-name-index` (query by signal name)
  - GSI: `status-index` (query active/deprecated signals)

- **DynamoDB Table**: `cms-{stage}-data-source-configs`
  - Partition Key: `source_id`
  - GSI: `source-type-index` (query by type: iot_core, fleetwise, oem)

- **S3 Bucket**: `cms-{stage}-transform-manifests-{account}`
  - Versioned
  - Auto-uploads manifests from `services/data_processing/manifests/`

### 2. Seed Script (`deployment/scripts/seed_signal_catalog.py`)
- Loads `signal-catalog.json`
- Populates DynamoDB with 70+ signals
- Handles errors gracefully
- Shows progress and summary

### 3. Makefile Integration
New target: `make data-processing`
- Deploys CDK stack
- Seeds signal catalog
- Shows deployment details

## How to Deploy

```bash
cd deployment

# Deploy the stack
make data-processing AWS_PROFILE=your-profile DEPLOYMENT_STAGE=dev
```

## What Happens

1. **CDK Deploy** (~2 minutes)
   - Creates DynamoDB tables
   - Creates S3 bucket
   - Uploads default manifests

2. **Seed Catalog** (~10 seconds)
   - Loads signal-catalog.json
   - Inserts 70+ signals into DynamoDB
   - Shows progress per signal

3. **Output**
   ```
   ✅ Phase 0.5 Complete: Data Processing Foundation deployed
   📋 Data Processing Details:
   ├── Signal Catalog Table: cms-dev-signal-catalog
   └── Manifests Bucket: cms-dev-transform-manifests-123456789012
   ```

## Verify Deployment

```bash
# Check DynamoDB table
aws dynamodb scan \
  --table-name cms-dev-signal-catalog \
  --max-items 5 \
  --profile your-profile

# Check S3 bucket
aws s3 ls s3://cms-dev-transform-manifests-123456789012/manifests/ \
  --profile your-profile
```

## What's Next

### Step 2: Signal Catalog API (Lambda)
- CRUD operations for signals
- Query by group, name, status
- Add custom signals via UI

### Step 3: Data Source Management
- Register data sources (FleetWise, OEM)
- Upload transform manifests
- Map source_id → manifest → kafka_topic

### Step 4: Generic Flink Transformer
- Single JAR that reads manifests
- Deploy per data source
- Transform to CMS format

## File Structure

```
services/data_processing/
├── signal-catalog.json              ✅ Created
├── transform-manifest-schema.json   ✅ Created
├── manifests/
│   ├── fleetwise-transform.json    ✅ Created
│   └── oem-transform-template.json ✅ Created
└── README.md                        ✅ Created

deployment/
├── stacks/
│   └── data_processing_stack.py    ✅ Created
├── scripts/
│   └── seed_signal_catalog.py      ✅ Created
├── app.py                           ✅ Updated
└── Makefile                         ✅ Updated
```

## Current Status

✅ **Step 1 Complete**: Signal Catalog Foundation
- DynamoDB tables created
- S3 bucket for manifests
- Seed script working
- Makefile integrated

🔄 **Next**: Step 2 - Signal Catalog API
⏳ **Future**: Step 3 - Data Source Management
⏳ **Future**: Step 4 - Generic Transformer

## Testing

After deployment, test the signal catalog:

```python
import boto3

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('cms-dev-signal-catalog')

# Get all location signals
response = table.query(
    KeyConditionExpression='signal_group = :group',
    ExpressionAttributeValues={':group': 'location'}
)

for signal in response['Items']:
    print(f"{signal['signal_name']}: {signal['description']}")
```

Expected output:
```
ts: Unix timestamp (seconds)
timestamp: ISO 8601 timestamp
lat: Latitude in decimal degrees
lon: Longitude in decimal degrees
spd: Speed in miles per hour
...
```
