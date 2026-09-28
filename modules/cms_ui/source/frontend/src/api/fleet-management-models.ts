// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// Common types
export type ARN = string;
export type ResourceName = string;
export type NonEmptyString = string;

export interface Tag {
  Key: string;
  Value: string;
}

export type Tags = Tag[];

// Signal catalog item model
export interface SignalCatalogItem {
  name: ResourceName;
  arn: ARN;
  description?: string;
  nodeCount: number;
  creationTime: string;
  lastModificationTime: string;
}

// Signal catalog node model
export interface SignalCatalogNode {
  name: string;
  dataType: string;
  description?: string;
  fullyQualifiedName: string;
  min?: number;
  max?: number;
  unit?: string;
  allowedValues?: string[];
}

// Signal catalog format enum
export enum SignalCatalogFormat {
  VSS = "VSS",
  JSON = "JSON",
  DBC = "DBC"
}

// Fleet item model
export interface FleetItem {
  id: ResourceName;
  name: ResourceName;
  numTotalVehicles: number;
  numConnectedVehicles: number;
  numActiveCampaigns: number;
  numTotalCampaigns: number;
  creationTime: string;
  lastModificationTime: string;
  description?: string;
  tags?: Tags;
}

// Fleet summary model
export interface FleetSummary {
  id: ResourceName;
  name: ResourceName;
}

// Vehicle status enum
export enum VehicleStatus {
  ACTIVE = "ACTIVE",
  INACTIVE = "INACTIVE"
}

// Vehicle attributes model
export interface VehicleAttributes {
  vin: string;
  make: NonEmptyString;
  model: NonEmptyString;
  year: number;
  licensePlate: NonEmptyString;
}

// Vehicle item model
export interface VehicleItem {
  name: ResourceName;
  vehicleId?: string;
  status: VehicleStatus;
  attributes: VehicleAttributes;
  tags?: Tags;
}

// Campaign item model
export interface CampaignItem {
  name: ResourceName;
  targetId: ResourceName;
  status: CampaignStatus;
}

// Campaign status enum
export enum CampaignStatus {
  CREATING = "CREATING",
  WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL",
  RUNNING = "RUNNING",
  SUSPENDED = "SUSPENDED"
}

// Campaign target type enum
export enum CampaignTargetType {
  VEHICLE = "VEHICLE",
  FLEET = "FLEET"
}

// Collection scheme types
export type CollectionScheme = {
  timeBasedCollectionScheme?: TimeBasedCollectionScheme;
  conditionBasedCollectionScheme?: ConditionBasedCollectionScheme;
}

export interface TimeBasedCollectionScheme {
  periodMs: number;
}

export interface ConditionBasedCollectionScheme {
  expression: string;
  triggerMode: TriggerMode;
  minimumTriggerIntervalMs?: number;
}

export enum TriggerMode {
  ALWAYS = "ALWAYS",
  RISING_EDGE = "RISING_EDGE",
  FALLING_EDGE = "FALLING_EDGE",
  CHANGE = "CHANGE"
}

// Campaign create input
export interface CampaignCreateInput {
  name: ResourceName;
  targetType: CampaignTargetType;
  targetId: ResourceName;
  description?: string;
  collectionScheme: CollectionScheme;
  signalCatalogArn?: ARN;
  signals?: string[];
}

// Campaign update input
export interface CampaignUpdateInput {
  description?: string;
  collectionScheme?: CollectionScheme;
  signals?: string[];
}

// Decoder manifest item model
export interface DecoderManifestItem {
  name: ResourceName;
  arn: ARN;
  description?: string;
  status: DecoderManifestStatus;
  networkInterfaceCount: number;
  signalCount: number;
  creationTime: string;
  lastModificationTime: string;
}

// Decoder manifest status enum
export enum DecoderManifestStatus {
  ACTIVE = "ACTIVE",
  DRAFT = "DRAFT",
  FAILED = "FAILED"
}

// Network interface type enum
export enum NetworkInterfaceType {
  CAN = "CAN",
  OBD = "OBD",
  CUSTOM = "CUSTOM"
}

// Network interface for decoder manifest
export interface DecoderNetworkInterface {
  interfaceId: string;
  type: NetworkInterfaceType;
  canInterface?: CanInterfaceConfig;
  obdInterface?: ObdInterfaceConfig;
}

export interface CanInterfaceConfig {
  name?: string;
  protocolName?: string;
  protocolVersion?: string;
}

export interface ObdInterfaceConfig {
  name?: string;
  requestMessageId?: string;
  dtcRequestIntervalSeconds?: number;
  hasTransmissionEcu?: boolean;
  obdStandard?: string;
  pidRequestIntervalSeconds?: number;
  useExtendedIds?: boolean;
}

// Signal type enum
export enum SignalType {
  CAN = "CAN",
  OBD = "OBD",
  CUSTOM = "CUSTOM"
}

