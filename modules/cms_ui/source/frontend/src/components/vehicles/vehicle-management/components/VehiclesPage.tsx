// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { Flashbar } from "@cloudscape-design/components";
import VehiclesTable from "./vehicles-table";

export function VehiclesPage({
  vehicles,
  totalVehicleCount,
  selectedItems,
  setSelectedItems,
  onDeleteInit,
  notifications,
  isLoading,
  error,
  // Server-side pagination props
  currentPage,
  pageSize,
  paginationInfo,
  onPageChange,
  onPageSizeChange,
  onFleetFilterChange,
}: any) {
  return (
    <>
      <Flashbar items={notifications} stackItems={true} />
      <VehiclesTable
        vehicles={vehicles}
        totalVehicleCount={totalVehicleCount}
        selectedItems={selectedItems}
        onSelectionChange={(event: any) =>
          setSelectedItems(event.detail.selectedItems)
        }
        onDelete={onDeleteInit}
        isLoading={isLoading}
        error={error}
        // Server-side pagination props
        currentPage={currentPage}
        pageSize={pageSize}
        paginationInfo={paginationInfo}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
        onFleetFilterChange={onFleetFilterChange}
      />
    </>
  );
}
