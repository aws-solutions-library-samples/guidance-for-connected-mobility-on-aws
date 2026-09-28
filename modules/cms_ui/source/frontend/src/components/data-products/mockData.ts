// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Mock data for the Data Products subscriber-side stub. Purely UI — no API,
// no CS-portal links. Once the shape is agreed with the user, this file gets
// replaced by a real backend (subscription list + per-subscription detail
// endpoints) and the shape of the interfaces below becomes the API contract.

export type SubscriptionStatus = 'Active' | 'Paused' | 'Pending' | 'Expired';
export type FeedHealth = 'streaming' | 'delayed' | 'down' | 'paused';
export type Tier = 'Premium' | 'Standard' | 'Basic' | 'Default';
export type SignalType = 'float' | 'integer' | 'string' | 'boolean';

export interface EnrolledVehicle {
  vehicleId: string;
  vin: string;
  model: string;
  feedHealth: FeedHealth;
  lastPacketAt: string;
}

export interface AvailableSignal {
  /** Canonical CMS field name — what the subscriber's queries use. */
  cmsField: string;
  dataType: SignalType;
  unit?: string;
  /** Recent value from the feed (redacted of vehicle identity). */
  sampleValue: string;
  /** Whether this signal is guaranteed by the tier, or only best-effort. */
  guarantee: 'guaranteed' | 'best-effort';
}

/** A single row of a data product's transform manifest — how one OEM signal
 *  maps to a CMS canonical field. Multiple mappings make up the transform
 *  manifest that CMS applies to inbound telemetry from this producer. */
export interface SignalMapping {
  /** OEM-side signal name (documentation/debugging only). */
  sourceSignal: string;
  /** Dot-notation path to the value in the source payload. Producer-specific. */
  sourcePath: string;
  /** CMS canonical field name the value maps to. Must match a field the
   *  Signal Catalog recognises. */
  cmsField: string;
  dataType: SignalType;
  /** Output unit after any conversion. */
  unit?: string;
  /** Named unit conversion applied to the source value. E.g. 'kph_to_mph'. */
  unitConversion?: string;
  /** Whether the field is required in every message. */
  required?: boolean;
}

/** A single row of a data product's event manifest — how one producer event
 *  maps to a CMS canonical event. */
export interface EventMapping {
  /** Producer-side event name. */
  sourceEvent: string;
  /** CMS canonical event name (must exist in CMS_CANONICAL_EVENTS). */
  cmsEvent: string;
}

export interface Subscription {
  id: string;
  productName: string;
  producer: string;
  tier: Tier;
  status: SubscriptionStatus;
  feedHealth: FeedHealth;
  lastPacketAt: string;
  p95LatencyMs: number;
  errorRate24h: string;
  contractStart: string;
  nextRenewal: string;
  vehiclesCapacity: number;
  entitledSignalCount: number;
  totalOemSignals: number;
  dataVolumeCycle: string;
  estimatedCostCycle: string;
  quotaHeadroomPct: number;
  description: string;
  /** Signals not available on this tier — upsell to higher tier. */
  unavailableSignals: string[];
  upgradeToTier?: Tier;
  enrolledVehicles: EnrolledVehicle[];
  availableSignals: AvailableSignal[];
  /** Producer-issued billing account number (e.g. Ford Pro's A01020708).
   *  Optional — some producer relationships bill via central AWS Marketplace
   *  rather than the producer directly. */
  accountNumber?: string;
  /** Invoice history for this subscription, newest first. Not every
   *  producer issues invoices in this format — Ford Pro does; some settle
   *  through AWS Marketplace and don't have this level of detail here. */
  invoices?: Invoice[];
}

/** A single billing invoice from the producer against this subscription.
 *  Modelled after a real Ford Pro invoice — SKU-based line items with
 *  optional per-unit costs, subtotal + sales tax + total, unpaid/paid/
 *  overdue status. */
export interface Invoice {
  invoiceNumber: string;
  /** Invoice issue date (YYYY-MM-DD). */
  invoiceDate: string;
  /** Human-readable billing period (e.g. 'July 2026'). */
  billingPeriod: string;
  /** Producer-issued account number this invoice is against. Duplicated
   *  onto the invoice so a printed invoice is self-contained. */
  accountNumber: string;
  /** Payment due date (YYYY-MM-DD). */
  dueDate: string;
  status: 'paid' | 'unpaid' | 'overdue';
  lineItems: InvoiceLineItem[];
  /** Sum of line-item amounts, pre-tax. */
  subtotal: number;
  salesTax: number;
  /** Grand total. Should equal subtotal + salesTax. */
  totalDue: number;
}

/** A single line on a producer invoice. Modelled on Ford Pro's format —
 *  SKU + description + unit cost + qty + amount. `unitCost` is optional
 *  because some lines (e.g. prorated mid-period adds) don't have a clean
 *  per-unit cost. */
export interface InvoiceLineItem {
  sku: string;
  description: string;
  /** Per-unit cost. Absent on prorated / one-off line items. */
  unitCost?: number;
  qty: number;
  amount: number;
}

// ─── Signal templates ───────────────────────────────────────────────────────
// Realistic CMS-canonical field names + sample values. Kept in one place so
// each tier's signal set is a slice of the same master list.

