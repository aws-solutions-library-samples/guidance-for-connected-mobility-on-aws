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
      
      const response = await fetch(`${getRuntimeConfig().apiEndpoint}/api/v1/fleets`);
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

// Hook for managing fleet filter state
export function useAlertsFleetFilter(initialFleet: string = "all") {
  const [selectedFleet, setSelectedFleet] = useState<string>("all");
  const [selectedFleetName, setSelectedFleetName] = useState<string>("All Fleets");
  const [fleetOptions, setFleetOptions] = useState<FleetOption[]>([
    { label: "All Fleets", value: "all" }
  ]);
  const [loading, setLoading] = useState(false);
  const api = useContext(ApiContext);

  // Fetch fleets on mount
  useEffect(() => {
    const fetchFleets = async () => {
      setLoading(true);
      try {
        const response = await fetch(`${getRuntimeConfig().apiEndpoint}/api/v1/fleets`);
        const output = await response.json();
        
        const fleets = output.fleets || [];
        console.log(`useAlertsFleetFilter: Received ${fleets.length} fleets`);
        console.log('First fleet object:', fleets[0]);
        
        const options: FleetOption[] = [
          { label: "All Fleets", value: "all" },
          ...fleets.map((fleet) => {
            console.log('Mapping fleet:', fleet, 'fleetId:', fleet.fleetId);
            return {
              label: fleet.name,
              value: fleet.fleetId
            };
          })
        ];
        
        setFleetOptions(options);
        
        // Only set default if no fleet is currently selected
        if (!selectedFleet || selectedFleet === '') {
          setSelectedFleet("all");
          setSelectedFleetName("All Fleets");
        }
      } catch (error) {
        console.error("Error fetching fleets:", error);
        // Keep default "All Fleets" option
      } finally {
        setLoading(false);
      }
    };

    fetchFleets();
  }, []); // Remove api.client dependency to prevent refetch

  const handleFleetChange = (fleetId: string, fleetName: string) => {
    setSelectedFleet(fleetId);
    setSelectedFleetName(fleetName);
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
