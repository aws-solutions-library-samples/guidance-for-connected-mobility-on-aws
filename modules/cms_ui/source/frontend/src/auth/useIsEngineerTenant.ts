// Engineer-tenant detection hook.
//
// Returns whether the current user, viewing a specific fleet or vehicle,
// should see the engineering layout (KPIs, ECUs, Parts, OTA Rollouts, etc.)
// instead of the operational layout (Trips, Charging, Drivers, Costs).
//
// The decision is: user is in the product-engineer Cognito group, AND the
// fleet/vehicle has tenantType set to 'internal' or 'external'. Both must be
// true. This means:
//
//   - An admin viewing an engineering fleet sees the operational view.
//     (Engineering view is gated to engineers, by design.)
//   - An engineer viewing a non-engineering fleet (e.g. FLEET-001) sees the
//     operational view. (Engineering content only makes sense for tenants
//     that have the engineering metadata.)
//   - Demo mode (isDemoMode()) treats the current user as an engineer.
//
// Usage:
//   const isEng = useIsEngineerTenant({ tenantType: fleet?.tenantType });
//   if (isEng) return <EngineeringFleetDetailsPage fleet={fleet} />;
//   return <OperationalFleetDetailsPage fleet={fleet} />;

import { useUserRole } from './useUserRole';

export interface EngineerTenantSubject {
  /** The fleet's or vehicle's tenantType field. */
  tenantType?: 'internal' | 'external' | string | null;
}

export function useIsEngineerTenant(subject: EngineerTenantSubject | null | undefined): boolean {
  const { isEngineer } = useUserRole();
  if (!isEngineer) return false;
  if (!subject) return false;
  return subject.tenantType === 'internal' || subject.tenantType === 'external';
}
