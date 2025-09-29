// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from "react";
import "./App.css";
import "./components/Header.css";
import { Navigate, Route, Routes, useNavigate, useLocation, useParams } from "react-router-dom";
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
} from "@cloudscape-design/components";
import DashboardView from "./components/dashboard/DashboardView";
import { PageHeader } from "./components/common/PageHeader";
import { HelpPanelProvider, useChatAgent } from "./components/commons";
import { ChatAgent } from "./components/commons/ChatAgent";
import {
  APP_TRADEMARK_NAME,
  IG_ROOT,
  IG_URLS,
  FEEDBACK_ISSUES_URL,
  UI_ROUTES,
} from "./utils/constants";
import { useCreateReducer } from "./hooks/useCreateReducer";
import { initialState, insertRuntimeConfig } from "./contexts/home.state";
import { HomeContextProvider } from "./contexts/home.context";
import { useEffect, useContext } from "react";
import { getRuntimeConfig, isDemoMode } from "./config/api";
import { useAuth } from "./auth/useAuth";
import ProtectedRoute from "./auth/ProtectedRoute";
import MaintenanceAlertsView from "./components/alerts/maintenance/MaintenanceAlertsView";
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
// Common Components
import NotFound from "./components/common/NotFound";

// Wrapper to extract fleetId from URL params and pass to FleetDetailsPage
function FleetDetailsWrapper() {
  const { fleetId } = useParams();
  return <FleetDetailsPage fleetId={fleetId} />;
}

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
      <HomeContextProvider
        value={{
          ...contextValue,
        }}
      >
        {(isLocalDemo || auth.isAuthenticated) ? (
          <I18nProvider locale="en" messages={[enMessages]}>
            <style>
              {`
                /* Global navigation and header styling */
                nav[aria-label="Side navigation"] {
                  padding-top: 8px !important;
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
                  border-color: #0f1b2a !important;
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
                  margin-top: -25px;
                  width: 100%;
                  padding: 20px;
                  box-sizing: border-box;
                  max-width: 100%;
                  background-color: #ffffff;
                  border-radius: 8px;
                  position: relative;
                  z-index: 2;
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
                            href: 'https://docs.aws.amazon.com/iot-fleetwise/',
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
                        // Add theme switching logic here
                      }
                      if (event.detail.id === 'profile') {
                        // Add profile navigation logic here
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
            <div style={{ height: '48px' }}></div>
            <AppLayout
              contentType={(() => {
                const pathname = location.pathname;
                if (pathname.includes('/management') || pathname.includes('/devices/') || pathname.includes('/signal-catalog') || pathname.includes('/vehicle-models') || pathname.includes('/campaigns')) return 'table';
                if (pathname.includes('/edit') || pathname.includes('/create') || pathname === '/settings') return 'form';
                if (pathname === '/' || pathname.includes('/dashboard') || pathname.includes('/telemetry') || pathname.includes('/analytics')) return 'dashboard';
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
                  activeHref={location.pathname}
                  header={{ href: '/', text: 'Connected Mobility Fleet Console' }}
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
                  items={[
                    { type: 'link', text: 'Fleets', href: UI_ROUTES.FLEET_MANAGEMENT },
                    { type: 'link', text: 'Vehicles', href: UI_ROUTES.VEHICLE_MANAGEMENT },
                    { type: 'link', text: 'Drivers', href: '/drivers' },
                    { type: 'link', text: 'Charging', href: '/charging' },
                    { type: 'link', text: 'Service', href: UI_ROUTES.ALERTS_MAINTENANCE },
                    { type: 'link', text: 'Safety', href: UI_ROUTES.ALERTS_SAFETY },
                    { type: 'link', text: 'Warranty', href: '/warranty' },
                    { type: 'link', text: 'System Monitoring', href: '/system-monitoring' },
                    { type: 'link', text: 'Analytics', href: '/analytics' },
                  ]}
                />
              }
              content={
                <div>
                  {(() => {
                    const getPageConfig = (pathname: string) => {
                      switch (pathname) {
                        case '/':
                          return {
                            title: 'Fleet Dashboard',
                            description: 'Monitor your fleet operations with real-time insights, performance metrics, and customizable widgets for comprehensive fleet management.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Fleet Dashboard'}
                            ],
                            buttons: [
                              { text: 'Map View', iconName: 'view-horizontal', onClick: () => navigate(UI_ROUTES.FLEET_VEHICLES_MAP) },
                              { text: 'Fleet Simulation', iconName: 'play', onClick: () => navigate(UI_ROUTES.FLEET_SIMULATION) },
                              { text: 'Manage Fleets', iconName: 'settings', variant: 'primary' as const, onClick: () => navigate(UI_ROUTES.FLEET_MANAGEMENT) }
                            ]
                          };
                        case UI_ROUTES.VEHICLE_MANAGEMENT:
                          return {
                            title: 'Vehicle Management',
                            description: 'Manage your vehicle fleet, monitor vehicle status, and configure device settings for optimal performance.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Vehicle Management', href: UI_ROUTES.VEHICLE_MANAGEMENT }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Create Vehicle', variant: 'primary' as const, onClick: () => navigate(UI_ROUTES.VEHICLE_CREATE) }
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
                              { text: 'Add Driver', variant: 'primary' as const, onClick: () => navigate('/drivers/create') }
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
                          return {
                            title: 'Warranty Management',
                            description: 'Track warranty coverage, manage claims, and monitor warranty compliance across your fleet.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Warranty Management', href: '/warranty' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'File Claim', variant: 'primary' as const, onClick: () => navigate('/warranty/claim') }
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
                            description: 'Simulate fleet operations and test scenarios for optimal fleet management.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Fleet Simulation', href: UI_ROUTES.FLEET_SIMULATION }
                            ],
                            buttons: [
                              { text: 'Start Simulation', variant: 'primary' as const },
                              { text: 'Reset', iconName: 'refresh' }
                            ]
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
                            title: 'Service Management',
                            description: 'Monitor service schedules, track maintenance compliance, and manage service appointments across your fleet.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Service Management', href: UI_ROUTES.ALERTS_MAINTENANCE }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Schedule Service', variant: 'primary' as const }
                            ]
                          };
                        default:
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
                            const vehicleId = pathname.split('/').pop();
                            return {
                              title: `Vehicle Details - ${vehicleId}`,
                              description: 'View detailed information about this vehicle including status, trips, and telemetry data.',
                              breadcrumbs: [
                                { text: 'Home', href: '/' },
                                { text: 'Vehicle Management', href: UI_ROUTES.VEHICLE_MANAGEMENT },
                                { text: vehicleId || 'Vehicle Details' }
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
                                      fetch(`${getRuntimeConfig().apiEndpoint}api/v1/vehicles/${vehicleId}`, {
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
                          
                          return {
                            title: 'Connected Mobility Fleet Console',
                            description: 'Manage your connected mobility fleet operations',
                            breadcrumbs: [{ text: 'Home', href: '/' }],
                            buttons: []
                          };
                      }
                    };

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
                                  <p>Welcome to the Connected Mobility Fleet Management Console.</p>
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
                    <Route path={UI_ROUTES.ROOT} element={<DashboardView />} />
                    <Route
                      path={UI_ROUTES.FLEET_MANAGEMENT}
                      element={<FleetManagementView />}
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
                      path="/vehicles/management/:vehicleId"
                      element={<VehicleDetailView />}
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
                      element={<MaintenanceAlertsView />}
                    />
                    <Route
                      path={UI_ROUTES.SETTINGS}
                      element={<SettingsView />}
                    />
                    
                    {/* New Navigation Routes */}
                    <Route path="/drivers" element={<DriversView />} />
                    <Route path="/drivers/:driverId" element={<DriverDetailView />} />
                    <Route path="/charging" element={<ChargingView />} />
                    <Route path="/warranty" element={<WarrantyView />} />
                    <Route path="/system-monitoring" element={<SystemMonitoringView />} />
                    <Route path="/analytics" element={<AnalyticsView />} />
                    
                    {/* Device Management Routes */}
                    <Route path="/devices/overview" element={<DeviceStatusOverview />} />
                    <Route path="/devices/connections" element={<DeviceClientList />} />
                    <Route path="/devices/topics" element={<DeviceTopicList />} />
                    <Route path="/devices/subscriptions" element={<DeviceSubscriptionList />} />
                    <Route path="/devices/retain-messages" element={<DeviceRetainMessageList />} />
                    <Route path="/devices/alarms" element={<DeviceAlarmList />} />
                    <Route path="/devices/rules" element={<DeviceRuleList />} />
                    <Route path="/devices/logs" element={<DeviceLogTrace />} />
                    
                    {/* Access Control Routes */}
                    <Route path="/user-list" element={<DeviceUserList />} />
                    <Route path="/policy-list" element={<DevicePolicyList />} />
                    
                    {/* Data Collection Routes */}
                    <Route path="/signal-catalog" element={<SignalCatalogView />} />
                    <Route path="/vehicle-models" element={<VehicleModelsView />} />
                    <Route path="/campaigns" element={<CampaignsView />} />
                    
                    <Route path="*" element={<NotFound />} />
                    
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
      </HomeContextProvider>
      
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
          <ChatAgent onClose={closeChatModal} />
        </div>
      )}
    </>
  );
}

export default App;