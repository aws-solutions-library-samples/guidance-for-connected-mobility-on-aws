// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

export interface CostBreakdown {
  category: string;
  amount: number;
  percentage: number;
}

export interface FleetCostSummary {
  fleetId: string;
  totalCostMtd: number;
  costPerMile: number;
  costPerVehicle: number;
  maintenanceRatio: number;
  breakdown: CostBreakdown[];
}

export interface VehicleCostSummary {
  vehicleId: string;
  costPerMile: number;
  maintenanceSpend: number;
  fuelSpend: number;
  vsFleetAvg: number;
}

export interface CostTransaction {
  id: string;
  vehicleId: string;
  category: string;
  amount: number;
  date: string;
  description: string;
}

export interface CostTrend {
  month: string;
  totalCost: number;
  maintenanceCost: number;
  fuelCost: number;
  chargingCost: number;
}

export interface CostOutlier {
  vehicleId: string;
  vin: string;
  costPerMile: number;
  fleetAvgCostPerMile: number;
  deviation: number;
}

export interface Diagnosis {
  code: string;
  description: string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
}

export interface Recommendation {
  id: string;
  vehicleId: string;
  priority: 'LOW' | 'MEDIUM' | 'HIGH';
  recommendation: string;
  diagnosis: Diagnosis;
  estimatedImpact: number;
  confidence: number;
  status: 'PENDING' | 'APPROVED' | 'REJECTED' | 'SNOOZED';
}

export interface ApprovalRule {
  id: string;
  rule: string;
  condition: string;
  threshold: number;
  status: 'ACTIVE' | 'INACTIVE';
}

export interface UploadResult {
  uploadId: string;
  recordsProcessed: number;
  errors: string[];
  status: 'SUCCESS' | 'PARTIAL' | 'FAILED';
}

export interface CostAlert {
  id: string;
  type: 'COST_ANOMALY' | 'MAINTENANCE_SPIKE' | 'FUEL_EFFICIENCY_DROP' | 'CHARGING_COST_SPIKE';
  vehicleId: string;
  message: string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH';
  timestamp: string;
}

export interface AgentActivity {
  id: string;
  timestamp: string;
  message: string;
  type: 'INFO' | 'WARNING' | 'SUCCESS' | 'ERROR';
  vehicleId?: string;
}
