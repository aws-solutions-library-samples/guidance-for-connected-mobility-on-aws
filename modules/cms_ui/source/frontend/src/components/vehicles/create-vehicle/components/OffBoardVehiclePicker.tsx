// SPDX-License-Identifier: Apache-2.0
//
// OffBoardVehiclePicker — the off-board branch of the create-vehicle flow.
//
// When the operator picks a fleet that's assigned to a data product
// (Meridian, Ford Pro, Nova) AND flips the data-source toggle to off-board,
// they should NOT have to type VIN/make/model manually. The producer already
// has a list of VINs available for this fleet — CMS just needs to pull that
// list, let the operator pick which VINs to import, and create the CMS
// vehicle records plus subscription enrollment in one shot.
//
// This component replaces the manual CreateVehicleInputPanel in that branch.
//
// Notes:
//   - Producer VINs come from getProducerVehicleOffer(producer) — mocked
//     today, but since the demo positions CMS as owning the CS platform,
//     this data is "available to us".
//   - If the fleet has more than one assigned data product, the operator
//     picks which producer to import from.
//   - If the operator holds multiple subscriptions against the picked data
//     product, we auto-select the first one for a stub. A real
//     implementation would prompt for the subscription/tier.
//   - "Import" mutates the mock backing store (CMS_FLEET_POOL grows,
//     enrollment overlay records the subscription linkage). No backend
//     call today.

import React, { useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Box,
  Button,
  Container,
  FormField,
  Header,
  Modal,
  Select,
  SpaceBetween,
  Table,
  TextFilter,
} from '@cloudscape-design/components';
import type { FleetItem } from '@/types/fleet-types';
import {
  assignDataProductToFleet,
  getAllCatalog,
  getAssignedProductIdsForFleet,
  getDataProductById,
  getProducerVehicleOffer,
  getSubscriptionsForDataProduct,
  importAndEnrollFromProducer,
  DataProduct,
  ProducerVehicleOffer,
  Subscription,
} from '../../../data-products/mockData';

interface Props {
  fleet: FleetItem;
  onCancel: () => void;
  onImported: (imported: number, fleetName: string, producer: string, subscription: Subscription) => void;
}