const ALL_SIGNALS: AvailableSignal[] = [
  { cmsField: 'vehicleSpeed',              dataType: 'float',   unit: 'mph',     sampleValue: '62.4',   guarantee: 'guaranteed' },
  { cmsField: 'odometer',                  dataType: 'float',   unit: 'mi',      sampleValue: '48,317', guarantee: 'guaranteed' },
  { cmsField: 'engineRunningState',        dataType: 'boolean', sampleValue: 'true',   guarantee: 'guaranteed' },
  { cmsField: 'ignitionState',             dataType: 'string',  sampleValue: 'on',     guarantee: 'guaranteed' },
  { cmsField: 'latitude',                  dataType: 'float',   unit: '°',       sampleValue: '37.7749', guarantee: 'guaranteed' },
  { cmsField: 'longitude',                 dataType: 'float',   unit: '°',       sampleValue: '-122.4194', guarantee: 'guaranteed' },
  { cmsField: 'altitude',                  dataType: 'float',   unit: 'ft',      sampleValue: '52.1',   guarantee: 'best-effort' },
  { cmsField: 'heading',                   dataType: 'float',   unit: '°',       sampleValue: '184.2',  guarantee: 'guaranteed' },
  { cmsField: 'gearPosition',              dataType: 'integer', sampleValue: '3',      guarantee: 'guaranteed' },
  { cmsField: 'fuelLevel',                 dataType: 'float',   unit: '%',       sampleValue: '68.4',   guarantee: 'guaranteed' },
  { cmsField: 'batteryStateOfCharge',      dataType: 'float',   unit: '%',       sampleValue: '78.2',   guarantee: 'guaranteed' },
  { cmsField: 'batteryStateOfHealth',      dataType: 'float',   unit: '%',       sampleValue: '92.7',   guarantee: 'best-effort' },
  { cmsField: 'batteryPackVoltage',        dataType: 'float',   unit: 'V',       sampleValue: '396.8',  guarantee: 'best-effort' },
  { cmsField: 'batteryPackCurrent',        dataType: 'float',   unit: 'A',       sampleValue: '-42.1',  guarantee: 'best-effort' },
  { cmsField: 'batteryPackTemperature',    dataType: 'float',   unit: '°F',      sampleValue: '84.5',   guarantee: 'guaranteed' },
  { cmsField: 'cellVoltageStdDev',         dataType: 'float',   unit: 'mV',      sampleValue: '12.4',   guarantee: 'best-effort' },
  { cmsField: 'thermalCompensationFactor', dataType: 'float',   sampleValue: '1.024',  guarantee: 'best-effort' },
  { cmsField: 'thermalDerateActiveSeconds',dataType: 'integer', unit: 's',       sampleValue: '0',      guarantee: 'best-effort' },
  { cmsField: 'coolantTemperature',        dataType: 'float',   unit: '°F',      sampleValue: '187.3',  guarantee: 'best-effort' },
  { cmsField: 'ambientTemperature',        dataType: 'float',   unit: '°F',      sampleValue: '71.6',   guarantee: 'guaranteed' },
  { cmsField: 'cabinTemperature',          dataType: 'float',   unit: '°F',      sampleValue: '68.2',   guarantee: 'best-effort' },
  { cmsField: 'tirePressureFL',            dataType: 'float',   unit: 'psi',     sampleValue: '34.1',   guarantee: 'guaranteed' },
  { cmsField: 'tirePressureFR',            dataType: 'float',   unit: 'psi',     sampleValue: '34.2',   guarantee: 'guaranteed' },
  { cmsField: 'tirePressureRL',            dataType: 'float',   unit: 'psi',     sampleValue: '32.8',   guarantee: 'guaranteed' },
  { cmsField: 'tirePressureRR',            dataType: 'float',   unit: 'psi',     sampleValue: '32.9',   guarantee: 'guaranteed' },
  { cmsField: 'tireTemperatureFL',         dataType: 'float',   unit: '°F',      sampleValue: '92.4',   guarantee: 'best-effort' },
  { cmsField: 'tireTemperatureFR',         dataType: 'float',   unit: '°F',      sampleValue: '91.8',   guarantee: 'best-effort' },
  { cmsField: 'tireTemperatureRL',         dataType: 'float',   unit: '°F',      sampleValue: '96.2',   guarantee: 'best-effort' },
  { cmsField: 'tireTemperatureRR',         dataType: 'float',   unit: '°F',      sampleValue: '95.7',   guarantee: 'best-effort' },
  { cmsField: 'brakeFluidLevel',           dataType: 'float',   unit: '%',       sampleValue: '87.3',   guarantee: 'best-effort' },
  { cmsField: 'brakePadWearFront',         dataType: 'float',   unit: '%',       sampleValue: '52.1',   guarantee: 'best-effort' },
  { cmsField: 'brakePadWearRear',          dataType: 'float',   unit: '%',       sampleValue: '68.4',   guarantee: 'best-effort' },
  { cmsField: 'wiperState',                dataType: 'boolean', sampleValue: 'false',  guarantee: 'best-effort' },
  { cmsField: 'headlightState',            dataType: 'string',  sampleValue: 'low_beam', guarantee: 'guaranteed' },
  { cmsField: 'doorLockState',             dataType: 'string',  sampleValue: 'locked', guarantee: 'best-effort' },
  { cmsField: 'seatbeltDriver',            dataType: 'boolean', sampleValue: 'true',   guarantee: 'guaranteed' },
  { cmsField: 'seatbeltPassenger',         dataType: 'boolean', sampleValue: 'false',  guarantee: 'best-effort' },
  { cmsField: 'accelerationX',             dataType: 'float',   unit: 'g',       sampleValue: '0.04',   guarantee: 'best-effort' },
  { cmsField: 'accelerationY',             dataType: 'float',   unit: 'g',       sampleValue: '-0.02',  guarantee: 'best-effort' },
  { cmsField: 'accelerationZ',             dataType: 'float',   unit: 'g',       sampleValue: '1.01',   guarantee: 'best-effort' },
  { cmsField: 'yawRate',                   dataType: 'float',   unit: 'deg/s',   sampleValue: '0.8',    guarantee: 'best-effort' },
  { cmsField: 'steeringAngle',             dataType: 'float',   unit: '°',       sampleValue: '-2.4',   guarantee: 'best-effort' },
  { cmsField: 'throttlePosition',          dataType: 'float',   unit: '%',       sampleValue: '18.2',   guarantee: 'best-effort' },
  { cmsField: 'brakePressure',             dataType: 'float',   unit: 'psi',     sampleValue: '0.0',    guarantee: 'best-effort' },
  { cmsField: 'dtcActiveCount',            dataType: 'integer', sampleValue: '0',      guarantee: 'guaranteed' },
  { cmsField: 'chargingState',             dataType: 'string',  sampleValue: 'not_charging', guarantee: 'guaranteed' },
  { cmsField: 'chargingPower',             dataType: 'float',   unit: 'kW',      sampleValue: '0.0',    guarantee: 'best-effort' },
  { cmsField: 'estimatedRange',            dataType: 'float',   unit: 'mi',      sampleValue: '214.6',  guarantee: 'guaranteed' },
  { cmsField: 'energyConsumptionRate',     dataType: 'float',   unit: 'kWh/mi',  sampleValue: '0.31',   guarantee: 'best-effort' },
  { cmsField: 'motorRpm',                  dataType: 'integer', unit: 'rpm',     sampleValue: '2840',   guarantee: 'best-effort' },
  { cmsField: 'motorTorque',               dataType: 'float',   unit: 'Nm',      sampleValue: '124.6',  guarantee: 'best-effort' },
];

// ─── Sample enrolled vehicles ───────────────────────────────────────────────

const meridianPremiumVehicles: EnrolledVehicle[] = [
  { vehicleId: 'VEH-MRD-001', vin: '2M4RDE10000000001', model: 'AM-200',  feedHealth: 'streaming', lastPacketAt: '2s ago' },
  { vehicleId: 'VEH-MRD-002', vin: '2M4RDE10000000002', model: 'AM-200',  feedHealth: 'streaming', lastPacketAt: '3s ago' },
  { vehicleId: 'VEH-MRD-003', vin: '2M4RDE10000000003', model: 'AM-100',  feedHealth: 'streaming', lastPacketAt: '1s ago' },
  { vehicleId: 'VEH-MRD-004', vin: '2M4RDE10000000004', model: 'AM-200',  feedHealth: 'streaming', lastPacketAt: '4s ago' },
  { vehicleId: 'VEH-MRD-005', vin: '2M4RDE10000000005', model: 'AM-100',  feedHealth: 'delayed',   lastPacketAt: '2m ago' },
  { vehicleId: 'VEH-MRD-006', vin: '2M4RDE10000000006', model: 'AM-200',  feedHealth: 'streaming', lastPacketAt: '2s ago' },
  { vehicleId: 'VEH-MRD-007', vin: '2M4RDE10000000007', model: 'AM-200',  feedHealth: 'streaming', lastPacketAt: '3s ago' },
  { vehicleId: 'VEH-MRD-008', vin: '2M4RDE10000000008', model: 'AM-100',  feedHealth: 'streaming', lastPacketAt: '2s ago' },
];

const meridianStandardVehicles: EnrolledVehicle[] = [
  { vehicleId: 'VEH-MRS-001', vin: '2M4RSA10000000001', model: 'AM-100',  feedHealth: 'delayed',   lastPacketAt: '4m ago' },
  { vehicleId: 'VEH-MRS-002', vin: '2M4RSA10000000002', model: 'AM-100',  feedHealth: 'streaming', lastPacketAt: '8s ago' },
  { vehicleId: 'VEH-MRS-003', vin: '2M4RSA10000000003', model: 'AM-100',  feedHealth: 'streaming', lastPacketAt: '6s ago' },
];

const fordProVehicles: EnrolledVehicle[] = [
  { vehicleId: 'VEH-FRD-001', vin: '1FTFW1E82NKA00001', model: 'F-150 Lightning',  feedHealth: 'streaming', lastPacketAt: '3s ago' },
  { vehicleId: 'VEH-FRD-002', vin: '1FTFW1E82NKA00002', model: 'F-150 Lightning',  feedHealth: 'streaming', lastPacketAt: '2s ago' },
  { vehicleId: 'VEH-FRD-003', vin: '1FDUF5GT2LEC00003', model: 'F-350 Super Duty', feedHealth: 'streaming', lastPacketAt: '4s ago' },
  { vehicleId: 'VEH-FRD-004', vin: '1FDUF5GT2LEC00004', model: 'F-350 Super Duty', feedHealth: 'delayed',   lastPacketAt: '1m ago' },
  { vehicleId: 'VEH-FRD-005', vin: '1FDUF5GT2LEC00005', model: 'F-350 Super Duty', feedHealth: 'streaming', lastPacketAt: '5s ago' },
];

// ─── Ford Pro invoice history ─────────────────────────────────────────────
//
// Modelled after a real Ford Pro monthly invoice. Line-item shape:
//   SKU-00000246  Data Services - Premium - 5S              $7.00 × N     $N×7
//   SKU-00000246  Data Services - Premium - 5S - Prorated   ---    × N    $... (mid-month adds)
//   SKU-00000101  Fleet Platform Access - Monthly           $199.99 × 1   $199.99
//   SKU-00000512  Data Egress Overage                       $0.05 × N     $...
//   Sales Tax                                                             $...
//   ─── Total ─────────────────────────────────────────────────────────────
//
// Ford Pro Account Number A01020708 is a synthetic-but-realistic value —
// real ones follow the same A-prefix + 8-digit shape.

