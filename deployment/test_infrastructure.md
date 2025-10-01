# Infrastructure Stack Testing

## Usage

```bash
# Deploy infrastructure foundation
make infrastructure

# Or use interactive deployment
make deploy
# Select option 0: Infrastructure Foundation
```

## What Gets Deployed

✅ **VPC** - Shared VPC (10.0.0.0/16) with public/private subnets  
✅ **Security Groups** - Internal services communication  
✅ **ElastiCache Redis** - Vehicle state cache (cache.t3.micro)  
✅ **Outputs** - VPC ID, subnet IDs, Redis endpoint for other stacks  

## Dependencies

**Before Infrastructure:**
- None (foundation stack)

**After Infrastructure:**
- MSK Stack (uses VPC)
- Flink Stack (uses VPC + Redis)
- UI Stack (uses Redis endpoint)

## Verification

```bash
# Check stack status
make status

# Get Redis endpoint
aws cloudformation describe-stacks \
  --stack-name cms-dev-infrastructure \
  --query 'Stacks[0].Outputs[?OutputKey==`RedisEndpoint`].OutputValue' \
  --output text
```

## Benefits

- **Clean Dependencies**: All other stacks reference infrastructure exports
- **Shared Resources**: Single VPC reduces complexity and cost
- **ElastiCache Ready**: Redis available for vehicle state caching
- **Proper Ordering**: Foundation-first deployment approach
