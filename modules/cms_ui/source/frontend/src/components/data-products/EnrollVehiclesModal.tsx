// SPDX-License-Identifier: Apache-2.0
//
// EnrollVehiclesModal — the "add vehicles to this subscription" flow.
//
// Fleet operators subscribe to a data product (e.g. Meridian Standard tier,
// 500-vehicle capacity) but nothing flows until specific VINs are enrolled.
// Enrollment has two paths — matching how a real fleet operator would
// actually work with a producer:
//
//   1. FROM MY FLEET — pick vehicles the operator already has in CMS's
//      inventory that are compatible with this producer. Standard fleet
//      management flow, no external state change.
//
//   2. AUTO-IMPORT FROM PRODUCER — the producer publishes a list of VINs
//      it can send data for; some of those already exist in CMS's fleet
//      (already-known), some don't (import candidates). Importing creates
//      the CMS vehicle record and enrolls it in one step.
//
// Selection is checkbox-based. Capacity remaining is enforced client-side;
// server would re-check in real CMS. Success closes the modal and the
// parent page re-renders from getEnrolledVehiclesForSub().

import React, { useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Box,
  Button,
  FormField,
  Header,
  Modal,
  SpaceBetween,
  Table,
  Tabs,
  Select,
  TextFilter,
} from '@cloudscape-design/components';
import {
  Subscription,
  AvailableFleetVehicle,
  ProducerVehicleOffer,
  enrollVehicles,
  importAndEnrollFromProducer,
  getFleetVehiclesEligibleFor,
  getProducerVehicleOffer,
  isFleetAssignedToProducer,
} from './mockData';
import { useDataProductFleets } from './useDataProductFleets';
import type { FleetItem } from '@/types/fleet-types';

interface Props {
  visible: boolean;
  subscription: Subscription;
  currentlyEnrolledCount: number;
  onDismiss: () => void;
  onEnrolled: (added: number) => void;
}