const FORD_INVOICES: Invoice[] = [
  {
    invoiceNumber: 'INV48564077',
    invoiceDate: '2026-07-31',
    billingPeriod: 'July 2026',
    accountNumber: 'A01020708',
    dueDate: '2026-08-30',
    status: 'unpaid',
    lineItems: [
      {
        sku: 'SKU-00000246',
        description: 'Data Services - Premium - 5S',
        unitCost: 7.00,
        qty: 35,
        amount: 245.00,
      },
      {
        sku: 'SKU-00000246',
        description: 'Data Services - Premium - 5S - Prorated',
        qty: 2,
        amount: 9.49,
      },
      {
        sku: 'SKU-00000101',
        description: 'Fleet Platform Access - Monthly',
        unitCost: 199.99,
        qty: 1,
        amount: 199.99,
      },
      {
        sku: 'SKU-00000512',
        description: 'Data Egress Overage',
        unitCost: 0.05,
        qty: 275,
        amount: 13.74,
      },
    ],
    subtotal: 468.22,
    salesTax: 49.40,
    totalDue: 517.62,
  },
  {
    invoiceNumber: 'INV48091334',
    invoiceDate: '2026-06-30',
    billingPeriod: 'June 2026',
    accountNumber: 'A01020708',
    dueDate: '2026-07-30',
    status: 'paid',
    lineItems: [
      {
        sku: 'SKU-00000246',
        description: 'Data Services - Premium - 5S',
        unitCost: 7.00,
        qty: 33,
        amount: 231.00,
      },
      {
        sku: 'SKU-00000246',
        description: 'Data Services - Premium - 5S - Prorated',
        qty: 2,
        amount: 8.71,
      },
      {
        sku: 'SKU-00000101',
        description: 'Fleet Platform Access - Monthly',
        unitCost: 199.99,
        qty: 1,
        amount: 199.99,
      },
      {
        sku: 'SKU-00000512',
        description: 'Data Egress Overage',
        unitCost: 0.05,
        qty: 218,
        amount: 10.90,
      },
    ],
    subtotal: 450.60,
    salesTax: 47.56,
    totalDue: 498.16,
  },
  {
    invoiceNumber: 'INV47623812',
    invoiceDate: '2026-05-31',
    billingPeriod: 'May 2026',
    accountNumber: 'A01020708',
    dueDate: '2026-06-30',
    status: 'paid',
    lineItems: [
      {
        sku: 'SKU-00000246',
        description: 'Data Services - Premium - 5S',
        unitCost: 7.00,
        qty: 31,
        amount: 217.00,
      },
      {
        sku: 'SKU-00000101',
        description: 'Fleet Platform Access - Monthly',
        unitCost: 199.99,
        qty: 1,
        amount: 199.99,
      },
      {
        sku: 'SKU-00000512',
        description: 'Data Egress Overage',
        unitCost: 0.05,
        qty: 189,
        amount: 9.45,
      },
    ],
    subtotal: 426.44,
    salesTax: 45.11,
    totalDue: 471.55,
  },
];

export const MOCK_SUBSCRIPTIONS: Subscription[] = [
  {
    id: 'sub_a1b2c3d4e5f6',
    productName: 'Meridian Premium Fleet Telemetry',
    producer: 'Meridian',
    tier: 'Premium',
    status: 'Active',
    feedHealth: 'streaming',
    lastPacketAt: '2s ago',
    p95LatencyMs: 340,
    errorRate24h: '0.02%',
    contractStart: '2026-01-15',
    nextRenewal: '2027-01-15',
    vehiclesCapacity: 100,
    entitledSignalCount: 50,
    totalOemSignals: 50,
    dataVolumeCycle: '1.24 TB',
    estimatedCostCycle: '$4,200',
    quotaHeadroomPct: 17,
    description: 'High-frequency fleet telemetry from Meridian production vehicles. Includes battery pack cell-level diagnostics, thermal management signals, and per-second telemetry cadence.',
    unavailableSignals: [],
    enrolledVehicles: meridianPremiumVehicles,
    availableSignals: ALL_SIGNALS,
  },
  {
    id: 'sub_x7y8z9a0b1c2',
    productName: 'Meridian Standard Fleet Telemetry',
    producer: 'Meridian',
    tier: 'Standard',
    status: 'Active',
    feedHealth: 'delayed',
    lastPacketAt: '4m ago',
    p95LatencyMs: 1240,
    errorRate24h: '0.18%',
    contractStart: '2026-03-01',
    nextRenewal: '2027-03-01',
    vehiclesCapacity: 50,
    entitledSignalCount: 28,
    totalOemSignals: 50,
    dataVolumeCycle: '382 GB',
    estimatedCostCycle: '$1,850',
    quotaHeadroomPct: 68,
    description: 'Standard fleet telemetry package. Core signals plus safety and vehicle health. Battery pack diagnostics available at Premium tier.',
    unavailableSignals: [
      'batteryPackTemperature',
      'batteryPackVoltage',
      'batteryPackCurrent',
      'cellVoltageStdDev',
      'thermalCompensationFactor',
      'thermalDerateActiveSeconds',
      'motorTorque',
      'yawRate',
    ],
    upgradeToTier: 'Premium',
    enrolledVehicles: meridianStandardVehicles,
    availableSignals: ALL_SIGNALS.slice(0, 28),
  },
  {
    id: 'sub_ford_pro_e4f5g6',
    productName: 'Ford Pro Fleet Telemetry',
    producer: 'Ford',
    tier: 'Premium',
    status: 'Active',
    feedHealth: 'streaming',
    lastPacketAt: '3s ago',
    p95LatencyMs: 280,
    errorRate24h: '0.01%',
    contractStart: '2026-02-01',
    nextRenewal: '2027-02-01',
    vehiclesCapacity: 25,
    entitledSignalCount: 50,
    totalOemSignals: 50,
    dataVolumeCycle: '842 GB',
    estimatedCostCycle: '$2,800',
    quotaHeadroomPct: 42,
    description: 'High-frequency fleet telemetry from Ford Pro commercial vehicles. Cell-level battery diagnostics for electric models (F-150 Lightning), engine and drivetrain signals for internal-combustion models (F-350 Super Duty), per-second cadence across the mixed fleet.',
    unavailableSignals: [],
    enrolledVehicles: fordProVehicles,
    availableSignals: ALL_SIGNALS,
    accountNumber: 'A01020708',
    invoices: FORD_INVOICES,
  },
  {
    id: 'sub_p4q5r6s7t8u9',
    productName: 'Tesla Fleet Telemetry',
    producer: 'Tesla',
    tier: 'Basic',
    status: 'Paused',
    feedHealth: 'paused',
    lastPacketAt: '3d ago',
    p95LatencyMs: 0,
    errorRate24h: '—',
    contractStart: '2025-11-01',
    nextRenewal: '2026-11-01',
    vehiclesCapacity: 25,
    entitledSignalCount: 12,
    totalOemSignals: 50,
    dataVolumeCycle: '—',
    estimatedCostCycle: '$400',
    quotaHeadroomPct: 100,
    description: "Basic-tier fleet telemetry via Tesla's public Fleet API. Location, speed, shift state, and odometer only. Subscription is currently paused — no data is being received.",
    unavailableSignals: [
      'batteryStateOfCharge',
      'batteryPackTemperature',
      'tirePressureFL',
      'tirePressureFR',
      'tirePressureRL',
      'tirePressureRR',
      'dtcActiveCount',
      'brakePadWearFront',
      'brakePadWearRear',
      'chargingPower',
      'motorRpm',
      'motorTorque',
    ],
    upgradeToTier: 'Standard',
    enrolledVehicles: [],
    availableSignals: ALL_SIGNALS.slice(0, 12),
  },
];

/** Convenience lookup by id, for the detail route. Searches both the
 *  built-in mock subscriptions AND any subscriptions added during this
 *  session via the Add Subscription modal (see `userAddedSubs` below). */
export function getSubscriptionById(id: string): Subscription | undefined {
  return getAllSubscriptions().find((s) => s.id === id);
}

// ─── Session-scoped 'user-added' subscriptions ──────────────────────────────
//
// The Add Subscription modal is a stub — no backend, no persistence. Newly
// added subscriptions live in this module-scope array for the tab's lifetime
// and disappear on refresh. This is deliberate for a demo: it lets the user
// walk through the create flow and see the new subscription appear in the
// list + be clickable through to a detail page, without touching a real API.
// Once a real subscriptions API lands, `userAddedSubs` and its helpers get
// deleted and the list page calls the API directly.

let userAddedSubs: Subscription[] = [];

/** Prepend a session-added subscription to the display list. */
export function addSubscription(sub: Subscription): void {
  userAddedSubs = [sub, ...userAddedSubs];
}

