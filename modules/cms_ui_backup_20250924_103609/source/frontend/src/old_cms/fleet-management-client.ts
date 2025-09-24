// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// Note: This file previously used Smithy packages
// Updated to remove Smithy dependencies

// Define the client configuration interface
export interface FleetManagementClientConfig {
  endpoint: string;
  apiKey?: { apiKey: string };
  sigv4Config?: {
    region: string;
    credentials: any; // Replace with appropriate credential type
  };
}

export interface FleetItem {
  id: string;
  name: string;
  numTotalVehicles?: number;
  numConnectedVehicles?: number;
  numTotalCampaigns?: number;
  numActiveCampaigns?: number;
  createdTime?: string;
  lastModifiedTime?: string;
  totalVehicles?: number;
  connectedVehicles?: number;
  operationalCity?: string;
  fleetId?: string;
  status?: string;
  description?: string;
}

export enum CampaignStatus {
  CREATING = "CREATING",
  WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL",
  RUNNING = "RUNNING",
  SUSPENDED = "SUSPENDED",
  CANCELLED = "CANCELLED",
  FAILING = "FAILING",
  FAILED = "FAILED"
}

export interface CampaignItem {
  name: string;
  arn?: string;
  description?: string;
  targetArn?: string;
  status?: CampaignStatus;
  signalCatalogArn?: string;
  startTime?: string;
  creationTime?: string;
  lastModificationTime?: string;
  collectionScheme?: any;
  dataDestinationConfigs?: any[];
  priority?: number;
  compression?: string;
  diagnosticsMode?: string;
  spoolingMode?: string;
  postTriggerCollectionDuration?: number;
}

export enum VehicleStatus {
  ACTIVE = "ACTIVE",
  INACTIVE = "INACTIVE"
}

export interface VehicleItem {
  name: string;
  status: VehicleStatus;
  attributes?: {
    make?: string;
    model?: string;
    year?: number;
    licensePlate?: string;
    vin?: string;
  };
  tags?: Record<string, string>;
}

// Define input and entry types
export interface CreateVehicleEntry {
  vin: string;
  make: string;
  model: string;
  year: number;
  licensePlate: string;
  decoderManifestName?: string;
  tags?: Record<string, string>;
}

export interface AssociateVehiclesToFleetInput {
  id: string;
  vehicleNames: string[];
}

// Vehicle Model types
export enum VehicleModelStatus {
  ACTIVE = "ACTIVE",
  DRAFT = "DRAFT",
  FAILED = "FAILED"
}

export interface NetworkInterface {
  interfaceId: string;
  type: "CAN" | "OBD" | "CUSTOM";
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

export interface SignalDecoder {
  fullyQualifiedName?: string;
  type?: "CAN" | "OBD" | "CUSTOM";
  interfaceId?: string;
  canSignal?: CanSignal;
  obdSignal?: ObdSignal;
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

export interface VehicleModelItem {
  name: string;
  arn: string;
  description?: string;
  status: VehicleModelStatus;
  signalCatalogArn: string;
  creationTime: Date;
  lastModificationTime: Date;
}

export interface VehicleModelCreateInput {
  name: string;
  description?: string;
  signalCatalogArn: string;
  networkInterfaces?: NetworkInterface[];
  signalDecoders?: SignalDecoder[];
}

export interface VehicleModelUpdateInput {
  description?: string;
  status?: VehicleModelStatus;
  networkInterfaces?: NetworkInterface[];
  signalDecoders?: SignalDecoder[];
}

// Base command class
class BaseCommand {
  input: { [key: string]: any };
  
  constructor(input: { [key: string]: any } = {}) {
    this.input = input;
  }
  