const EnrollVehiclesModal: React.FC<Props> = ({
  visible,
  subscription,
  currentlyEnrolledCount,
  onDismiss,
  onEnrolled,
}) => {
  const [activeTab, setActiveTab] = useState<'fleet' | 'producer'>('fleet');
  const [selectedFleetVins, setSelectedFleetVins] = useState<Set<string>>(new Set());
  const [selectedProducerVins, setSelectedProducerVins] = useState<Set<string>>(new Set());
  const [fleetFilter, setFleetFilter] = useState('');
  const [producerFilter, setProducerFilter] = useState('');
  // Fleet-scope filter: 'all' = every eligible vehicle; otherwise limit to
  // vehicles whose CMS fleet is the specific real fleet selected. Fleets
  // assigned to the producer come from the real /api/v1/fleets endpoint
  // filtered through the fleetAssignments overlay.
  const [fleetScope, setFleetScope] = useState<string>('all');
  const { fleets: allFleets } = useDataProductFleets();
  const assignedFleets = useMemo<FleetItem[]>(
    () =>
      allFleets.filter((f) => {
        const id = (f.id ?? f.fleetId ?? '') as string;
        return id && isFleetAssignedToProducer(id, subscription.producer);
      }),
    [allFleets, subscription.producer],
  );
  const scopedFleet = useMemo<FleetItem | undefined>(
    () => assignedFleets.find((f) => (f.id ?? f.fleetId) === fleetScope),
    [assignedFleets, fleetScope],
  );

  const remainingCapacity = subscription.vehiclesCapacity - currentlyEnrolledCount;

  // Refresh the pools on every open — getProducerVehicleOffer filters out
  // VINs that were auto-imported into the CMS fleet during a previous
  // session's enrollment, keeping the "already in your fleet" story clean.
  const fleetPool = useMemo<AvailableFleetVehicle[]>(() => {
    const base = getFleetVehiclesEligibleFor(subscription.producer);
    if (fleetScope === 'all') return base;
    // Narrow to a specific real fleet by name. Mock fleet-pool vehicles
    // are tagged with a fleetName that likely won't match a real fleet's
    // name — that's OK; the empty state below coaches the user to use the
    // producer-import tab, which now lands VINs INTO the selected fleet.
    if (!scopedFleet) return base;
    return base.filter((v) => v.fleetName === (scopedFleet.name ?? ''));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscription.producer, visible, fleetScope, scopedFleet]);
  // Producer's advertised VINs — excluding VINs already in the CMS
  // fleet inventory (importing them again would be a no-op). Keeping
  // them out of the list rather than showing them disabled avoids
  // confusion between the two enrollment paths.
  const producerOffer = useMemo<ProducerVehicleOffer[]>(
    () => getProducerVehicleOffer(subscription.producer).filter((v) => !v.alreadyInCmsFleet),
    [subscription.producer, visible],
  );

  // Filter pools by user's text search.
  const filteredFleet = useMemo(() => {
    if (!fleetFilter.trim()) return fleetPool;
    const q = fleetFilter.toLowerCase();
    return fleetPool.filter(
      (v) =>
        v.vin.toLowerCase().includes(q) ||
        v.vehicleId.toLowerCase().includes(q) ||
        v.model.toLowerCase().includes(q) ||
        v.fleetName.toLowerCase().includes(q),
    );
  }, [fleetPool, fleetFilter]);

  const filteredProducer = useMemo(() => {
    if (!producerFilter.trim()) return producerOffer;
    const q = producerFilter.toLowerCase();
    return producerOffer.filter(
      (v) => v.vin.toLowerCase().includes(q) || v.model.toLowerCase().includes(q),
    );
  }, [producerOffer, producerFilter]);

  const selectedCount =
    activeTab === 'fleet' ? selectedFleetVins.size : selectedProducerVins.size;
  const overCapacity = selectedCount > remainingCapacity;

  const reset = () => {
    setSelectedFleetVins(new Set());
    setSelectedProducerVins(new Set());
    setFleetFilter('');
    setProducerFilter('');
    setActiveTab('fleet');
    setFleetScope('all');
  };

  const handleDismiss = () => {
    reset();
    onDismiss();
  };

  const handleEnroll = () => {
    if (overCapacity || selectedCount === 0) return;
    let added = 0;
    if (activeTab === 'fleet') {
      const picked = fleetPool.filter((v) => selectedFleetVins.has(v.vin));
      added = enrollVehicles(subscription.id, picked.map((v) => ({
        vehicleId: v.vehicleId,
        vin: v.vin,
        model: v.model,
        feedHealth: 'streaming' as const,
        lastPacketAt: 'just now',
      })));
    } else {
      const picked = producerOffer.filter((v) => selectedProducerVins.has(v.vin));
      const targetName = (scopedFleet?.name ?? undefined) as string | undefined;
      added = importAndEnrollFromProducer(subscription.id, subscription.producer, picked, targetName);
    }
    reset();
    onEnrolled(added);
  };

  return (
    <Modal
      visible={visible}
      onDismiss={handleDismiss}
      header={`Enroll vehicles — ${subscription.productName}`}
      size="large"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={handleDismiss}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={selectedCount === 0 || overCapacity}
              onClick={handleEnroll}
            >
              {activeTab === 'fleet'
                ? `Enroll ${selectedCount} vehicle${selectedCount === 1 ? '' : 's'}`
                : `Import & enroll ${selectedCount} vehicle${selectedCount === 1 ? '' : 's'}`}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="l">
        <Alert type="info" statusIconAriaLabel="Info">
          <b>{remainingCapacity}</b> of {subscription.vehiclesCapacity} vehicle slots remaining on
          this subscription. Each enrolled vehicle starts receiving data from{' '}
          <b>{subscription.producer}</b> immediately.
        </Alert>

        {overCapacity && (
          <Alert type="error">
            You've selected {selectedCount} vehicles but only {remainingCapacity} slots remain.
            Deselect some or increase subscription capacity.
          </Alert>
        )}

        {/* Fleet-scope filter — narrows the vehicle pool to a specific fleet
            that's already assigned to this subscription's producer. Fleets
            appear here only if they were linked to {subscription.producer}
            via the Catalog detail page's "Assigned fleets" section. */}
        {assignedFleets.length > 0 && (
          <FormField
            label="Scope by fleet"
            description={`Restrict the vehicles below to a single fleet already assigned to ${subscription.producer}, or view every eligible vehicle across all your fleets. Producer-side imports will land into the selected fleet.`}
          >
            <Select
              selectedOption={
                fleetScope === 'all'
                  ? { label: 'All fleets', value: 'all' }
                  : {
                      label:
                        (scopedFleet?.name ?? scopedFleet?.fleetId ?? 'All fleets') as string,
                      value: fleetScope,
                    }
              }
              options={[
                { label: 'All fleets', value: 'all' },
                ...assignedFleets.map((f) => {
                  const id = (f.id ?? f.fleetId ?? '') as string;
                  const name = (f.name ?? f.fleetId ?? id) as string;
                  const count = f.vehicleCount ?? f.totalVehicles ?? f.numTotalVehicles ?? 0;
                  return {
                    label: name,
                    value: id,
                    description: `${count} vehicles`,
                  };
                }),
              ]}
              onChange={({ detail }) => setFleetScope(detail.selectedOption.value ?? 'all')}
            />
          </FormField>
        )}

        <Tabs
          activeTabId={activeTab}
          onChange={({ detail }) => setActiveTab(detail.activeTabId as 'fleet' | 'producer')}
          tabs={[
            {
              id: 'fleet',
              label: `From my fleet (${fleetPool.length})`,
              content: (
                <SpaceBetween size="m">
                  <Box color="text-body-secondary">
                    Pick vehicles you already have in CMS. Compatibility is determined by the
                    producer's supported model families.
                  </Box>
                  <Table
                    variant="embedded"
                    selectionType="multi"
                    trackBy="vin"
                    items={filteredFleet}
                    selectedItems={filteredFleet.filter((v) => selectedFleetVins.has(v.vin))}
                    onSelectionChange={({ detail }) => {
                      setSelectedFleetVins(new Set(detail.selectedItems.map((v) => v.vin)));
                    }}
                    columnDefinitions={[
                      {
                        id: 'vehicleId',
                        header: 'Vehicle ID',
                        cell: (v: AvailableFleetVehicle) => (
                          <Box fontFamily="monospace" fontSize="body-s">
                            {v.vehicleId}
                          </Box>
                        ),
                      },
                      {
                        id: 'vin',
                        header: 'VIN',
                        cell: (v: AvailableFleetVehicle) => (
                          <Box fontFamily="monospace" fontSize="body-s">
                            {v.vin}
                          </Box>
                        ),
                      },
                      { id: 'model', header: 'Model', cell: (v: AvailableFleetVehicle) => v.model },
                      { id: 'fleet', header: 'Fleet', cell: (v: AvailableFleetVehicle) => v.fleetName },
                    ]}
                    filter={
                      <TextFilter
                        filteringText={fleetFilter}
                        filteringPlaceholder="Filter vehicles"
                        filteringAriaLabel="Filter fleet vehicles"
                        onChange={(e) => setFleetFilter(e.detail.filteringText)}
                        countText={`${filteredFleet.length} of ${fleetPool.length}`}
                      />
                    }
                    empty={
                      <Box textAlign="center" color="inherit">
                        <b>No compatible vehicles</b>
                        <Box variant="p" color="inherit">
                          None of your fleet vehicles match {subscription.producer}'s supported
                          models. Try the "From producer" tab to import.
                        </Box>
                      </Box>
                    }
                    header={
                      <Header
                        variant="h3"
                        counter={selectedFleetVins.size ? `(${selectedFleetVins.size} selected)` : undefined}
                      >
                        Your fleet vehicles
                      </Header>
                    }
                  />
                </SpaceBetween>
              ),
            },
            {
              id: 'producer',
              label: `Auto-import from ${subscription.producer} (${producerOffer.length})`,
              content: (
                <SpaceBetween size="m">
                  <Alert type="info">
                    {subscription.producer} advertises {producerOffer.length} VIN
                    {producerOffer.length === 1 ? '' : 's'} it can send data for that aren't
                    yet in your CMS fleet. Select the ones you want to import and enroll —
                    each becomes a new CMS vehicle record streaming data from{' '}
                    {subscription.producer} immediately.
                  </Alert>
                  <Table
                    variant="embedded"
                    selectionType="multi"
                    trackBy="vin"
                    items={filteredProducer}
                    selectedItems={filteredProducer.filter((v) => selectedProducerVins.has(v.vin))}
                    onSelectionChange={({ detail }) => {
                      setSelectedProducerVins(new Set(detail.selectedItems.map((v) => v.vin)));
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
                      { id: 'model', header: 'Model', cell: (v: ProducerVehicleOffer) => v.model },
                    ]}
                    filter={
                      <TextFilter
                        filteringText={producerFilter}
                        filteringPlaceholder="Filter VINs"
                        filteringAriaLabel="Filter producer VINs"
                        onChange={(e) => setProducerFilter(e.detail.filteringText)}
                        countText={`${filteredProducer.length} of ${producerOffer.length}`}
                      />
                    }
                    empty={
                      <Box textAlign="center" color="inherit">
                        <b>No VINs offered</b>
                        <Box variant="p" color="inherit">
                          {subscription.producer} has no vehicles to offer for this subscription.
                        </Box>
                      </Box>
                    }
                    header={
                      <Header
                        variant="h3"
                        counter={selectedProducerVins.size ? `(${selectedProducerVins.size} selected)` : undefined}
                      >
                        Producer-advertised VINs
                      </Header>
                    }
                  />
                </SpaceBetween>
              ),
            },
          ]}
        />
      </SpaceBetween>
    </Modal>
  );
};

export default EnrollVehiclesModal;
