# Utility Scripts

Collection of utility scripts for managing and maintaining the Connected Mobility.

## Overview

Administrative and maintenance scripts for:
- **Data Management**: Vehicle data operations
- **Table Operations**: DynamoDB table management  
- **Flink Management**: Apache Flink application updates
- **System Maintenance**: Cleanup and optimization

## Scripts

### Data Management

#### `delete-vehicle-data.py`
Remove vehicle data from DynamoDB tables.
```bash
python delete-vehicle-data.py --vehicle-id vehicle-001 --table telemetry
python delete-vehicle-data.py --fleet-id fleet-001 --all-tables
```

#### `find-vehicle-data.py`
Search and locate vehicle data across tables.
```bash
python find-vehicle-data.py --vehicle-id vehicle-001
python find-vehicle-data.py --date-range 2024-01-01 2024-01-31
```

### Table Operations

#### `extract-table-names.py`
Extract and list all DynamoDB table names.
```bash
python extract-table-names.py
python extract-table-names.py --environment prod
```

#### `rename-table.py`
Rename DynamoDB tables with data migration.
```bash
python rename-table.py --old-name old-table --new-name new-table
python rename-table.py --batch-rename --config table-mapping.json
```

### Flink Management

#### `update-flink-tables.py`
Update Flink application table configurations.
```bash
python update-flink-tables.py --application cms-telemetry-processor
python update-flink-tables.py --config flink-config.json
```

## Usage Examples

### Bulk Data Operations
```bash
# Delete all data for a fleet
python delete-vehicle-data.py \
  --fleet-id fleet-production \
  --tables telemetry,trips,alerts \
  --confirm

# Find vehicles with recent activity
python find-vehicle-data.py \
  --active-since 2024-01-01 \
  --output-format json
```

### Table Management
```bash
# List all tables with prefix
python extract-table-names.py \
  --prefix cms-dev \
  --output tables.txt

# Rename tables for environment migration
python rename-table.py \
  --old-prefix cms-dev \
  --new-prefix cms-prod \
  --dry-run
```

### Flink Operations
```bash
# Update table references after rename
python update-flink-tables.py \
  --application cms-telemetry-processor \
  --table-mapping table-mapping.json
```

## Configuration

### Environment Variables
```bash
export AWS_REGION=us-east-1
export AWS_PROFILE=cms-admin
export DRY_RUN=true  # For testing operations
```

### Configuration Files

#### `table-mapping.json`
```json
{
  "cms-dev-telemetry": "cms-prod-telemetry",
  "cms-dev-trips": "cms-prod-trips",
  "cms-dev-alerts": "cms-prod-alerts"
}
```

#### `flink-config.json`
```json
{
  "application": "cms-telemetry-processor",
  "tables": {
    "telemetry": "cms-prod-telemetry",
    "trips": "cms-prod-trips"
  },
  "parallelism": 4
}
```

## Safety Features

### Dry Run Mode
All destructive operations support `--dry-run`:
```bash
python delete-vehicle-data.py --vehicle-id test --dry-run
python rename-table.py --old-name test --new-name test2 --dry-run
```

### Confirmation Prompts
Interactive confirmation for dangerous operations:
```bash
python delete-vehicle-data.py --fleet-id production --confirm
# Prompts: "Are you sure you want to delete data for fleet 'production'? (yes/no)"
```

### Backup Creation
Automatic backups before destructive operations:
```bash
python rename-table.py --old-name important-table --new-name new-table --backup
# Creates: important-table-backup-20240115-103045
```

## Error Handling

### Common Issues
- **Permission Denied**: Check AWS credentials and IAM permissions
- **Table Not Found**: Verify table names and region
- **Rate Limiting**: Scripts include automatic retry with backoff

### Logging
All scripts log operations to `scripts.log`:
```bash
tail -f scripts.log
```

### Recovery
Failed operations can be resumed:
```bash
python delete-vehicle-data.py --resume-from checkpoint.json
```

## Best Practices

1. **Always test with --dry-run first**
2. **Use specific vehicle/table filters**
3. **Monitor CloudWatch during operations**
4. **Keep backups of important data**
5. **Run during low-traffic periods**

## Troubleshooting

### Performance Issues
```bash
# Increase batch sizes for large operations
export BATCH_SIZE=100

# Use parallel processing
export MAX_WORKERS=10
```

### Memory Issues
```bash
# Process in smaller chunks
python delete-vehicle-data.py --chunk-size 1000

# Monitor memory usage
watch -n 1 'ps aux | grep python'
```
