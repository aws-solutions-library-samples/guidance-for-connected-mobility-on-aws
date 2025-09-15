#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

TARGET_SUFFIX="cms-631ca2-591631"
PROFILE="${AWS_PROFILE:-default}"

echo -e "${BLUE}🔄 CMS Table Update and Cleanup${NC}"
echo "=================================="
echo ""
echo -e "${YELLOW}Target table suffix: ${TARGET_SUFFIX}${NC}"
echo -e "${YELLOW}AWS Profile: ${PROFILE}${NC}"
echo ""

# List all CMS tables
echo -e "${BLUE}📋 Finding all CMS tables...${NC}"
all_tables=$(AWS_PROFILE=$PROFILE aws dynamodb list-tables --query 'TableNames[?starts_with(@, `cms-`)]' --output text 2>/dev/null)

if [ -z "$all_tables" ]; then
    echo -e "${RED}❌ No CMS tables found${NC}"
    exit 1
fi

# Separate target tables from others
target_tables=""
other_tables=""

for table in $all_tables; do
    if [[ $table == *"$TARGET_SUFFIX"* ]]; then
        target_tables="$target_tables $table"
    else
        other_tables="$other_tables $table"
    fi
done

echo -e "${GREEN}✅ Target tables (will be kept):${NC}"
if [ -n "$target_tables" ]; then
    for table in $target_tables; do
        echo "   - $table"
    done
else
    echo -e "${RED}   No target tables found with suffix: $TARGET_SUFFIX${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}⚠️  Tables to be deleted:${NC}"
if [ -n "$other_tables" ]; then
    for table in $other_tables; do
        echo "   - $table"
    done
    
    echo ""
    read -p "Do you want to delete these tables? (yes/no): " confirm
    
    if [ "$confirm" = "yes" ]; then
        echo -e "${BLUE}🗑️  Deleting unused tables...${NC}"
        for table in $other_tables; do
            echo "   Processing $table..."
            
            # First, disable deletion protection if enabled
            echo "     Disabling deletion protection..."
            AWS_PROFILE=$PROFILE aws dynamodb update-table \
                --table-name "$table" \
                --deletion-protection-enabled false >/dev/null 2>&1
            
            # Wait a moment for the update to take effect
            sleep 2
            
            # Now try to delete the table
            echo "     Deleting table..."
            AWS_PROFILE=$PROFILE aws dynamodb delete-table --table-name "$table" >/dev/null 2>&1
            if [ $? -eq 0 ]; then
                echo -e "   ${GREEN}✅ Deleted $table${NC}"
            else
                echo -e "   ${RED}❌ Failed to delete $table${NC}"
                echo -e "   ${YELLOW}💡 You may need to manually delete this table from the AWS console${NC}"
            fi
        done
        echo -e "${GREEN}✅ Table cleanup completed${NC}"
    else
        echo -e "${YELLOW}⏭️  Skipping table deletion${NC}"
    fi
else
    echo -e "${GREEN}   No other tables to delete${NC}"
fi

echo ""
echo -e "${BLUE}🔧 Next step: Update Lambda environment variables${NC}"
echo "Run the following command to update Lambda functions:"
echo ""
echo -e "${YELLOW}make update-lambda-tables SUFFIX=$TARGET_SUFFIX${NC}"
