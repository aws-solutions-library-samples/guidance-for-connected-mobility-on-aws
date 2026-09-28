// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * App — root component for the Connected Services portal.
 *
 * Routes are built entirely from the screen registry — no second list.
 *
 * Route table (23 entries from SCREEN_REGISTRY + 2 parameterised sub-routes):
 *   /                                    → CommandCenterView
 *   /connectivity/fleet-health           → FleetHealthView
 *   /connectivity/subscriber-lookup      → SubscriberLookupView
 *   /connectivity/subscriber-lookup/:vin → SubscriberLookupView (VIN pre-filled)
 *   /connectivity/policy                 → PolicyControlView
 *   /connectivity/rate-plans             → RatePlansView
 *   /software/signals                    → SignalDetectionView
 *   /software/workbench                  → DiagnosisWorkbenchView
 *   /software/workbench/:signalId        → DiagnosisWorkbenchView (signal-scoped)
 *   /software/campaigns                  → SoftwareCampaignsView
 *   /software/rollout                    → RolloutMonitorView
 *   /software/security                   → SecurityMonitorView (placeholder)
 *   /diagnostics/fault-patterns          → FaultPatternsView
 *   /diagnostics/quality-signals         → QualitySignalsView
 *   /manufacturing/build-order           → BuildOrderView
 *   /manufacturing/factory-registration  → FactoryRegistrationView
 *   /manufacturing/homologation          → HomologationView
 *   /manufacturing/ownership-transfer    → OwnershipTransferView
 *   /manufacturing/stop-ship             → StopShipView
 *   /sales/feature-catalog               → FeatureCatalogView
 *   /sales/connectivity-plans            → ConnectivityPlansView
 *   /sales/data-products                 → DataProductsView
 *   /sales/subscriptions                 → SubscriptionsListView
 *   /compliance/market-posture           → MarketPostureView
 *   /compliance/consent                  → ConsentPrivacyView
 *   /compliance/audit-log                → AuditLogView
 *
 * Legacy redirects (preserve the query string, minus the forbidden parameter):
 *   /fleet-health      → /connectivity/fleet-health
 *   /subscriber-lookup → /connectivity/subscriber-lookup
 *
 * !! A bare <Navigate to="/x" replace /> drops the query string entirely, which breaks
 * legitimate deep links (commit d3dee699). LegacyRedirect preserves search EXCEPT the
 * session parameter, which it strips — see its own note. The original rationale for
 * this component was to carry that parameter through; that mechanism is Talos
 * 69d7f6e6 and is deleted at every end.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md T3.4
 */

import "@cloudscape-design/global-styles/index.css";

