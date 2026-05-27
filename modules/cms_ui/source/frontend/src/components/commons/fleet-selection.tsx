// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { FormField, Select, SpaceBetween, StatusIndicator, Button, Container, Header } from "@cloudscape-design/components";
import { useContext, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { UserContext } from "./UserContext";
import {
  FleetItem,
  ListFleetsCommand,
} from "@/api/fleet-management-client";
import { ApiContext } from "@/api/provider";
import { UI_ROUTES } from "@/utils/constants";

type SelectContent = {
  label: string;
  value: string;
};

export function FleetSelectionItem() {
  const uc = useContext(UserContext);
  const navigate = useNavigate();

  const [fleetsData, setFleetsData] = useState<FleetItem[]>([]);
  const [fleetSelections, setFleetSelections] = useState<SelectContent[]>([]);
  const [fleetDataStatus, setFleetDataStatus] = useState<
    "loading" | "finished" | "error"
  >("loading");
  const [errorMessage, setErrorMessage] = useState<string>("");

  const api = useContext(ApiContext);

  const fetchFleets = async () => {
    setFleetDataStatus("loading");
    setErrorMessage("");
    try {
      console.log("FleetSelectionItem: Fetching fleets with managed service:", uc.managedService.isEnabled);
      
      const cmd = new ListFleetsCommand();
      console.log("FleetSelectionItem: Sending ListFleetsCommand");
      const output = await api.client.send(cmd);
      
      console.log(`FleetSelectionItem: Received ${output.fleets?.length || 0} fleets`);
      
      if (!output.fleets || output.fleets.length === 0) {
        console.log("FleetSelectionItem: No fleets returned from API");
        setErrorMessage("No fleets found. Create your first fleet to get started.");
      }
      
      return output.fleets || [];
    } catch (error) {
      console.error("Error fetching fleets:", error);
      setFleetDataStatus("error");
      setErrorMessage(error instanceof Error ? error.message : "Unknown error occurred while fetching fleets");
      return [];
    }
  };

  // Fetch fleets when the component mounts or when the managed service toggle changes
  useEffect(() => {
    console.log("FleetSelectionItem: useEffect triggered with managed service state:", uc.managedService.isEnabled);
    
    fetchFleets().then((fleets) => {
      setFleetsData(fleets);
      
      // Add "All Fleets" option at the beginning
      const selectionFleets = [
        { label: "All Fleets", value: "all" },
        ...fleets.map((fleet) => ({
          label: fleet.name,
          value: fleet.id,
        }))
      ];
      
      setFleetSelections(selectionFleets);
      setFleetDataStatus("finished");
      
      // Set default to "All Fleets" if no fleet is selected
      if (!uc.fleet.selectedFleet) {
        console.log("FleetSelectionItem: Setting default to All Fleets");
        uc.fleet.setSelectedFleet({ id: "all", name: "All Fleets" } as any);
      }
    });
  }, [uc.managedService.isEnabled]); // Re-fetch when managed service toggle changes

  const handleCreateFleet = () => {
    navigate(UI_ROUTES.FLEETS_CREATE);
  };

  return (
    <Container
      header={
        <Header 
          variant="h3"
          description="Select a fleet to view its dashboard and manage operations"
        >
          Fleet Selection
        </Header>
      }
    >
      <SpaceBetween size="s" direction="horizontal" alignItems="end">
        <FormField label="Active fleet">
          <Select
            loadingText="Fetching fleets..."
            placeholder="Select a fleet"
            errorText={errorMessage || "Failed to fetch fleets"}
            statusType={fleetDataStatus}
            selectedOption={
              uc.fleet.selectedFleet
                ? {
                    label: uc.fleet.selectedFleet.name,
                    value: uc.fleet.selectedFleet.id,
                  }
                : null
            }
            onChange={({ detail }) => {
              if (detail.selectedOption.value === "all") {
                uc.fleet.setSelectedFleet({ id: "all", name: "All Fleets" } as any);
              } else {
                uc.fleet.setSelectedFleet(
                  fleetsData.find(
                    (fleet) => fleet.id === detail.selectedOption.value,
                  ) || null,
                );
              }
            }}
            options={fleetSelections}
            empty={
              fleetDataStatus === "finished" && fleetSelections.length === 0
                ? "No fleets available. Create your first fleet to get started."
                : undefined
            }
          />
        </FormField>
        {fleetDataStatus === "finished" && fleetSelections.length === 0 && (
          <Button 
            variant="primary" 
            onClick={handleCreateFleet}
            iconName="add-plus"
          >
            Create Fleet
          </Button>
        )}
      </SpaceBetween>
    </Container>
  );
}
