// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { ReactNode } from 'react';
import { Alert, Spinner } from '@cloudscape-design/components';
import { useAuth } from './useAuth';

interface ProtectedRouteProps {
  children: ReactNode;
  fallback?: ReactNode;
  requiredRoles?: string[];
  requiredGroups?: string[];
  isDemoMode?: boolean;
}

export const ProtectedRoute: React.FC<ProtectedRouteProps> = ({
  children,
  fallback,
  requiredRoles = [],
  requiredGroups = [],
  isDemoMode = false,
}) => {
  const auth = useAuth();

  // In demo mode, always allow access
  if (isDemoMode) {
    return <>{children}</>;
  }

  // Show loading spinner while authentication is in progress
  if (auth.isLoading) {
    return (
      <div style={{ 
        display: 'flex', 
        justifyContent: 'center', 
        alignItems: 'center', 
        height: '100vh',
        flexDirection: 'column',
        gap: '16px'
      }}>
        <Spinner size="large" />
        <div>Authenticating...</div>
      </div>
    );
  }

  // Show error if authentication failed
  if (auth.error) {
    return (
      <div style={{ padding: '20px' }}>
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
    );
  }

  // Redirect to login if not authenticated
  if (!auth.isAuthenticated) {
    if (fallback) {
      return <>{fallback}</>;
    }

    return (
      <div style={{ padding: '20px' }}>
        <Alert
          statusIconAriaLabel="Info"
          type="info"
          header="Authentication Required"
          action={{
            children: 'Sign In',
            onClick: auth.login
          }}
        >
          Please sign in to access this application.
        </Alert>
      </div>
    );
  }

  // Check role-based access
  if (requiredRoles.length > 0 && auth.user) {
    const hasRequiredRole = requiredRoles.some(role => 
      auth.user!.roles.includes(role)
    );

    if (!hasRequiredRole) {
      return (
        <div style={{ padding: '20px' }}>
          <Alert
            statusIconAriaLabel="Warning"
            type="warning"
            header="Access Denied"
          >
            You don't have the required permissions to access this resource.
            Required roles: {requiredRoles.join(', ')}
          </Alert>
        </div>
      );
    }
  }

  // Check group-based access
  if (requiredGroups.length > 0 && auth.user) {
    const hasRequiredGroup = requiredGroups.some(group => 
      auth.user!.groups.includes(group)
    );

    if (!hasRequiredGroup) {
      return (
        <div style={{ padding: '20px' }}>
          <Alert
            statusIconAriaLabel="Warning"
            type="warning"
            header="Access Denied"
          >
            You don't have the required group membership to access this resource.
            Required groups: {requiredGroups.join(', ')}
          </Alert>
        </div>
      );
    }
  }

  // User is authenticated and authorized
  return <>{children}</>;
};

export default ProtectedRoute;
