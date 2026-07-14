// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from "react";
import "./App.css";
import "./components/Header.css";
import { Navigate, Route, Routes, useNavigate, useLocation, useParams } from "react-router-dom";
import { VehicleProvider, useVehicle } from './contexts/VehicleContext';
import {
  Alert,
  AppLayout,
  TopNavigation,
  Spinner,
  BreadcrumbGroup,
  ButtonDropdownProps,
  SideNavigation,
  Header,
  Button,
  Box,
} from "@cloudscape-design/components";
import DashboardView from "./components/dashboard/DashboardView";
import { FleetCommandCenter } from "./components/command-center";
import { PageHeader } from "./components/common/PageHeader";
import { HelpPanelProvider, useChatAgent } from "./components/commons";
import { ChatAgent } from "./components/commons/ChatAgent";
import { CCPPanel, EscalationContextPanel, ConnectProvider, type ActiveConnectContact } from "./components/connect";
import {
  APP_TRADEMARK_NAME,
  IG_ROOT,
  IG_URLS,
  FEEDBACK_ISSUES_URL,
  UI_ROUTES,
  USER_GROUPS,
} from "./utils/constants";
import { useCreateReducer } from "./hooks/useCreateReducer";
import { initialState, insertRuntimeConfig } from "./contexts/home.state";
import { HomeContextProvider } from "./contexts/home.context";
import { useEffect, useContext } from "react";
import { getRuntimeConfig, isDemoMode } from "./config/api";
import { useAuth } from "./auth/useAuth";
import { useUserRole } from "./auth/useUserRole";
import ProtectedRoute from "./auth/ProtectedRoute";
import AgentWorkspace from "./components/connect/AgentWorkspace";
import MaintenanceAlertsView from "./components/alerts/maintenance/MaintenanceAlertsView";
import ServiceDashboard from "./components/alerts/maintenance/ServiceDashboard";
import { SafetyAlertsPage } from "./components/safety-alerts";

import FleetManagementView from "./components/fleets/fleet-management/FleetManagementView";
import { FleetDetailsPage } from "./components/fleets/fleet-management/components/FleetDetailsPage";
import VehicleManagementView from "./components/vehicles/vehicle-management/VehicleManagementView";
import { UserContext } from "./components/commons/UserContext";
import FleetVehiclesMapView from "./components/fleets/vehicle-map/FleetVehicleMapView";
import FleetSimulationView from "./components/simulation/FleetSimulationView";
import { CreateFleetPage } from "./components/fleets/create-fleet/CreateFleetPage";
import { EditFleetPage } from "./components/fleets/edit-fleet/EditFleetPage";
import { EditVehiclePage } from "./components/vehicles/edit-vehicle/EditVehiclePage";
import VehicleDetailView from "./components/vehicles/vehicle-detail/VehicleDetailView";
import TripDetailView from "./components/vehicles/trip-detail/TripDetailView";
import { AssociateVehiclesPage } from "./components/fleets/associate-vehicles/AssociateVehiclesPage";
import { I18nProvider } from "@cloudscape-design/components/i18n";
import enMessages from "@cloudscape-design/components/i18n/messages/all.en.json";
import { CreateVehiclePage } from "./components/vehicles/create-vehicle/CreateVehiclePage";
import SettingsView from "./components/settings/SettingsView";
import ProfilePage from "./components/settings/ProfilePage";
// Device Management Components
import DeviceStatusOverview from "./components/iot/DeviceStatusOverview";
import DeviceClientList from "./components/iot/DeviceClientList";
import DeviceTopicList from "./components/iot/DeviceTopicList";
import DeviceSubscriptionList from "./components/iot/DeviceSubscriptionList";
import DeviceRetainMessageList from "./components/iot/DeviceRetainMessageList";
import DeviceRuleList from "./components/iot/DeviceRuleList";
import DeviceAlarmList from "./components/iot/DeviceAlarmList";
import DeviceLogTrace from "./components/iot/DeviceLogTrace";
import DeviceUserList from "./components/iot/DeviceUserList";
import UserManagement from "./components/admin/UserManagement";
import SubscriptionManagement from "./components/admin/SubscriptionManagement";
import DevicePolicyList from "./components/iot/DevicePolicyList";
// Data Collection Components
import SignalCatalogView from "./components/data-collection/SignalCatalogView";
import VehicleModelsView from "./components/data-collection/VehicleModelsView";
import CampaignsView from "./components/data-collection/CampaignsView";
// Analytics Components
import TelemetryDashboard from "./components/analytics/TelemetryDashboard";
import DriverBehaviorView from "./components/analytics/DriverBehaviorView";
import GeofenceEventsView from "./components/analytics/GeofenceEventsView";
import TripAnalyticsView from "./components/analytics/TripAnalyticsView";
// New Navigation Components
import DriversView from "./components/drivers/DriversView";
import DriverDetailView from "./components/drivers/DriverDetailView";
import ChargingView from "./components/charging/ChargingView";
import WarrantyView from "./components/warranty/WarrantyView";
import SystemMonitoringView from "./components/system-monitoring/SystemMonitoringView";
import AnalyticsView from "./components/analytics/AnalyticsView";
import DataProcessingView from "./components/data-processing/DataProcessingView";
import { FleetCostDashboard, VehicleCostTab, ActionQueue } from "./components/tco";
import { FleetRebalancingDashboard, RebalanceActionQueue } from "./components/rebalancing";
import { DocumentBrowser } from "./components/documents";
import { RecallDashboard, WarrantyPage } from "./components/recall-warranty";
// Common Components
import NotFound from "./components/common/NotFound";

// Engineer persona — Product Digital Thread closed loop
import EngineerInsightsView from "./components/engineering/EngineerInsightsView";
import InvestigationWorkspace from "./components/engineering/InvestigationWorkspace";
import DesignOptionsView from "./components/engineering/DesignOptionsView";

import DigitalThreadView from "./components/digital-thread/DigitalThreadView";

import { authFetch } from 'utils/authFetch';

// Wrapper to extract fleetId from URL params and pass to FleetDetailsPage
function FleetDetailsWrapper() {
  const { fleetId } = useParams();
  return <FleetDetailsPage fleetId={fleetId} />;
}

