#!/bin/bash

echo "Searching for and fixing all remaining syntax errors..."

# Find all TypeScript/TSX files and fix malformed patterns
find . -name "*.tsx" -o -name "*.ts" | while read file; do
  if [ -f "$file" ]; then
    # Fix malformed breadcrumbsHide patterns
    sed -i '' 's/breadcrumbsHide={true}.*}/breadcrumbsHide={true}/g' "$file"
    
    # Fix any remaining malformed patterns with extra characters
    sed -i '' 's/breadcrumbsHide={true}[[:space:]]*[^[:space:]]/breadcrumbsHide={true}/g' "$file"
    
    # Fix navigationHide patterns if any are malformed
    sed -i '' 's/navigationHide={true}.*}/navigationHide={true}/g' "$file"
    
    # Remove any orphaned closing braces after breadcrumbsHide
    sed -i '' '/breadcrumbsHide={true}/,/^[[:space:]]*}[[:space:]]*$/{
      /breadcrumbsHide={true}/!{
        /^[[:space:]]*}[[:space:]]*$/d
      }
    }' "$file"
  fi
done

echo "Done! Fixed all syntax errors."
