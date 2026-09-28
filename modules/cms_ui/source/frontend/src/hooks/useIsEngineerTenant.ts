// CQ-3 resolution: zero-argument hook for source-picker context where no fleet/vehicle
// subject is available. Returns true iff the current user is in the product-engineer
// Cognito group. The richer subject-parameterised version lives at
// @/auth/useIsEngineerTenant and is used where a fleet/vehicle tenantType is known.
import { useUserRole } from '@/auth/useUserRole';

export const useIsEngineerTenant = (): boolean => {
  const { isEngineer } = useUserRole();
  return isEngineer;
};
