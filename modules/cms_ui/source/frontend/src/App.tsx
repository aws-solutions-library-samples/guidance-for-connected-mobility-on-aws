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
// FleetCommandCenter removed 2026-09-06 per spec `2026-09-05-cms-vfo-teardown` —
// the fleet-view surface is now served by `services/fleet_intelligence/`
// (Tier 1 deterministic renderer). Root route redirects to
// `/fleet-intelligence/lifecycle` (see the Route below).
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
import { DealerPersonaBanner } from "./auth/DealerPersonaBanner";
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
// Data Products — subscriber view (spec forthcoming; currently a UI stub with mock data)
import SubscriptionsListPage from "./components/data-products/SubscriptionsListPage";
import SubscriptionDetailPage from "./components/data-products/SubscriptionDetailPage";
import CatalogListPage from "./components/data-products/CatalogListPage";
import CatalogDetailPage from "./components/data-products/CatalogDetailPage";
// Analytics Components
import TelemetryDashboard from "./components/analytics/TelemetryDashboard";
import DriverBehaviorView from "./components/analytics/DriverBehaviorView";
import GeofenceEventsView from "./components/analytics/GeofenceEventsView";
import TripAnalyticsView from "./components/analytics/TripAnalyticsView";
// New Navigation Components
import DriversView from "./components/drivers/DriversView";
import DriverDetailView from "./components/drivers/DriverDetailView";
import ChargingView from "./components/charging/ChargingView";
import SystemMonitoringView from "./components/system-monitoring/SystemMonitoringView";
import AnalyticsView from "./components/analytics/AnalyticsView";
import DataProcessingView from "./components/data-processing/DataProcessingView";
import { FleetCostDashboard, VehicleCostTab, ActionQueue } from "./components/tco";
import { FleetRebalancingDashboard, RebalanceActionQueue } from "./components/rebalancing";
import { RecallDashboard, WarrantyPage } from "./components/recall-warranty";
// Common Components
import NotFound from "./components/common/NotFound";
// Fleet Intelligence — PM Compliance (T4.2 / T5.4)
import { PmComplianceView } from "./components/pm";
import { LifecycleView } from "./components/fleet-intelligence/LifecycleView";
// Ambient fleet filter provider (spec 2026-09-02-cms-fleet-intelligence-v1
// § D5 / T5.4). The picker itself moved out of the SideNavigation into the
// TopNavigation utilities strip 2026-09-14 — see `useFleetPickerUtility`.
import { FleetFilterProvider } from "./components/fleet-filter/FleetFilter";
import { useFleetPickerUtility } from "./components/fleet-picker/useFleetPickerUtility";

// Engineer persona — Product Digital Thread closed loop
import EngineerInsightsView from "./components/engineering/EngineerInsightsView";
import InvestigationWorkspace from "./components/engineering/InvestigationWorkspace";
import DesignOptionsView from "./components/engineering/DesignOptionsView";

import DigitalThreadView from "./components/digital-thread/DigitalThreadView";

import { authFetch } from 'utils/authFetch';

// Demo persona switcher is now integrated into the account dropdown (Fix Group E,
// spec 2026-08-05-cms-demo-identity-model). Persona items live in profileActions;
// persona overlay removed.
import { buildProfileItems } from './components/profile/buildProfileItems';

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
const ChatAgentWithVin: React.FC<{ onClose: () => void; initialPrompt?: string; personaId?: string }> = ({
  onClose,
  initialPrompt,
  personaId,
}) => {
  const { vehicleVin } = useVehicle();
  return (
    <ChatAgent onClose={onClose} vin={vehicleVin ?? undefined} initialPrompt={initialPrompt} personaId={personaId} />
  );
};

/**
 * Event a descendant dispatches to open the assistant, optionally with a question.
 *
 * A window event rather than a context or prop chain, because the chat modal's
 * open/close state is local to `App` while the callers are deep inside `<Routes>`.
 * The obvious alternative — `useChatAgent()` from `components/commons` — looks like
 * the intended API but is NOT usable here: it depends on `HelpPanelProvider`, which
 * is mounted only inside CreateFleetPage, CreateVehiclePage and NotFound, so calling
 * it from a routed page throws "Missing HelpPanelProvider" and blanks the app. That
 * happened on 2026-08-21; the hook had zero call sites before then, so nothing had
 * ever exercised it.
 */
export const OPEN_ASSISTANT_EVENT = 'cms:open-assistant';

