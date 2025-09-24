// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import { Authenticator } from '@aws-amplify/ui-react';
import { getCurrentUser, signOut, fetchAuthSession } from 'aws-amplify/auth';
import { configureAmplify, AmplifyConfig } from './amplifyConfig';
import '@aws-amplify/ui-react/styles.css';

export interface AuthUser {
  username: string;
  email?: string;
  name?: string;
  groups: string[];
  roles: string[];
}

export interface AmplifyAuthContextProps {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: () => void;
  logout: () => void;
  getAccessToken: () => Promise<string | null>;
  getAuthHeaders: () => Promise<Record<string, string>>;
}

const AmplifyAuthContext = createContext<AmplifyAuthContextProps | null>(null);

export const useAmplifyAuth = (): AmplifyAuthContextProps => {
  const context = useContext(AmplifyAuthContext);
  if (!context) {
    throw new Error('useAmplifyAuth must be used within an AmplifyAuthProvider');
  }
  return context;
};

interface AmplifyAuthProviderProps {
  children: ReactNode;
  config: AmplifyConfig;
}

export const AmplifyAuthProvider: React.FC<AmplifyAuthProviderProps> = ({
  children,
  config,
}) => {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    configureAmplify(config);
    checkAuthState();
  }, [config]);

  const checkAuthState = async () => {
    try {
      const currentUser = await getCurrentUser();
      const session = await fetchAuthSession();
      
      if (currentUser && session.tokens) {
        const authUser: AuthUser = {
          username: currentUser.username,
          email: currentUser.signInDetails?.loginId,
          name: currentUser.username,
          groups: [], // Can be extracted from tokens if needed
          roles: [],
        };
        
        setUser(authUser);
        setIsAuthenticated(true);
      }
    } catch (error) {
      console.log('User not authenticated:', error);
      setUser(null);
      setIsAuthenticated(false);
    } finally {
      setIsLoading(false);
    }
  };

  const login = () => {
    // Handled by Authenticator component
  };

  const logout = async () => {
    try {
      await signOut();
      setUser(null);
      setIsAuthenticated(false);
    } catch (error) {
      console.error('Error signing out:', error);
    }
  };

  const getAccessToken = async (): Promise<string | null> => {
    try {
      const session = await fetchAuthSession();
      return session.tokens?.accessToken?.toString() || null;
    } catch (error) {
      console.error('Error getting access token:', error);
      return null;
    }
  };

  const getAuthHeaders = async (): Promise<Record<string, string>> => {
    const token = await getAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  };

  const contextValue: AmplifyAuthContextProps = {
    user,
    isAuthenticated,
    isLoading,
    login,
    logout,
    getAccessToken,
    getAuthHeaders,
  };

  if (isLoading) {
    return <div>Loading...</div>;
  }

  return (
    <AmplifyAuthContext.Provider value={contextValue}>
      <Authenticator hideSignUp={true}>
        {({ signOut, user: amplifyUser }) => {
          // Update context when Amplify user changes
          useEffect(() => {
            if (amplifyUser) {
              checkAuthState();
            }
          }, [amplifyUser]);

          return children;
        }}
      </Authenticator>
    </AmplifyAuthContext.Provider>
  );
};
