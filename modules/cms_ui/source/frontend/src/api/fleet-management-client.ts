// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

export interface FleetManagementClientConfig {
  region?: string;
  credentials?: any;
}

export interface FleetManagementClient {
  send(command: any): Promise<any>;
}

// Fleet item model
export interface FleetItem {
  id: string;
  name: string;
  numTotalVehicles: number;
  numConnectedVehicles: number;
  numActiveCampaigns: number;
  numTotalCampaigns: number;
  creationTime: string;
  lastModificationTime: string;
  description?: string;
  tags?: any;
}

// Vehicle status enum
export enum VehicleStatus {
  ACTIVE = "ACTIVE",
  INACTIVE = "INACTIVE"
}

// Vehicle item model
export interface VehicleItem {
  name: string;
  vehicleId?: string;
  status: VehicleStatus;
  attributes: any;
  tags?: any;
}

// Command classes
export class CreateFleetCommand {
  constructor(public input: any) {}
}

export class ListFleetsCommand {
  constructor(public input: any = {}) {}
}

export class GetFleetCommand {
  constructor(public input: { id: string }) {}
}

export class EditFleetCommand {
  constructor(public input: any) {}
}

export class UpdateFleetCommand {
  constructor(public input: any) {}
}

export class DeleteFleetCommand {
  constructor(public input: { id: string }) {}
}

export class CreateVehicleCommand {
  constructor(public input: any) {}
}

export class ListVehiclesCommand {
  constructor(public input: any = {}) {}
}

export class GetVehicleCommand {
  constructor(public input: { name: string }) {}
}

export class UpdateVehicleCommand {
  constructor(public input: any) {}
}

export class DeleteVehicleCommand {
  constructor(public input: { name: string }) {}
}

export class ListSafetyEventsCommand {
  constructor(public input: any = {}) {}
}

export class ListMaintenanceEventsCommand {
  constructor(public input: any = {}) {}
}

export class ListTripsCommand {
  constructor(public input: any = {}) {}
}

// Driver commands
export class CreateDriverCommand {
  constructor(public input: any) {}
}

export class ListDriversCommand {
  constructor(public input: any = {}) {}
}

export class GetDriverCommand {
  constructor(public input: { id: string }) {}
}

export class UpdateDriverCommand {
  constructor(public input: any) {}
}

export class DeleteDriverCommand {
  constructor(public input: { id: string }) {}
}

export interface CreateVehicleEntry {
  vin: string;
  make: string;
  model: string;
  year: number;
  licensePlate?: string;
  fleetId?: string;
  color?: string;
  fuelType?: string;
  transmission?: string;
  tags?: any;
}