  middlewareStack: any = {
    add: () => {},
    addRelativeTo: () => {},
    clone: () => this.middlewareStack,
    remove: () => {},
    use: () => {},
    resolve: () => {},
  };
}
// Make the type assertion when using the command
// Define the commands
export class ListSignalCatalogsCommand extends BaseCommand {}

export class ListSignalCatalogNodesCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class GetFleetCommand extends BaseCommand {
  constructor(input: { id: string }) {
    super(input);
  }
}

export class ListFleetsCommand extends BaseCommand {}

export class ListCampaignsForTargetCommand extends BaseCommand {
  constructor(input: { targetId: string }) {
    super(input);
  }
}

export class ListVehiclesInFleetCommand extends BaseCommand {
  constructor(input: { id: string }) {
    super(input);
  }
}

export class ListVehiclesCommand extends BaseCommand {}

export class GetVehicleCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class ListFleetsForVehicleCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class CreateFleetCommand extends BaseCommand {
  constructor(input: { entry: { id: string; name: string } }) {
    super(input);
  }
}

export class DeleteFleetCommand extends BaseCommand {
  constructor(input: { id: string }) {
    super(input);
  }
}

export class DeleteVehicleCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class DeleteCampaignCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class DisassociateVehicleCommand extends BaseCommand {
  constructor(input: { fleetId: string; name: string }) {
    super(input);
  }
}

export class EditFleetCommand extends BaseCommand {
  constructor(input: { id: string; entry: { name: string } }) {
    super(input);
  }
}

export class StartCampaignCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class StopCampaignCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class ListDecoderManifestsCommand extends BaseCommand {}

export class EditVehicleCommand extends BaseCommand {
  constructor(input: { name: string; entry: { make: string; model: string; year: number; licensePlate: string } }) {
    super(input);
  }
}

export class CreateVehicleCommand extends BaseCommand {
  constructor(input: { entry: { name: string; make: string; model: string; year: number; licensePlate: string } }) {
    super(input);
  }
}

export class AssociateVehiclesToFleetCommand extends BaseCommand {
  constructor(input: { id: string; vehicleNames: string[] }) {
    super(input);
  }
}

export class ListCampaignsCommand extends BaseCommand {}

export class GetCampaignCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class UpdateUserPreferencesCommand extends BaseCommand {
  constructor(input: { useManagedService: boolean }) {
    super(input);
  }
}

export class GetUserPreferencesCommand extends BaseCommand {}

// New standardized command classes
export class ListSafetyEventsCommand extends BaseCommand {}
export class ListMaintenanceEventsCommand extends BaseCommand {}
export class ListTripsCommand extends BaseCommand {}

// Define the client class
export class FleetManagementClient {
  private config: FleetManagementClientConfig;
  private sigv4Middleware: ReturnType<typeof createSigV4Middleware> | null = null;
  
  constructor(config: FleetManagementClientConfig) {
    this.config = config;
    
    // Initialize SigV4 middleware if config is provided
    if (config.sigv4Config) {
      const { region, credentials } = config.sigv4Config;
      
      const getCredentials = typeof credentials === 'function' 
        ? credentials 
        : () => Promise.resolve(credentials);
      
      const sigv4Config: SigV4MiddlewareConfig = {
        region,
        service: 'execute-api',
        getCredentials,
      };
      
      this.sigv4Middleware = createSigV4Middleware(sigv4Config);
    }
  }
  
  async send<T extends Command<any, any, any>>(command: T): Promise<any> {
    console.log("FleetManagementClient.send", command);
    
    try {
      // Extract command name and input
      const commandName = command.constructor.name;
      const input = command.input;
      
      // Build request URL and method
      const url = this.buildUrl(commandName, input);
      const method = this.getMethodForCommand(commandName);
      
      // Prepare headers
      let headers: Record<string, string> = {
        'Accept': 'application/json',
      };
      
      // Only set Content-Type for requests with a body
      if (method !== 'GET' && method !== 'DELETE') {
        headers['Content-Type'] = 'application/json';
      }
      
      // Add authorization header if using API key (Cognito token)
      if (this.config.apiKey) {
        headers['Authorization'] = this.config.apiKey.apiKey;
      }
      
      // Prepare request body for POST/PUT methods
      const body = method === 'GET' || method === 'DELETE' ? undefined : JSON.stringify(input);
      
      // We're using Cognito tokens now, so we don't need SigV4 signing
      // if (this.sigv4Middleware) {
      //   headers = await this.sigv4Middleware.signRequest(url, method, headers, body);
      // }
      
      // Make the request
      const response = await fetch(url, {
        method,
        headers,
        body,
      });
      
      if (!response.ok) {
        throw new Error(`API request failed with status ${response.status}: ${await response.text()}`);
      }
      
      // Parse and return the response
      const data = await response.json();
      
      // Handle specific response formats for our API
      if (commandName === 'ListFleetsCommand' && data.fleets) {
        // Our API returns {fleets: [...], total: N}, but client expects {fleets: [...]}
        return data;
      }
      
      return data;
    } catch (error) {
      console.error("Error in FleetManagementClient.send:", error);
      throw error;
    }
  }
  
