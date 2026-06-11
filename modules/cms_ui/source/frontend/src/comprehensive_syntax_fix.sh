#!/bin/bash

echo "Running comprehensive syntax error detection and fixes..."

# Function to check for common syntax issues
check_and_fix_file() {
    local file="$1"
    echo "Checking: $file"
    
    # Fix missing closing braces in JSX props
    sed -i '' 's/actions={[[:space:]]*<SpaceBetween[^>]*>[^}]*<\/SpaceBetween>[[:space:]]*>/&}/g' "$file"
    
    # Fix malformed breadcrumbsHide patterns
    sed -i '' 's/breadcrumbsHide={true}[[:space:]]*[^}[:space:]][^}]*/breadcrumbsHide={true}/g' "$file"
    
    # Fix malformed navigationHide patterns  
    sed -i '' 's/navigationHide={true}[[:space:]]*[^}[:space:]][^}]*/navigationHide={true}/g' "$file"
    
    # Remove orphaned closing braces after props
    sed -i '' '/breadcrumbsHide={true}/,/^[[:space:]]*}[[:space:]]*$/{
        /breadcrumbsHide={true}/!{
            /^[[:space:]]*}[[:space:]]*$/d
        }
    }' "$file"
    
    # Fix missing closing braces in if statements
    sed -i '' '/if[[:space:]]*([^)]*)[[:space:]]*{/,/^[[:space:]]*}[[:space:]]*$/{
        /if[[:space:]]*([^)]*)[[:space:]]*{/!{
            /^[[:space:]]*}[[:space:]]*$/!{
                /^[[:space:]]*}[[:space:]]*else/!{
                    s/^[[:space:]]*$/&}/
                }
            }
        }
    }' "$file"
}

# Find all TypeScript/TSX files and process them
find . -name "*.tsx" -o -name "*.ts" | while read file; do
    if [ -f "$file" ]; then
        check_and_fix_file "$file"
    fi
done

echo "Comprehensive syntax fixes completed!"