export const OffBoardVehiclePicker: React.FC<Props> = ({ fleet, onCancel, onImported }) => {
  const fleetId = (fleet.id ?? fleet.fleetId ?? '') as string;
  const fleetName = (fleet.name ?? fleet.fleetId ?? fleetId) as string;

  // Bump forces the assigned-product read to re-run after an inline
  // assignment happens. Assignments live in the fleetAssignments overlay
  // which doesn't emit change events.
  const [assignBump, setAssignBump] = useState(0);
  const assignedProductIds = useMemo(
    () => getAssignedProductIdsForFleet(fleetId),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [fleetId, assignBump],
  );

  // Data-product picker — defaults to the first assignment; user can switch
  // if this fleet is linked to more than one producer.
  const [selectedProductId, setSelectedProductId] = useState<string | null>(
    assignedProductIds[0] ?? null,
  );

  const selectedProduct = useMemo(
    () => (selectedProductId ? getDataProductById(selectedProductId) : undefined),
    [selectedProductId],
  );

  // Subscription auto-picker — for the stub, first subscription for the
  // selected data product wins. Reality would prompt for tier.
  const availableSubscriptions = useMemo(
    () => (selectedProductId ? getSubscriptionsForDataProduct(selectedProductId) : []),
    [selectedProductId],
  );
  const [selectedSubscriptionId, setSelectedSubscriptionId] = useState<string | null>(null);

  // Auto-select first subscription when the product changes.
  React.useEffect(() => {
    setSelectedSubscriptionId(availableSubscriptions[0]?.id ?? null);
  }, [availableSubscriptions]);

  const selectedSubscription = useMemo(
    () => availableSubscriptions.find((s) => s.id === selectedSubscriptionId),
    [availableSubscriptions, selectedSubscriptionId],
  );

  // Producer's advertised VINs — excluding any VIN that's already in the
  // CMS fleet inventory. Those can't be imported again (they'd be a
  // no-op), so they shouldn't clutter the picker or be selectable.
  const producerOffer = useMemo(
    () =>
      selectedProduct
        ? getProducerVehicleOffer(selectedProduct.producer).filter((v) => !v.alreadyInCmsFleet)
        : [],
    [selectedProduct],
  );

  const [selectedVins, setSelectedVins] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('');
  const [flash, setFlash] = useState<{ count: number; producer: string } | null>(null);

  const filteredOffer = useMemo(() => {
    if (!filter.trim()) return producerOffer;
    const q = filter.toLowerCase();
    return producerOffer.filter(
      (v) => v.vin.toLowerCase().includes(q) || v.model.toLowerCase().includes(q),
    );
  }, [producerOffer, filter]);

  const handleImport = () => {
    if (!selectedProduct || !selectedSubscription || selectedVins.size === 0) return;
    const picks = producerOffer.filter((v) => selectedVins.has(v.vin));
    const added = importAndEnrollFromProducer(
      selectedSubscription.id,
      selectedProduct.producer,
      picks,
      fleetName,
    );
    setSelectedVins(new Set());
    setFlash({ count: added, producer: selectedProduct.producer });
    onImported(added, fleetName, selectedProduct.producer, selectedSubscription);
  };

  // No assignments case — the fleet isn't linked to any data product yet.
  // Rather than sending the operator off to the catalog page, offer an
  // inline "Assign a data product" mini-form. Once assigned, the picker
  // re-renders with the assignment and the VIN table below appears.
  if (assignedProductIds.length === 0) {
    return (
      <InlineAssignPanel
        fleetName={fleetName}
        onAssigned={(productId) => {
          assignDataProductToFleet(fleetId, productId);
          setAssignBump((n) => n + 1);
          // First assignment auto-selects itself so the picker rolls
          // straight into the VIN table below.
          setSelectedProductId(productId);
        }}
      />
    );
  }

  if (!selectedProduct) {
    return (
      <Container header={<Header variant="h2">Off-board vehicles</Header>}>
        <Alert type="error">Data product not found.</Alert>
      </Container>
    );
  }

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={`Fleet ${fleetName} is assigned to ${selectedProduct.producer}. Pick VINs from the producer's inventory below — CMS will create the vehicle records and enroll them in a subscription in one step. No manual VIN/make/model entry needed.`}
        >
          Off-board vehicles — pick from {selectedProduct.producer}
        </Header>
      }
    >
      <SpaceBetween size="l">
        {flash && (
          <Alert
            type="success"
            dismissible
            onDismiss={() => setFlash(null)}
            statusIconAriaLabel="Success"
          >
            Imported {flash.count} vehicle{flash.count === 1 ? '' : 's'} from {flash.producer}{' '}
            into <b>{fleetName}</b>. They are now enrolled in <b>{selectedSubscription?.productName}</b>.
          </Alert>
        )}

        {/* Data-product picker — only shown if the fleet has multiple assignments. */}
        {assignedProductIds.length > 1 && (
          <FormField
            label="Data product"
            description="This fleet is assigned to multiple producers. Pick which one to import from."
          >
            <Select
              selectedOption={{
                label: `${selectedProduct.producer} — ${selectedProduct.productName}`,
                value: selectedProductId ?? '',
              }}
              options={assignedProductIds.map((id) => {
                const p = getDataProductById(id);
                return {
                  label: p ? `${p.producer} — ${p.productName}` : id,
                  value: id,
                };
              })}
              onChange={({ detail }) => setSelectedProductId(detail.selectedOption.value ?? null)}
            />
          </FormField>
        )}

        {/* Subscription (tier) picker — auto-selected for the stub, but the
            operator can switch if multiple subscriptions exist. */}
        {availableSubscriptions.length === 0 ? (
          <Alert type="warning">
            You don't have any active subscriptions to {selectedProduct.producer}. Subscribe
            first (from the Data Products page) before importing vehicles.
          </Alert>
        ) : availableSubscriptions.length > 1 ? (
          <FormField
            label="Subscription"
            description={`Multiple subscriptions to ${selectedProduct.producer} are active. Pick which tier to enroll these vehicles under.`}
          >
            <Select
              selectedOption={{
                label: selectedSubscription
                  ? `${selectedSubscription.productName} (${selectedSubscription.tier})`
                  : '',
                value: selectedSubscriptionId ?? '',
              }}
              options={availableSubscriptions.map((s) => ({
                label: `${s.productName} (${s.tier})`,
                value: s.id,
                description: `${s.vehiclesCapacity} vehicle cap`,
              }))}
              onChange={({ detail }) =>
                setSelectedSubscriptionId(detail.selectedOption.value ?? null)
              }
            />
          </FormField>
        ) : (
          <Box color="text-body-secondary">
            Subscription: <b>{selectedSubscription?.productName}</b> ({selectedSubscription?.tier})
          </Box>
        )}

        {/* Producer VIN picker. */}
        <Table
          variant="embedded"
          selectionType="multi"
          trackBy="vin"
          items={filteredOffer}
          selectedItems={filteredOffer.filter((v) => selectedVins.has(v.vin))}
          onSelectionChange={({ detail }) => {
            setSelectedVins(new Set(detail.selectedItems.map((v) => v.vin)));
          }}
          columnDefinitions={[
            {
              id: 'vin',
              header: 'VIN',
              cell: (v: ProducerVehicleOffer) => (
                <Box fontFamily="monospace" fontSize="body-s">
                  {v.vin}
                </Box>
              ),
            },
            {
              id: 'model',
              header: 'Model',
              cell: (v: ProducerVehicleOffer) => v.model,
            },
          ]}
          filter={
            <TextFilter
              filteringText={filter}
              filteringPlaceholder="Filter VINs"
              filteringAriaLabel="Filter producer VINs"
              onChange={(e) => setFilter(e.detail.filteringText)}
              countText={`${filteredOffer.length} of ${producerOffer.length}`}
            />
          }
          empty={
            <Box textAlign="center" color="inherit">
              <b>No VINs offered</b>
              <Box variant="p" color="inherit">
                {selectedProduct.producer} has no vehicles to offer for this fleet.
              </Box>
            </Box>
          }
          header={
            <Header
              variant="h3"
              counter={selectedVins.size ? `(${selectedVins.size} selected)` : undefined}
              description={`Producer-advertised VINs — pick which ones to import into ${fleetName} and enroll under ${selectedSubscription?.productName ?? 'the selected subscription'}.`}
            >
              Available vehicles
            </Header>
          }
        />

        {/* Actions */}
        <Box>
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onCancel}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={
                selectedVins.size === 0 ||
                !selectedSubscription ||
                availableSubscriptions.length === 0
              }
              onClick={handleImport}
            >
              Import {selectedVins.size || ''} vehicle{selectedVins.size === 1 ? '' : 's'} to{' '}
              {fleetName}
            </Button>
          </SpaceBetween>
        </Box>
      </SpaceBetween>
    </Container>
  );
};

