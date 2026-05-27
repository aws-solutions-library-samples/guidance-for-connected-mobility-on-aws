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
      canWrite: isAdmin || isOperator || isEngineer,
      fleetIds,
    };
  }, [user]);
};