/** All subscriptions currently visible in the UI: session-added first,
 *  then the built-in mock set. */
export function getAllSubscriptions(): Subscription[] {
  return [...userAddedSubs, ...MOCK_SUBSCRIPTIONS];
}

// ─── Vehicle enrollment (mock backing store) ───────────────────────────────
//
// A subscription's `enrolledVehicles` is what the SubscriptionDetailPage
// renders. To let the user actually enroll new vehicles from the UI, we
// keep an in-memory overlay of enrolled VINs per subscription id. Reads
// stack the overlay on top of the seed data so the built-in subscriptions
// keep their pre-baked vehicles AND get the newly-enrolled ones.
//
// Session-scoped, like the subscription overlay. Refresh loses enrollments.

const enrollmentOverlay: Record<string, EnrolledVehicle[]> = {};

/** Merge the overlay into a subscription's enrolled-vehicles list. */
export function getEnrolledVehiclesForSub(subId: string): EnrolledVehicle[] {
  const sub = getSubscriptionById(subId);
  const seed = sub?.enrolledVehicles ?? [];
  const added = enrollmentOverlay[subId] ?? [];
  return [...seed, ...added];
}

/** Enroll one or more vehicles in a subscription. Idempotent by VIN — a
 *  VIN already present (in seed or overlay) is silently skipped. */
export function enrollVehicles(subId: string, vehicles: EnrolledVehicle[]): number {
  const existing = new Set(getEnrolledVehiclesForSub(subId).map((v) => v.vin));
  const fresh = vehicles.filter((v) => !existing.has(v.vin));
  if (!fresh.length) return 0;
  enrollmentOverlay[subId] = [...(enrollmentOverlay[subId] ?? []), ...fresh];
  return fresh.length;
}

// ─── Vehicle pools — for the Enroll-vehicles modal ─────────────────────────
//
// Two sources of vehicles feed the enrollment flow, matching the two paths
// the operator can take:
//
//   1. `getFleetVehiclePool()` — vehicles the operator already has in CMS
//      (would come from /api/v1/vehicles in a real deploy). Pick a subset
//      to enroll into a subscription.
//
//   2. `getProducerVehicleOffer(productId)` — VINs the PRODUCER can send
//      data for. Any of these NOT already in CMS's fleet get created +
//      enrolled in one shot ("auto-import from producer").
//
// The two lists overlap for realistic-feeling demos: some producer VINs
// already exist in the CMS fleet (they'd show as "already enrolled" or
// "already in fleet"), some are new (import to add + enroll).

/** A vehicle the operator has in CMS's own fleet inventory. */
export interface AvailableFleetVehicle {
  vehicleId: string;
  vin: string;
  model: string;
  fleetName: string;
  /** Compatible producers this vehicle can receive data from. Filled by
   *  matching the model prefix against the producer's model family — in a
   *  real deploy this would come from the producer's vehicle-eligibility
   *  API. */
  compatibleProducers: string[];
}

const CMS_FLEET_POOL: AvailableFleetVehicle[] = [
  // Meridian fleet — the CMS operator has 8 Meridian AM-200s
  { vehicleId: 'VEH-CMS-1001', vin: '2M4RDE10000000101', model: 'AM-200', fleetName: 'North Delivery', compatibleProducers: ['Meridian'] },
  { vehicleId: 'VEH-CMS-1002', vin: '2M4RDE10000000102', model: 'AM-200', fleetName: 'North Delivery', compatibleProducers: ['Meridian'] },
  { vehicleId: 'VEH-CMS-1003', vin: '2M4RDE10000000103', model: 'AM-200', fleetName: 'North Delivery', compatibleProducers: ['Meridian'] },
  { vehicleId: 'VEH-CMS-1004', vin: '2M4RDE10000000104', model: 'AM-200', fleetName: 'South Delivery', compatibleProducers: ['Meridian'] },
  { vehicleId: 'VEH-CMS-1005', vin: '2M4RDE10000000105', model: 'AM-200', fleetName: 'South Delivery', compatibleProducers: ['Meridian'] },
  { vehicleId: 'VEH-CMS-1006', vin: '2M4RDE10000000106', model: 'AM-300', fleetName: 'Long Haul', compatibleProducers: ['Meridian'] },
  { vehicleId: 'VEH-CMS-1007', vin: '2M4RDE10000000107', model: 'AM-300', fleetName: 'Long Haul', compatibleProducers: ['Meridian'] },
  { vehicleId: 'VEH-CMS-1008', vin: '2M4RDE10000000108', model: 'AM-300', fleetName: 'Long Haul', compatibleProducers: ['Meridian'] },
  // Ford fleet — 6 F-150 Lightnings
  { vehicleId: 'VEH-CMS-2001', vin: '1FTFW1RG7NF100201', model: 'F-150 Lightning', fleetName: 'North Delivery', compatibleProducers: ['Ford'] },
  { vehicleId: 'VEH-CMS-2002', vin: '1FTFW1RG7NF100202', model: 'F-150 Lightning', fleetName: 'North Delivery', compatibleProducers: ['Ford'] },
  { vehicleId: 'VEH-CMS-2003', vin: '1FTFW1RG7NF100203', model: 'F-150 Lightning', fleetName: 'South Delivery', compatibleProducers: ['Ford'] },
  { vehicleId: 'VEH-CMS-2004', vin: '1FTFW1RG7NF100204', model: 'E-Transit',        fleetName: 'South Delivery', compatibleProducers: ['Ford'] },
  { vehicleId: 'VEH-CMS-2005', vin: '1FTFW1RG7NF100205', model: 'E-Transit',        fleetName: 'South Delivery', compatibleProducers: ['Ford'] },
  { vehicleId: 'VEH-CMS-2006', vin: '1FTFW1RG7NF100206', model: 'E-Transit',        fleetName: 'Yard Ops',       compatibleProducers: ['Ford'] },
  // Tesla fleet — 4 mixed Model 3 / Model Y
  { vehicleId: 'VEH-CMS-3001', vin: '5YJ3E1EA0PF400301', model: 'Model 3',        fleetName: 'Yard Ops',       compatibleProducers: ['Tesla'] },
  { vehicleId: 'VEH-CMS-3002', vin: '5YJ3E1EA0PF400302', model: 'Model 3',        fleetName: 'Yard Ops',       compatibleProducers: ['Tesla'] },
  { vehicleId: 'VEH-CMS-3003', vin: '5YJYGDEE0PF400303', model: 'Model Y',        fleetName: 'Yard Ops',       compatibleProducers: ['Tesla'] },
  { vehicleId: 'VEH-CMS-3004', vin: '5YJYGDEE0PF400304', model: 'Model Y',        fleetName: 'Yard Ops',       compatibleProducers: ['Tesla'] },
  // Synthetic 'Tesla Fleet' — matches the virtual FleetItem injected by
  // useDataProductFleets so the enrollment scope filter has one vehicle
  // to attach to when the fleet is picked.
  { vehicleId: 'VEH-TES-0001', vin: '5YJ3E1EA0PF450001', model: 'Model 3',        fleetName: 'Tesla Fleet',    compatibleProducers: ['Tesla'] },
];

/** Vehicles the operator currently has in CMS. */
export function getFleetVehiclePool(): AvailableFleetVehicle[] {
  return CMS_FLEET_POOL;
}

/** Vehicles from the CMS fleet that a given producer can send data for. */
export function getFleetVehiclesEligibleFor(producer: string): AvailableFleetVehicle[] {
  return CMS_FLEET_POOL.filter((v) => v.compatibleProducers.includes(producer));
}

// ─── Fleet ↔ Data product assignment (1:N) ─────────────────────────────────
//
// Fleets themselves come from the real /api/v1/fleets endpoint (see
// useFleetsForAssignment hook). This module owns ONLY the assignment
// overlay — a session-scoped map from a real fleet's id to the data
// products the operator has linked to it. Refresh loses assignments,
// consistent with the rest of the demo's in-memory backing store.
//
// The full mental model:
//
//   Fleet ──1:N──> DataProduct ──1:N──> Subscription (tier/plan)
//   Fleet ──1:N──> Vehicle    (each vehicle is on-board OR off-board)
//   Off-board Vehicle ──1:1──> Subscription

/** Session-scoped overlay: real fleet id → data product ids the operator
 *  has assigned to that fleet. Exported for the hook layer; do NOT mutate
 *  directly, use the assign/unassign helpers. */
const fleetAssignments: Record<string, string[]> = {};

/** Data product ids currently assigned to the given fleet. Never returns
 *  undefined — an unassigned fleet returns an empty array. */
