// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { 
  Flashbar
} from "@cloudscape-design/components";
import FleetsTable from "./fleets-table";

export function FleetsPage({
  fleets,
  selectedItems,
  setSelectedItems,
  onEditInit,
  onDeleteInit,
  notifications,
  isLoading,
  error,
}: any) {
  return (
    <>
      <Flashbar items={notifications} stackItems={true} />
      <FleetsTable
        fleets={fleets}
        selectedItems={selectedItems}
        onSelectionChange={(event: any) =>
          setSelectedItems(event.detail.selectedItems)
        }
        onDelete={onDeleteInit}
        onEdit={onEditInit}
        isLoading={isLoading}
        error={error}
      />
    </>
  );
}