// ---------------------------------------------------------------------------
// buildProfileItems — exported from a dedicated module so tests can import it
// without pulling in App.tsx's heavy dependencies (maplibre-gl, etc.).
// See src/components/profile/buildProfileItems.ts for the implementation.
// ---------------------------------------------------------------------------
// Demo persona switcher was removed 2026-09-02 as part of the Federate-only
// auth-model cleanup. See issues/2026-09-02-cms-persona-dropdown-removed/.

export { buildProfileItems } from './components/profile/buildProfileItems';

// ---------------------------------------------------------------------------
// AppTopNavigation — thin wrapper around Cloudscape TopNavigation that lets
// us splice the fleet-picker menu-dropdown utility (see `useFleetPickerUtility`)
// into the utilities strip. Defined at file scope so the fleet-picker hook is
// called from a component INSIDE the `FleetFilterProvider` tree.
// Added 2026-09-14 as part of the left-nav restructure — see
// `issues/2026-09-14-cms-fleet-filter-duplicate-label/`.
// ---------------------------------------------------------------------------

const AppTopNavigation: React.FC<{
  auth: { user?: { email?: string; username?: string } };
  onSettingsClick: () => void;
  profileActions: ButtonDropdownProps.Items;
  onProfileFollow: (event: CustomEvent<ButtonDropdownProps.ItemClickDetails>) => void;
}> = ({ auth, onSettingsClick, profileActions, onProfileFollow }) => {
  const fleetUtility = useFleetPickerUtility();
  return (
    <TopNavigation
      identity={{
        href: '/',
        title: APP_TRADEMARK_NAME,
      }}
      utilities={[
        // Fleet picker sits leftmost in the utilities strip — the "session
        // context" position (like AWS Console's region switcher).
        fleetUtility,
        {
          type: 'button',
          iconName: 'settings',
          ariaLabel: 'Settings',
          title: 'Settings',
          onClick: onSettingsClick,
        },
        {
          type: 'menu-dropdown',
          text: auth.user?.email || auth.user?.username || 'Demo User',
          iconName: 'user-profile',
          items: profileActions,
          onItemClick: onProfileFollow,
        },
      ]}
    />
  );
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
    setChatSeedPrompt(undefined);
    setChatSeedPersona(undefined);
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
  // Seed question for the assistant, set by OPEN_ASSISTANT_EVENT. Cleared on close
  // so reopening from the header button does not replay the last suggestion.
  const [chatSeedPrompt, setChatSeedPrompt] = useState<string | undefined>(undefined);
  // Optional persona routing hint carried by the same event (e.g. the Fleet
  // Intelligence panel routes fleet-level questions to `fleet_manager`). Cleared
  // on close alongside the prompt so an unrelated reopen does not inherit it.
  const [chatSeedPersona, setChatSeedPersona] = useState<string | undefined>(undefined);
  useEffect(() => {
    const onOpenAssistant = (e: Event) => {
      const detail = (e as CustomEvent<{ prompt?: string; personaId?: string }>).detail;
      setChatSeedPrompt(detail?.prompt);
      setChatSeedPersona(detail?.personaId);
      openChatModal();
    };
    window.addEventListener(OPEN_ASSISTANT_EVENT, onOpenAssistant);
    return () => window.removeEventListener(OPEN_ASSISTANT_EVENT, onOpenAssistant);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
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
  const { isAdmin, isOperator, isConnectAgent, isEngineer, isDispatcher, canWrite } = useUserRole();
  // One condition for BOTH rendering the app-wide CCPPanel and telling
  // ConnectProvider it exists, so the two cannot drift apart.
  const showCcpPanel = auth.isAuthenticated && isConnectAgent;
  
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

  // --------------------------------------------------------------------------
  // Account dropdown items. 2026-09-02: the persona-switch group (Fix Group E,
  // spec 2026-08-05-cms-demo-identity-model) was deleted as the final part of
  // retiring the demo-password auth surface — see
  // issues/2026-09-02-cms-persona-dropdown-removed/. `buildProfileItems` now
  // returns a static items list; no persona group, no `runtimeConfig.demoPasswords`
  // consumption at this call site.
  const profileActions: ButtonDropdownProps.Items = buildProfileItems(
    { igRoot: IG_ROOT, feedbackUrl: FEEDBACK_ISSUES_URL, supportUrl: IG_URLS.SUPPORT },
  );

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
    if (event.detail.id === "profile") {
      navigate(UI_ROUTES.PROFILE);
    }
    if (event.detail.id === "preferences") {
      navigate(UI_ROUTES.SETTINGS);
    }
  };

  return (
    <>
      <ConnectProvider panelEnabled={showCcpPanel}>
      <HomeContextProvider
        value={{
          ...contextValue,
        }}
      >
        <VehicleProvider>
        {/* Ambient fleet-filter state (spec § D5 / T5.4). Session-scoped: fleet
            selection is ephemeral context, not a saved user preference. */}
        <FleetFilterProvider>
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
                /* Left-nav scroll behaviour: only show the scroll bar when content
                   actually overflows. Some Cloudscape versions ship 'overflow-y:
                   scroll' on the inner nav scroll region, which paints a permanent
                   scroll bar even when there's nothing to scroll. Force 'auto' on
                   both the outer panel and every inner overflowable region so the
                   scroll bar hides itself when the nav fits. Discovered 2026-09-14
                   — user reported scroll bar visible when the nav easily fits. */
                [class*="awsui_navigation-panel"],
                [class*="awsui_navigation-panel"] [class*="awsui_content"],
                [class*="awsui_navigation-panel"] [class*="awsui_list"] {
                  overflow-y: auto !important;
                }
                /* Pin the built-in "Close side navigation" button to the top-right
                   of the nav panel so it sits inline with the "Fleet Intelligence"
                   header. Cloudscape renamed this class from 'awsui_hide-navigation'
                   to 'awsui_navigation-close' (kept the old selector as a fallback in
                   case the rename is reverted). Discovered 2026-09-14 — the button
                   was rendering in the nav items area below the header divider. */
                [class*="awsui_navigation-close"],
                [class*="awsui_hide-navigation"] {
                  position: absolute !important;
                  top: 22px !important;
                  right: 12px !important;
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
                style={{ cursor: 'pointer', position: 'relative' }}
              >
                <AppTopNavigation
                  auth={auth}
                  onSettingsClick={() => navigate(UI_ROUTES.SETTINGS)}
                  profileActions={profileActions}
                  onProfileFollow={onProfileFollow}
                />
              </div>
            </header>
            {/* Spacer for the position:fixed top nav (#h). AppLayout's
                headerSelector prop tells Cloudscape the header's height for
                sticky-content offsets, but does NOT push AppLayout's own flow
                position — so without this spacer, page content slides UP under
                the fixed header. Restored 2026-09-14 after removing it turned
                out to cause exactly that. */}
            <div style={{ height: '56px' }}></div>
            <AppLayout
              contentType={(() => {
                const pathname = location.pathname;
                if (pathname.includes('/devices/') || pathname.includes('/signal-catalog') || pathname.includes('/vehicle-models') || pathname.includes('/campaigns')) return 'table';
                if (pathname.includes('/edit') || pathname.includes('/create') || pathname === '/settings') return 'form';
                if (pathname === '/' || pathname.includes('/dashboard') || pathname.includes('/telemetry') || pathname.includes('/analytics') || pathname.includes('/charging') || pathname.includes('/fleets/management') || pathname === '/vehicles/management' || pathname === '/drivers' || pathname.includes('/alerts') || pathname.includes('/simulation') || pathname.includes('/data-products')) return 'dashboard';
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
                  header={{ href: '/', text: 'Fleet Intelligence' }}
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
                    // Pure dispatcher (not also admin/operator/engineer/
                    // connect-agent): show the narrowed 6-item monitoring
                    // nav — Vehicles, Vehicle Map, Fleets, Drivers,
                    // Service, Safety. No Warranty/Costs/Charging/
                    // Rebalancing (those are broader fleet-operator
                    // surfaces), no admin, no engineering, no CCP, no
                    // Settings link (the top-nav gear icon at line ~497
                    // remains accessible for theme/simulator prefs).
                    // Precedes the pure-engineer branch below to preserve
                    // the existing engineer flow when a user is BOTH.
                    // See spec
                    // .kiro/specs/2026-07-17-cms-dispatcher-persona-nav-scope/.
                    isDispatcher && !isAdmin && !isOperator && !isEngineer && !isConnectAgent
                      ? [
                          {
                            type: 'section' as const,
                            text: 'Operations',
                            items: [
                              { type: 'link' as const, text: 'Vehicles', href: UI_ROUTES.VEHICLE_MANAGEMENT },
                              { type: 'link' as const, text: 'Vehicle Map', href: `${UI_ROUTES.VEHICLE_MANAGEMENT}?view=map` },
                              { type: 'link' as const, text: 'Fleets', href: UI_ROUTES.FLEET_MANAGEMENT },
                              { type: 'link' as const, text: 'Drivers', href: '/drivers' },
                              { type: 'link' as const, text: 'Service', href: UI_ROUTES.ALERTS_MAINTENANCE },
                              { type: 'link' as const, text: 'Deadhead', href: '/fleet-rebalancing' },
                            ],
                          },
                          {
                            type: 'section' as const,
                            text: 'Compliance',
                            items: [
                              { type: 'link' as const, text: 'Safety', href: UI_ROUTES.ALERTS_SAFETY },
                            ],
                          },
                        ]
                      :
                    // Pure engineer (not also admin/operator): show a focused
                    // engineering workflow nav. Fleet/vehicle/driver context
                    // is preserved (they still need to inspect the fleet) but
                    // operator surfaces (Service, Safety, Warranty, Costs,
                    // Charging, Rebalancing, Subscriptions) are hidden as
                    // they're not part of the engineering workflow.
                    isEngineer && !isOperator && !isAdmin
                      ? [
                          {
                            type: 'section' as const,
                            text: 'Operations',
                            items: [
                              { type: 'link' as const, text: 'Fleets', href: UI_ROUTES.FLEET_MANAGEMENT },
                              { type: 'link' as const, text: 'Vehicles', href: UI_ROUTES.VEHICLE_MANAGEMENT },
                              { type: 'link' as const, text: 'Vehicle Map', href: `${UI_ROUTES.VEHICLE_MANAGEMENT}?view=map` },
                              { type: 'link' as const, text: 'Drivers', href: '/drivers' },
                            ],
                          },
                          { type: 'divider' as const },
                          { type: 'link' as const, text: 'Settings', href: UI_ROUTES.SETTINGS },
                        ]
                      : [
                          {
                            type: 'section' as const,
                            text: 'Operations',
                            items: [
                              { type: 'link' as const, text: 'Vehicles', href: UI_ROUTES.VEHICLE_MANAGEMENT },
                              { type: 'link' as const, text: 'Vehicle Map', href: `${UI_ROUTES.VEHICLE_MANAGEMENT}?view=map` },
                              ...(!isConnectAgent || isAdmin || canWrite ? [
                                { type: 'link' as const, text: 'Fleets', href: UI_ROUTES.FLEET_MANAGEMENT },
                                { type: 'link' as const, text: 'Drivers', href: '/drivers' },
                                { type: 'link' as const, text: 'Service', href: UI_ROUTES.ALERTS_MAINTENANCE },
                                { type: 'link' as const, text: 'Deadhead', href: '/fleet-rebalancing' },
                              ] : []),
                              // Agent Workspace. The route has existed at
                              // UI_ROUTES.AGENT_WORKSPACE all along but NOTHING linked to
                              // it, so the page was reachable only by typing the URL.
                              // Gated on isConnectAgent to match how CCPPanel and
                              // EscalationContextPanel are gated — an operator who cannot
                              // receive a contact has nothing to do on this screen.
                              // A connect-agent always lands in this nav branch: the
                              // dispatcher branch excludes isConnectAgent explicitly and
                              // the engineer branch requires isEngineer.
                              ...(isConnectAgent ? [
                                { type: 'link' as const, text: 'Agent Workspace', href: UI_ROUTES.AGENT_WORKSPACE },
                              ] : []),
                            ],
                          },
                          ...(!isConnectAgent || isAdmin || canWrite ? [
                            {
                              type: 'section' as const,
                              text: 'Data Products',
                              items: [
                                { type: 'link' as const, text: 'Subscriptions', href: '/data-products' },
                              ],
                            },
                            {
                              type: 'section' as const,
                              text: 'Cost',
                              items: [
                                { type: 'link' as const, text: 'CPM', href: '/fleet-costs' },
                                { type: 'link' as const, text: 'Energy', href: '/charging' },
                                // 'Subscriptions' under Cost intentionally hidden — it
                                // routed to SubscriptionManagement (per-vehicle telemetry
                                // tier billing) which is the legacy CMS-as-platform
                                // surface. The 3P-fleet-portal reframe puts CMS on the
                                // subscriber side; the surface is out of scope for the
                                // demo. Route stays URL-accessible for anyone with a
                                // bookmark. The visible 'Subscriptions' entry lives
                                // under Data Products above.
                              ],
                            },
                            {
                              type: 'section' as const,
                              text: 'Compliance',
                              items: [
                                { type: 'link' as const, text: 'Safety', href: UI_ROUTES.ALERTS_SAFETY },
                                { type: 'link' as const, text: 'Recalls & Coverage', href: '/warranty' },
                                { type: 'link' as const, text: 'PM Compliance', href: UI_ROUTES.PM_COMPLIANCE },
                              ],
                            },
                            {
                              type: 'section' as const,
                              text: 'Assets',
                              items: [
                                { type: 'link' as const, text: 'Lifecycle', href: '/fleet-intelligence/lifecycle' },
                              ],
                            },
                          ] : []),
                          // ─── Setup — infrastructure the operator manages once ────
                          //
                          // Catalog lives here rather than under Data Products because
                          // registering a producer is a one-time technical setup, not
                          // the day-to-day interaction. Fleet operators subscribe more
                          // often than they add new producers.
                          ...(!isConnectAgent || isAdmin || canWrite ? [
                            {
                              type: 'section' as const,
                              text: 'Setup',
                              items: [
                                { type: 'link' as const, text: 'Data Source Catalog', href: '/data-products/catalog' },
                              ],
                            },
                          ] : []),
                          ...(isAdmin ? [
                            { type: 'divider' as const },
                            { type: 'link' as const, text: 'User Management', href: '/admin/users' },
                            { type: 'link' as const, text: 'Simulation', href: UI_ROUTES.FLEET_SIMULATION },
                            { type: 'link' as const, text: 'System Monitoring', href: '/system-monitoring' },
                          ] : []),
                          // Final divider before Settings is unconditional —
                          // guaranteed one HR between the last content section
                          // and Settings, no matter which conditional branches
                          // above rendered. Previously we also emitted an admin
                          // → engineer → connect-agent chain of conditional
                          // dividers here that could double up (two HRs in a
                          // row) or trail nothing (a divider with no items
                          // after it, if only the engineer branch fired). All
                          // three of those blocks are gone.
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
                            description: "Today's fleet briefing and overall fleet health.",
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
                            ]
                          };
                        case '/warranty':
                        case '/recall-warranty':
                          // Warranty ownership: DMS owns the claim lifecycle (rules engine,
                          // claim submission, adjudication, financial recovery — see the DMS
                          // repo's source/handlers/warranty_claims.py and _lib/warranty_rules.py).
                          // The fleet portal keeps a READ-ONLY subset: recall status and whether
                          // a vehicle/repair is covered. Title and description are scoped to that
                          // deliberately — the previous copy read "Warranty Management /
                          // Warranty claim automation, coverage tracking, and financial recovery",
                          // which advertised the DMS capability from the fleet portal.
                          // Ruling recorded 2026-09-05 in
                          // issues/2026-09-05-warranty-ownership-cms-vs-dms/.
                          return {
                            title: 'Recalls & Coverage',
                            description: 'Open recalls and warranty coverage status for your fleet — read-only. Claim submission and recovery are handled in the dealer management system.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Recalls & Coverage', href: '/warranty' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              // 'Export Recovery Report' removed 2026-09-05: it named a
                              // DMS-owned capability (warranty financial recovery) AND was a
                              // dead control — PageHeader renders onClick={button.onClick}
                              // and this config supplied none, so it never did anything.
                            ]
                          };
                        case '/fleet-costs':
                          return {
                            title: 'Cost Intelligence',
                            description: 'Total cost of ownership analytics, agentic cost optimization, and fleet spend intelligence.',
                            breadcrumbs: [
                              { text: 'Home', href: '/' },
                              { text: 'Costs', href: '/fleet-costs' }
                            ],
                            buttons: [
                              { text: 'Refresh', iconName: 'refresh' },
                              { text: 'Export Report', iconName: 'download' },
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
                            description: 'Real-time utilization monitoring, demand forecasting, and agent-recommended vehicle moves.',
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
                            description: 'Meridian PLM digital-thread destination. Receives design-change updates from the CMS Product Engineering Agent for engineering review and approval.',
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
                          
                          if (pathname.startsWith('/data-products')) {
                            const parts = pathname.split('/').filter(Boolean);
                            // /data-products                         → list of subscriptions
                            // /data-products/catalog                  → catalog list
                            // /data-products/catalog/:productId       → catalog entry detail
                            // /data-products/:subscriptionId          → subscription detail
                            const isCatalogList = parts[1] === 'catalog' && parts.length === 2;
                            const isCatalogDetail = parts[1] === 'catalog' && parts.length > 2;
                            const isSubscriptionDetail = parts.length > 1 && parts[1] !== 'catalog';
                            const catalogProductId = isCatalogDetail ? parts[2] : undefined;
                            const subscriptionId = isSubscriptionDetail ? parts[1] : undefined;

                            if (isCatalogDetail) {
                              return {
                                title: 'Data product detail',
                                description: 'Connection, authentication, and tier offering for a registered data product.',
                                breadcrumbs: [
                                  { text: 'Home', href: '/' },
                                  { text: 'Catalog', href: '/data-products/catalog' },
                                  { text: catalogProductId! },
                                ],
                                buttons: []
                              };
                            }
                            if (isCatalogList) {
                              return {
                                title: 'Catalog',
                                description: 'Data products this fleet operator has defined. Subscribe to any Active product from the Subscriptions page.',
                                breadcrumbs: [
                                  { text: 'Home', href: '/' },
                                  { text: 'Catalog' },
                                ],
                                buttons: []
                              };
                            }
                            if (isSubscriptionDetail) {
                              return {
                                title: 'Subscription detail',
                                description: 'Data product subscription — feed status, enrolled vehicles, and available signals.',
                                breadcrumbs: [
                                  { text: 'Home', href: '/' },
                                  { text: 'Subscriptions', href: '/data-products' },
                                  { text: subscriptionId! },
                                ],
                                buttons: []
                              };
                            }
                            // /data-products (list)
                            return {
                              title: 'Subscriptions',
                              description: 'Data products this fleet consumes.',
                              breadcrumbs: [
                                { text: 'Home', href: '/' },
                                { text: 'Subscriptions' },
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
                    <DealerPersonaBanner />
                    <Routes>
                    <Route path={UI_ROUTES.ROOT} element={<Navigate to="/fleet-intelligence/lifecycle" replace />} />
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
                    {/* Fleet Intelligence — Lifecycle (spec 2026-09-14-cms-fleet-lifecycle-view) */}
                    <Route path="/fleet-intelligence/lifecycle" element={<LifecycleView />} />
                    {/* Fleet Intelligence — PM Compliance (T4.2 / T5.4) */}
                    <Route path={UI_ROUTES.PM_COMPLIANCE} element={<PmComplianceView />} />
                    <Route path="/fleet-costs/actions" element={<ActionQueue />} />
                    <Route path="/vehicles/:vehicleId/costs" element={<VehicleCostTab />} />
                    <Route path="/fleet-rebalancing" element={<FleetRebalancingDashboard />} />
                    <Route path="/fleet-rebalancing/actions" element={<RebalanceActionQueue />} />
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

                    {/* Data Products — subscriber view (UI stub w/ mock data) */}
                    <Route path="/data-products" element={<SubscriptionsListPage />} />
                    <Route path="/data-products/catalog" element={<CatalogListPage />} />
                    <Route path="/data-products/catalog/:productId" element={<CatalogDetailPage />} />
                    <Route path="/data-products/:id" element={<SubscriptionDetailPage />} />

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
            <ChatAgentWithVin onClose={closeChatModal} initialPrompt={chatSeedPrompt} personaId={chatSeedPersona} />
          </div>
        )}
        </FleetFilterProvider>
        </VehicleProvider>
      </HomeContextProvider>
      
      {/* Amazon Connect CCP (agent soft-phone widget).
       *   - Gated on isConnectAgent (Cognito group connect-agent or agent) and on
       *     isAuthenticated, via showCcpPanel.
       *   - Mounting it does NOT start Connect. The CCP starts only after the
       *     agent opts in (clicking the offline pill, or opening Agent Workspace),
       *     because initCCP opens the Connect login popup. See ConnectContext.tsx
       *     and issues/2026-09-24-connect-login-popup-on-every-page-load/.
       *   - Navigates the router to the escalated vehicle's detail page when
       *     the agent clicks Accept on a VSA contact. Screen-pop is the
       *     whole point of embedding the CCP here. */}
      {showCcpPanel && (
        <CCPPanel
          onEscalationAccepted={(contact: ActiveConnectContact) => {
            // Agent Workspace already renders the vehicle context for the
            // active contact itself (HandoverContextPanel + VehicleDetailView),
            // so navigating would eject the agent from the very screen they
            // chose to work escalations from, mid-contact. Stay put and let the
            // workspace react to the contact it is already tracking via
            // ConnectCCP's subscribers.
            if (location.pathname === UI_ROUTES.AGENT_WORKSPACE) {
              console.info(
                "[App] Escalation accepted on Agent Workspace — staying put; the workspace renders its own vehicle context",
              );
              return;
            }
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