// Decoder signal
export interface DecoderSignal {
  fullyQualifiedName: string;
  interfaceId: string;
  type: SignalType;
  canSignal?: CanSignalConfig;
  obdSignal?: ObdSignalConfig;
}

export interface CanSignalConfig {
  messageId?: number;
  isBigEndian?: boolean;
  isSigned?: boolean;
  startBit?: number;
  offset?: number;
  factor?: number;
  length?: number;
}

export interface ObdSignalConfig {
  pidRequestInterval?: number;
  pid?: number;
  scaling?: number;
  offset?: number;
  startByte?: number;
  byteLength?: number;
  bitRightShift?: number;
  bitMaskLength?: number;
  serviceMode?: number;
}

// Decoder manifest create input
export interface DecoderManifestCreateInput {
  name: ResourceName;
  description?: string;
  networkInterfaces?: DecoderNetworkInterface[];
  signals?: DecoderSignal[];
}

// Decoder manifest update input
export interface DecoderManifestUpdateInput {
  description?: string;
  status?: DecoderManifestStatus;
  networkInterfaces?: DecoderNetworkInterface[];
  signals?: DecoderSignal[];
}

// Model manifest item model
export interface ModelManifestItem {
  name: ResourceName;
  arn: ARN;
  description?: string;
  status: ModelManifestStatus;
  signalCatalogArn: ARN;
  creationTime: string;
  lastModificationTime: string;
}

// Model manifest status enum
export enum ModelManifestStatus {
  ACTIVE = "ACTIVE",
  DRAFT = "DRAFT",
  FAILED = "FAILED"
}

// Model manifest node
export interface ModelManifestNode {
  fullyQualifiedName: string;
  dataType: string;
  description?: string;
  min?: number;
  max?: number;
  unit?: string;
  allowedValues?: string[];
}

// Network interface for model manifest
export interface NetworkInterface {
  interfaceId: string;
  type: NetworkInterfaceType;
  canInterface?: CanInterface;
  obdInterface?: ObdInterface;
}

export interface CanInterface {
  name?: string;
  protocolName?: string;
  protocolVersion?: string;
}

export interface ObdInterface {
  name?: string;
  requestMessageId?: string;
  dtcRequestIntervalSeconds?: number;
  hasTransmissionEcu?: boolean;
  obdStandard?: string;
  pidRequestIntervalSeconds?: number;
  useExtendedIds?: boolean;
}

// Signal decoder
export interface SignalDecoder {
  fullyQualifiedName: string;
  type: SignalDecoderType;
  interfaceId: string;
  canSignal?: CanSignal;
  obdSignal?: ObdSignal;
}

export enum SignalDecoderType {
  CAN = "CAN",
  OBD = "OBD",
  CUSTOM = "CUSTOM"
}

export interface CanSignal {
  messageId?: number;
  isBigEndian?: boolean;
  isSigned?: boolean;
  startBit?: number;
  offset?: number;
  factor?: number;
  length?: number;
}

export interface ObdSignal {
  pidRequestInterval?: number;
  pid?: number;
  scaling?: number;
  offset?: number;
  startByte?: number;
  byteLength?: number;
  bitRightShift?: number;
  bitMaskLength?: number;
  serviceMode?: number;
}

// Model manifest create input
export interface ModelManifestCreateInput {
  name: ResourceName;
  description?: string;
  signalCatalogArn: ARN;
  networkInterfaces?: NetworkInterface[];
  signalDecoders?: SignalDecoder[];
  nodes?: ModelManifestNode[];
}

// Model manifest update input
export interface ModelManifestUpdateInput {
  description?: string;
  status?: ModelManifestStatus;
  networkInterfaces?: NetworkInterface[];
  signalDecoders?: SignalDecoder[];
  nodes?: ModelManifestNode[];
}

// User preferences
export interface UpdateUserPreferencesInput {
  useManagedService: boolean;
}

// Create vehicle entry
export interface CreateVehicleEntry {
  name: ResourceName;
  decoderManifestName?: ResourceName; // Made optional since we provide default
  vin: NonEmptyString;
  make: NonEmptyString;
  model: NonEmptyString;
  year: number;
  licensePlate: NonEmptyString;
  tags?: Tags;
}

// Edit vehicle entry
export interface EditVehicleEntry {
  name: ResourceName;
  vin: NonEmptyString;
  make: NonEmptyString;
  model: NonEmptyString;
  year: number;
  licensePlate: NonEmptyString;
  tags?: Tags;
}

// Create fleet entry
export interface CreateFleetEntry {
  id: ResourceName;
  name: ResourceName;
  description?: string;
  tags?: Tags;
}

// Edit fleet entry
export interface EditFleetEntry {
  id: ResourceName;
  name: ResourceName;
  description?: string;
  tags?: Tags;
}