  private buildUrl(commandName: string, input: any): string {
    const baseUrl = this.config.endpoint.endsWith('/') 
      ? this.config.endpoint.slice(0, -1) 
      : this.config.endpoint;
    
    // Map command names to API paths
    switch (commandName) {
      case 'GetFleetCommand':
        return `${baseUrl}/api/v1/fleets/${input.id}`;
      case 'ListFleetsCommand':
        return `${baseUrl}/api/v1/fleets`;
      case 'CreateFleetCommand':
        return `${baseUrl}/fleet`;
      case 'DeleteFleetCommand':
        return `${baseUrl}/fleet/${input.id}`;
      case 'EditFleetCommand':
        return `${baseUrl}/fleet/${input.id}`;
      case 'ListVehiclesInFleetCommand':
        return `${baseUrl}/fleet/${input.id}/vehicles`;
      case 'AssociateVehiclesToFleetCommand':
        return `${baseUrl}/fleet/${input.id}/vehicles`;
      case 'ListVehiclesCommand':
        return `${baseUrl}/api/v1/vehicles`;
      case 'GetVehicleCommand':
        return `${baseUrl}/api/v1/vehicles?vin=${input.name}`;
      case 'CreateVehicleCommand':
        return `${baseUrl}/vehicle`;
      case 'EditVehicleCommand':
        return `${baseUrl}/vehicle/${input.name}`;
      case 'DeleteVehicleCommand':
        return `${baseUrl}/vehicle/${input.name}`;
      case 'ListFleetsForVehicleCommand':
        return `${baseUrl}/vehicle/${input.name}/fleets`;
      case 'DisassociateVehicleCommand':
        return `${baseUrl}/vehicle/${input.name}/disassociate-fleet/${input.fleetId}`;
      case 'ListCampaignsCommand':
        return `${baseUrl}/campaign`;
      case 'GetCampaignCommand':
        return `${baseUrl}/campaign/${input.name}`;
      case 'DeleteCampaignCommand':
        return `${baseUrl}/campaign/${input.name}`;
      case 'StartCampaignCommand':
        return `${baseUrl}/campaign/${input.name}/start`;
      case 'StopCampaignCommand':
        return `${baseUrl}/campaign/${input.name}/stop`;
      case 'ListCampaignsForTargetCommand':
        return `${baseUrl}/campaign/list/${input.targetType}/${input.targetId}`;
      case 'ListDecoderManifestsCommand':
        return `${baseUrl}/decoder-manifests`;
      case 'GetDecoderManifestCommand':
        return `${baseUrl}/decoder-manifests/${input.name}`;
      case 'CreateDecoderManifestCommand':
        return `${baseUrl}/decoder-manifests`;
      case 'UpdateDecoderManifestCommand':
        return `${baseUrl}/decoder-manifests/${input.name}`;
      case 'DeleteDecoderManifestCommand':
        return `${baseUrl}/decoder-manifests/${input.name}`;
      case 'ListDecoderManifestNetworkInterfacesCommand':
        return `${baseUrl}/decoder-manifests/${input.name}/network-interfaces`;
      case 'ListDecoderManifestSignalsCommand':
        return `${baseUrl}/decoder-manifests/${input.name}/signals`;
      case 'CreateModelManifestCommand':
        return `${baseUrl}/model-manifests`;
      case 'GetModelManifestCommand':
        return `${baseUrl}/model-manifests/${input.name}`;
      case 'ListModelManifestsCommand':
        return `${baseUrl}/model-manifests`;
      case 'DeleteModelManifestCommand':
        return `${baseUrl}/model-manifests/${input.name}`;
      case 'UpdateModelManifestCommand':
        return `${baseUrl}/model-manifests/${input.name}`;
      case 'ListModelManifestNodesCommand':
        return `${baseUrl}/model-manifests/${input.name}/nodes`;
      case 'ListSignalCatalogsCommand':
        return `${baseUrl}/signal-catalogs`;
      case 'ListSignalCatalogNodesCommand':
        return `${baseUrl}/signal-catalogs/${input.name}/nodes`;
      case 'CreateSignalCatalogCommand':
        return `${baseUrl}/signal-catalogs`;
      case 'DeleteSignalCatalogCommand':
        return `${baseUrl}/signal-catalogs/${input.name}`;
      case 'ImportSignalCatalogCommand':
        return `${baseUrl}/signal-catalogs/import`;
      case 'UpdateUserPreferencesCommand':
        return `${baseUrl}/user-preferences`;
      case 'GetUserPreferencesCommand':
        return `${baseUrl}/user-preferences`;
      default:
        throw new Error(`Unknown command: ${commandName}`);
    }
  }
  