import React, { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import AppShell from "./components/layout/AppShell";
import AuthCallback from "./components/AuthCallback";
import NotFound from "./components/layout/NotFound";
import RequireAuth from "./components/RequireAuth";

// ---------------------------------------------------------------------------
// Lazy screen imports
//
// Screen components live under src/components/screens/<section>/<NameView>.tsx.
// All 23 screen modules exist as scaffolding stubs (see decisions.md last entry).
// Groups 4-6 replace the stub content with real implementations.
// ---------------------------------------------------------------------------

// Command Center
const CommandCenterView = lazy(
  () => import("./components/screens/command-center/CommandCenterView")
);

// Connectivity
const FleetHealthView = lazy(
  () => import("./components/screens/connectivity/FleetHealthView")
);
const SubscriberLookupView = lazy(
  () => import("./components/screens/connectivity/SubscriberLookupView")
);
const PolicyControlView = lazy(
  () => import("./components/screens/connectivity/PolicyControlView")
);
const RatePlansView = lazy(
  () => import("./components/screens/connectivity/RatePlansView")
);
const AvailableVehiclesView = lazy(
  () => import("./components/screens/connectivity/AvailableVehiclesView")
);
const SimulateVehicleView = lazy(
  () => import("./components/screens/connectivity/SimulateVehicleView")
);

// Software
const SignalDetectionView = lazy(
  () => import("./components/screens/software/SignalDetectionView")
);
const DiagnosisWorkbenchView = lazy(
  () => import("./components/screens/software/DiagnosisWorkbenchView")
);
const SoftwareCampaignsView = lazy(
  () => import("./components/screens/software/SoftwareCampaignsView")
);
const RolloutMonitorView = lazy(
  () => import("./components/screens/software/RolloutMonitorView")
);
const SecurityMonitorView = lazy(
  () => import("./components/screens/software/SecurityMonitorView")
);

// Population Diagnostics
const FaultPatternsView = lazy(
  () => import("./components/screens/diagnostics/FaultPatternsView")
);
const QualitySignalsView = lazy(
  () => import("./components/screens/diagnostics/QualitySignalsView")
);

// Manufacturing & Lifecycle
const BuildOrderView = lazy(
  () => import("./components/screens/manufacturing/BuildOrderView")
);
const FactoryRegistrationView = lazy(
  () => import("./components/screens/manufacturing/FactoryRegistrationView")
);
const HomologationView = lazy(
  () => import("./components/screens/manufacturing/HomologationView")
);
const OwnershipTransferView = lazy(
  () => import("./components/screens/manufacturing/OwnershipTransferView")
);
const StopShipView = lazy(
  () => import("./components/screens/manufacturing/StopShipView")
);

// Sales & Subscriptions
const FeatureCatalogView = lazy(
  () => import("./components/screens/sales/FeatureCatalogView")
);
const ConnectivityPlansView = lazy(
  () => import("./components/screens/sales/ConnectivityPlansView")
);
const DataProductsView = lazy(
  () => import("./components/screens/sales/DataProductsView")
);
const SubscribersRosterView = lazy(
  () => import("./components/screens/sales/SubscribersRosterView")
);
const SubscriberDetailView = lazy(
  () => import("./components/screens/sales/SubscriberDetailView")
);
const GrantsView = lazy(
  () => import("./components/screens/sales/GrantsView")
);
const SignalCatalogView = lazy(
  () => import("./components/screens/sales/SignalCatalogView")
);
const SignalDetailView = lazy(
  () => import("./components/screens/sales/SignalDetailView")
);
const DataProductDetailView = lazy(
  () => import("./components/screens/sales/DataProductDetailView")
);

// Data Model section (stub pass 2026-09-14 extension — moved SignalCatalog/SignalDetail
// URLs, added Vehicle Models / ECUs / Decoder Manifests). Files live under
// screens/sales/ (Signal Catalog + Signal Detail — historical location) and
// screens/data-model/ (the new ones). This is deliberately-preserved layout;
// wired spec may consolidate the folder structure.
const VehicleModelsView = lazy(
  () => import("./components/screens/data-model/VehicleModelsView")
);
const VehicleModelDetailView = lazy(
  () => import("./components/screens/data-model/VehicleModelDetailView")
);
const ECUsView = lazy(
  () => import("./components/screens/data-model/ECUsView")
);
const DecoderManifestsView = lazy(
  () => import("./components/screens/data-model/DecoderManifestsView")
);
const DecoderManifestDetailView = lazy(
  () => import("./components/screens/data-model/DecoderManifestDetailView")
);

// Data-collection campaigns (spec 2026-09-14-cs-portal-data-model-backend T5.2)
// Reads the CMS campaign API. NOT the OTA screens — those are untouched above.
const DataCollectionCampaignsView = lazy(
  () => import("./components/screens/data-model/DataCollectionCampaignsView")
);
// Parameterised sub-route — campaign detail (spec 2026-09-20-cs-campaigns-screen-restructure T3.1)
// Reached only by clicking a row in DataCollectionCampaignsView, not from the sidebar.
const CampaignDetailView = lazy(
  () => import("./components/screens/data-model/CampaignDetailView")
);

// Markets & Compliance
const MarketPostureView = lazy(
  () => import("./components/screens/compliance/MarketPostureView")
);
const ConsentPrivacyView = lazy(
  () => import("./components/screens/compliance/ConsentPrivacyView")
);
const AuditLogView = lazy(
  () => import("./components/screens/compliance/AuditLogView")
);

// ---------------------------------------------------------------------------
// LegacyRedirect — query-string-preserving redirect for v1 routes.
//
// A bare <Navigate to="/new-path" replace /> drops the query string entirely, which
// breaks legitimate deep links like /subscriber-lookup?vin=... — hence preserving
// search.
//
// EXCEPT `session`, which is stripped. 2026-09-05, security review Cycle 1 Warning 1
// of spec 2026-09-05-cms-connected-services-auth-integration.
//
// This component's original purpose was the opposite: its v1 docstring explained that
// it existed so `?session=alias:group` would survive the redirect and reach
// getSession(). That mechanism is Talos Critical 69d7f6e6 and is now deleted at both
// ends — the reader in auth.ts and the parser in index.tsx. Forwarding the parameter
// would leave the transport for a channel with no receiver, and would put the value
// back into a URL that gets copied, logged and shared.
//
// Stripping one named parameter rather than dropping all search keeps the deep-link
// behaviour this component was also serving.
// ---------------------------------------------------------------------------

/** Query parameter that must never be forwarded. See the note above. */
const FORBIDDEN_QUERY_PARAM = "session";

interface LegacyRedirectProps {
  /** Absolute target path (no trailing slash). */
  to: string;
}

// Exported for test: registryCompleteness.test.tsx renders it directly so the
// session-stripping contract is asserted against the component rather than
// against a test-local reimplementation.
export const LegacyRedirect: React.FC<LegacyRedirectProps> = ({ to }) => {
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  params.delete(FORBIDDEN_QUERY_PARAM);
  const remaining = params.toString();
  return <Navigate to={remaining ? `${to}?${remaining}` : to} replace />;
};

// ---------------------------------------------------------------------------
// ProtectedRoute — wraps a screen element with RequireAuth and AppShell.
//
// AppShell takes `children: React.ReactNode` (not React Router's <Outlet>), so
// each protected route declares its screen element inline rather than relying
// on a layout-route outlet.  The RequireAuth guard is applied once per route;
// for this portal every authenticated route uses the same guard so there is no
// per-screen variance needed.
// ---------------------------------------------------------------------------

interface ProtectedRouteProps {
  children: React.ReactNode;
}

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children }) => (
  <RequireAuth>
    <AppShell>
      <Suspense fallback={null}>{children}</Suspense>
    </AppShell>
  </RequireAuth>
);

