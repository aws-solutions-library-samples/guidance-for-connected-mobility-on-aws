// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useContext, useEffect, useRef, useState } from "react";
import { getRuntimeConfig } from "../../../config/api";
import useNotifications from "./use-notifications";
import { UI_ROUTES } from "@/utils/constants";
import { joinRoutes } from "@/utils/path";
import { FleetManagementContext } from "./FleetManagementContext";
import { FleetDetailsPage } from "./components/FleetDetailsPage";
import { FleetsPage } from "./components/FleetsPage";
import { DeleteModal } from "./components/DeleteModal";
import { StatusIndicator, Container, Header, Box, BreadcrumbGroup } from "@cloudscape-design/components";
import { ApiContext } from "@/api/provider";
import { useNavigate, useParams } from "react-router-dom";
import { DeleteFleetCommand } from "@/api/fleet-management-client";

export function Content() {
  const [fleets, setFleets] = useState<Array<any>>([]);
  const [selectedItems, setSelectedItems] = useState<Array<any>>([]);
  const [showDeleteModal, setShowDeleteModal] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<any>(null);
  const fmc = useContext(FleetManagementContext);

  const [locationFleet, setLocationFleet] = useState<any>(undefined);

  const api = useContext(ApiContext);

  const navigate = useNavigate();
  const { fleetId } = useParams(); // Get fleetId from URL params

  const { addNotification } = useNotifications();

  const handleDeleteFleets = async () => {
    try {
      setIsLoading(true);
      
      // Delete each selected fleet
      for (const fleet of selectedItems) {
        await api.send(new DeleteFleetCommand({ id: fleet.id || fleet.fleetId }));
      }
      
      addNotification({
        type: 'success',
        content: `Successfully deleted ${selectedItems.length} fleet${selectedItems.length > 1 ? 's' : ''}`,
        dismissible: true,
        onDismiss: () => {}
      });
      
      // Refresh fleet list
      await fetchFleets();
      setSelectedItems([]);
      setShowDeleteModal(false);
    } catch (error: any) {
      addNotification({
        type: 'error',
        content: `Failed to delete fleet${selectedItems.length > 1 ? 's' : ''}: ${error.message}`,
        dismissible: true,
        onDismiss: () => {}
      });
    } finally {
      setIsLoading(false);
    }
  };

  const fetchFleets = async () => {
    try {
      console.log('📋 Fetching fleet list directly from API...');
      
      // Call API directly to avoid transformation
      const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/fleets?_t=${Date.now()}`);
      const data = await response.json();
      
      console.log('📋 Direct API response:', data);
      
      const fleets = data.fleets || [];
      console.log('📋 Raw fleets count:', fleets.length);
      console.log('📋 First fleet raw data:', fleets[0]);
      
      // Minimal mapping to match UI expectations
      const fleetsWithCounts = fleets.map((fleet: any) => {
        console.log(`📊 Raw fleet from API:`, fleet);
        console.log(`📊 Fleet ${fleet.name} operationalCity:`, fleet.operationalCity);
        
        const mappedFleet = {
          ...fleet,
          id: fleet.fleetId, // Map fleetId to id for UI compatibility
          numTotalVehicles: fleet.totalVehicles ?? 0,
          numConnectedVehicles: fleet.connectedVehicles ?? 0,
          numTotalCampaigns: fleet.numTotalCampaigns ?? 0,
          numActiveCampaigns: fleet.numActiveCampaigns ?? 0
        };
        
        console.log(`📊 Mapped fleet operationalCity:`, mappedFleet.operationalCity);
        return mappedFleet;
        return mappedFleet;
      });
      
      console.log('✅ Fleet list with accurate counts:', fleetsWithCounts);
      setFleets(fleetsWithCounts);
    } catch (error) {
      console.error('❌ Error fetching fleet list:', error);
      setFleets([]);
    }
  };

  const fetchFleet = async (fleetId: string) => {
    if (!fleetId || fleetId === 'undefined' || fleetId.trim() === '') {
      console.warn('Cannot fetch fleet: fleetId is undefined or empty:', fleetId);
      return undefined;
    }
    
    try {
      const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/fleets/${fleetId}`);
      const data = await response.json();
      return data.fleet || data;
    } catch (error) {
      console.error('Error fetching fleet:', error);
      return undefined;
    }
  };

  useEffect(() => {
    async function setLocationFleetAsync() {
      // Use fleetId from URL params instead of hash
      if (!fleetId || fleetId === 'undefined' || fleetId.trim() === '') {
        setLocationFleet(undefined);
        setIsLoading(false); // Set loading to false when no specific fleet is needed
        return;
      }

      //first check if we already have the fleet available in memory
      let newLocationFleet = fleets.find((it) => it.id === fleetId);

      //if not, next try to fetch it
      if (!newLocationFleet) {
        const fleet = await fetchFleet(fleetId);
        if (fleet != undefined) {
          newLocationFleet = fleet;
        } else {
          // Fleet not found - set loading to false and show error state
          setIsLoading(false);
          setLocationFleet(null); // Use null to indicate "not found" vs undefined for "loading"
          return;
        }
      }

      setLocationFleet(newLocationFleet);
      setIsLoading(false); // Set loading to false when fleet loading is complete

      const breadcrumbItems = [
        { text: "Fleets", href: UI_ROUTES.FLEET_MANAGEMENT },
      ];

      if (newLocationFleet && newLocationFleet.id)
        breadcrumbItems.push({
          text: newLocationFleet.id,
          href: `/fleets/management/${newLocationFleet.id}`,
        });

      fmc.breadcrumbs.setBreadcrumbItems(breadcrumbItems);
    }

    // Run this effect when fleetId changes
    if (fleetId) {
      // If we have a fleetId, fetch that specific fleet (don't wait for fleets list)
      setLocationFleetAsync();
    } else if (fleets.length > 0) {
      // If no fleetId but we have fleets loaded, handle fleet list view
      setLocationFleetAsync();
    }
  }, [fleetId, fleets]);

  const { notifications, notify } = useNotifications();

  const onDeleteInit = () => setShowDeleteModal(true);
  const onEditInit = () => {
    navigate(`${UI_ROUTES.FLEET_EDIT}#${selectedItems[0].id}`);
  };
  const onDeleteDiscard = () => {
    setShowDeleteModal(false);
  };
  const onDeleteConfirm = async () => {
    const fleetsToDelete: FleetItem[] = locationFleet
      ? [locationFleet]
      : selectedItems;
    setSelectedItems([]);
    setShowDeleteModal(false);

    const fleetsDeletePromises = fleetsToDelete.map(async (fleet) => {
      notify([
        {
          id: fleet.id,
          action: "delete",
          status: "in-progress",
          message: `Deleting fleet ${fleet.id}`,
        },
      ]);
      try {
        const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/fleets/${fleet.id}`, {
          method: 'DELETE'
        });
        
        if (response.ok) {
          notify([
            {
              id: fleet.id,
              action: "delete",
              status: "success",
              message: `Successfully deleted fleet ${fleet.id}`,
            },
          ]);
        } else {
          notify([
            {
              id: fleet.id,
              action: "delete",
              status: "error",
              message: `Error deleting fleet ${fleet.id}`,
            },
          ]);
        }
      } catch (err) {
        notify([
          {
            id: fleet.id,
            action: "delete",
            status: "error",
            message: `Error deleting fleet ${fleet.id}`,
          },
        ]);
        console.log(err);
      }
    });
    await Promise.all(fleetsDeletePromises);
    fetchFleets();
  };

  useEffect(() => {
    // Only fetch fleet list if we're not viewing a specific fleet
    if (!fleetId) {
      setIsLoading(true);
      setError(null);

      async function getFleetsSummary() {
        try {
          await fetchFleets();
        } catch (err) {
          setError(err);
        } finally {
          setShowDeleteModal(false);
          setIsLoading(false);
        }
      }
      getFleetsSummary();
    }
  }, [fleetId]); // Depend on fleetId instead of empty array

  return (
    <>
      {fleetId && !locationFleet && isLoading ? (
        <StatusIndicator type="loading">Loading...</StatusIndicator>
      ) : fleetId && locationFleet === null ? (
        <Container>
          <Header variant="h1">Fleet Not Found</Header>
          <Box>
            <StatusIndicator type="error">
              Fleet "{fleetId}" was not found. Please check the fleet ID or return to the fleet management page.
            </StatusIndicator>
          </Box>
        </Container>
      ) : locationFleet ? (
        <FleetDetailsPage
          fleetId={locationFleet.fleet?.id || locationFleet.fleet?.fleet_id || locationFleet.fleet?.fleetId || locationFleet.id || fleetId}
          onDeleteInit={onDeleteInit}
          notifications={notifications}
        />
      ) : (
        <FleetsPage
          fleets={fleets}
          selectedItems={selectedItems}
          setSelectedItems={setSelectedItems}
          onEditInit={onEditInit}
          onDeleteInit={onDeleteInit}
          notifications={notifications}
          isLoading={isLoading}
          error={error}
        />
      )}
      <DeleteModal
        visible={showDeleteModal}
        onDiscard={onDeleteDiscard}
        onDelete={onDeleteConfirm}
        fleets={locationFleet ? [locationFleet] : selectedItems}
      />
    </>
  );
}
