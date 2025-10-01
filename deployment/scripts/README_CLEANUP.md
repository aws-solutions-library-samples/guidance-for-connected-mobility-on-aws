# Data Cleanup Scripts

This directory contains scripts to manage data retention and cleanup old records from DynamoDB tables to control storage costs.

## Scripts

### 1. `cleanup_old_telemetry.py`
Simple script focused on telemetry data cleanup.

**Usage:**
```bash
# Dry run to see what would be deleted (30 days retention)
python3 cleanup_old_telemetry.py --dry-run

# Clean telemetry data older than 7 days
python3 cleanup_old_telemetry.py --days 7

# Clean specific table
python3 cleanup_old_telemetry.py --table cms-dev-storage-telemetry --days 30
```

### 2. `cleanup_old_data.py` (Recommended)
Comprehensive script that handles multiple tables with different retention policies.

**Usage:**
```bash
# List configured tables and their retention policies
python3 cleanup_old_data.py --list-tables

# Dry run for all tables (uses default retention periods)
python3 cleanup_old_data.py --dry-run

# Clean all tables with custom retention period
python3 cleanup_old_data.py --days 30

# Clean specific table only
python3 cleanup_old_data.py --table cms-dev-storage-telemetry --dry-run

# Actually perform cleanup (remove --dry-run)
python3 cleanup_old_data.py
```

### 3. `daily_cleanup.sh`
Automation wrapper for scheduled execution.

**Usage:**
```bash
# Run manual cleanup
./daily_cleanup.sh

# Add to crontab for daily execution at 2 AM
# 0 2 * * * /path/to/connected-mobility-workspace/deployment/scripts/daily_cleanup.sh
```

## Default Retention Policies

| Table | Retention Period | Reason |
|-------|------------------|---------|
| telemetry | 30 days | High volume, recent data most valuable |
| trips | 90 days | Business analytics need longer history |
| safety-events | 365 days | Compliance and safety analysis |
| maintenance-alerts | 180 days | Maintenance planning and trends |

## Safety Features

- **Dry Run Mode**: Always test with `--dry-run` first
- **Batch Processing**: Deletes in small batches to avoid throttling
- **Rate Limiting**: Built-in delays between batches
- **Error Handling**: Continues processing other tables if one fails
- **Logging**: Automation script logs all activities

## Cost Impact

Telemetry data is typically the highest volume. With default settings:
- Telemetry: ~30 days of data retained
- Estimated storage reduction: 80-90% for high-frequency telemetry
- DynamoDB costs scale with storage, so cleanup directly reduces costs

## Monitoring

Check logs in `../logs/cleanup_YYYYMMDD.log` for:
- Number of records processed
- Any errors or failures
- Execution time and performance

## Customization

Edit `TABLE_CONFIGS` in `cleanup_old_data.py` to:
- Add new tables
- Modify retention periods
- Change timestamp field names
- Update descriptions

## Emergency Recovery

If data is accidentally deleted:
- Check if Point-in-Time Recovery is enabled on tables
- Use AWS DynamoDB console to restore from backup
- Review logs to understand what was deleted