  private getMethodForCommand(commandName: string): string {
    if (commandName.startsWith('Get') || commandName.startsWith('List')) {
      return 'GET';
    } else if (commandName.startsWith('Create')) {
      return 'POST';
    } else if (commandName.startsWith('Edit') || commandName.startsWith('Update')) {
      return 'PUT';
    } else if (commandName.startsWith('Delete')) {
      return 'DELETE';
    } else if (commandName === 'AssociateVehiclesToFleetCommand' || 
               commandName === 'DisassociateVehicleCommand' ||
               commandName === 'StartCampaignCommand' ||
               commandName === 'StopCampaignCommand' ||
               commandName === 'ImportSignalCatalogCommand') {
      return 'POST';
    } else {
      return 'GET';
    }
  }
}

// Add the new command classes for the operations
export class CreateCampaignCommand extends BaseCommand {
  constructor(input: { entry: { name: string; targetType: string; targetId: string; collectionScheme: any } }) {
    super(input);
  }
}

export class UpdateCampaignCommand extends BaseCommand {
  constructor(input: { name: string; entry: any }) {
    super(input);
  }
}

export class GetDecoderManifestCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class CreateDecoderManifestCommand extends BaseCommand {
  constructor(input: { entry: any }) {
    super(input);
  }
}

export class UpdateDecoderManifestCommand extends BaseCommand {
  constructor(input: { name: string; entry: any }) {
    super(input);
  }
}

export class DeleteDecoderManifestCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class ListDecoderManifestNetworkInterfacesCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class ListDecoderManifestSignalsCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class CreateModelManifestCommand extends BaseCommand {
  constructor(input: { entry: any }) {
    super(input);
  }
}

export class GetModelManifestCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class ListModelManifestsCommand extends BaseCommand {}

export class DeleteModelManifestCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class UpdateModelManifestCommand extends BaseCommand {
  constructor(input: { name: string; entry: any }) {
    super(input);
  }
}

export class ListModelManifestNodesCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class CreateSignalCatalogCommand extends BaseCommand {
  constructor(input: { entry: any }) {
    super(input);
  }
}

export class DeleteSignalCatalogCommand extends BaseCommand {
  constructor(input: { name: string }) {
    super(input);
  }
}

export class ImportSignalCatalogCommand extends BaseCommand {
  constructor(input: { entry: any }) {
    super(input);
  }
}

// Add additional type definitions for the new operations
export interface ModelManifestItem {
  name: string;
  arn: string;
  description?: string;
  status: ModelManifestStatus;
  signalCatalogArn: string;
  creationTime: Date;
  lastModificationTime: Date;
}

export enum ModelManifestStatus {
  ACTIVE = "ACTIVE",
  DRAFT = "DRAFT",
  FAILED = "FAILED"
}

export interface ModelManifestNode {
  fullyQualifiedName: string;
  dataType: string;
  description?: string;
  min?: number;
  max?: number;
  unit?: string;
  allowedValues?: string[];
}

export interface ModelManifestCreateInput {
  name: string;
  description?: string;
  signalCatalogArn: string;
  networkInterfaces?: NetworkInterface[];
  signalDecoders?: SignalDecoder[];
  nodes?: ModelManifestNode[];
}

export interface ModelManifestUpdateInput {
  description?: string;
  status?: ModelManifestStatus;
  networkInterfaces?: NetworkInterface[];
  signalDecoders?: SignalDecoder[];
  nodes?: ModelManifestNode[];
}

export interface SignalCatalogItem {
  name: string;
  arn: string;
  description?: string;
  nodeCount: number;
  creationTime: Date;
  lastModificationTime: Date;
}

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

export interface SignalCatalogCreateInput {
  name: string;
  description?: string;
  nodes?: SignalCatalogNode[];
}

export enum SignalCatalogFormat {
  VSS = "VSS",
  JSON = "JSON",
  DBC = "DBC"
}

export interface SignalCatalogImportInput {
  format: SignalCatalogFormat;
  content: string;
  targetCatalogName?: string;
}

// Add missing type definitions for decoder manifests
export interface DecoderManifestItem {
  name: string;
  arn: string;
  description?: string;
  status: DecoderManifestStatus;
  networkInterfaceCount: number;
  signalCount: number;
  creationTime: Date;
  lastModificationTime: Date;
}

export enum DecoderManifestStatus {
  ACTIVE = "ACTIVE",
  DRAFT = "DRAFT",
  FAILED = "FAILED"
}

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

export interface DecoderSignal {
  fullyQualifiedName: string;
  interfaceId: string;
  type: SignalType;
  canSignal?: CanSignalConfig;
  obdSignal?: ObdSignalConfig;
}

export enum SignalType {
  CAN = "CAN",
  OBD = "OBD",
  CUSTOM = "CUSTOM"
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

export interface DecoderManifestCreateInput {
  name: string;
  description?: string;
  networkInterfaces?: DecoderNetworkInterface[];
  signals?: DecoderSignal[];
}

export interface DecoderManifestUpdateInput {
  description?: string;
  status?: DecoderManifestStatus;
  networkInterfaces?: DecoderNetworkInterface[];
  signals?: DecoderSignal[];
}

export enum CampaignTargetType {
  VEHICLE = "VEHICLE",
  FLEET = "FLEET"
}

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

export enum NetworkInterfaceType {
  CAN = "CAN",
  OBD = "OBD",
  CUSTOM = "CUSTOM"
}