export function getAssignedProductIdsForFleet(fleetId: string): string[] {
  return fleetAssignments[fleetId] ?? [];
}

/** Assign a data product to a fleet. Idempotent — a duplicate assignment
 *  is a no-op. Returns true if the map actually gained an entry. */
export function assignDataProductToFleet(fleetId: string, productId: string): boolean {
  const existing = fleetAssignments[fleetId] ?? [];
  if (existing.includes(productId)) return false;
  fleetAssignments[fleetId] = [...existing, productId];
  return true;
}

/** Remove a data product assignment from a fleet. Returns true if the
 *  assignment was actually present. */
export function unassignDataProductFromFleet(fleetId: string, productId: string): boolean {
  const existing = fleetAssignments[fleetId] ?? [];
  if (!existing.includes(productId)) return false;
  const next = existing.filter((id) => id !== productId);
  if (next.length === 0) delete fleetAssignments[fleetId];
  else fleetAssignments[fleetId] = next;
  return true;
}

/** Whether the given fleet has been assigned to the given data product. */
export function isFleetAssignedToProduct(fleetId: string, productId: string): boolean {
  return (fleetAssignments[fleetId] ?? []).includes(productId);
}

/** Whether the given fleet has any assignment to a data product whose
 *  producer matches. Cross-references the catalog to resolve producer from
 *  productId. */
export function isFleetAssignedToProducer(fleetId: string, producer: string): boolean {
  const productIds = fleetAssignments[fleetId] ?? [];
  if (productIds.length === 0) return false;
  const producerProductIds = new Set(
    getAllCatalog().filter((p) => p.producer === producer).map((p) => p.productId),
  );
  return productIds.some((id) => producerProductIds.has(id));
}

/** Subscriptions the operator holds against a specific data product. Used
 *  by the enrollment flow to show tier options when a vehicle is added to a
 *  fleet that's assigned to a data product with multiple subscriptions. */
export function getSubscriptionsForDataProduct(productId: string): Subscription[] {
  // Subscriptions today don't carry productId directly — they carry
  // productName + producer. Match by producer name derived from the catalog
  // entry. Once the schema grows a productId FK, this becomes trivial.
  const product = getDataProductById(productId);
  if (!product) return [];
  return getAllSubscriptions().filter((s) => s.producer === product.producer);
}


/** A VIN the producer says it can deliver data for. Mimics what a
 *  producer's vehicle-eligibility endpoint would return: the VINs
 *  registered on the producer's side, whether or not CMS knows about
 *  them. */
export interface ProducerVehicleOffer {
  vin: string;
  model: string;
  /** Whether CMS's fleet inventory already contains this VIN. Vehicles
   *  the producer offers that CMS DOESN'T have yet are the "auto-import
   *  from producer" candidates. */
  alreadyInCmsFleet: boolean;
}

/**
 * The producer's advertised list of VINs it can deliver data for. Mock:
 * for each producer, we return the CMS-side compatible pool marked
 * `alreadyInCmsFleet: true` plus additional producer-only VINs marked
 * false — those are the auto-import candidates.
 */
export function getProducerVehicleOffer(producer: string): ProducerVehicleOffer[] {
  const cmsVins = new Set(CMS_FLEET_POOL.map((v) => v.vin));
  const cmsOffered: ProducerVehicleOffer[] = getFleetVehiclesEligibleFor(producer).map((v) => ({
    vin: v.vin,
    model: v.model,
    alreadyInCmsFleet: true,
  }));

  // Producer-only VINs — not yet in CMS. Deterministic by producer name so
  // the demo is repeatable.
  let extras: ProducerVehicleOffer[] = [];
  if (producer === 'Meridian') {
    extras = [
      { vin: '2M4RDE10000000201', model: 'AM-200', alreadyInCmsFleet: false },
      { vin: '2M4RDE10000000202', model: 'AM-200', alreadyInCmsFleet: false },
      { vin: '2M4RDE10000000203', model: 'AM-200', alreadyInCmsFleet: false },
      { vin: '2M4RDE10000000204', model: 'AM-300', alreadyInCmsFleet: false },
      { vin: '2M4RDE10000000205', model: 'AM-300', alreadyInCmsFleet: false },
      { vin: '2M4RDE10000000206', model: 'AM-300', alreadyInCmsFleet: false },
      { vin: '2M4RDE10000000207', model: 'AM-500', alreadyInCmsFleet: false },
      { vin: '2M4RDE10000000208', model: 'AM-500', alreadyInCmsFleet: false },
    ];
  } else if (producer === 'Ford') {
    extras = [
      { vin: '1FTFW1RG7NF100301', model: 'F-150 Lightning', alreadyInCmsFleet: false },
      { vin: '1FTFW1RG7NF100302', model: 'F-150 Lightning', alreadyInCmsFleet: false },
      { vin: '1FTFW1RG7NF100303', model: 'E-Transit',       alreadyInCmsFleet: false },
      { vin: '1FTFW1RG7NF100304', model: 'E-Transit',       alreadyInCmsFleet: false },
      { vin: '1FTFW1RG7NF100305', model: 'F-150 Lightning', alreadyInCmsFleet: false },
      { vin: '1FTFW1RG7NF100306', model: 'F-150 Lightning', alreadyInCmsFleet: false },
    ];
  } else if (producer === 'Tesla') {
    extras = [
      { vin: '5YJ3E1EA0PF400401', model: 'Model 3', alreadyInCmsFleet: false },
      { vin: '5YJ3E1EA0PF400402', model: 'Model 3', alreadyInCmsFleet: false },
      { vin: '5YJYGDEE0PF400403', model: 'Model Y', alreadyInCmsFleet: false },
      { vin: '5YJYGDEE0PF400404', model: 'Model Y', alreadyInCmsFleet: false },
    ];
  }
  return [...cmsOffered, ...extras].filter((o) => o.alreadyInCmsFleet || !cmsVins.has(o.vin));
}

/**
 * "Auto-import" — a producer VIN gets added to CMS's fleet AND enrolled
 * in the subscription in one shot. In real CMS this would POST to
 * /api/v1/vehicles then /subscriptions/:id/enroll; here we just mutate
 * the in-memory pool and enrollment overlay.
 *
 * If `targetFleetName` is provided, imported VINs are attached to that
 * fleet (matches the enroll modal's "Scope by fleet" selection). If
 * omitted, imports land in a generic "<producer> — imported" bucket
 * for backwards compatibility with earlier demos.
 */
export function importAndEnrollFromProducer(
  subId: string,
  producer: string,
  offers: ProducerVehicleOffer[],
  targetFleetName?: string,
): number {
  const fleetName = targetFleetName ?? `${producer} — imported`;
  // Grow the fleet pool with each imported VIN so subsequent Enroll-modal
  // opens show them under the "From my fleet" tab too.
  const newFleetIds: EnrolledVehicle[] = [];
  offers
    .filter((o) => !o.alreadyInCmsFleet)
    .forEach((o) => {
      const vehicleId = `VEH-IMP-${o.vin.slice(-6)}`;
      CMS_FLEET_POOL.push({
        vehicleId,
        vin: o.vin,
        model: o.model,
        fleetName,
        compatibleProducers: [producer],
      });
      newFleetIds.push({
        vehicleId,
        vin: o.vin,
        model: o.model,
        feedHealth: 'streaming',
        lastPacketAt: 'just now',
      });
    });
  // Include the offers that were ALREADY in the CMS fleet too — treat the
  // producer's whole selected list as enrollments.
  offers
    .filter((o) => o.alreadyInCmsFleet)
    .forEach((o) => {
      const existing = CMS_FLEET_POOL.find((v) => v.vin === o.vin);
      if (existing) {
        newFleetIds.push({
          vehicleId: existing.vehicleId,
          vin: existing.vin,
          model: existing.model,
          feedHealth: 'streaming',
          lastPacketAt: 'just now',
        });
      }
    });
  return enrollVehicles(subId, newFleetIds);
}

// ─── Data Products Catalog ──────────────────────────────────────────────────
//
// CMS is a third-party fleet platform. It is NOT aware of a Connected
// Services portal. So the fleet operator must define, in CMS, every data
// product they want to consume — producer identity, connection endpoint,
// authentication, tier offering, credentials pointer. That definition
// lives in this catalog.
//
// Add-Subscription reads from this catalog. Adding a subscription creates a
// consumer relationship against a catalog entry at a specific tier.
//
// Like the subscriptions store, this is stub-in-memory for now: built-in
// entries are hard-coded; user-added entries via the Wizard live in a
// session-scoped array. A real backend replaces both once wired.

