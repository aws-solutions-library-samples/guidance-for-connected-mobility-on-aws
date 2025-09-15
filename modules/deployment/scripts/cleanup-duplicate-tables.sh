#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PROFILE=${1:-givenand-CMS}

echo -e "${BLUE}🧹 Cleaning up duplicate DynamoDB tables...${NC}"
echo -e "${YELLOW}Profile: ${PROFILE}${NC}"
echo ""

# Get all CMS tables with unique suffixes (not the base telemetry tables)
echo -e "${BLUE}🔍 Finding duplicate tables...${NC}"
duplicate_tables=$(AWS_PROFILE=$PROFILE aws dynamodb list-tables \
    --query 'TableNames[?starts_with(@, `cms-`) && contains(@, `-`) && !ends_with(@, `telemetry-trips`) && !ends_with(@, `telemetry-safety-events`) && !ends_with(@, `telemetry-maintenance-alerts`)]' \
    --output text)

if [ -z "$duplicate_tables" ]; then
    echo -e "${GREEN}✅ No duplicate tables found${NC}"
    exit 0
fi

echo -e "${YELLOW}📋 Found duplicate tables:${NC}"
echo "$duplicate_tables" | tr '\t' '\n' | sed 's/^/   - /'
echo ""

table_count=$(echo "$duplicate_tables" | wc -w)
echo -e "${YELLOW}Total tables to delete: ${table_count}${NC}"
echo ""

# Keep the base telemetry tables
echo -e "${GREEN}✅ Keeping base tables:${NC}"
echo "   - cms-telemetry-trips"
echo "   - cms-telemetry-safety-events" 
echo "   - cms-telemetry-maintenance-alerts"
echo ""

read -p "$(echo -e ${RED}⚠️  Delete ${table_count} duplicate tables? [y/N]: ${NC})" -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}❌ Cancelled${NC}"
    exit 0
fi

echo -e "${RED}🗑️  Deleting duplicate tables...${NC}"

deleted_count=0
for table in $duplicate_tables; do
    echo -n "   Deleting $table... "
    if AWS_PROFILE=$PROFILE aws dynamodb delete-table --table-name "$table" >/dev/null 2>&1; then
        echo -e "${GREEN}✅${NC}"
        ((deleted_count++))
    else
        echo -e "${RED}❌${NC}"
    fi
done

echo ""
echo -e "${GREEN}✅ Deleted ${deleted_count} duplicate tables${NC}"
echo -e "${BLUE}💰 Estimated monthly savings: \$$(($deleted_count * 5)) (assuming \$5/table/month)${NC}"
echo ""
echo -e "${YELLOW}📋 Remaining CMS tables:${NC}"
AWS_PROFILE=$PROFILE aws dynamodb list-tables \
    --query 'TableNames[?starts_with(@, `cms-`)]' \
    --output text | tr '\t' '\n' | sed 's/^/   - /'
