#!/bin/bash

# List of FleetWise files that need fixing
files=(
  "components/fleetwise/campaigns/CampaignDetailView.tsx"
  "components/fleetwise/signal-catalog/SignalCatalogView.tsx"
  "components/fleetwise/vehicle-models/VehicleModelDetailView.tsx"
  "components/fleetwise/vehicle-models/VehicleModelsView.tsx"
)

echo "Fixing remaining malformed breadcrumbs in FleetWise components..."

for file in "${files[@]}"; do
  if [ -f "$file" ]; then
    echo "Processing: $file"
    
    # Fix malformed breadcrumbs patterns
    sed -i '' '/breadcrumbsHide={true}/,/ariaLabel="Breadcrumbs"/c\
      breadcrumbsHide={true}' "$file"
    
    # Remove any remaining malformed patterns
    sed -i '' '/breadcrumbsHide={true}.*{.*text:/,/}/c\
      breadcrumbsHide={true}' "$file"
    
  else
    echo "File not found: $file"
  fi
done

echo "Done! Fixed remaining malformed breadcrumbs."
