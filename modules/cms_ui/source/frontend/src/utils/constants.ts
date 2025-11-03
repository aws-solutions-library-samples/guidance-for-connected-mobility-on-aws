// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

export const API_NAME = "api";
export const APP_TRADEMARK_NAME = "Connected Mobility on AWS";

export const DELAY_AFTER_DELETE_MS = 2000; // 2 seconds

export const UI_ROUTES = {
  ROOT: "/",
  FLEET_MANAGEMENT: "/fleets/management",
  FLEET_VEHICLES_MAP: "/fleets/vehicles-map",
  FLEET_SIMULATION: "/fleets/simulation",
  FLEET_CREATE: "/fleets/create",
  FLEET_EDIT: "/fleets/edit",
  FLEET_ASSOCIATE_VEHICLES: "/fleets/associate-vehicles",
  VEHICLE_MANAGEMENT: "/vehicles/management",
  VEHICLE_DASHBOARD: "/vehicles/dashboard",
  VEHICLE_CREATE: "/vehicles/create",
  VEHICLE_EDIT: "/vehicles/edit",
  VEHICLE_DETAIL: "/vehicles/management",
  TRIP_DETAIL: "/vehicles/management/:vehicleId/trips/:tripId",
  CAMPAIGN_MANAGEMENT: "/campaigns/management",
  CAMPAIGN_CREATE: "/campaigns/create",
  CAMPAIGN_EDIT: "/campaigns/edit",
  ALERTS_SAFETY: "/alerts/safety",
  ALERTS_MAINTENANCE: "/alerts/maintenance",
  SETTINGS: "/settings",
  
  FLEETWISE_CAMPAIGN_DETAIL: "/fleetwise/campaigns/detail",
  // AWS IoT FleetWise routes
  FLEETWISE_SIGNAL_CATALOG: "/fleetwise/signal-catalog",
  FLEETWISE_VEHICLE_MODELS: "/fleetwise/vehicle-models",
  FLEETWISE_VEHICLE_MODEL_DETAIL: "/fleetwise/vehicle-models/detail",
  FLEETWISE_CAMPAIGNS: "/fleetwise/campaigns",
  
  // Device Management routes
  IOT_STATUS_OVERVIEW: "/iot-status-overview",
  IOT_CLIENT_LIST: "/iot-client-list",
  IOT_TOPIC_LIST: "/iot-topic-list",
  IOT_SUBSCRIPTION_LIST: "/iot-subscription-list",
  IOT_RETAIN_MESSAGE_LIST: "/iot-retain-message-list",
  IOT_RULE_LIST: "/iot-rule-list",
  IOT_ALARM_LIST: "/iot-alarm-list",
  IOT_LOG_TRACE: "/iot-log-trace",
  IOT_USER_LIST: "/iot-user-list",
  IOT_POLICY_LIST: "/iot-policy-list",
  
  // Analytics routes
  TELEMETRY_DASHBOARD: "/telemetry-dashboard",
  DRIVER_BEHAVIOR: "/driver-behavior",
  GEOFENCE_EVENTS: "/geofence-events",
  TRIP_ANALYTICS: "/trip-analytics",
  
  // Data Processing routes
  DATA_PROCESSING: "/data-processing",
};

export const ERROR_MESSAGES = {
  UNAUTHORIZED: "Request failed with status code 403",
};

export const LANDING_PAGE_URL =
  "https://aws.amazon.com/solutions/implementations/connected-mobility-solution-on-aws/";

export const FEEDBACK_ISSUES_URL =
  "https://github.com/aws-solutions/connected-mobility-solution-on-aws/issues/new/choose";

export const IG_ROOT =
  "https://docs.aws.amazon.com/solutions/latest/connected-mobility-solution-on-aws";
export const IG_URLS = {
  SUPPORT: `${IG_ROOT}/contact-aws-support.html`,
};