export type ConnectionType = 'rest_polling' | 'grpc_streaming' | 'kafka' | 'websocket_inbound';
export type AuthType = 'oauth2' | 'api_key' | 'mtls' | 'sasl_scram';
export type CatalogStatus = 'Active' | 'Draft' | 'Deprecated';

export interface DataProduct {
  productId: string;
  productName: string;
  producer: string;
  description: string;
  connectionType: ConnectionType;
  /** Base endpoint / bootstrap servers / gRPC target — meaning varies by connectionType. */
  endpointUrl: string;
  authType: AuthType;
  /** OAuth2 only. */
  tokenEndpoint?: string;
  credentialsSecretArn: string;
  /** Optional. Seed products declare tier offerings; wizard-added products
   *  omit this and the Subscription flow falls back to a single "Default"
   *  tier. Not every producer thinks in tiers, so this is not required. */
  supportedTiers?: Tier[];
  /** Optional. Derived from signalMappings.length for wizard-added products;
   *  seed products declare a headline count that may exceed the number of
   *  mappings actually surfaced in the catalog. */
  totalSignals?: number;
  status: CatalogStatus;
  createdAt: string;

  // ─── Connection-type-specific parameters ───────────────────────────
  /** rest_polling: how often to poll (seconds). */
  pollingIntervalSeconds?: number;
  /** grpc_streaming: fully-qualified gRPC service name (com.oem.fleet.v1.TelemetryService). */
  grpcServiceName?: string;
  /** grpc_streaming: server-streaming RPC method name. */
  grpcMethodName?: string;
  /** kafka: topic name to consume from. */
  kafkaTopic?: string;
  /** kafka: consumer group id (CMS-side identity for offset tracking). */
  kafkaConsumerGroup?: string;
  /** websocket_inbound: URL the producer connects TO (owned by CMS). */
  wsListenEndpoint?: string;
  /** websocket_inbound: expected origin header / SNI value from the producer. */
  wsAllowedOrigin?: string;

  // ─── Auth-type-specific parameters ─────────────────────────────────
  /** oauth2: space-separated scopes requested with the token. */
  oauthScopes?: string;
  /** api_key: HTTP header name that carries the key. */
  apiKeyHeaderName?: string;
  /** sasl_scram: which SCRAM mechanism to use. */
  scramMechanism?: 'SCRAM-SHA-256' | 'SCRAM-SHA-512';

  // ─── Transform manifest — how OEM signals map to CMS canonical fields ─
  /** Signal-by-signal transform rules. Producers publish signals under their
   *  own names/paths; this mapping normalises them into CMS's canonical
   *  Signal Catalog. Optional — a data product may be created with 0 mappings
   *  and have them added later from the catalog detail page. */
  signalMappings?: SignalMapping[];

  /** Event-by-event transform rules. Producers emit events under their own
   *  names; this mapping normalises them into CMS's canonical event catalog
   *  (dtc_active, hard_braking, oil_change_due, etc.). Optional. */
  eventMappings?: EventMapping[];
}

// ─── Signal mapping generators (producer-flavoured) ───────────────────────
//
// Real transform manifests carry producer-specific source paths and signal
// names. Each producer uses their own schema (camelCase nested for Meridian's
// Kafka payloads, snake_case protobuf field paths for Ford's gRPC stream,
// nested state-family paths for Tesla's Fleet API). This lets the
// CatalogDetailPage's Signal-Mapping table demonstrate WHY a transform
// manifest is needed — three producers, three source schemas, one CMS
// canonical target.

const MERIDIAN_MAPPINGS: SignalMapping[] = [
  { sourceSignal: 'speedMph',              sourcePath: 'telemetry.vehicle.speedMph',           cmsField: 'vehicleSpeed',              dataType: 'float',   unit: 'mph',  required: true },
  { sourceSignal: 'odometerMi',            sourcePath: 'telemetry.vehicle.odometerMi',         cmsField: 'odometer',                  dataType: 'float',   unit: 'mi',   required: true },
  { sourceSignal: 'engineRunningState',    sourcePath: 'telemetry.powertrain.engineOn',        cmsField: 'engineRunningState',        dataType: 'boolean', required: true },
  { sourceSignal: 'ignitionState',         sourcePath: 'telemetry.powertrain.ignition',        cmsField: 'ignitionState',             dataType: 'string',  required: true },
  { sourceSignal: 'gpsLatDeg',             sourcePath: 'telemetry.location.latDeg',            cmsField: 'latitude',                  dataType: 'float',   unit: '°',    required: true },
  { sourceSignal: 'gpsLonDeg',             sourcePath: 'telemetry.location.lonDeg',            cmsField: 'longitude',                 dataType: 'float',   unit: '°',    required: true },
  { sourceSignal: 'gpsHeadingDeg',         sourcePath: 'telemetry.location.headingDeg',        cmsField: 'heading',                   dataType: 'float',   unit: '°' },
  { sourceSignal: 'gearPosition',          sourcePath: 'telemetry.powertrain.gearPos',         cmsField: 'gearPosition',              dataType: 'integer' },
  { sourceSignal: 'stateOfCharge',         sourcePath: 'telemetry.battery.socPct',             cmsField: 'batteryStateOfCharge',      dataType: 'float',   unit: '%',    required: true },
  { sourceSignal: 'stateOfHealth',         sourcePath: 'telemetry.battery.sohPct',             cmsField: 'batteryStateOfHealth',      dataType: 'float',   unit: '%' },
  { sourceSignal: 'packVoltage',           sourcePath: 'telemetry.battery.packVoltageV',       cmsField: 'batteryPackVoltage',        dataType: 'float',   unit: 'V' },
  { sourceSignal: 'packCurrent',           sourcePath: 'telemetry.battery.packCurrentA',       cmsField: 'batteryPackCurrent',        dataType: 'float',   unit: 'A' },
  { sourceSignal: 'packTempC',             sourcePath: 'telemetry.battery.packTempC',          cmsField: 'batteryPackTemperature',    dataType: 'float',   unit: '°F',   unitConversion: 'C_to_F' },
  { sourceSignal: 'cellVoltageStdDevMv',   sourcePath: 'telemetry.battery.cellVoltageStdDevMv', cmsField: 'cellVoltageStdDev',        dataType: 'float',   unit: 'mV' },
  { sourceSignal: 'thermalCompensation',   sourcePath: 'telemetry.thermal.compensationFactor', cmsField: 'thermalCompensationFactor', dataType: 'float' },
  { sourceSignal: 'thermalDerateSec',      sourcePath: 'telemetry.thermal.derateActiveSec',    cmsField: 'thermalDerateActiveSeconds', dataType: 'integer', unit: 's' },
  { sourceSignal: 'ambientTempC',          sourcePath: 'telemetry.environment.ambientTempC',   cmsField: 'ambientTemperature',        dataType: 'float',   unit: '°F',   unitConversion: 'C_to_F' },
  { sourceSignal: 'tirePressureFlKpa',     sourcePath: 'telemetry.tires.fl.pressureKpa',       cmsField: 'tirePressureFL',            dataType: 'float',   unit: 'psi',  unitConversion: 'kpa_to_psi' },
  { sourceSignal: 'tirePressureFrKpa',     sourcePath: 'telemetry.tires.fr.pressureKpa',       cmsField: 'tirePressureFR',            dataType: 'float',   unit: 'psi',  unitConversion: 'kpa_to_psi' },
  { sourceSignal: 'tirePressureRlKpa',     sourcePath: 'telemetry.tires.rl.pressureKpa',       cmsField: 'tirePressureRL',            dataType: 'float',   unit: 'psi',  unitConversion: 'kpa_to_psi' },
  { sourceSignal: 'tirePressureRrKpa',     sourcePath: 'telemetry.tires.rr.pressureKpa',       cmsField: 'tirePressureRR',            dataType: 'float',   unit: 'psi',  unitConversion: 'kpa_to_psi' },
  { sourceSignal: 'chargingState',         sourcePath: 'telemetry.charging.state',             cmsField: 'chargingState',             dataType: 'string' },
  { sourceSignal: 'chargingPowerKw',       sourcePath: 'telemetry.charging.powerKw',           cmsField: 'chargingPower',             dataType: 'float',   unit: 'kW' },
  { sourceSignal: 'rangeEstimateMi',       sourcePath: 'telemetry.range.estimateMi',           cmsField: 'estimatedRange',            dataType: 'float',   unit: 'mi',   required: true },
  { sourceSignal: 'motorRpm',              sourcePath: 'telemetry.motor.rpm',                  cmsField: 'motorRpm',                  dataType: 'integer', unit: 'rpm' },
];

