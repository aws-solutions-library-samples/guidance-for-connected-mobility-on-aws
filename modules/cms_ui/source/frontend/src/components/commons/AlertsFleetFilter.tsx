// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useContext } from 'react';
import {
  Select,
  SpaceBetween,
  StatusIndicator,
  Box,
  Container,
  Header
} from '@cloudscape-design/components';
import { UserContext } from './UserContext';
import { ApiContext } from '@/api/provider';
import { FleetItem } from '@/types/fleet-types';
import { getRuntimeConfig } from '../../config/api';
import { authFetch } from '../../utils/authFetch';
import { useFleetFilter } from '../fleet-filter/FleetFilter';
import { ALL_FLEETS_ID } from '../fleet-picker/useFleetSelection';

interface AlertsFleetFilterProps {
  selectedFleet: string;
  onFleetChange: (fleetId: string, fleetName: string) => void;
  label?: string;
  placeholder?: string;
  showContext?: boolean;
}

interface FleetOption {
  label: string;
  value: string;
  description?: string;
}

export function AlertsFleetFilter({
  selectedFleet,
  onFleetChange,
  label = "Fleet Filter",
  placeholder = "Select fleet to filter alerts",
  showContext = true
}: AlertsFleetFilterProps) {
  const uc = useContext(UserContext);
  const api = useContext(ApiContext);

  const [fleetsData, setFleetsData] = useState<FleetItem[]>([]);
  const [fleetOptions, setFleetOptions] = useState<FleetOption[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>("");

  const fetchFleets = async () => {
    setLoading(true);
    setError("");
    
    try {
      console.log("AlertsFleetFilter: Fetching fleets for alerts filtering");
      
      const response = await authFetch(`${getRuntimeConfig().apiEndpoint}api/v1/fleets`);
      const output = await response.json();
      
      const fleets = output.fleets || [];
      console.log(`AlertsFleetFilter: Received ${fleets.length} fleets`);
      
      setFleetsData(fleets);
      
      // Create fleet options with context information
      const options: FleetOption[] = [
        { 
          label: "All Fleets", 
          value: "all"
        },
        ...fleets.map((fleet) => ({
          label: fleet.name,
          value: fleet.fleetId,
          description: showContext 
            ? `${fleet.numTotalVehicles || 0} vehicles, ${fleet.numConnectedVehicles || 0} connected`
            : undefined
        }))
      ];
      
      setFleetOptions(options);
      
      // Set default to "All Fleets" if no fleet is selected
      if (!selectedFleet || selectedFleet === '') {
        console.log("AlertsFleetFilter: Setting default to All Fleets");
        onFleetChange("all", "All Fleets");
      }
      
    } catch (error) {
      console.error("Error fetching fleets for alerts filter:", error);
      console.log("AlertsFleetFilter: Using fallback fleet data due to API error");
      
      // Provide fallback fleet options when API fails
      const fallbackOptions: FleetOption[] = [
        { 
          label: "All Fleets", 
          value: "all"
        },
        { 
          label: "Fleet 0001", 
          value: "fleet_0001", 
          description: showContext ? "Fleet management vehicles" : undefined
        },
        { 
          label: "Fleet 0002", 
          value: "fleet_0002", 
          description: showContext ? "Fleet management vehicles" : undefined
        },
        { 
          label: "Fleet 0003", 
          value: "fleet_0003", 
          description: showContext ? "Fleet management vehicles" : undefined
        },
        { 
          label: "Fleet 0004", 
          value: "fleet_0004", 
          description: showContext ? "Fleet management vehicles" : undefined
        },
        { 
          label: "Fleet 0005", 
          value: "fleet_0005", 
          description: showContext ? "Fleet management vehicles" : undefined
        }
      ];
      
      setFleetOptions(fallbackOptions);
      
      // Set default to "All Fleets" if no fleet is selected
      if (!selectedFleet || selectedFleet === '') {
        console.log("AlertsFleetFilter: Setting default to All Fleets (fallback)");
        onFleetChange("all", "All Fleets");
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // Only fetch fleets once on component mount
    fetchFleets();
  }, []);

  const handleFleetChange = (selectedOption: any) => {
    const fleetId = selectedOption.value;
    const fleetName = selectedOption.label;
    
    console.log(`AlertsFleetFilter: Fleet changed to ${fleetName} (${fleetId})`);
    console.log(`AlertsFleetFilter: Available options:`, fleetOptions);
    console.log(`AlertsFleetFilter: Current selectedFleet prop:`, selectedFleet);
    
    onFleetChange(fleetId, fleetName);
  };

  const selectedOption = fleetOptions.find(option => option.value === selectedFleet);
  console.log(`AlertsFleetFilter: selectedOption found:`, selectedOption, `for selectedFleet:`, selectedFleet);

  return (
    <Select
      selectedOption={selectedOption || null}
      onChange={({ detail }) => handleFleetChange(detail.selectedOption)}
      options={fleetOptions}
      placeholder={placeholder}
      loading={loading}
      loadingText="Loading fleets..."
      empty="No fleets available"
      expandToViewport
      renderHighlightedAriaLive={(option) => 
        `${option.label}${option.description ? ` - ${option.description}` : ''}`
      }
    />
  );
}

// Hook for managing fleet filter state.
//
// Reads from and writes to FleetFilterContext — the ambient fleet selection
// driven by the top-nav fleet picker. It previously held its own `useState`,
// which meant every surface consuming it (dashboard, vehicle map, fleet
// vehicle map, safety alerts) silently ignored the top-nav picker: each got
// its own isolated state initialised to "all" that nothing else could update.
//
// Sentinel translation is deliberate and load-bearing. Callers of this hook
// compare against the string "all" (and derive `isAllFleets` from it), while
// FleetFilterContext uses ALL_FLEETS_ID ("__all__"). Translating here rather
// than at the four call sites keeps those callers unchanged.
export function useAlertsFleetFilter(initialFleet: string = "all") {
  const [fleetOptions, setFleetOptions] = useState<FleetOption[]>([
    { label: "All Fleets", value: "all" }
  ]);
  const [loading, setLoading] = useState(false);

  const { selectedFleetId, setSelectedFleetId } = useFleetFilter();

  // Context sentinel -> this hook's "all" sentinel.
  const selectedFleet = selectedFleetId === ALL_FLEETS_ID ? "all" : selectedFleetId;

  // Fetch fleets on mount
  useEffect(() => {
    const fetchFleets = async () => {
      setLoading(true);
      try {
        const response = await authFetch(`${getRuntimeConfig().apiEndpoint}api/v1/fleets`);
        const output = await response.json();

        const fleets = output.fleets || [];
        console.log(`useAlertsFleetFilter: Received ${fleets.length} fleets`);

        const options: FleetOption[] = [
          { label: "All Fleets", value: "all" },
          ...fleets.map((fleet) => ({
            label: fleet.name,
            value: fleet.fleetId
          }))
        ];

        setFleetOptions(options);
      } catch (error) {
        console.error("Error fetching fleets:", error);
        // Keep default "All Fleets" option
      } finally {
        setLoading(false);
      }
    };

    fetchFleets();
  }, []);

  // Derived, not stored — so a top-nav picker change updates the displayed
  // name too. Storing it in local state was why the name could go stale.
  const selectedFleetName =
    fleetOptions.find((o) => o.value === selectedFleet)?.label ?? "All Fleets";

  // Writes back to the shared context so an in-page dropdown and the top-nav
  // picker stay in sync in both directions.
  const handleFleetChange = (fleetId: string, _fleetName?: string) => {
    setSelectedFleetId(fleetId === "all" ? ALL_FLEETS_ID : fleetId);
  };

  const isAllFleets = selectedFleet === "all";

  return {
    selectedFleet,
    selectedFleetName,
    fleetOptions,
    handleFleetChange,
    isAllFleets,
    loading,
    filteredData: [] // Placeholder for filtered data
  };
}
