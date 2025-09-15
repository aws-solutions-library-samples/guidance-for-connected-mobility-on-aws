/**
 * Unified Routes Configuration
 * Combines CMS Fleet Management routes with AIOT Management routes
 */

import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';

// Existing CMS Components
import DashboardView from '../components/dashboard/DashboardView';
import FleetManagementView from '../components/fleets/fleet-management/FleetManagementView';
import VehicleManagementView from '../components/vehicles/vehicle-management/VehicleManagementView';
import FleetVehiclesMapView from '../components/fleets/vehicle-map/FleetVehicleMapView';
import MaintenanceAlertsView from '../components/alerts/maintenance/MaintenanceAlertsView';
import SettingsView from '../components/settings/SettingsView';

// FleetWise Components
import SignalCatalogView from '../components/fleetwise/signal-catalog/SignalCatalogView';
import VehicleModelsView from '../components/fleetwise/vehicle-models/VehicleModelsView';
import CampaignsView from '../components/fleetwise/campaigns/CampaignsView';

// New Fleet Analytics Components (to be created)
import TelemetryDashboard from '../components/analytics/TelemetryDashboard';
import DriverBehaviorView from '../components/analytics/DriverBehaviorView';
import GeofenceEventsView from '../components/analytics/GeofenceEventsView';
import TripAnalyticsView from '../components/analytics/TripAnalyticsView';

// AIOT Management Components (to be integrated)
// Device Management Components
import DeviceStatusOverview from '../components/iot/DeviceStatusOverview';
import DeviceClientList from '../components/iot/DeviceClientList';
import DeviceTopicList from '../components/iot/DeviceTopicList';
import DeviceSubscriptionList from '../components/iot/DeviceSubscriptionList';
import DeviceRetainMessageList from '../components/iot/DeviceRetainMessageList';
import DeviceRuleList from '../components/iot/DeviceRuleList';
import DeviceAlarmList from '../components/iot/DeviceAlarmList';
import DeviceLogTrace from '../components/iot/DeviceLogTrace';
import DeviceUserList from '../components/iot/DeviceUserList';
import DevicePolicyList from '../components/iot/DevicePolicyList';

// Error Components
import NotFound from '../components/common/NotFound';

const UnifiedRoutes: React.FC = () => {
  return (
    <Routes>
      {/* Default Route */}
      <Route path="/" element={<DashboardView />} />
      
      {/* Fleet Management Routes */}
      <Route path="/fleet-management" element={<FleetManagementView />} />
      <Route path="/vehicle-management" element={<VehicleManagementView />} />
      <Route path="/fleet-vehicles-map" element={<FleetVehiclesMapView />} />
      <Route path="/maintenance-alerts" element={<MaintenanceAlertsView />} />
      
      {/* Vehicle Analytics Routes */}
      <Route path="/telemetry-dashboard" element={<TelemetryDashboard />} />
      <Route path="/driver-behavior" element={<DriverBehaviorView />} />
      <Route path="/geofence-events" element={<GeofenceEventsView />} />
      <Route path="/trip-analytics" element={<TripAnalyticsView />} />
      
      {/* Device Management Routes */}
      <Route path="/iot-status-overview" element={<DeviceStatusOverview />} />
      <Route path="/iot-client-list" element={<DeviceClientList />} />
      <Route path="/iot-topic-list" element={<DeviceTopicList />} />
      <Route path="/iot-subscription-list" element={<DeviceSubscriptionList />} />
      <Route path="/iot-retain-message-list" element={<DeviceRetainMessageList />} />
      
      {/* Rules & Monitoring Routes */}
      <Route path="/iot-rule-list" element={<DeviceRuleList />} />
      <Route path="/iot-alarm-list" element={<DeviceAlarmList />} />
      <Route path="/iot-log-trace" element={<DeviceLogTrace />} />
      
      {/* AWS IoT FleetWise Routes */}
      <Route path="/signal-catalog" element={<SignalCatalogView />} />
      <Route path="/vehicle-models" element={<VehicleModelsView />} />
      <Route path="/campaigns" element={<CampaignsView />} />
      
      {/* Access Control Routes */}
      <Route path="/iot-user-list" element={<DeviceUserList />} />
      <Route path="/iot-policy-list" element={<DevicePolicyList />} />
      <Route path="/settings" element={<SettingsView />} />
      
      {/* Legacy Route Redirects */}
      <Route path="/client-list" element={<Navigate to="/iot-client-list" replace />} />
      <Route path="/topic-list" element={<Navigate to="/iot-topic-list" replace />} />
      <Route path="/subscription-list" element={<Navigate to="/iot-subscription-list" replace />} />
      <Route path="/retain-message-list" element={<Navigate to="/iot-retain-message-list" replace />} />
      <Route path="/rule-list" element={<Navigate to="/iot-rule-list" replace />} />
      <Route path="/alarm-list" element={<Navigate to="/iot-alarm-list" replace />} />
      <Route path="/log-trace" element={<Navigate to="/iot-log-trace" replace />} />
      <Route path="/user-list" element={<Navigate to="/iot-user-list" replace />} />
      <Route path="/policy-list" element={<Navigate to="/iot-policy-list" replace />} />
      
      {/* 404 Route */}
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
};

export default UnifiedRoutes;