const FORD_MAPPINGS: SignalMapping[] = [
  { sourceSignal: 'vehicle_speed_mph',     sourcePath: 'vehicle_data.speed_mph',               cmsField: 'vehicleSpeed',              dataType: 'float',   unit: 'mph',  required: true },
  { sourceSignal: 'odometer_miles',        sourcePath: 'vehicle_data.odometer_miles',          cmsField: 'odometer',                  dataType: 'float',   unit: 'mi',   required: true },
  { sourceSignal: 'engine_running',        sourcePath: 'vehicle_data.engine_running',          cmsField: 'engineRunningState',        dataType: 'boolean', required: true },
  { sourceSignal: 'ignition_status',       sourcePath: 'vehicle_data.ignition',                cmsField: 'ignitionState',             dataType: 'string',  required: true },
  { sourceSignal: 'gps_lat',               sourcePath: 'gps.latitude',                         cmsField: 'latitude',                  dataType: 'float',   unit: '°',    required: true },
  { sourceSignal: 'gps_lon',               sourcePath: 'gps.longitude',                        cmsField: 'longitude',                 dataType: 'float',   unit: '°',    required: true },
  { sourceSignal: 'gps_heading',           sourcePath: 'gps.heading',                          cmsField: 'heading',                   dataType: 'float',   unit: '°' },
  { sourceSignal: 'transmission_gear',     sourcePath: 'powertrain.transmission_gear',         cmsField: 'gearPosition',              dataType: 'integer' },
  { sourceSignal: 'fuel_level_percent',    sourcePath: 'powertrain.fuel_level_percent',        cmsField: 'fuelLevel',                 dataType: 'float',   unit: '%' },
  { sourceSignal: 'hv_battery_soc',        sourcePath: 'hv_battery.state_of_charge',           cmsField: 'batteryStateOfCharge',      dataType: 'float',   unit: '%' },
  { sourceSignal: 'hv_battery_soh',        sourcePath: 'hv_battery.state_of_health',           cmsField: 'batteryStateOfHealth',      dataType: 'float',   unit: '%' },
  { sourceSignal: 'hv_battery_temp_f',     sourcePath: 'hv_battery.temperature_f',             cmsField: 'batteryPackTemperature',    dataType: 'float',   unit: '°F' },
  { sourceSignal: 'coolant_temp_f',        sourcePath: 'powertrain.coolant_temperature_f',     cmsField: 'coolantTemperature',        dataType: 'float',   unit: '°F' },
  { sourceSignal: 'ambient_temp_f',        sourcePath: 'environment.ambient_temperature_f',    cmsField: 'ambientTemperature',        dataType: 'float',   unit: '°F' },
  { sourceSignal: 'tire_pressure_fl_psi',  sourcePath: 'tires.front_left.pressure_psi',        cmsField: 'tirePressureFL',            dataType: 'float',   unit: 'psi' },
  { sourceSignal: 'tire_pressure_fr_psi',  sourcePath: 'tires.front_right.pressure_psi',       cmsField: 'tirePressureFR',            dataType: 'float',   unit: 'psi' },
  { sourceSignal: 'tire_pressure_rl_psi',  sourcePath: 'tires.rear_left.pressure_psi',         cmsField: 'tirePressureRL',            dataType: 'float',   unit: 'psi' },
  { sourceSignal: 'tire_pressure_rr_psi',  sourcePath: 'tires.rear_right.pressure_psi',        cmsField: 'tirePressureRR',            dataType: 'float',   unit: 'psi' },
  { sourceSignal: 'seatbelt_driver',       sourcePath: 'safety.seatbelt.driver',               cmsField: 'seatbeltDriver',            dataType: 'boolean' },
  { sourceSignal: 'seatbelt_passenger',    sourcePath: 'safety.seatbelt.passenger',            cmsField: 'seatbeltPassenger',         dataType: 'boolean' },
  { sourceSignal: 'headlight_state',       sourcePath: 'lighting.headlight_state',             cmsField: 'headlightState',            dataType: 'string' },
  { sourceSignal: 'door_lock_state',       sourcePath: 'security.door_lock_state',             cmsField: 'doorLockState',             dataType: 'string' },
  { sourceSignal: 'dtc_active_count',      sourcePath: 'diagnostics.dtc_active_count',         cmsField: 'dtcActiveCount',            dataType: 'integer', required: true },
  { sourceSignal: 'charging_state',        sourcePath: 'charging.state',                       cmsField: 'chargingState',             dataType: 'string' },
  { sourceSignal: 'estimated_range_mi',    sourcePath: 'vehicle_data.estimated_range_miles',   cmsField: 'estimatedRange',            dataType: 'float',   unit: 'mi',   required: true },
];

const TESLA_MAPPINGS: SignalMapping[] = [
  { sourceSignal: 'speed',                 sourcePath: 'drive_state.speed',                    cmsField: 'vehicleSpeed',              dataType: 'float',   unit: 'mph',  required: true },
  { sourceSignal: 'odometer',              sourcePath: 'vehicle_state.odometer',               cmsField: 'odometer',                  dataType: 'float',   unit: 'mi',   required: true },
  { sourceSignal: 'shift_state',           sourcePath: 'drive_state.shift_state',              cmsField: 'gearPosition',              dataType: 'string' },
  { sourceSignal: 'latitude',              sourcePath: 'drive_state.latitude',                 cmsField: 'latitude',                  dataType: 'float',   unit: '°',    required: true },
  { sourceSignal: 'longitude',             sourcePath: 'drive_state.longitude',                cmsField: 'longitude',                 dataType: 'float',   unit: '°',    required: true },
  { sourceSignal: 'heading',               sourcePath: 'drive_state.heading',                  cmsField: 'heading',                   dataType: 'float',   unit: '°' },
  { sourceSignal: 'battery_level',         sourcePath: 'charge_state.battery_level',           cmsField: 'batteryStateOfCharge',      dataType: 'float',   unit: '%' },
  { sourceSignal: 'battery_range',         sourcePath: 'charge_state.battery_range',           cmsField: 'estimatedRange',            dataType: 'float',   unit: 'mi' },
  { sourceSignal: 'charging_state',        sourcePath: 'charge_state.charging_state',          cmsField: 'chargingState',             dataType: 'string' },
  { sourceSignal: 'tpms_pressure_fl',      sourcePath: 'vehicle_state.tpms_pressure_fl',       cmsField: 'tirePressureFL',            dataType: 'float',   unit: 'psi', unitConversion: 'bar_to_psi' },
  { sourceSignal: 'tpms_pressure_fr',      sourcePath: 'vehicle_state.tpms_pressure_fr',       cmsField: 'tirePressureFR',            dataType: 'float',   unit: 'psi', unitConversion: 'bar_to_psi' },
  { sourceSignal: 'tpms_pressure_rl',      sourcePath: 'vehicle_state.tpms_pressure_rl',       cmsField: 'tirePressureRL',            dataType: 'float',   unit: 'psi', unitConversion: 'bar_to_psi' },
  { sourceSignal: 'tpms_pressure_rr',      sourcePath: 'vehicle_state.tpms_pressure_rr',       cmsField: 'tirePressureRR',            dataType: 'float',   unit: 'psi', unitConversion: 'bar_to_psi' },
];

