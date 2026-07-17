import { useMemo } from 'react';
import { useAuth } from './useAuth';
import { isDemoMode } from '../config/api';

export interface UserRole {
  isAdmin: boolean;
  isOperator: boolean;
  isViewer: boolean;
  /** Member of the `connect-agent` Cognito group — sees the Amazon Connect
   *  CCP widget for receiving escalated driver chats. Independent of admin
   *  status: a user can be a Connect agent without being platform-admin,
   *  and vice versa. */
  isConnectAgent: boolean;
  /** Member of the `product-engineer` Cognito group — sees the Engineering
   *  persona section (Insights, Investigation Workspace, test/prod fleets,
   *  Digital Thread destination). Independent of admin status. */
  isEngineer: boolean;
  /** Member of the `dispatcher` Cognito group — a read-only fleet-monitoring
   *  persona. When present ALONE (not combined with admin/operator/engineer/
   *  connect-agent), the SideNavigation renders the narrowed 6-item scope:
   *  Vehicles, Vehicle Map, Fleets, Drivers, Service, Safety. Does NOT grant
   *  write authority — `canWrite` remains false unless the user is ALSO in
   *  a write-eligible group. Added 2026-07-17 for the prod demo persona
   *  kevin.dispatch@example.com — see spec
   *  .kiro/specs/2026-07-17-cms-dispatcher-persona-nav-scope/. */
  isDispatcher: boolean;
  canWrite: boolean;
  fleetIds: string[];
}

export const useUserRole = (): UserRole => {
  const { user } = useAuth();

  return useMemo(() => {
    // Demo mode: synthesize a multi-persona user so all UI surfaces are
    // accessible to the demoer without Cognito setup. Includes engineer
    // (for the Acme Motors Product Digital Thread demo) and operator
    // (for the standard fleet ops demo).
    if (isDemoMode()) {
      return {
        isAdmin: false,
        isOperator: true,
        isViewer: false,
        isConnectAgent: true,
        isEngineer: true,
        // Demo mode already grants operator + engineer + connect-agent
        // access, which produces the FULL nav. Setting isDispatcher: false
        // avoids the dispatcher-only branch triggering (its guard already
        // requires !isOperator etc., so this is belt-and-suspenders but
        // makes intent explicit).
        isDispatcher: false,
        canWrite: true,
        fleetIds: ['be6-prod-cohort-001', 'be07-test-fleet-001'],
      };
    }

    const groups = user?.groups || [];
    const isAdmin = groups.includes('platform-admin');
    const isOperator = groups.includes('fleet-operator');
    const isViewer = groups.includes('fleet-viewer') && !isOperator;
    const isConnectAgent = groups.includes('connect-agent') || groups.includes('agent');
    const isEngineer = groups.includes('product-engineer');
    const isDispatcher = groups.includes('dispatcher');

    // Extract fleetIds from token custom attribute
    // cognito custom attributes come through as 'custom:fleetIds'
    const rawFleetIds = (user as any)?.fleetIds || '';
    const fleetIds = rawFleetIds ? rawFleetIds.split(',').map((s: string) => s.trim()).filter(Boolean) : [];

    return {
      isAdmin,
      isOperator,
      isViewer,
      isConnectAgent,
      isEngineer,
      isDispatcher,
      // canWrite intentionally does NOT include isDispatcher — dispatcher
      // is a read-only monitoring persona. Adding it here would restore
      // the "Add Driver" / "Create Vehicle" / "Manage Fleets" buttons on
      // the pages the dispatcher can see, which contradicts the persona's
      // intent.
      canWrite: isAdmin || isOperator || isEngineer,
      fleetIds,
    };
  }, [user]);
};