// Reactive PageHeader component that responds to vehicle context changes
const ReactivePageHeader = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { vehicleVin, driverName } = useVehicle();
  
  // Handle trip detail pages (must be before vehicle detail check)
  if (location.pathname.match(/\/vehicles\/management\/[^/]+\/trips\/[^/]+/)) {
    const pathSegments = location.pathname.replace('/vehicles/management/', '').split('/');
    const vehicleId = pathSegments[0];
    const tripId = pathSegments[2];
    const displayName = vehicleVin || vehicleId;
    
    return (
      <PageHeader
        title="Trip Details"
        description={`Trip ${tripId}`}
        breadcrumbs={[
          { text: 'Home', href: '/' },
          { text: 'Vehicle Management', href: UI_ROUTES.VEHICLE_MANAGEMENT },
          { text: displayName, href: `/vehicles/management/${vehicleId}` },
          { text: 'Trip Details' }
        ]}
        buttons={[
          {
            text: 'Back to Vehicle',
            iconName: 'arrow-left',
            onClick: () => navigate(`/vehicles/management/${vehicleId}`)
          }
        ]}
        onBreadcrumbFollow={(e) => {
          e.preventDefault();
          if (e.detail.href) {
            navigate(e.detail.href);
          }
        }}
      />
    );
  }

  // Handle vehicle detail pages
  if (location.pathname.startsWith('/vehicles/management/') && location.pathname !== '/vehicles/management') {
    const pathSegments = location.pathname.replace('/vehicles/management/', '').split('/');
    const vehicleId = pathSegments[0];
    console.log('🚗 App.tsx - vehicleId:', vehicleId, 'vehicleVin:', vehicleVin);
    const displayName = vehicleVin || vehicleId;
    
    return (
      <PageHeader
        title={`Vehicle Details - ${displayName}`}
        description="View detailed information about this vehicle including status, trips, and telemetry data."
        breadcrumbs={[
          { text: 'Home', href: '/' },
          { text: 'Vehicle Management', href: UI_ROUTES.VEHICLE_MANAGEMENT },
          { text: displayName || 'Vehicle Details' }
        ]}
        buttons={[
          { 
            text: 'Edit Vehicle', 
            iconName: 'edit',
            onClick: () => navigate(`${UI_ROUTES.VEHICLE_EDIT}?vehicleId=${vehicleId}`)
          },
          { 
            text: 'View Trips', 
            iconName: 'view-horizontal',
            onClick: () => navigate(`/vehicles/management/${vehicleId}?tab=trips`)
          },
          { 
            text: 'Delete Vehicle', 
            iconName: 'remove',
            onClick: () => console.log('Delete vehicle:', vehicleId)
          }
        ]}
        onBreadcrumbFollow={(e) => {
          e.preventDefault();
          if (e.detail.href) {
            navigate(e.detail.href);
          }
        }}
      />
    );
  }
  
  // Handle driver detail pages
  if (location.pathname.startsWith('/drivers/')) {
    const driverId = location.pathname.split('/')[2];
    console.log('👤 App.tsx - driverId:', driverId, 'driverName:', driverName);
    const displayName = driverName || driverId;
    
    return (
      <PageHeader
        title="Driver Details"
        description={`View detailed information, trip history, and safety events for driver ${displayName}.`}
        breadcrumbs={[
          { text: 'Home', href: '/' },
          { text: 'Driver Management', href: '/drivers' },
          { text: displayName || 'Driver Details' }
        ]}
        buttons={[]}
        onBreadcrumbFollow={(e) => {
          e.preventDefault();
          if (e.detail.href) {
            navigate(e.detail.href);
          }
        }}
      />
    );
  }
  
  // For non-vehicle/driver pages, return null
  return null;
};

// Reads vehicleVin from VehicleContext (must render inside VehicleProvider).
const ChatAgentWithVin: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const { vehicleVin } = useVehicle();
  return <ChatAgent onClose={onClose} vin={vehicleVin ?? undefined} />;
};

