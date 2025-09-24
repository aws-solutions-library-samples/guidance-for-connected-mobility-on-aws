// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from 'react';
import { useSimpleAuth } from './SimpleAuthProvider';

export interface AuthUser {
  username: string;
  email?: string;
  name?: string;
  groups: string[];
  roles: string[];
}

export interface UseAuthReturn {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: () => void;
  logout: () => void;
  getAccessToken: () => string | null;
  getAuthHeaders: () => Record<string, string>;
  isTokenValid: () => boolean;
}

export const useAuth = (): UseAuthReturn => {
  const simpleAuth = useSimpleAuth();
  
  // Extract user info from token
  const getUserFromToken = useCallback(() => {
    if (!simpleAuth.idToken) return null;
    
    try {
      const payload = JSON.parse(atob(simpleAuth.idToken.split('.')[1]));
      return {
        username: payload.email || payload.username || 'user',
        email: payload.email || 'user@example.com',
        name: payload.name || payload.email || 'User',
        groups: payload['cognito:groups'] || [],
        roles: payload['cognito:groups'] || ['user'],
      };
    } catch (error) {
      console.error('Error parsing token:', error);
      return {
        username: 'user',
        email: 'user@example.com',
        name: 'User',
        groups: [],
        roles: ['user'],
      };
    }
  }, [simpleAuth.idToken]);
  
  const login = useCallback(() => {
    // This is handled by the LoginForm component
  }, []);

  const logout = useCallback(() => {
    console.log('🚪 Logout called from useAuth');
    simpleAuth.logout();
  }, [simpleAuth]);
  
  return {
    user: simpleAuth.isAuthenticated ? getUserFromToken() : null,
    isAuthenticated: simpleAuth.isAuthenticated,
    isLoading: simpleAuth.isLoading,
    login,
    logout,
    getAccessToken: () => simpleAuth.token,
    getAuthHeaders: () => simpleAuth.token ? { Authorization: `Bearer ${simpleAuth.token}` } : {},
    isTokenValid: () => simpleAuth.isAuthenticated,
  };
};
