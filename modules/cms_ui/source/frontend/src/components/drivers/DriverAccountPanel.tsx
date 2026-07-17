// SPDX-License-Identifier: Apache-2.0

/**
 * DriverAccountPanel — Cognito sign-in account status + lock/unlock controls
 * for a given driver.
 *
 * Sits on the driver detail page to close the loop between the drivers
 * table (CMS operator-facing) and the consolidated CMS Cognito pool (iOS-app-facing).
 * Shows whether the driver has a provisioned Cognito user, and provides
 * inline lock/unlock actions.
 *
 * Integration contract
 * --------------------
 *  - GET  /api/v1/driver-users/{driverId}  → account status
 *  - PUT  /api/v1/driver-users/{driverId}  body {"action": "lock" | "unlock"}
 *
 * The GET endpoint returns {exists: false, email} when no Cognito user
 * exists yet — in that state we show a prompt to run the seed script
 * rather than offering a create button. Provisioning is idempotent and
 * batched, so we avoid one-off account creation from the UI.
 *
 * The "Manage in Cognito" link opens the AWS console directly — faster
 * path to ops actions we don't expose in the CMS UI (reset password,
 * MFA toggle, sign-out all sessions, view login history).
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  ColumnLayout,
  Container,
  Header,
  Link,
  SpaceBetween,
  Spinner,
  StatusIndicator,
} from '@cloudscape-design/components';
import { getRuntimeConfig } from '../../config/api';

interface DriverAccountStatus {
  exists: boolean;
  username?: string;
  email: string;
  status?: string; // CONFIRMED, FORCE_CHANGE_PASSWORD, etc.
  enabled?: boolean;
  createdAt?: string;
  lastModified?: string;
  driverId?: string;
  tenantId?: string;
  vehicleId?: string;
  poolId: string;
  region: string;
}

interface Props {
  driverId: string;
  /** Pull from useAuth() in parent — same pattern as the rest of the view. */
  getIdToken: () => string | null;
}

export const DriverAccountPanel: React.FC<Props> = ({ driverId, getIdToken }) => {
  const [status, setStatus] = useState<DriverAccountStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState<'lock' | 'unlock' | null>(null);
  const [alert, setAlert] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);

  const apiBase =
    (window as any).runtimeConfig?.apiEndpoint || getRuntimeConfig().apiEndpoint || '';

  const hdrs = useCallback(() => {
    const token =
      getIdToken() ||
      sessionStorage.getItem('idToken') ||
      localStorage.getItem('idToken');
    return {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
  }, [getIdToken]);

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetch(`${apiBase}api/v1/driver-users/${driverId}`, {
        headers: hdrs(),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || `HTTP ${resp.status}`);
      }
      setStatus(data);
    } catch (e: any) {
      setAlert({ type: 'error', msg: `Failed to load account status: ${e.message}` });
    } finally {
      setLoading(false);
    }
  }, [apiBase, driverId, hdrs]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const toggleLock = async (action: 'lock' | 'unlock') => {
    setActing(action);
    setAlert(null);
    try {
      const resp = await fetch(`${apiBase}api/v1/driver-users/${driverId}`, {
        method: 'PUT',
        headers: hdrs(),
        body: JSON.stringify({ action }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || `HTTP ${resp.status}`);
      }
      setAlert({ type: 'success', msg: data.message || `Account ${action}ed` });
      await fetchStatus();
    } catch (e: any) {
      setAlert({ type: 'error', msg: e.message });
    } finally {
      setActing(null);
    }
  };

  // Cognito console URL for manual ops (reset password, view sessions, etc.)
  // Falls back gracefully if poolId/region aren't populated yet.
  const consoleUrl = status
    ? `https://${status.region || 'us-east-1'}.console.aws.amazon.com/cognito/v2/idp/user-pools/${status.poolId}/users/details/${encodeURIComponent(status.username || status.email)}?region=${status.region || 'us-east-1'}`
    : undefined;

  return (
    <Container
      header={
        <Header
          variant="h2"
          description="Sign-in account for the driver's iOS companion app"
          actions={
            <SpaceBetween size="xs" direction="horizontal">
              {consoleUrl && status?.exists && (
                <Button
                  iconName="external"
                  iconAlign="right"
                  href={consoleUrl}
                  target="_blank"
                >
                  Manage in Cognito
                </Button>
              )}
              {status?.exists && status.enabled && (
                <Button
                  iconName="lock-private"
                  loading={acting === 'lock'}
                  disabled={acting !== null}
                  onClick={() => toggleLock('lock')}
                >
                  Lock account
                </Button>
              )}
              {status?.exists && status.enabled === false && (
                <Button
                  iconName="unlocked"
                  variant="primary"
                  loading={acting === 'unlock'}
                  disabled={acting !== null}
                  onClick={() => toggleLock('unlock')}
                >
                  Unlock account
                </Button>
              )}
              <Button iconName="refresh" onClick={fetchStatus} disabled={loading} />
            </SpaceBetween>
          }
        >
          Account
        </Header>
      }
    >
      {alert && (
        <Box padding={{ bottom: 's' }}>
          <Alert type={alert.type} dismissible onDismiss={() => setAlert(null)}>
            {alert.msg}
          </Alert>
        </Box>
      )}
      {loading ? (
        <Box textAlign="center" padding="l">
          <Spinner />
        </Box>
      ) : !status ? (
        <Box color="text-body-secondary">No account data available</Box>
      ) : !status.exists ? (
        <Alert type="warning" header="No iOS app account provisioned">
          Driver <strong>{driverId}</strong> ({status.email}) has no sign-in account yet.
          Run{' '}
          <Box variant="code" display="inline">
            make seed-driver-users
          </Box>{' '}
          to create accounts for all drivers.
        </Alert>
      ) : (
        <ColumnLayout columns={4} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Status</Box>
            <StatusIndicator
              type={status.enabled ? (status.status === 'CONFIRMED' ? 'success' : 'pending') : 'error'}
            >
              {status.enabled ? status.status : 'LOCKED'}
            </StatusIndicator>
          </div>
          <div>
            <Box variant="awsui-key-label">Email</Box>
            <Box>{status.email}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Tenant</Box>
            <Box>{status.tenantId || '—'}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Vehicle (from token)</Box>
            <Box>{status.vehicleId || '—'}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Created</Box>
            <Box>{status.createdAt ? new Date(status.createdAt).toLocaleString() : '—'}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Last modified</Box>
            <Box>{status.lastModified ? new Date(status.lastModified).toLocaleString() : '—'}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Pool</Box>
            <Box fontSize="body-s">{status.poolId}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Cognito username</Box>
            <Box fontSize="body-s">
              {consoleUrl ? (
                <Link href={consoleUrl} external target="_blank">
                  {status.username}
                </Link>
              ) : (
                status.username
              )}
            </Box>
          </div>
        </ColumnLayout>
      )}
    </Container>
  );
};