function App({ runtimeConfig = getRuntimeConfig() }: Record<string, any>) {
  const initStateWithConfig = insertRuntimeConfig(initialState, runtimeConfig);
  const openChatModal = () => {
    setChatModalOpen(true);
    setTimeout(() => setChatModalAnimating(true), 10);
  };

  const closeChatModal = () => {
    setChatModalAnimating(false);
    setTimeout(() => setChatModalOpen(false), 200);
  };

  const toggleChatModal = () => {
    if (chatModalOpen) {
      closeChatModal();
    } else {
      openChatModal();
    }
  };

  const [navigationOpen, setNavigationOpen] = useState(true);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [toolsContent, setToolsContent] = useState<React.ReactNode>(null);
  const [chatModalOpen, setChatModalOpen] = useState(false);
  const [chatModalAnimating, setChatModalAnimating] = useState(false);
  
  // Hidden session clearing function (Easter Egg)
  // Double-click on the app title in the top navigation to clear chat session
  const clearChatSession = () => {
    sessionStorage.removeItem('chatSessionId');
    console.log('🧹 Chat session cleared via easter egg');
    
    // Show a subtle visual feedback
    const header = document.querySelector('#h');
    if (header) {
      header.style.transition = 'background-color 0.3s ease';
      header.style.backgroundColor = '#e8f5e8';
      setTimeout(() => {
        header.style.backgroundColor = '';
      }, 500);
    }
  };
  
  // Check if we're in local development mode
  const isLocalDemo = import.meta.env.VITE_LOCAL_DEMO === 'true' || 
                      import.meta.env.VITE_BYPASS_AUTH === 'true' || 
                      isDemoMode();
  
  // Use the new authentication hook
  const auth = useAuth();
  const { isAdmin, isOperator, isConnectAgent, isEngineer, canWrite } = useUserRole();
  
  const navigate = useNavigate();
  const location = useLocation();

  // Close tools panel on route change
  useEffect(() => {
    setToolsOpen(false);
    setToolsContent(null);
    console.log('Route changed, tools reset');
  }, [location.pathname]);

  const uc = useContext(UserContext);

  useEffect(() => {
    uc.theme.applyInitialTheme();
    uc.demoMode.setIsDemoMode(isDemoMode());
  }, []);

  const initState = sessionStorage.getItem("init-state")
    ? JSON.parse(sessionStorage.getItem("init-state")!)
    : initStateWithConfig;

  // Extract AWS credentials configuration from runtime config
  const awsCredentials = runtimeConfig.awsCredentials && 
                        runtimeConfig.awsCredentials.identityPoolId && 
                        runtimeConfig.awsCredentials.identityPoolId !== 'test' ? {
    identityPoolId: runtimeConfig.awsCredentials.identityPoolId,
    userPoolId: runtimeConfig.awsCredentials.userPoolId,
    region: runtimeConfig.awsCredentials.region || runtimeConfig.awsRegion,
  } : undefined;

  // Update API config with AWS credentials
  const apiConfig = {
    baseUrl: runtimeConfig.apiEndpoint,
    isDemoMode: runtimeConfig.isDemoMode,
    awsCredentials,
  };

  const contextValue = useCreateReducer({
    initialState: initState,
  });

  //must be here to make breadcrumbs work
  useNavigate();

  useEffect(() => {
    // Skip authentication checks in local demo mode
    if (isLocalDemo) return;
    
    // Auto-login if not authenticated and not currently logging in
    if (!auth.isLoading && !auth.isAuthenticated && !auth.error) {
      auth.login();
    }
  }, [auth.isLoading, auth.isAuthenticated, auth.error, auth.login, isLocalDemo]);

  const onSignout = async () => {
    console.log('🚪 App.tsx onSignout called');
    sessionStorage.removeItem("init-state");
    localStorage.removeItem("Preferences");
    console.log('🚪 Calling auth.logout()');
    auth.logout();
  };

  console.log('🏗️ App.tsx is rendering');

  const profileActions: ButtonDropdownProps.Items = [
    // { id: 'profile', text: 'Profile' },
    { id: "preferences", text: "Preferences" },
    { id: "switchTheme", text: "Switch Theme" },
    // { id: 'security', text: 'Security' },
    {
      id: "support-group",
      text: "Support",
      items: [
        {
          id: "documentation",
          text: "Documentation",
          href: IG_ROOT,
          external: true,
          externalIconAriaLabel: " (opens in new tab)",
        },
        {
          id: "feedback",
          text: "Feedback",
          href: FEEDBACK_ISSUES_URL,
          external: true,
          externalIconAriaLabel: " (opens in new tab)",
        },
        {
          id: "support",
          text: "Customer support",
          href: IG_URLS.SUPPORT,
          external: true,
          externalIconAriaLabel: " (opens in new tab)",
        },
      ],
    },
    {
      id: "signout",
      text: "Sign out",
      ariaLabel: "Sign out",
      iconName: "lock-private",
    },
  ];

  const onProfileFollow = (
    event: CustomEvent<ButtonDropdownProps.ItemClickDetails>,
  ) => {
    console.log('🔘 onProfileFollow called with:', event.detail);
    if (event.detail.id === "signout") {
      console.log('🚪 Sign out clicked');
      onSignout();
    }
    if (event.detail.id === "switchTheme") {
      uc.theme.switchThemeMode();
    }
  };

  return (
    <>
      <ConnectProvider>
      <HomeContextProvider
        value={{
          ...contextValue,
        }}
      >
        <VehicleProvider>
          {(isLocalDemo || auth.isAuthenticated) ? (
          <I18nProvider locale="en" messages={[enMessages]}>
            <style>
              {`
                /* Global navigation and header styling */
                nav[aria-label="Side navigation"] {
                  padding-top: 20px !important;
                }
                [class*="awsui_navigation-panel"] {
                }
                [class*="awsui_hide-navigation"] {
                  top: 56px !important;
                  position: absolute !important;
                  right: 16px !important;
                  z-index: 1001 !important;
                }
                [class*="awsui_show-navigation"] {
                  top: 56px !important;
                  left: 16px !important;
                  position: fixed !important;
                  z-index: 1001 !important;
                }
                .awsui_app-layout [class*="awsui_navigation"] {
                  background-color: transparent;
                }
                #b [class*="awsui_header-wrapper"],
                #b div[class*="header-wrapper"],
                #b [class*="header-wrapper"],
                div[class*=scrolling-background], 
                #b [class*=awsui_scrolling-background] {
                  border: none !important;
                  border-color: #161d26 !important;
                }
                /* Layout and container consolidation */
                [class*="awsui_layout"]:not(#\\9 ) {
                  --awsui-default-max-content-width: 100vw !important;
                  --awsui-max-content-width: 100vw !important;
                  --awsui-breadcrumbs-gap: 0px;
                  --awsui-content-gap-left: 0px;
                  --awsui-content-gap-right: 0px;
                }
                [class*="awsui_background"],
                [class*="awsui_scrolling-background"] {
                  padding: 0;
                  margin: 0;
                }
                [class*="awsui_container"] {
                  width: 100%;
                  box-sizing: border-box;
                  max-width: 100%;
                  background-color: var(--color-background-container-content-6u8rvp, #ffffff);
                  border-radius: 8px;
                  position: relative;
                  z-index: 2;
                }
                span[class*="awsui_container"] {
                  padding: 0;
                  width: auto;
                  max-width: none;
                  background-color: transparent;
                  border-radius: 0;
                  position: static;
                  z-index: auto;
                }
                [class*="awsui_layout_hyvsj"] {
                  margin-top: 0px;
                  --awsui-main-gap-y15yd5: 0px !important;
                  --awsui-header-gap-y15yd5: 0px !important;
                  --awsui-breadcrumbs-gap-y15yd5: 0px !important;
                }
                /* Page header and breadcrumb styling - moved to Header.css */
                .full-width-dashboard * {
                  max-width: none;
                }
              `}
            </style>
            <header id="h">
              {/* Easter Egg: Double-click on app title to clear chat session */}
              <div 
                onDoubleClick={clearChatSession}
                style={{ cursor: 'pointer' }}
              >
                <TopNavigation
                  identity={{
                    href: '/',
                    title: APP_TRADEMARK_NAME,
                  }}
                utilities={[
                  {
                    type: 'button',
                    iconName: 'settings',
                    ariaLabel: 'Settings',
                    title: 'Settings',
                    onClick: () => navigate(UI_ROUTES.SETTINGS)
                  },
                  {
                    type: 'menu-dropdown',
                    text: auth.user?.email || auth.user?.username || 'Demo User',
                    iconName: 'user-profile',
                    items: [
                      { 
                        id: 'profile', 
                        text: 'Profile',
                        iconName: 'user-profile'
                      },
                      { 
                        id: 'preferences', 
                        text: 'Preferences',
                        iconName: 'settings'
                      },
                      { 
                        id: 'switchTheme', 
                        text: 'Switch Theme',
                        iconName: 'view-full'
                      },
                      { type: 'divider' },
                      {
                        id: 'support-group',
                        text: 'Support',
                        iconName: 'contact',
                        items: [
                          {
                            id: 'documentation',
                            text: 'Documentation',
                            href: 'https://docs.aws.amazon.com/guidance/latest/connected-mobility-on-aws/solution-overview.html',
                            external: true,
                            externalIconAriaLabel: ' (opens in new tab)',
                            iconName: 'file'
                          },
                          {
                            id: 'feedback',
                            text: 'Feedback',
                            href: 'https://github.com/aws-solutions/connected-mobility-solution-on-aws/issues',
                            external: true,
                            externalIconAriaLabel: ' (opens in new tab)',
                            iconName: 'feedback'
                          },
                          {
                            id: 'support',
                            text: 'Customer support',
                            href: 'https://aws.amazon.com/support/',
                            external: true,
                            externalIconAriaLabel: ' (opens in new tab)',
                            iconName: 'call'
                          },
                        ],
                      },
                      { type: 'divider' },
                      {
                        id: 'signout',
                        text: 'Sign out',
                        ariaLabel: 'Sign out',
                        iconName: 'lock-private',
                      },
                    ],
                    onItemClick: (event) => {
                      if (event.detail.id === 'signout') {
                        auth.logout();
                      }
                      if (event.detail.id === 'switchTheme') {
                        uc.theme.switchThemeMode();
                      }
                      if (event.detail.id === 'profile') {
                        navigate(UI_ROUTES.PROFILE);
                      }
                      if (event.detail.id === 'preferences') {
                        navigate(UI_ROUTES.SETTINGS);
                      }
                    }
                  },
                ]}
              />
              </div>
            </header>
            <div style={{ height: '56px' }}></div>
            <AppLayout
              contentType={(() => {
                const pathname = location.pathname;
                if (pathname.includes('/devices/') || pathname.includes('/signal-catalog') || pathname.includes('/vehicle-models') || pathname.includes('/campaigns')) return 'table';
                if (pathname.includes('/edit') || pathname.includes('/create') || pathname === '/settings') return 'form';
                if (pathname === '/' || pathname.includes('/dashboard') || pathname.includes('/telemetry') || pathname.includes('/analytics') || pathname.includes('/charging') || pathname.includes('/fleets/management') || pathname === '/vehicles/management' || pathname === '/drivers' || pathname.includes('/alerts') || pathname.includes('/simulation')) return 'dashboard';
                return 'default';
              })()}
              headerSelector="#h"
              stickyNotifications
              toolsHide={!toolsContent}
              toolsOpen={!!toolsContent && toolsOpen}
              toolsWidth={toolsContent ? 500 : undefined}
              onToolsChange={({ detail }) => {
                if (!detail.open) {
                  setToolsContent(null);
                }
                setToolsOpen(detail.open);
              }}
              tools={toolsContent}
              navigationOpen={navigationOpen}
              onNavigationChange={({ detail }) => setNavigationOpen(detail.open)}
              navigationWidth={280}
              navigation={
                <SideNavigation
                  activeHref={location.pathname + location.search}
                  header={{ href: '/', text: 'Connected Mobility Intelligence' }}
                  ariaLabels={{
                    navigationClose: 'Close navigation',
                    navigationToggle: 'Toggle navigation'
                  }}
                  onFollow={(event) => {
                    if (!event.detail.external) {
                      event.preventDefault();
                      navigate(event.detail.href);
                    }
                  }}
                  items={
                    // Pure engineer (not also admin/operator): show a focused
                    // engineering workflow nav. Fleet/vehicle/driver context
                    // is preserved (they still need to inspect the fleet) but
                    // operator surfaces (Service, Safety, Warranty, Costs,
                    // Charging, Rebalancing, Subscriptions) are hidden as
                    // they're not part of the engineering workflow.
                    isEngineer && !isOperator && !isAdmin
                      ? [
                          { type: 'link' as const, text: 'Fleets', href: UI_ROUTES.FLEET_MANAGEMENT },
                          { type: 'link' as const, text: 'Vehicles', href: UI_ROUTES.VEHICLE_MANAGEMENT },
                          { type: 'link' as const, text: 'Vehicle Map', href: `${UI_ROUTES.VEHICLE_MANAGEMENT}?view=map` },
                          { type: 'link' as const, text: 'Drivers', href: '/drivers' },
                          { type: 'divider' as const },
                          { type: 'link' as const, text: 'Engineering Insights', href: UI_ROUTES.ENGINEERING_INSIGHTS },
                          { type: 'link' as const, text: 'Digital Thread', href: UI_ROUTES.DIGITAL_THREAD, info: <Box color="text-status-info" fontSize="body-s">↗ PLM</Box> },
                          { type: 'divider' as const },
                          { type: 'link' as const, text: 'Data Processing', href: UI_ROUTES.DATA_PROCESSING },
                          { type: 'divider' as const },
                          { type: 'link' as const, text: 'Settings', href: UI_ROUTES.SETTINGS },
                        ]
                      : [
                          { type: 'link' as const, text: 'Vehicles', href: UI_ROUTES.VEHICLE_MANAGEMENT },
                          { type: 'link' as const, text: 'Vehicle Map', href: `${UI_ROUTES.VEHICLE_MANAGEMENT}?view=map` },
                          ...(!isConnectAgent || isAdmin || canWrite ? [
                            { type: 'link' as const, text: 'Fleets', href: UI_ROUTES.FLEET_MANAGEMENT },
                            { type: 'link' as const, text: 'Drivers', href: '/drivers' },
                            { type: 'link' as const, text: 'Service', href: UI_ROUTES.ALERTS_MAINTENANCE },
                            { type: 'link' as const, text: 'Safety', href: UI_ROUTES.ALERTS_SAFETY },
                            { type: 'link' as const, text: 'Warranty', href: '/warranty' },
                            { type: 'link' as const, text: 'Costs', href: '/fleet-costs' },
                            // Charging sits next to Rebalancing because they're
                            // both fleet-utilisation tools an operator opens
                            // together (e.g. "shift this EV to a depot with
                            // charger headroom"). Group with the rebalancing
                            // item rather than the per-vehicle items above.
                            { type: 'link' as const, text: 'Charging', href: '/charging' },
                            { type: 'link' as const, text: 'Rebalancing', href: '/fleet-rebalancing' },
                          ] : []),
                          ...(canWrite ? [{ type: 'divider' as const }, { type: 'link' as const, text: 'Subscriptions', href: '/subscriptions' }] : []),
                          ...(isAdmin ? [
                            { type: 'divider' as const },
                            { type: 'link' as const, text: 'User Management', href: '/admin/users' },
                            { type: 'link' as const, text: 'Simulation', href: UI_ROUTES.FLEET_SIMULATION },
                            { type: 'link' as const, text: 'Data Processing', href: UI_ROUTES.DATA_PROCESSING },
                            { type: 'link' as const, text: 'System Monitoring', href: '/system-monitoring' },
                            { type: 'link' as const, text: 'Documents', href: '/documents' },
                          ] : []),
                          ...((isConnectAgent || isAdmin) ? [
                            { type: 'divider' as const },
                            { type: 'link' as const, text: 'Agent Workspace', href: UI_ROUTES.AGENT_WORKSPACE },
                          ] : []),
                          ...((isEngineer || isAdmin) ? [
                            { type: 'divider' as const },
                            { type: 'link' as const, text: 'Engineering Insights', href: UI_ROUTES.ENGINEERING_INSIGHTS },
                            { type: 'link' as const, text: 'Digital Thread', href: UI_ROUTES.DIGITAL_THREAD, info: <Box color="text-status-info" fontSize="body-s">↗ PLM</Box> },
                          ] : []),
                          { type: 'divider' as const },
                          { type: 'link' as const, text: 'Settings', href: UI_ROUTES.SETTINGS },
                        ]
                  }
                />
              }
              content={
                <div>
                  {(() => {
                    const getPageConfig = (pathname: string) => {
                      switch (pathname) {
                        case '/':
                          return {
                            title: 'Fleet Command Center',
                            description: 'Virtual Fleet Operator — unified intelligence across cost, rebalancing, recall, and maintenance domains',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Fleet Command Center'}
                            ],
                            buttons: [
                              { text: 'All Fleets', iconName: 'group-active' },
                              { text: 'Map View', iconName: 'view-horizontal', onClick: () => navigate(UI_ROUTES.FLEET_VEHICLES_MAP) },
                              ...(canWrite ? [{ text: 'Manage Fleets', iconName: 'settings', variant: 'primary' as const, onClick: () => navigate(UI_ROUTES.FLEET_MANAGEMENT) }] : [])
                            ]
                          };
                        case UI_ROUTES.VEHICLE_MANAGEMENT:
                          const isMapView = new URLSearchParams(location.search).get('view') === 'map';
                          return {
                            title: isMapView ? 'Vehicle Map' : 'Vehicle Management',
                            description: isMapView ? 'View all vehicles on the map.' : 'Manage your vehicle fleet, monitor vehicle status, and configure device settings for optimal performance.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Vehicle Management', href: UI_ROUTES.VEHICLE_MANAGEMENT }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: isMapView ? 'Table View' : 'Map View', iconName: 'view-full', onClick: () => navigate(isMapView ? UI_ROUTES.VEHICLE_MANAGEMENT : `${UI_ROUTES.VEHICLE_MANAGEMENT}?view=map`) },
                              ...(canWrite && !isMapView ? [{ text: 'Create Vehicle', variant: 'primary' as const, onClick: () => navigate(UI_ROUTES.VEHICLE_CREATE) }] : [])
                            ]
                          };
                        case '/drivers':
                          return {
                            title: 'Driver Management',
                            description: 'Manage drivers, track performance, and monitor safety metrics across your fleet.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Driver Management', href: '/drivers' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              ...(canWrite ? [{ text: 'Add Driver', variant: 'primary' as const, onClick: () => navigate('/drivers/create') }] : [])
                            ]
                          };
                        case '/charging':
                          return {
                            title: 'Charging Management',
                            description: 'Monitor charging stations, track charging sessions, and manage charging infrastructure across your fleet.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Charging Management', href: '/charging' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Add Station', variant: 'primary' as const, onClick: () => navigate('/charging/create') }
                            ]
                          };
                        case '/warranty':
                        case '/recall-warranty':
                          return {
                            title: 'Warranty Management',
                            description: 'Warranty claim automation, coverage tracking, and financial recovery — agent-detected eligible failures with telemetry evidence',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Warranty', href: '/warranty' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Export Recovery Report', iconName: 'download' },
                            ]
                          };
                        case '/documents':
                          return {
                            title: 'Knowledge Base Documents',
                            description: 'Service invoices, warranty claims, fleet operations playbooks, and fleet context documents',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Documents', href: '/documents' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                            ]
                          };
                        case '/fleet-costs':
                          return {
                            title: 'Cost Intelligence',
                            description: 'Total cost of ownership analytics, agentic cost optimization, and fleet spend intelligence — Munich Operations | 100 vehicles | March 2026',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Costs', href: '/fleet-costs' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Export Report', iconName: 'download' },
                              { text: 'Upload Cost Data', variant: 'primary' as const, onClick: () => navigate('/fleet-costs/upload') }
                            ]
                          };
                        case '/fleet-costs/actions':
                          return {
                            title: 'Cost Agent Recommendations',
                            description: '4 pending approval | 1 auto-approved | Agentic cost optimization pipeline — Monitor → Diagnose → Recommend → Learn',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Costs', href: '/fleet-costs' },
                              { text: 'Agent Recommendations' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Configure Auto-Approval', iconName: 'settings', onClick: () => {} }
                            ]
                          };
                        case '/fleet-rebalancing':
                          return {
                            title: 'Fleet Rebalancing',
                            description: 'Real-time utilization monitoring, demand forecasting, and agent-recommended vehicle moves — Munich Operations | 8 locations | March 2026',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Rebalancing', href: '/fleet-rebalancing' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Export Report', iconName: 'download' },
                            ]
                          };
                        case '/fleet-rebalancing/actions':
                          return {
                            title: 'Rebalance Recommendations',
                            description: 'Agent-recommended vehicle moves with transfer cost, revenue impact, and confidence scores',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Rebalancing', href: '/fleet-rebalancing' },
                              { text: 'Recommendations' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Configure Auto-Approval', iconName: 'settings', onClick: () => {} }
                            ]
                          };
                        case '/admin/users':
                          return {
                            title: 'User Management',
                            description: 'Create and manage users, assign roles, and control fleet access for operators and companion app users.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'User Management' }
                            ],
                            buttons: [
                            ]
                          };
                        case '/system-monitoring':
                          return {
                            title: 'System Monitoring',
                            description: 'Monitor IoT device connectivity, system health, and real-time diagnostics across your fleet infrastructure.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'System Monitoring', href: '/system-monitoring' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Export Logs', variant: 'primary' as const }
                            ]
                          };
                        case UI_ROUTES.DATA_PROCESSING:
                        case '/signal-catalog':
                          return {
                            title: 'Signal & Event Catalog',
                            description: 'Browse the full signal catalog, event definitions, and data transformation configurations for fleet telemetry processing.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Data Processing', href: UI_ROUTES.DATA_PROCESSING }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' }
                            ]
                          };
                        case '/analytics':
                          return {
                            title: 'Analytics & Reports',
                            description: 'Analyze fleet performance, driver behavior, and operational metrics with comprehensive reporting and insights.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Analytics & Reports', href: '/analytics' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Export Report', variant: 'primary' as const }
                            ]
                          };
                        case UI_ROUTES.VEHICLE_CREATE:
                          return {
                            title: 'Create Vehicle',
                            description: 'Add a new vehicle to your fleet with device configuration and IoT certificate generation.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Vehicle Management', href: UI_ROUTES.VEHICLE_MANAGEMENT },
                              { text: 'Create Vehicle' }
                            ],
                            buttons: [
                              { text: 'Cancel', onClick: () => navigate(UI_ROUTES.VEHICLE_MANAGEMENT) },
                              { text: 'Save Vehicle', variant: 'primary' as const }
                            ]
                          };
                        case UI_ROUTES.VEHICLE_EDIT:
                          const editVehicleId = new URLSearchParams(window.location.search).get('vehicleId');
                          return {
                            title: `Edit Vehicle - ${editVehicleId}`,
                            description: 'Edit vehicle details and save changes.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Vehicle Management', href: UI_ROUTES.VEHICLE_MANAGEMENT },
                              { text: editVehicleId || 'Edit Vehicle' }
                            ],
                            buttons: [
                              { text: 'Cancel', onClick: () => navigate(UI_ROUTES.VEHICLE_MANAGEMENT) }
                            ]
                          };
                        case UI_ROUTES.FLEET_MANAGEMENT:
                          return {
                            title: 'Fleet Management',
                            description: 'Manage your fleet configurations, create new fleets, and monitor fleet performance.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Fleet Management', href: UI_ROUTES.FLEET_MANAGEMENT }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Create Fleet', variant: 'primary' as const, onClick: () => navigate(UI_ROUTES.FLEET_CREATE) }
                            ]
                          };
                        case UI_ROUTES.FLEET_VEHICLES_MAP:
                          return {
                            title: 'Fleet Map View',
                            description: 'Real-time map view of your fleet vehicles with location tracking and status monitoring.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Fleet Map', href: UI_ROUTES.FLEET_VEHICLES_MAP }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Full Screen', iconName: 'view-full' }
                            ]
                          };
                        case UI_ROUTES.FLEET_SIMULATION:
                          return {
                            title: 'Fleet Simulation',
                            description: 'Generate realistic fleet telemetry data to test and demonstrate your fleet management system.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Fleet Simulation', href: UI_ROUTES.FLEET_SIMULATION }
                            ],
                            buttons: []
                          };
                        case UI_ROUTES.TELEMETRY_DASHBOARD:
                          return {
                            title: 'Telemetry Dashboard',
                            description: 'Monitor real-time telemetry data from your connected vehicles.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Analytics', href: '#' },
                              { text: 'Telemetry Dashboard', href: UI_ROUTES.TELEMETRY_DASHBOARD }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Export Data', iconName: 'download' }
                            ]
                          };
                        case UI_ROUTES.DRIVER_BEHAVIOR:
                          return {
                            title: 'Driver Behavior Analytics',
                            description: 'Analyze driver behavior patterns and safety metrics across your fleet.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Analytics', href: '#' },
                              { text: 'Driver Behavior', href: UI_ROUTES.DRIVER_BEHAVIOR }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Generate Report', iconName: 'file' }
                            ]
                          };
                        case UI_ROUTES.SETTINGS:
                          return {
                            title: 'Settings',
                            description: 'Configure application settings and preferences.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Settings', href: UI_ROUTES.SETTINGS }
                            ],
                            buttons: [
                              { text: 'Save Changes', variant: 'primary' as const },
                              { text: 'Reset to Defaults', iconName: 'refresh' }
                            ]
                          };
                        case UI_ROUTES.PROFILE:
                          return {
                            title: 'Profile',
                            description: 'View your account details and preferences.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Profile', href: UI_ROUTES.PROFILE }
                            ],
                            buttons: []
                          };
                        case UI_ROUTES.ALERTS_SAFETY:
                          return {
                            title: 'Safety Management',
                            description: 'Monitor driver behavior, track safety incidents, and manage safety compliance across your fleet.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Safety Management', href: UI_ROUTES.ALERTS_SAFETY }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Create Alert', variant: 'primary' as const }
                            ]
                          };
                        case UI_ROUTES.ALERTS_MAINTENANCE:
                          return {
                            title: 'Service',
                            description: 'Maintenance alerts from vehicle telemetry, NHTSA recall tracking, and service scheduling — real-time fleet health monitoring',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Service', href: UI_ROUTES.ALERTS_MAINTENANCE }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                            ]
                          };
                        case UI_ROUTES.ENGINEERING_INSIGHTS:
                          return {
                            title: 'Engineering Insights',
                            description: 'Cohort-level signals surfaced from production telemetry. Click an anomaly to engage the Product Engineering Agent in the Investigation Workspace.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Engineering Insights', href: UI_ROUTES.ENGINEERING_INSIGHTS }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' }
                            ]
                          };
                        case UI_ROUTES.DIGITAL_THREAD:
                          return {
                            title: 'Digital Thread',
                            description: 'Acme Motors PLM digital-thread destination. Receives design-change updates from the CMS Product Engineering Agent for engineering review and approval.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Engineering Insights', href: UI_ROUTES.ENGINEERING_INSIGHTS },
                              { text: 'Digital Thread', href: UI_ROUTES.DIGITAL_THREAD }
                            ],
                            buttons: [
                              { text: 'Return to Insights', iconName: 'arrow-left', onClick: () => navigate(UI_ROUTES.ENGINEERING_INSIGHTS) }
                            ]
                          };
                        default:
                          // Investigation Workspace — /engineering/investigate or /engineering/investigate/:caseId
                          if (pathname.startsWith('/engineering/investigate')) {
                            const parts = pathname.split('/');
                            const caseId = parts[3];
                            return {
                              title: 'Investigation Workspace',
                              description: caseId
                                ? `Investigating anomaly ${caseId} with the Product Engineering Agent. The agent traces telemetry, manufacturing context, supplier datasheets, and engineering specs to synthesize a root cause and propose design options.`
                                : 'Engaging the Product Engineering Agent to investigate signals from production telemetry.',
                              breadcrumbs: [
                                { text: 'Home', href: '/' },
                                { text: 'Engineering Insights', href: UI_ROUTES.ENGINEERING_INSIGHTS },
                                { text: 'Investigation Workspace' }
                              ],
                              buttons: [
                                { text: 'Back to Insights', iconName: 'arrow-left', onClick: () => navigate(UI_ROUTES.ENGINEERING_INSIGHTS) }
                              ]
                            };
                          }

                          // Design Options view — /engineering/design-options or /engineering/design-options/:caseId
                          if (pathname.startsWith('/engineering/design-options')) {
                            const parts = pathname.split('/');
                            const caseId = parts[3];
                            const investigationHref = caseId
                              ? `${UI_ROUTES.ENGINEERING_INVESTIGATE}/${caseId}`
                              : UI_ROUTES.ENGINEERING_INVESTIGATE;
                            return {
                              title: 'Design Options',
                              description: 'Design options generated by the Product Engineering Agent. Review projected impact, BOM cost, and qualification lead time. Accept an option to push the proposed change to the Digital Thread for engineering review.',
                              breadcrumbs: [
                                { text: 'Home', href: '/' },
                                { text: 'Engineering Insights', href: UI_ROUTES.ENGINEERING_INSIGHTS },
                                { text: 'Investigation', href: investigationHref },
                                { text: 'Design Options' }
                              ],
                              buttons: [
                                { text: 'Back to Investigation', iconName: 'arrow-left', onClick: () => navigate(investigationHref) }
                              ]
                            };
                          }

                          // Handle driver detail routes
                          if (pathname.startsWith('/drivers/')) {
                            const driverId = pathname.split('/')[2];
                            return {
                              title: 'Driver Details',
                              description: `View detailed information, trip history, and safety events for driver ${driverId}.`,
                              breadcrumbs: [
                                { text: 'Home', href: '/' },
                                { text: 'Driver Management', href: '/drivers' },
                                { text: 'Driver Details', href: pathname }
                              ],
                              buttons: [
                                { text: 'Back to Drivers', iconName: 'arrow-left', onClick: () => navigate('/drivers') }
                              ]
                            };
                          }
                          
                          // Handle dynamic routes
                          if (pathname.match(/\/vehicles\/management\/[^\/]+\/trips\/[^\/]+/)) {
                            const pathParts = pathname.split('/');
                            const vehicleId = pathParts[3];
                            const tripId = pathParts[5];
                            return {
                              title: `Trip Summary - ${tripId}`,
                              description: 'View detailed trip information including route, telemetry data, and performance metrics.',
                              breadcrumbs: [
                                { text: 'Home', href: '/' },
                                { text: 'Vehicle Management', href: UI_ROUTES.VEHICLE_MANAGEMENT },
                                { text: vehicleId, href: `/vehicles/management/${vehicleId}` },
                                { text: `Trip ${tripId}` }
                              ],
                              buttons: [
                                { text: 'View Route', iconName: 'view-horizontal' },
                                { text: 'Export Data', iconName: 'download' },
                                { text: 'Back to Vehicle', variant: 'primary' as const }
                              ]
                            };
                          }
                          
                          if (pathname.startsWith('/vehicles/management/') && pathname !== '/vehicles/management') {
                            const vehicleId = pathname.replace('/vehicles/management/', '').split('/')[0];
                            const { vehicleVin } = useVehicle();
                            console.log('🚗 App.tsx - vehicleId:', vehicleId, 'vehicleVin:', vehicleVin);
                            const displayName = vehicleVin || vehicleId;
                            
                            return {
                              title: `Vehicle Details - ${displayName}`,
                              description: 'View detailed information about this vehicle including status, trips, and telemetry data.',
                              breadcrumbs: [
                                { text: 'Home', href: '/' },
                                { text: 'Vehicle Management', href: UI_ROUTES.VEHICLE_MANAGEMENT },
                                { text: displayName || 'Vehicle Details' }
                              ],
                              buttons: [
                                { 
                                  text: 'Edit Vehicle', 
                                  iconName: 'edit',
                                  onClick: () => navigate(`${UI_ROUTES.VEHICLE_EDIT}?vehicleId=${vehicleId}`)
                                },
                                { 
                                  text: 'View Trips', 
                                  iconName: 'view-horizontal',
                                  onClick: () => {
                                    // Navigate to trips tab or trips view
                                    const currentUrl = window.location.pathname;
                                    if (currentUrl.includes('/vehicles/management/')) {
                                      // Already on vehicle detail page, could scroll to trips section
                                      const tripsSection = document.querySelector('[data-testid="trips-tab"]');
                                      if (tripsSection) {
                                        tripsSection.scrollIntoView({ behavior: 'smooth' });
                                      }
                                    }
                                  }
                                },
                                { 
                                  text: 'Delete Vehicle', 
                                  variant: 'primary' as const,
                                  onClick: () => {
                                    if (confirm(`Are you sure you want to delete vehicle ${vehicleId}? This action cannot be undone.`)) {
                                      authFetch(`${getRuntimeConfig().apiEndpoint}api/v1/vehicles/${vehicleId}`, {
                                        method: 'DELETE'
                                      }).then(response => {
                                        if (response.ok) {
                                          navigate(UI_ROUTES.VEHICLE_MANAGEMENT);
                                        } else {
                                          alert('Failed to delete vehicle');
                                        }
                                      }).catch(error => {
                                        console.error('Error deleting vehicle:', error);
                                        alert('Failed to delete vehicle');
                                      });
                                    }
                                  }
                                }
                              ]
                            };
                          }
                          
                          if (pathname.startsWith('/fleets/management/') && pathname !== '/fleets/management') {
                            const fleetId = pathname.split('/').pop();
                            
                            // Try to get fleet name from localStorage cache or use fleetId as fallback
                            const fleetNameKey = `fleet_name_${fleetId}`;
                            const cachedFleetName = localStorage.getItem(fleetNameKey);
                            const displayName = cachedFleetName || fleetId || 'Fleet Details';
                            
                            return {
                              title: `Fleet Details - ${displayName}`,
                              description: 'View detailed information about this fleet including vehicles, campaigns, and performance metrics.',
                              breadcrumbs: [
                                { text: 'Home', href: '/' },
                                { text: 'Fleet Management', href: UI_ROUTES.FLEET_MANAGEMENT },
                                { text: displayName }
                              ],
                              buttons: [
                                { text: 'Edit Fleet', iconName: 'edit' },
                                { text: 'Associate Vehicles', iconName: 'add-plus' },
                                { text: 'Delete Fleet', variant: 'primary' as const }
                              ]
                            };
                          }
                          
                          if (pathname.startsWith('/admin')) {
                            return {
                              title: 'User Management',
                              description: 'Create and manage users, assign roles, and control fleet access for operators and companion app users.',
                              breadcrumbs: [
                                { text: 'Home', href: '/' },
                                { text: 'User Management' }
                              ],
                              buttons: []
                            };
                          }
                          
                          if (pathname.startsWith('/subscriptions')) {
                            return {
                              title: 'Telemetry Subscriptions',
                              description: 'Enroll vehicles in telemetry plans to control which data signals are delivered to your fleet.',
                              breadcrumbs: [
                                { text: 'Home', href: '/' },
                                { text: 'Subscriptions' }
                              ],
                              buttons: []
                            };
                          }
                          
                          return {
                            title: 'Connected Mobility Intelligence',
                            description: 'Manage your connected mobility fleet operations',
                            breadcrumbs: [{ text: 'Home', href: '/' }],
                            buttons: []
                          };
                      }
                    };

                    // Use ReactivePageHeader for vehicle and driver detail pages
                    if ((location.pathname.startsWith('/vehicles/management/') && location.pathname !== '/vehicles/management') ||
                        location.pathname.startsWith('/drivers/')) {
                      return <ReactivePageHeader />;
                    }
                    
                    // Fallback to original logic for other pages
                    const config = getPageConfig(location.pathname);
                    return (
                      <PageHeader
                        title={config.title}
                        description={config.description}
                        breadcrumbs={config.breadcrumbs}
                        buttons={config.buttons}
                        onBreadcrumbFollow={(e) => {
                          e.preventDefault();
                          navigate(e.detail.href);
                        }}
                        helpIcon={
                          <Button
                            variant="icon"
                            iconName="status-info"
                            ariaLabel="Help"
                            onClick={() => {
                              const helpContent = (
                                <div style={{ padding: '16px' }}>
                                  <h3>Help & Support</h3>
                                  <p>Welcome to Connected Mobility Intelligence.</p>
                                  <p>Use this interface to monitor and manage your fleet operations.</p>
                                </div>
                              );
                              setToolsContent(helpContent);
                              setToolsOpen(true);
                            }}
                          />
                        }
                      />
                    );
                  })()}
                  <ProtectedRoute isDemoMode={isLocalDemo}>
                    <Routes>
                    <Route path={UI_ROUTES.ROOT} element={<FleetCommandCenter />} />
                    <Route
                      path={UI_ROUTES.FLEET_MANAGEMENT}
                      element={
                        <FleetManagementView />
                      }
                    />
                    <Route
                      path="/fleets/management/:fleetId"
                      element={<FleetDetailsWrapper />}
                    />
                    <Route
                      path={UI_ROUTES.FLEET_VEHICLES_MAP}
                      element={<FleetVehiclesMapView />}
                    />
                    <Route
                      path={UI_ROUTES.FLEET_SIMULATION}
                      element={<FleetSimulationView />}
                    />
                    <Route
                      path={UI_ROUTES.FLEET_CREATE}
                      element={<CreateFleetPage />}
                    />
                    <Route
                      path={UI_ROUTES.FLEET_EDIT}
                      element={<EditFleetPage />}
                    />
                    <Route
                      path={UI_ROUTES.FLEET_ASSOCIATE_VEHICLES}
                      element={<AssociateVehiclesPage />}
                    />
                    <Route
                      path={UI_ROUTES.VEHICLE_MANAGEMENT}
                      element={<VehicleManagementView />}
                    />
                    <Route
                      path="/vehicles/management/:vehicleId"
                      element={<VehicleDetailView />}
                    />
                    <Route
                      path={UI_ROUTES.VEHICLE_CREATE}
                      element={<CreateVehiclePage />}
                    />
                    <Route
                      path={UI_ROUTES.VEHICLE_EDIT}
                      element={<EditVehiclePage />}
                    />
                    <Route
                      path="/vehicles/management/:vehicleId/trips/:tripId"
                      element={<TripDetailView />}
                    />
                    <Route
                      path={UI_ROUTES.ALERTS_SAFETY}
                      element={<SafetyAlertsPage />}
                    />
                    <Route
                      path={UI_ROUTES.ALERTS_MAINTENANCE}
                      element={<ServiceDashboard />}
                    />
                    <Route
                      path={UI_ROUTES.SETTINGS}
                      element={<SettingsView />}
                    />
                    <Route
                      path={UI_ROUTES.PROFILE}
                      element={<ProfilePage />}
                    />
                    
                    {/* New Navigation Routes */}
                    <Route path="/drivers" element={<DriversView />} />
                    <Route path="/drivers/:driverId" element={<DriverDetailView />} />
                    <Route path="/charging" element={<ChargingView />} />
                    <Route path="/warranty" element={<WarrantyPage />} />
                    <Route path="/recall-warranty" element={<WarrantyPage />} />
                    <Route path="/fleet-costs" element={<FleetCostDashboard />} />
                    <Route path="/fleet-costs/actions" element={<ActionQueue />} />
                    <Route path="/vehicles/:vehicleId/costs" element={<VehicleCostTab />} />
                    <Route path="/fleet-rebalancing" element={<FleetRebalancingDashboard />} />
                    <Route path="/fleet-rebalancing/actions" element={<RebalanceActionQueue />} />
                    <Route path="/documents" element={<DocumentBrowser />} />
                    <Route path={UI_ROUTES.DATA_PROCESSING} element={
                      <ProtectedRoute isDemoMode={isLocalDemo} requiredGroups={['platform-admin', 'product-engineer']}>
                        <DataProcessingView />
                      </ProtectedRoute>
                    } />
                    
                    {/* Agent Workspace */}
                    <Route path={UI_ROUTES.AGENT_WORKSPACE} element={
                      <ProtectedRoute isDemoMode={isLocalDemo} requiredGroups={[USER_GROUPS.AGENT, USER_GROUPS.CONNECT_AGENT, USER_GROUPS.ADMIN]}>
                        <AgentWorkspace />
                      </ProtectedRoute>
                    } />
                    {/* Device Management Routes */}
                    <Route path="/devices/overview" element={<DeviceStatusOverview />} />
                    <Route path="/devices/connections" element={<DeviceClientList />} />
                    <Route path="/devices/topics" element={<DeviceTopicList />} />
                    <Route path="/devices/subscriptions" element={<DeviceSubscriptionList />} />
                    <Route path="/devices/retain-messages" element={<DeviceRetainMessageList />} />
                    <Route path="/devices/alarms" element={<DeviceAlarmList />} />
                    <Route path="/devices/rules" element={<DeviceRuleList />} />
                    <Route path="/devices/logs" element={<DeviceLogTrace />} />
                    
                    {/* Admin-only Routes */}
                    <Route path="/admin/users" element={
                      <ProtectedRoute isDemoMode={isLocalDemo} requiredGroups={['platform-admin']}>
                        <UserManagement />
                      </ProtectedRoute>
                    } />
                    <Route path="/system-monitoring" element={
                      <ProtectedRoute isDemoMode={isLocalDemo} requiredGroups={['platform-admin']}>
                        <SystemMonitoringView />
                      </ProtectedRoute>
                    } />
                    <Route path="/analytics" element={
                      <ProtectedRoute isDemoMode={isLocalDemo} requiredGroups={['platform-admin']}>
                        <AnalyticsView />
                      </ProtectedRoute>
                    } />
                    <Route path="/subscriptions" element={<SubscriptionManagement />} />
                    <Route path="/user-list" element={<DeviceUserList />} />
                    <Route path="/policy-list" element={<DevicePolicyList />} />
                    
                    {/* Data Collection Routes */}
                    <Route path="/signal-catalog" element={<DataProcessingView />} />
                    <Route path="/vehicle-models" element={<VehicleModelsView />} />
                    <Route path="/campaigns" element={<CampaignsView />} />

                    {/* Engineer persona — Product Digital Thread closed loop */}
                    <Route path={UI_ROUTES.ENGINEERING_INSIGHTS} element={<EngineerInsightsView />} />
                    <Route path={`${UI_ROUTES.ENGINEERING_INVESTIGATE}/:caseId`} element={<InvestigationWorkspace />} />
                    <Route path={UI_ROUTES.ENGINEERING_INVESTIGATE} element={<InvestigationWorkspace />} />
                    <Route path={`${UI_ROUTES.ENGINEERING_DESIGN_OPTIONS}/:caseId`} element={<DesignOptionsView />} />
                    <Route path={UI_ROUTES.ENGINEERING_DESIGN_OPTIONS} element={<DesignOptionsView />} />
                    <Route path={UI_ROUTES.DIGITAL_THREAD} element={<DigitalThreadView />} />

                    <Route path="*" element={<NotFound />} />
                    
                    {/* Agent Workspace */}
                    <Route path={UI_ROUTES.AGENT_WORKSPACE} element={
                      <ProtectedRoute isDemoMode={isLocalDemo} requiredGroups={[USER_GROUPS.AGENT, USER_GROUPS.CONNECT_AGENT, USER_GROUPS.ADMIN]}>
                        <AgentWorkspace />
                      </ProtectedRoute>
                    } />
                    {/* Device Management Routes */}
                    <Route
                      path={UI_ROUTES.IOT_STATUS_OVERVIEW}
                      element={<DeviceStatusOverview />}
                    />
                    <Route
                      path={UI_ROUTES.IOT_CLIENT_LIST}
                      element={<DeviceClientList />}
                    />
                    <Route
                      path={UI_ROUTES.IOT_TOPIC_LIST}
                      element={<DeviceTopicList />}
                    />
                    <Route
                      path={UI_ROUTES.IOT_SUBSCRIPTION_LIST}
                      element={<DeviceSubscriptionList />}
                    />
                    <Route
                      path={UI_ROUTES.IOT_RETAIN_MESSAGE_LIST}
                      element={<DeviceRetainMessageList />}
                    />
                    <Route
                      path={UI_ROUTES.IOT_RULE_LIST}
                      element={<DeviceRuleList />}
                    />
                    <Route
                      path={UI_ROUTES.IOT_ALARM_LIST}
                      element={<DeviceAlarmList />}
                    />
                    <Route
                      path={UI_ROUTES.IOT_LOG_TRACE}
                      element={<DeviceLogTrace />}
                    />
                    <Route
                      path={UI_ROUTES.IOT_USER_LIST}
                      element={<DeviceUserList />}
                    />
                    <Route
                      path={UI_ROUTES.IOT_POLICY_LIST}
                      element={<DevicePolicyList />}
                    />
                    
                    {/* Vehicle Analytics Routes */}
                    <Route
                      path={UI_ROUTES.TELEMETRY_DASHBOARD}
                      element={<TelemetryDashboard />}
                    />
                    <Route
                      path={UI_ROUTES.DRIVER_BEHAVIOR}
                      element={<DriverBehaviorView />}
                    />
                    <Route
                      path={UI_ROUTES.GEOFENCE_EVENTS}
                      element={<GeofenceEventsView />}
                    />
                    <Route
                      path={UI_ROUTES.TRIP_ANALYTICS}
                      element={<TripAnalyticsView />}
                    />
                  </Routes>
                </ProtectedRoute>
              </div>
              }
            />
          </I18nProvider>
        ) : auth.isLoading ? (
          <>
            <Spinner size="large" />
            <div>Authenticating...</div>
          </>
        ) : auth.error ? (
          <div>
            <Alert
              statusIconAriaLabel="Error"
              type="error"
              header="Authentication Error"
              action={{
                children: 'Retry',
                onClick: () => {
                  auth.clearError();
                  auth.login();
                }
              }}
            >
              {auth.error}
            </Alert>
          </div>
        ) : (
          <div>
            <Alert
              statusIconAriaLabel="Info"
              type="info"
              header="Authentication Required"
              action={{
                children: 'Sign In',
                onClick: auth.login
              }}
            >
              Please sign in to access the Connected Mobility Fleet Management Console.
            </Alert>
          </div>
        )}

        {/* Floating Chat Button */}
        <div style={{
          position: 'fixed',
          bottom: '24px',
          right: '24px',
          zIndex: 1000,
          width: '60px',
          height: '60px',
          borderRadius: '50%',
          boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: '#0073bb'
        }}>
          <Button
            variant="primary"
            iconName="contact"
            onClick={toggleChatModal}
            ariaLabel="Toggle Chat Assistant"
          />
        </div>

        {/* Chat Modal */}
        {chatModalOpen && (
          <div style={{
            position: 'fixed',
            bottom: '100px',
            right: '24px',
            width: '600px',
            height: '650px',
            backgroundColor: 'white',
            borderRadius: '12px',
            boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
            zIndex: 1001,
            border: '1px solid #e0e0e0',
            padding: '16px',
            display: 'flex',
            flexDirection: 'column',
            transform: chatModalAnimating ? 'scale(1) translateY(0)' : 'scale(0.8) translateY(20px)',
            opacity: chatModalAnimating ? 1 : 0,
            transition: 'all 0.2s ease-out',
            transformOrigin: 'bottom right'
          }}>
            <ChatAgentWithVin onClose={closeChatModal} />
          </div>
        )}
        </VehicleProvider>
      </HomeContextProvider>
      
      {/* Amazon Connect CCP (agent soft-phone widget).
       *   - Gated on platform-admin Cognito group (that's what Kevin is).
       *   - Gated on isAuthenticated so it doesn't render on the login screen
       *     (initCCP opens a popup and would be disruptive during sign-in).
       *   - Navigates the router to the escalated vehicle's detail page when
       *     the agent clicks Accept on a VSA contact. Screen-pop is the
       *     whole point of embedding the CCP here. */}
      {auth.isAuthenticated && isConnectAgent && (
        <CCPPanel
          onEscalationAccepted={(contact: ActiveConnectContact) => {
            if (contact.vehicleId) {
              console.info(
                `[App] Escalation accepted — navigating to vehicle ${contact.vehicleId}`,
              );
              navigate(`/vehicles/management/${contact.vehicleId}`);
            } else {
              // No vehicleId attribute — probably a non-VSA contact routed
              // through the same queue, or the Lambda didn't set the
              // attribute. Leave the agent where they are.
              console.warn(
                "[App] Escalation accepted but no vehicleId attribute on contact",
                contact,
              );
            }
          }}
        />
      )}

      {/* Escalation context sidebar. Renders to the right of the vehicle
       *   detail content when an active Connect contact exists. Gated on
       *   isConnectAgent — only agents who can actually accept contacts
       *   (i.e., Kevin) need this panel; fleet managers/operators don't.
       *   The panel handles its own visibility (renders null when no active
       *   contact) but without the gate it would mount the context hook
       *   for users who can never populate it. */}
      {auth.isAuthenticated && isConnectAgent && <EscalationContextPanel />}
      </ConnectProvider>
    </>
  );
}

export default App;
