// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from 'react';
import { Select, FormField } from '@cloudscape-design/components';
import { getRuntimeConfig } from '../../config/api';

export interface FleetSelectorProps {
  selectedFleet?: string;
  onFleetChange?: (fleetId: string) => void;
  label?: string;
  showVehicleCounts?: boolean;
  showAllOption?: boolean;
}

interface FleetOption {
  label: string;
  value: string;
  description?: string;
}

export const FleetSelector: React.FC<FleetSelectorProps> = ({
  selectedFleet = 'all',
  onFleetChange,
  label = 'Fleet',
  showVehicleCounts = true,
  showAllOption = true
}) => {
  const [fleetOptions, setFleetOptions] = useState<FleetOption[]>(
    showAllOption ? [{ label: 'All Fleets', value: 'all' }] : []
  );
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const fetchFleets = async () => {
      setLoading(true);
      try {
        const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/fleets?_t=${Date.now()}`);
        const output = await response.json();
        
        const fleets = output.fleets || [];
        console.log('🚗 FleetSelector: Raw fleet data:', fleets);
        console.log('🚗 FleetSelector: First fleet object:', fleets[0]);
        
        const options: FleetOption[] = [
          ...(showAllOption ? [{ label: 'All Fleets', value: 'all' }] : []),
          ...fleets.map((fleet: any) => {
            console.log('🚗 FleetSelector: Processing fleet:', fleet.name, 'vehicleCount:', fleet.vehicleCount);
            return {
              label: fleet.name,
              value: fleet.fleetId,
              description: showVehicleCounts 
                ? `${fleet.vehicleCount || 0} vehicles`
                : undefined
            };
          })
        ];
        
        console.log('🚗 FleetSelector: Final options:', options);
        setFleetOptions(options);
      } catch (error) {
        console.error('Error fetching fleets:', error);
        // Use fallback options
        const fallbackOptions: FleetOption[] = [
          ...(showAllOption ? [{ label: 'All Fleets', value: 'all' }] : []),
          { label: 'Fleet A', value: 'fleet-a', description: showVehicleCounts ? '25 vehicles' : undefined },
          { label: 'Fleet B', value: 'fleet-b', description: showVehicleCounts ? '18 vehicles' : undefined },
          { label: 'Fleet C', value: 'fleet-c', description: showVehicleCounts ? '32 vehicles' : undefined }
        ];
        setFleetOptions(fallbackOptions);
      } finally {
        setLoading(false);
      }
    };

    fetchFleets();
  }, [showVehicleCounts]);

  return (
    <FormField label={label}>
      <Select
        selectedOption={fleetOptions.find(option => option.value === selectedFleet)}
        onChange={({ detail }) => onFleetChange?.(detail.selectedOption.value!)}
        options={fleetOptions}
        placeholder="Select fleet"
        loading={loading}
        loadingText="Loading fleets..."
        empty="No fleets available"
        expandToViewport
        renderHighlightedAriaLive={(option) => 
          `${option.label}${option.description ? ` - ${option.description}` : ''}`
        }
      />
    </FormField>
  );
};
