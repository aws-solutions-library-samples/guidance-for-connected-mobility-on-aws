#!/bin/bash

# Script to add breadcrumbs to all major pages

echo "Adding breadcrumbs to all major components..."

# List of files to update with their breadcrumb paths
declare -A BREADCRUMB_PATHS=(
    ["src/components/vehicles/vehicle-management/VehicleManagementView.tsx"]="Home,Vehicle Management"
    ["src/components/fleetwise/campaigns/CampaignsView.tsx"]="Home,FleetWise,Campaigns"
    ["src/components/fleetwise/signal-catalog/SignalCatalogView.tsx"]="Home,FleetWise,Signal Catalog"
    ["src/components/fleetwise/vehicle-models/VehicleModelsView.tsx"]="Home,FleetWise,Vehicle Models"
    ["src/components/analytics/TelemetryDashboard.tsx"]="Home,Analytics,Telemetry Dashboard"
    ["src/components/analytics/TripAnalyticsView.tsx"]="Home,Analytics,Trip Analytics"
    ["src/components/analytics/DriverBehaviorView.tsx"]="Home,Analytics,Driver Behavior"
    ["src/components/analytics/GeofenceEventsView.tsx"]="Home,Analytics,Geofence Events"
    ["src/components/alerts/maintenance/MaintenanceAlertsView.tsx"]="Home,Alerts,Maintenance"
    ["src/components/settings/SettingsView.tsx"]="Home,Settings"
)

# Function to add breadcrumbs to a file
add_breadcrumbs() {
    local file="$1"
    local breadcrumb_text="$2"
    
    if [[ ! -f "$file" ]]; then
        echo "File not found: $file"
        return
    fi
    
    echo "Processing: $file"
    
    # Check if BreadcrumbGroup is already imported
    if ! grep -q "BreadcrumbGroup" "$file"; then
        # Add BreadcrumbGroup to imports
        sed -i '' 's/} from "@cloudscape-design\/components";/, BreadcrumbGroup} from "@cloudscape-design\/components";/' "$file"
    fi
    
    # Check if useNavigate is imported
    if ! grep -q "useNavigate" "$file"; then
        # Add useNavigate import
        if grep -q "react-router-dom" "$file"; then
            sed -i '' 's/} from "react-router-dom";/, useNavigate} from "react-router-dom";/' "$file"
        else
            # Add new import line after existing imports
            sed -i '' '/from "@cloudscape-design\/components";/a\
import { useNavigate } from "react-router-dom";
' "$file"
        fi
    fi
    
    # Replace breadcrumbsHide={true} with actual breadcrumbs
    IFS=',' read -ra BREADCRUMBS <<< "$breadcrumb_text"
    local breadcrumb_items=""
    local href="/"
    
    for i in "${!BREADCRUMBS[@]}"; do
        local text="${BREADCRUMBS[$i]}"
        if [[ $i -eq 0 ]]; then
            breadcrumb_items="{ text: '$text', href: '$href' }"
        else
            # Generate appropriate href based on text
            case "$text" in
                "Vehicle Management") href="/vehicles" ;;
                "Fleet Management") href="/fleets" ;;
                "FleetWise") href="/fleetwise" ;;
                "Campaigns") href="/fleetwise/campaigns" ;;
                "Signal Catalog") href="/fleetwise/signal-catalog" ;;
                "Vehicle Models") href="/fleetwise/vehicle-models" ;;
                "Analytics") href="/analytics" ;;
                "Telemetry Dashboard") href="/analytics/telemetry" ;;
                "Trip Analytics") href="/analytics/trips" ;;
                "Driver Behavior") href="/analytics/driver-behavior" ;;
                "Geofence Events") href="/analytics/geofence" ;;
                "Alerts") href="/alerts" ;;
                "Maintenance") href="/alerts/maintenance" ;;
                "Settings") href="/settings" ;;
                *) href="/" ;;
            esac
            breadcrumb_items="$breadcrumb_items, { text: '$text', href: '$href' }"
        fi
    done
    
    # Create the breadcrumb replacement
    local breadcrumb_replacement="breadcrumbs={
          <BreadcrumbGroup
            items={[$breadcrumb_items]}
            expandAriaLabel=\"Show path\"
            ariaLabel=\"Breadcrumbs\"
            onFollow={(e) => {
              e.preventDefault();
              navigate(e.detail.href);
            }}
          />
        }"
    
    # Replace breadcrumbsHide={true} with the breadcrumb component
    sed -i '' "s/breadcrumbsHide={true}/$breadcrumb_replacement/" "$file"
    
    echo "Updated: $file"
}

# Process each file
for file in "${!BREADCRUMB_PATHS[@]}"; do
    add_breadcrumbs "$file" "${BREADCRUMB_PATHS[$file]}"
done

echo "Breadcrumb addition complete!"
