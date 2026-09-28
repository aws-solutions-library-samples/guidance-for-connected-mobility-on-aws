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
import { useFleetFilter } from "@/components/fleet-filter/FleetFilter";
import { ALL_FLEETS_ID } from "@/components/fleet-picker/useFleetSelection";

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
  const [searchText, setSearchText] = useState<string>('');
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [pageSize, setPageSize] = useState<number>(25);
  const [paginationInfo, setPaginationInfo] = useState<any>(null);
  // Server-side sort. Defaults match the previous hardcoded query string so
  // the initial page order is unchanged. `createdAt` deliberately matches no
  // column's sortingField, so no sort indicator shows until the user sorts.
  const [sortBy, setSortBy] = useState<string>('createdAt');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  const vmc = useContext(VehicleManagementContext);
  const api = useContext(ApiContext);
  const userContext = useContext(UserContext);
  const navigate = useNavigate();
  const location = useLocation();

  // Drive fleet filter from the top-nav fleet picker (FleetFilterContext).
  // Previously this component maintained its own disconnected `selectedFleet`
  // local state — the top-nav picker had no effect on the vehicles table.
  const { selectedFleetId } = useFleetFilter();

  async function fetchVehicles(page: number = 1, pageSz: number = 25, fleetId?: string, search?: string, sortField?: string, sortDir?: string): Promise<{ vehicles: VehicleItem[], total: number, pagination: any }> {
    const effSortBy = sortField ?? 'createdAt';
    const effSortOrder = sortDir ?? 'desc';
    console.log("📄 Requesting page:", page, "pageSize:", pageSz, "fleetId:", fleetId, "search:", search, "sortBy:", effSortBy, "sortOrder:", effSortOrder);

    try {
      let url = `${getRuntimeConfig().apiEndpoint}api/v1/vehicles?limit=${pageSz}&page=${page}&sortBy=${encodeURIComponent(effSortBy)}&sortOrder=${encodeURIComponent(effSortOrder)}`;
      if (fleetId && fleetId !== ALL_FLEETS_ID) {
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

      const total = data.total || data.count || (data.vehicles?.length || 0);
      console.log("📊 Using total from API response:", total);

      return {
        vehicles: data.vehicles || [],
        total: total,
        pagination: data.pagination || {
          currentPage: page,
          totalPages: data.totalPages || Math.ceil(total / pageSz),
          pageSize: pageSz,
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
          pageSize: pageSz,
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
      const result = await fetchVehicles(page, pageSize, selectedFleetId, searchText, sortBy, sortOrder);
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
    setCurrentPage(1);
    setIsLoading(true);

    try {
      const result = await fetchVehicles(1, newPageSize, selectedFleetId, searchText, sortBy, sortOrder);
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

  // Handle search filter change — server-side filter on vin/make/model.
  // Required because pagination loads only 25 vehicles per page; client-side
  // filter alone can't find matches outside the current page.
  const handleSearchChange = async (newSearchText: string) => {
    console.log('🔍 Search filter changed to:', newSearchText);
    setSearchText(newSearchText);
    setCurrentPage(1);
    setIsLoading(true);

    try {
      const result = await fetchVehicles(1, pageSize, selectedFleetId, newSearchText, sortBy, sortOrder);
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

  // Handle sort change — server-side, because the table shows 25 of ~155 rows
  // and a client-side sort would only order the current page while appearing
  // to order the whole fleet. Resets to page 1: after a re-sort, the old page
  // number refers to a different slice of a differently-ordered set.
  const handleSortChange = async (field: string, direction: 'asc' | 'desc') => {
    console.log('↕️ Sort changed to:', field, direction);
    setSortBy(field);
    setSortOrder(direction);
    setCurrentPage(1);
    setIsLoading(true);

    try {
      const result = await fetchVehicles(1, pageSize, selectedFleetId, searchText, field, direction);
      setVehicles(result.vehicles);
      setTotalVehicleCount(result.total);
      setPaginationInfo(result.pagination);
    } catch (err) {
      console.error('❌ Sort change error:', err);
      setError(err);
    } finally {
      setIsLoading(false);
    }
  };

  const { notifications, notify } = useNotifications();

  useEffect(() => {
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
    const result = await fetchVehicles(currentPage, pageSize, selectedFleetId, searchText, sortBy, sortOrder);
    setVehicles(result.vehicles);
    setTotalVehicleCount(result.total);
    setPaginationInfo(result.pagination);
  };

  // Re-fetch whenever the top-nav fleet selection changes. Reset to page 1 so
  // we don't land on a page that doesn't exist for a smaller fleet.
  useEffect(() => {
    let cancelled = false;
    setCurrentPage(1);
    setIsLoading(true);
    setError(null);

    fetchVehicles(1, pageSize, selectedFleetId, searchText, sortBy, sortOrder).then((result) => {
      if (cancelled) return;
      setVehicles(result.vehicles);
      setTotalVehicleCount(result.total);
      setPaginationInfo(result.pagination);
    }).catch((err) => {
      if (cancelled) return;
      console.error('❌ Fleet filter change error:', err);
      setError(err);
    }).finally(() => {
      if (!cancelled) setIsLoading(false);
    });

    return () => { cancelled = true; };
  // pageSize and searchText intentionally excluded — each has its own handler
  // that resets page and re-fetches. Including them here would double-fetch.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFleetId]);

  // Hash change effect - handles navigation to specific vehicles
  useEffect(() => {
    const locationVehicleId = window.location.hash.slice(1);

    if (locationVehicleId && locationVehicle) {
      return;
    }

    if (locationVehicleId) {
      console.log('🔗 Hash changed to:', locationVehicleId);
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
          onFleetFilterChange={() => {}} // fleet filter is now driven by the top-nav picker
          searchText={searchText}
          onSearchChange={handleSearchChange}
          sortBy={sortBy}
          sortOrder={sortOrder}
          onSortChange={handleSortChange}
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
