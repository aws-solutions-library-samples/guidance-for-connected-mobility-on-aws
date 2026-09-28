#!/bin/bash

# List of files that need to be updated
files=(
  "components/fleets/associate-vehicles/AssociateVehiclesPage.tsx"
  "components/fleets/edit-fleet/EditFleetPage.tsx"
  "components/fleets/create-fleet/CreateFleetPage.tsx"
  "components/fleets/fleet-management/components/FleetsPage.tsx"
  "components/fleets/vehicle-map/content.tsx"
  "components/alerts/maintenance/MaintenanceAlertsView.tsx"
  "components/fleetwise/campaigns/campaign-management/CampaignManagementView.tsx"
  "components/fleetwise/campaigns/campaign-management/components/CampaignsPage.tsx"
  "components/fleetwise/campaigns/CampaignsView.tsx"
  "components/fleetwise/campaigns/CampaignDetailView.tsx"
  "components/fleetwise/signal-catalog/SignalCatalogView.tsx"
  "components/fleetwise/vehicle-models/VehicleModelDetailView.tsx"
  "components/fleetwise/vehicle-models/VehicleModelsView.tsx"
  "components/vehicles/vehicle-management/VehicleManagementView.tsx"
  "components/vehicles/vehicle-management/components/VehiclesPage.tsx"
  "components/vehicles/edit-vehicle/EditVehiclePage.tsx"
)

echo "Removing secondary navigation from view components..."

for file in "${files[@]}"; do
  if [ -f "$file" ]; then
    echo "Processing: $file"
    
    # Remove Navigation import
    sed -i '' '/import.*Navigation.*from.*common-components/d' "$file"
    
    # Remove Breadcrumbs import  
    sed -i '' '/import.*Breadcrumbs.*from/d' "$file"
    
    # Replace navigation prop with navigationHide
    sed -i '' 's/navigation={<Navigation activeHref=[^>]*>}/navigationHide={true}/g' "$file"
    
    # Replace breadcrumbs prop with breadcrumbsHide
    sed -i '' '/breadcrumbs={/,/}/c\
        breadcrumbsHide={true}' "$file"
    
  else
    echo "File not found: $file"
  fi
done

echo "Done! Secondary navigation removed from all view components."