export const CATALOG_PRODUCTS: DataProduct[] = [
  {
    productId: 'prd_meridian_fleet',
    productName: 'Meridian Fleet Telemetry',
    producer: 'Meridian',
    description: 'Kafka-delivered fleet telemetry from Meridian production vehicles. Subscribers consume from the fleet-telemetry-v1 topic on the Meridian Kafka cluster. Battery pack cell diagnostics, thermal management, per-second cadence. Available at Basic, Standard, and Premium tiers.',
    connectionType: 'kafka',
    // Kafka bootstrap servers — comma-separated broker addresses. The topic
    // is named separately in the client config (fleet-telemetry-v1).
    endpointUrl: 'broker1.meridian.example:9093,broker2.meridian.example:9093,broker3.meridian.example:9093',
    authType: 'sasl_scram',
    // No token endpoint — SASL/SCRAM is direct-authenticated per-connection.
    credentialsSecretArn: 'arn:aws:secretsmanager:us-west-2:123456789012:secret/meridian-kafka-scram-Xy8T2b',
    supportedTiers: ['Basic', 'Standard', 'Premium'],
    totalSignals: 50,
    status: 'Active',
    createdAt: '2026-01-15',
    kafkaTopic: 'fleet-telemetry-v1',
    kafkaConsumerGroup: 'cms-meridian-consumer-prod',
    scramMechanism: 'SCRAM-SHA-512',
    signalMappings: MERIDIAN_MAPPINGS,
  },
  {
    productId: 'prd_ford_pro',
    productName: 'Ford Pro Fleet Telemetry',
    producer: 'Ford',
    description: 'gRPC-streaming telemetry from Ford Pro commercial vehicles. Long-lived streaming connection to the Ford Pro Fleet API, authenticated via OAuth2 client credentials. Battery diagnostics for electric models (F-150 Lightning), engine and drivetrain signals for internal-combustion models (F-350 Super Duty). Standard and Premium tiers.',
    connectionType: 'grpc_streaming',
    // gRPC service URL — port 443 for TLS, service defined in proto.
    endpointUrl: 'grpc+tls://fleet.fordpro.example:443',
    authType: 'oauth2',
    tokenEndpoint: 'https://auth.fordpro.example/oauth/token',
    credentialsSecretArn: 'arn:aws:secretsmanager:us-west-2:123456789012:secret/ford-pro-oauth-Kj9L4c',
    supportedTiers: ['Standard', 'Premium'],
    totalSignals: 50,
    status: 'Active',
    createdAt: '2026-02-01',
    grpcServiceName: 'com.ford.fleet.v1.TelemetryService',
    grpcMethodName: 'StreamTelemetry',
    oauthScopes: 'fleet.read telemetry.read',
    signalMappings: FORD_MAPPINGS,
  },
  {
    productId: 'prd_tesla_fleet',
    productName: 'Tesla Fleet Telemetry',
    producer: 'Tesla',
    description: "Fleet telemetry from Tesla vehicles via Tesla's public Fleet API. REST polling on a 30-second interval against fleet-api.prd.na.vn.cloud.tesla.com, authenticated via OAuth 2.0 client credentials. Nested state families (drive_state, charge_state, vehicle_state, climate_state) as documented at developer.tesla.com/docs/fleet-api. Basic and Standard tiers.",
    connectionType: 'rest_polling',
    endpointUrl: 'https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/vehicles/{vehicle_id}/vehicle_data',
    authType: 'oauth2',
    tokenEndpoint: 'https://auth.tesla.com/oauth2/v3/token',
    credentialsSecretArn: 'arn:aws:secretsmanager:us-west-2:123456789012:secret/tesla-fleet-oauth-Xk9M3p',
    supportedTiers: ['Basic', 'Standard'],
    totalSignals: 23,
    status: 'Active',
    createdAt: '2025-11-01',
    pollingIntervalSeconds: 30,
    oauthScopes: 'openid vehicle_device_data vehicle_cmds',
    signalMappings: TESLA_MAPPINGS,
  },
];

let userAddedCatalog: DataProduct[] = [];

/** Prepend a session-added catalog entry. */
export function addDataProduct(product: DataProduct): void {
  userAddedCatalog = [product, ...userAddedCatalog];
}

/** All catalog entries currently visible in the UI: session-added first,
 *  then the built-in seeded set. */
export function getAllCatalog(): DataProduct[] {
  return [...userAddedCatalog, ...CATALOG_PRODUCTS];
}

// ─── Mapping mutation overlay ─────────────────────────────────────────────
//
// The seed products (Meridian, Ford Pro, Tesla) ship with pre-baked
// signalMappings inside CATALOG_PRODUCTS. Once the operator starts editing
// on the catalog detail page — mapping a CMS signal to a producer signal,
// or adding an event mapping — we need those edits to persist for the
// session even though the array is embedded in a const. Rather than mutate
// the const in-place (fragile against re-renders), we keep a per-product
// overlay and merge it in the read path.

const signalMappingOverlay: Record<string, SignalMapping[] | undefined> = {};
const eventMappingOverlay: Record<string, EventMapping[] | undefined> = {};

/** Returns the effective signal-mapping list for a data product — the
 *  session overlay wins if set, otherwise the seed embedded on the
 *  DataProduct. */
export function getSignalMappingsFor(productId: string): SignalMapping[] {
  if (signalMappingOverlay[productId] !== undefined) {
    return signalMappingOverlay[productId] ?? [];
  }
  const p = getDataProductById(productId);
  return p?.signalMappings ?? [];
}

/** Returns the effective event-mapping list for a data product. */
export function getEventMappingsFor(productId: string): EventMapping[] {
  if (eventMappingOverlay[productId] !== undefined) {
    return eventMappingOverlay[productId] ?? [];
  }
  const p = getDataProductById(productId);
  return p?.eventMappings ?? [];
}

/** Replace the effective signal-mapping list for a data product. */
export function setSignalMappingsFor(productId: string, mappings: SignalMapping[]): void {
  signalMappingOverlay[productId] = mappings;
}

/** Replace the effective event-mapping list for a data product. */
export function setEventMappingsFor(productId: string, mappings: EventMapping[]): void {
  eventMappingOverlay[productId] = mappings;
}

export function getDataProductById(id: string): DataProduct | undefined {
  return getAllCatalog().find((p) => p.productId === id);
}

/**
 * Returns the tiers offered by a data product, falling back to a single
 * synthetic "Default" tier when the product declares no tier list. This
 * keeps the subscription UI viable for wizard-added products (which don't
 * collect tiers) without forcing every seed product to declare them.
 */
export function getSupportedTiers(p: DataProduct): Tier[] {
  return p.supportedTiers && p.supportedTiers.length > 0 ? p.supportedTiers : ['Default'];
}

/**
 * Total signal count for a data product. Prefers the declared headline
 * `totalSignals` (seed products set this); falls back to the length of the
 * signalMappings array (wizard-added products); falls back to 0 if neither
 * is set.
 */
export function getTotalSignalCount(p: DataProduct): number {
  if (typeof p.totalSignals === 'number') return p.totalSignals;
  return p.signalMappings?.length ?? 0;
}

// ─── Deprecated: AVAILABLE_PRODUCTS ────────────────────────────────────────
//
// Kept as a thin re-export of the catalog so the AddSubscription modal can
// switch to reading from the catalog without a big refactor. New code should
// call getAllCatalog() directly.

export interface AvailableProduct {
  productId: string;
  productName: string;
  producer: string;
  description: string;
  supportedTiers: Tier[];
  totalSignals: number;
}

/** Backwards-compatible view of the catalog as a list of AvailableProduct.
 *  Prefer getAllCatalog() in new code. Applies the same tier/signal-count
 *  fallbacks used elsewhere: wizard-added products with no declared tiers
 *  get a single "Default" tier, and totalSignals falls back to the mapping
 *  count when not explicitly set. */
export function getAvailableProducts(): AvailableProduct[] {
  return getAllCatalog().map((p) => ({
    productId: p.productId,
    productName: p.productName,
    producer: p.producer,
    description: p.description,
    supportedTiers: getSupportedTiers(p),
    totalSignals: getTotalSignalCount(p),
  }));
}

/** Build a mock Subscription record from the Add modal's inputs. Newly-added
 *  subscriptions start in 'Pending' status with 0 enrolled vehicles — matches
 *  the real-world flow where the OEM must confirm before data flows. */
export function buildSubscriptionFromForm(input: {
  product: AvailableProduct;
  tier: Tier;
  vehiclesCapacity: number;
}): Subscription {
  const { product, tier, vehiclesCapacity } = input;
  // Rough tier-to-entitlement mapping — matches the pattern in the built-in
  // mock set (Premium=all, Standard=~28, Basic=~12).
  const entitledSignalCount =
    tier === 'Premium' ? product.totalSignals :
    tier === 'Standard' ? Math.min(28, product.totalSignals) :
    Math.min(12, product.totalSignals);
  return {
    id: `sub_new_${Date.now().toString(36)}`,
    productName: product.productName,
    producer: product.producer,
    tier,
    status: 'Pending',
    feedHealth: 'paused',
    lastPacketAt: '—',
    p95LatencyMs: 0,
    errorRate24h: '—',
    contractStart: new Date().toISOString().slice(0, 10),
    nextRenewal: new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
    vehiclesCapacity,
    entitledSignalCount,
    totalOemSignals: product.totalSignals,
    dataVolumeCycle: '—',
    estimatedCostCycle: '—',
    quotaHeadroomPct: 100,
    description: `${product.description} Pending activation — data will begin flowing once the producer confirms this subscription.`,
    unavailableSignals: [],
    enrolledVehicles: [],
    availableSignals: [], // no signal preview until activated
  };
}
