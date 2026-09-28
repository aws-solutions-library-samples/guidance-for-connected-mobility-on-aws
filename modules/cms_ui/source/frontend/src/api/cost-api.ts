// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  FleetCostSummary,
  CostTrend,
  CostOutlier,
  VehicleCostSummary,
  CostTransaction,
  UploadResult,
  Recommendation,
} from '../types/tco';

// TODO: Replace stubs with real API calls

export const getFleetCosts = async (_fleetId: string): Promise<FleetCostSummary> => {
  throw new Error('TODO: implement getFleetCosts');
};

export const getFleetCostTrend = async (_fleetId: string, _months: number): Promise<CostTrend[]> => {
  throw new Error('TODO: implement getFleetCostTrend');
};

export const getFleetCostOutliers = async (_fleetId: string): Promise<CostOutlier[]> => {
  throw new Error('TODO: implement getFleetCostOutliers');
};

export const getVehicleCosts = async (_vehicleId: string): Promise<VehicleCostSummary> => {
  throw new Error('TODO: implement getVehicleCosts');
};

export const getVehicleCostHistory = async (_vehicleId: string): Promise<CostTransaction[]> => {
  throw new Error('TODO: implement getVehicleCostHistory');
};

export const uploadCostCsv = async (_fleetId: string, _file: File): Promise<UploadResult> => {
  throw new Error('TODO: implement uploadCostCsv');
};

export const getRecommendations = async (_fleetId: string): Promise<Recommendation[]> => {
  throw new Error('TODO: implement getRecommendations');
};

export const approveRecommendation = async (_id: string): Promise<void> => {
  throw new Error('TODO: implement approveRecommendation');
};

export const rejectRecommendation = async (_id: string): Promise<void> => {
  throw new Error('TODO: implement rejectRecommendation');
};
