// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useContext, useEffect, useRef, useState } from "react";

import useNotifications from "./use-notifications";
import { UI_ROUTES } from "@/utils/constants";
import { joinRoutes } from "@/utils/path";
import { VehicleManagementContext } from "./VehicleManagementContext";
import { VehiclesPage } from "./components/VehiclesPage";
import { DeleteModal } from "./components/DeleteModal";
import VehicleDashboardView from "./components/vehicle-dashboard/VehicleDashboardView";
import VehicleMapView from "./components/VehicleMapView";
import { Button, Header, SpaceBetween, StatusIndicator } from "@cloudscape-design/components";
import { ApiContext } from "@/api/provider";
import { UserContext } from "@/components/commons/UserContext";
import { VehicleItem } from "@/types/fleet-types";
import { getRuntimeConfig } from "../../../config/api";
import { useLocation, useNavigate } from "react-router-dom";
import { authFetch } from '../../../utils/authFetch';

export function Content() {
  const [vehicles, setVehicles] = useState<Array<VehicleItem>>([]);
  const [totalVehicleCount, setTotalVehicleCount] = useState<number>(0);
  const [selectedItems, setSelectedItems] = useState<Array<any>>([]);
  const [showDeleteModal, setShowDeleteModal] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<any>(null);
  const [locationVehicle, setLocationVehicle] = useState<
    VehicleItem | undefined
  >(undefined);
  const [selectedFleet, setSelectedFleet] = useState<string>('all');
  const [searchText, setSearchText] = useState<string>('');
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [pageSize, setPageSize] = useState<number>(25);
  const [paginationInfo, setPaginationInfo] = useState<any>(null);
  
  const vmc = useContext(VehicleManagementContext);

  const api = useContext(ApiContext);
  const userContext = useContext(UserContext);
  const navigate = useNavigate();

  const location = useLocation();

  async function fetchVehicles(page: number = 1, pageSize: number = 25, fleetId?: string, search?: string): Promise<{ vehicles: VehicleItem[], total: number, pagination: any }> {
    console.log("VehicleManagement - Fetching vehicles with managed service:", userContext.managedService.isEnabled);
    console.log("🔧 API client type:", api.client.constructor.name);
    console.log("📄 Requesting page:", page, "with pageSize:", pageSize, "fleetId:", fleetId, "search:", search);
    
    try {
      let url = `${getRuntimeConfig().apiEndpoint}api/v1/vehicles?limit=${pageSize}&page=${page}&sortBy=createdAt&sortOrder=desc`;
      if (fleetId && fleetId !== 'all') {
        url += `&fleetId=${fleetId}`;
      }
      if (search && search.trim()) {
        url += `&search=${encodeURIComponent(search.trim())}`;
      }
      
      const response = await authFetch(url);
      const data = await response.json();
      
      console.log("📥 Received API response:", {
        vehicleCount: data.vehicles?.length,
        total: data.total,
        page: data.page,
      });
    
    // Use the proper total from the API response
    const total = data.total || data.count || (data.vehicles?.length || 0);
    console.log("📊 Using total from API response:", total);
    
    return {
      vehicles: data.vehicles || [],
      total: total,
      pagination: data.pagination || {
        currentPage: page,
        totalPages: data.totalPages || Math.ceil(total / pageSize),
        pageSize: pageSize,
        totalItems: total,
        hasNextPage: data.hasMore || false,
        hasPreviousPage: page > 1
      }
    };
    } catch (error) {
      console.error('Error fetching vehicles:', error);
      return {
        vehicles: [],
        total: 0,
        pagination: {
          currentPage: page,
          totalPages: 0,
          pageSize: pageSize,
          totalItems: 0,
          hasNextPage: false,
          hasPreviousPage: false
        }
      };
    }
  }

  useEffect(() => {
    async function setLocationVehicleAsync(locationVehicleId: string) {
      if (!locationVehicleId) {
        vmc.vehicle.setLocationVehicle(undefined);
        return;
      }

      //first check if we already have the vehicle available in memory
      setLocationVehicle(vehicles.find((it) => it.name === locationVehicleId));

      //if not, next try to fetch it (but VINs are now handled by route-based navigation)
      if (!locationVehicle) {
        // Check if this looks like a VIN (16-17 characters, alphanumeric, or fleet pattern)
        const isVinPattern = /^[A-HJ-NPR-Z0-9]{16,17}$/i.test(locationVehicleId) || 
                            /^1FLEET\d{10}$/i.test(locationVehicleId);
        
        if (isVinPattern) {
          console.log(`VIN detected in hash - redirecting to route-based URL: ${locationVehicleId}`);
          // VINs should use route-based navigation, not hash-based
          navigate(`/vehicles/management/${locationVehicleId}`, { replace: true });
          return;
        }
        
        // For non-VINs, try to fetch from API
        try {
          const response = await authFetch(`${getRuntimeConfig().apiEndpoint}api/v1/vehicles/${locationVehicleId}`);
          const vehicle = await response.json();
          if (vehicle) {
            setLocationVehicle(vehicle);
          }
        } catch (error) {
          console.warn('Vehicle not found in fleet management system:', locationVehicleId, error);
          // Clear the hash if vehicle not found
          window.location.hash = '';
        }
      }

      vmc.vehicle.setLocationVehicle(locationVehicle);
    }

    const locationVehicleId = window.location.hash.substring(1);

    const breadcrumbItems = [
      { text: "Vehicles", href: UI_ROUTES.VEHICLE_MANAGEMENT },
    ];

    if (window.location.hash.length > 0)
      breadcrumbItems.push({
        text: locationVehicleId,
        href: joinRoutes(UI_ROUTES.VEHICLE_MANAGEMENT, locationVehicleId),
      });

    vmc.breadcrumbs.setBreadcrumbItems(breadcrumbItems);

    setLocationVehicleAsync(locationVehicleId);
  }, [window.location.hash, locationVehicle]);

  // Pagination handlers
  const handlePageChange = async (page: number) => {
    console.log('📄 Page changed to:', page);
    setCurrentPage(page);
    setIsLoading(true);
    
    try {
      const result = await fetchVehicles(page, pageSize, selectedFleet);
      setVehicles(result.vehicles);
      setTotalVehicleCount(result.total);
      setPaginationInfo(result.pagination);
    } catch (err) {
      console.error('❌ Page change error:', err);
      setError(err);
    } finally {
      setIsLoading(false);
    }
  };

  const handlePageSizeChange = async (newPageSize: number) => {
    console.log('📏 Page size changed to:', newPageSize);
    setPageSize(newPageSize);
    setCurrentPage(1); // Reset to first page when changing page size
    setIsLoading(true);
    
    try {
      const result = await fetchVehicles(1, newPageSize, selectedFleet);
      setVehicles(result.vehicles);
      setTotalVehicleCount(result.total);
      setPaginationInfo(result.pagination);
    } catch (err) {
      console.error('❌ Page size change error:', err);
      setError(err);
    } finally {
      setIsLoading(false);
    }
  };

  // Handle fleet filter change
  const handleFleetFilterChange = async (fleetId: string) => {
    console.log('🚗 Fleet filter changed to:', fleetId);
    setSelectedFleet(fleetId);
    setCurrentPage(1); // Reset to first page when filter changes
    setIsLoading(true);
    
    try {
      const result = await fetchVehicles(1, pageSize, fleetId, searchText);
      setVehicles(result.vehicles);
      setTotalVehicleCount(result.total);
      setPaginationInfo(result.pagination);
    } catch (err) {
      console.error('❌ Fleet filter error:', err);
      setError(err);
    } finally {
      setIsLoading(false);
    }
  };

  // Handle search filter change — server-side filter on vin/make/model.
  // Required because pagination loads only 25 vehicles per page; client-side
  // filter alone can't find matches outside the current page.
  const handleSearchChange = async (newSearchText: string) => {
    console.log('🔍 Search filter changed to:', newSearchText);
    setSearchText(newSearchText);
    setCurrentPage(1);
    setIsLoading(true);

    try {
      const result = await fetchVehicles(1, pageSize, selectedFleet, newSearchText);
      setVehicles(result.vehicles);
      setTotalVehicleCount(result.total);
      setPaginationInfo(result.pagination);
    } catch (err) {
      console.error('❌ Search filter error:', err);
      setError(err);
    } finally {
      setIsLoading(false);
    }
  };

  const { notifications, notify } = useNotifications();

  useEffect(() => {
    // Check if there's a notification in the location state
    if (location.state?.notification) {
      notify([
        {
          id: location.state.notification.id,
          action: "create",
          status: location.state?.notification.status,
          message:
            location.state?.notification.status === "success"
              ? `Successfully created vehicle ${location.state.notification.id}.`
              : `Failed to create vehicle ${location.state.notification.id}.`,
        },
      ]);
      navigate(UI_ROUTES.VEHICLE_MANAGEMENT, { state: null });
    }
  }, [location]);

  const onDeleteInit = () => setShowDeleteModal(true);
  const onDeleteDiscard = () => setShowDeleteModal(false);
  const onDeleteConfirm = async () => {
    const vehiclesToDelete: VehicleItem[] = locationVehicle
      ? [locationVehicle]
      : selectedItems;
    setSelectedItems([]);
    setShowDeleteModal(false);

    const vehiclesDeletePromises = vehiclesToDelete.map(async (vehicle) => {
      notify([
        {
          id: vehicle.name,
          action: "delete",
          status: "in-progress",
          message: `Deleting vehicle ${vehicle.name}`,
        },
      ]);
      try {
        const response = await authFetch(`${getRuntimeConfig().apiEndpoint}api/v1/vehicles/${vehicle.vehicleId || vehicle.name}`, {
          method: 'DELETE'
        });
        if (response.ok) {
          notify([
            {
              id: vehicle.name,
              action: "delete",
              status: "success",
              message: `Successfully deleted vehicle ${vehicle.name}`,
            },
          ]);
        } else {
          notify([
            {
              id: vehicle.name,
              action: "delete",
              status: "error",
              message: `Error deleting vehicle ${vehicle.name}`,
            },
          ]);
        }
      } catch (err) {
        notify([
          {
            id: vehicle.name,
            action: "delete",
            status: "error",
            message: `Error deleting vehicle ${vehicle.name}`,
          },
        ]);
        console.log(err);
      }
    });
    await Promise.all(vehiclesDeletePromises);
    const result = await fetchVehicles(currentPage, pageSize);
    setVehicles(result.vehicles);
    setTotalVehicleCount(result.total);
    setPaginationInfo(result.pagination);
  };

  // Initial load effect - only runs once
  useEffect(() => {
    async function initialLoad() {
      setIsLoading(true);
      setError(null);
      
      try {
        console.log('🚗 Initial load - fetching vehicles...');
        const result = await fetchVehicles(currentPage, pageSize);
        console.log('✅ Initial load - fetched vehicles:', result.vehicles.length);
        console.log('✅ Total vehicle count:', result.total);
        console.log('✅ Pagination info:', result.pagination);
        setVehicles(result.vehicles);
        setTotalVehicleCount(result.total);
        setPaginationInfo(result.pagination);
      } catch (err) {
        console.error('❌ Initial load error:', err);
        setError(err);
      } finally {
        setIsLoading(false);
      }
    }

    initialLoad();
  }, [currentPage, pageSize]); // Re-run when page or pageSize changes

  // Hash change effect - handles navigation to specific vehicles
  useEffect(() => {
    const locationVehicleId = window.location.hash.slice(1);
    
    if (locationVehicleId && locationVehicle) {
      // We already have the vehicle loaded, no need to fetch again
      return;
    }
    
    // Handle hash changes for vehicle navigation
    if (locationVehicleId) {
      console.log('🔗 Hash changed to:', locationVehicleId);
      // The setLocationVehicleAsync will handle this
    }
  }, [window.location.hash, locationVehicle]);

  useEffect(() => {
    setSelectedItems([]);
  }, [window.location.hash]);

  const viewMode = new URLSearchParams(location.search).get('view') === 'map' ? 'map' : 'table';

  return (
    <>
      {window.location.hash && !vmc.vehicle.locationVehicle ? (
        <StatusIndicator type="loading">Loading...</StatusIndicator>
      ) : vmc.vehicle.locationVehicle ? (
        <VehicleDashboardView />
      ) : (
        <VehiclesPage
          vehicles={vehicles}
          totalVehicleCount={totalVehicleCount}
          selectedItems={selectedItems}
          setSelectedItems={setSelectedItems}
          onDeleteInit={onDeleteInit}
          notifications={notifications}
          isLoading={isLoading}
          error={error}
          currentPage={currentPage}
          pageSize={pageSize}
          paginationInfo={paginationInfo}
          onPageChange={handlePageChange}
          onPageSizeChange={handlePageSizeChange}
          onFleetFilterChange={handleFleetFilterChange}
          searchText={searchText}
          onSearchChange={handleSearchChange}
          viewMode={viewMode}
        />
      )}
      <DeleteModal
        visible={showDeleteModal}
        onDiscard={onDeleteDiscard}
        onDelete={onDeleteConfirm}
        vehicles={
          vmc.vehicle.locationVehicle
            ? [vmc.vehicle.locationVehicle]
            : selectedItems
        }
      />
    </>
  );
}