export default OffBoardVehiclePicker;


/**
 * InlineAssignPanel — shown when the operator picked off-board on a fleet
 * that has NO data-product assignment yet. Rather than sending them off
 * to the catalog page, this lets them pick a data product from the full
 * catalog and assign it to the current fleet without leaving the create-
 * vehicle flow. On assignment, the parent re-renders as the full picker.
 */
const InlineAssignPanel: React.FC<{
  fleetName: string;
  onAssigned: (productId: string) => void;
}> = ({ fleetName, onAssigned }) => {
  const catalog = useMemo<DataProduct[]>(() => getAllCatalog(), []);
  const [selectedId, setSelectedId] = useState<string | null>(catalog[0]?.productId ?? null);
  const selectedProduct = useMemo(
    () => (selectedId ? getDataProductById(selectedId) : undefined),
    [selectedId],
  );

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={`Off-board vehicles come from a producer's advertised inventory. Link a data product to ${fleetName} first — then pick VINs to import.`}
        >
          Off-board — pick a data product for {fleetName}
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Alert type="info">
          <b>{fleetName}</b> isn't linked to a data product yet. Choose a producer to link to
          this fleet, then continue picking vehicles from that producer's list. This assignment
          also appears on the data product's catalog page and its subscriptions.
        </Alert>
        <FormField
          label="Data product"
          description="Pick which producer this fleet should receive off-board data from."
        >
          <Select
            selectedOption={
              selectedProduct
                ? {
                    label: `${selectedProduct.producer} — ${selectedProduct.productName}`,
                    value: selectedProduct.productId,
                  }
                : null
            }
            options={catalog.map((p) => ({
              label: `${p.producer} — ${p.productName}`,
              value: p.productId,
              description: p.connectionType,
            }))}
            onChange={({ detail }) => setSelectedId(detail.selectedOption.value ?? null)}
          />
        </FormField>
        <Box>
          <Button
            variant="primary"
            disabled={!selectedId}
            onClick={() => selectedId && onAssigned(selectedId)}
            iconName="check"
          >
            Assign {selectedProduct?.producer ?? 'producer'} to {fleetName}
          </Button>
        </Box>
      </SpaceBetween>
    </Container>
  );
};