// ---------------------------------------------------------------------------
// AppRoutes — router-free route table, exported for test harnesses.
//
// Tests wrap this in a <MemoryRouter> at the desired path.  The default export
// (App) keeps <BrowserRouter> for production.  No route path, redirect
// behaviour, or RequireAuth wrapping changes here.
// ---------------------------------------------------------------------------

export const AppRoutes: React.FC = () => {
  return (
    <Routes>
        {/* ── OAuth callback — NOT wrapped in RequireAuth ───────────────── */}
        {/* Must be reachable by unauthenticated users completing the Hosted  */}
        {/* UI round-trip, so it is declared before the RequireAuth-wrapped   */}
        {/* routes and relies on React Router's ordered matching. Spec T9.1.  */}
        {/*                                                                   */}
        {/* This is not a screen and deliberately has no registry entry.      */}
        {/* registryCompleteness P2 excludes it by name via                   */}
        {/* NON_SCREEN_ROUTE_PATHS — the same mechanism used for the legacy   */}
        {/* redirects. It is declared with ordinary path="…" syntax so the    */}
        {/* guard can still see it.                                          */}
        <Route path="/auth/callback" element={<AuthCallback />} />

        {/* ── Legacy redirects ───────────────────────────────────────────── */}
        {/* These must be declared BEFORE the protected routes so React Router  */}
        {/* matches them first. Both preserve location.search apart from the    */}
        {/* session parameter, which LegacyRedirect strips.                     */}
        <Route
          path="/fleet-health"
          element={<LegacyRedirect to="/connectivity/fleet-health" />}
        />
        <Route
          path="/subscriber-lookup"
          element={<LegacyRedirect to="/connectivity/subscriber-lookup" />}
        />

        {/* ── Command Center — root path ────────────────────────────────────── */}
        {/* The Command Center is the landing screen — not a redirect.           */}
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <CommandCenterView />
            </ProtectedRoute>
          }
        />

        {/* ── Connectivity ─────────────────────────────────────────────────── */}
        <Route
          path="/connectivity/fleet-health"
          element={
            <ProtectedRoute>
              <FleetHealthView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/connectivity/subscriber-lookup"
          element={
            <ProtectedRoute>
              <SubscriberLookupView />
            </ProtectedRoute>
          }
        />
        {/* Parameterised sub-route — VIN pre-filled detail view */}
        <Route
          path="/connectivity/subscriber-lookup/:vin"
          element={
            <ProtectedRoute>
              <SubscriberLookupView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/connectivity/policy"
          element={
            <ProtectedRoute>
              <PolicyControlView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/connectivity/rate-plans"
          element={
            <ProtectedRoute>
              <RatePlansView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/connectivity/available-vehicles"
          element={
            <ProtectedRoute>
              <AvailableVehiclesView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/data-model/simulate-vehicle"
          element={
            <ProtectedRoute>
              <SimulateVehicleView />
            </ProtectedRoute>
          }
        />
        {/* Legacy path — kept for bookmarks; redirects to new Data Model location */}
        <Route
          path="/connectivity/simulate-vehicle"
          element={<Navigate to="/data-model/simulate-vehicle" replace />}
        />

        {/* ── Software ─────────────────────────────────────────────────────── */}
        <Route
          path="/software/signals"
          element={
            <ProtectedRoute>
              <SignalDetectionView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/software/workbench"
          element={
            <ProtectedRoute>
              <DiagnosisWorkbenchView />
            </ProtectedRoute>
          }
        />
        {/* Parameterised sub-route — signal-scoped workbench */}
        <Route
          path="/software/workbench/:signalId"
          element={
            <ProtectedRoute>
              <DiagnosisWorkbenchView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/software/campaigns"
          element={
            <ProtectedRoute>
              <SoftwareCampaignsView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/software/rollout"
          element={
            <ProtectedRoute>
              <RolloutMonitorView />
            </ProtectedRoute>
          }
        />
        {/* Security Monitor is 'placeholder' — AppShell handles the panel. */}
        <Route
          path="/software/security"
          element={
            <ProtectedRoute>
              <SecurityMonitorView />
            </ProtectedRoute>
          }
        />

        {/* ── Population Diagnostics ───────────────────────────────────────── */}
        <Route
          path="/diagnostics/fault-patterns"
          element={
            <ProtectedRoute>
              <FaultPatternsView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/diagnostics/quality-signals"
          element={
            <ProtectedRoute>
              <QualitySignalsView />
            </ProtectedRoute>
          }
        />

        {/* ── Manufacturing & Lifecycle ─────────────────────────────────────── */}
        <Route
          path="/manufacturing/build-order"
          element={
            <ProtectedRoute>
              <BuildOrderView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/manufacturing/factory-registration"
          element={
            <ProtectedRoute>
              <FactoryRegistrationView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/manufacturing/homologation"
          element={
            <ProtectedRoute>
              <HomologationView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/manufacturing/ownership-transfer"
          element={
            <ProtectedRoute>
              <OwnershipTransferView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/manufacturing/stop-ship"
          element={
            <ProtectedRoute>
              <StopShipView />
            </ProtectedRoute>
          }
        />

        {/* ── Sales & Subscriptions ─────────────────────────────────────────── */}
        <Route
          path="/sales/feature-catalog"
          element={
            <ProtectedRoute>
              <FeatureCatalogView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/sales/connectivity-plans"
          element={
            <ProtectedRoute>
              <ConnectivityPlansView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/sales/data-products"
          element={
            <ProtectedRoute>
              <DataProductsView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/sales/subscribers"
          element={
            <ProtectedRoute>
              <SubscribersRosterView />
            </ProtectedRoute>
          }
        />
        {/* Parameterised sub-route — subscriber detail (stub pass 2026-09-14) */}
        <Route
          path="/sales/subscribers/:consumerId"
          element={
            <ProtectedRoute>
              <SubscriberDetailView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/sales/grants"
          element={
            <ProtectedRoute>
              <GrantsView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/sales/signals"
          element={<Navigate to="/data-model/signals" replace />}
        />
        <Route
          path="/sales/signals/:signalId"
          element={<LegacyRedirect to="/data-model/signals" />}
        />
        <Route
          path="/data-model/signals"
          element={
            <ProtectedRoute>
              <SignalCatalogView />
            </ProtectedRoute>
          }
        />
        {/* Parameterised sub-route — signal detail (stub pass 2026-09-14 extension) */}
        <Route
          path="/data-model/signals/:signalId"
          element={
            <ProtectedRoute>
              <SignalDetailView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/data-model/vehicle-models"
          element={
            <ProtectedRoute>
              <VehicleModelsView />
            </ProtectedRoute>
          }
        />
        {/* Parameterised sub-route — vehicle model detail (stub pass 2026-09-14 Data Model extension) */}
        <Route
          path="/data-model/vehicle-models/:modelId"
          element={
            <ProtectedRoute>
              <VehicleModelDetailView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/data-model/ecus"
          element={
            <ProtectedRoute>
              <ECUsView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/data-model/decoder-manifests"
          element={
            <ProtectedRoute>
              <DecoderManifestsView />
            </ProtectedRoute>
          }
        />
        {/* Parameterised sub-route — decoder manifest detail (stub pass 2026-09-14 Data Model extension) */}
        <Route
          path="/data-model/decoder-manifests/:manifestId"
          element={
            <ProtectedRoute>
              <DecoderManifestDetailView />
            </ProtectedRoute>
          }
        />
        {/* Data-collection campaigns — spec 2026-09-14-cs-portal-data-model-backend T5.2 */}
        {/* NOT the OTA SoftwareCampaignsView. Reads the CMS fleet-campaigns API. */}
        <Route
          path="/data-model/data-collection-campaigns"
          element={
            <ProtectedRoute>
              <DataCollectionCampaignsView />
            </ProtectedRoute>
          }
        />
        {/* Parameterised sub-route — campaign detail (spec 2026-09-20-cs-campaigns-screen-restructure T3.1) */}
        {/* Reached only by clicking a row in DataCollectionCampaignsView; no sidebar entry. */}
        <Route
          path="/data-model/data-collection-campaigns/:campaignName"
          element={
            <ProtectedRoute>
              <CampaignDetailView />
            </ProtectedRoute>
          }
        />
        {/* Parameterised sub-route — data product detail (stub pass 2026-09-14 extension) */}
        <Route
          path="/sales/data-products/:productId"
          element={
            <ProtectedRoute>
              <DataProductDetailView />
            </ProtectedRoute>
          }
        />

        {/* ── Markets & Compliance ─────────────────────────────────────────── */}
        <Route
          path="/compliance/market-posture"
          element={
            <ProtectedRoute>
              <MarketPostureView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/compliance/consent"
          element={
            <ProtectedRoute>
              <ConsentPrivacyView />
            </ProtectedRoute>
          }
        />
        <Route
          path="/compliance/audit-log"
          element={
            <ProtectedRoute>
              <AuditLogView />
            </ProtectedRoute>
          }
        />

        {/* ── SPA catchall ─────────────────────────────────────────────────── */}
        <Route path="*" element={<NotFound />} />
      </Routes>
  );
};

// ---------------------------------------------------------------------------
// App — production entry point. Wraps AppRoutes in BrowserRouter.
// ---------------------------------------------------------------------------

const App: React.FC = () => (
  <BrowserRouter>
    <AppRoutes />
  </BrowserRouter>
);

export default App;
