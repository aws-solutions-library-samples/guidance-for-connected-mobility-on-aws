// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useContext, useState, useRef, useEffect } from "react";
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  FormField,
  Toggle,
  AppLayout,
  AppLayoutProps,
  Flashbar,
  BreadcrumbGroup,
  TopNavigation,
} from "@cloudscape-design/components";
import { useNavigate } from "react-router-dom";
import { UserContext } from "../commons/UserContext";
import { Mode } from "@cloudscape-design/global-styles";
import { SettingsHeader } from "./header";
import { UI_ROUTES, APP_TRADEMARK_NAME } from "../../utils/constants";
import { HelpPanelProvider } from "../commons";
import { ApiContext } from "@/api/provider";
import { getRuntimeConfig } from "../../config/api";
import { useAuth } from "../../auth/useAuth";

const SettingsView: React.FC = () => {
  const uc = useContext(UserContext);
  const api = useContext(ApiContext);
  const navigate = useNavigate();
  const auth = useAuth();
  const isDarkMode = uc.theme.currentThemeMode === Mode.Dark;
  const appLayoutRef = useRef<AppLayoutProps.Ref>();
  
  const [toolsOpen, setToolsOpen] = useState(false);
  const [toolsContent, setToolsContent] = useState(() => <SettingsHeader />);
  const [flashbarItems, setFlashbarItems] = useState<any[]>([]);
  
  // Log the managed service state from context
  console.log("SettingsView - Managed service state:", uc.managedService.isEnabled);
  
  // Fetch user preferences when component mounts
  useEffect(() => {
    const fetchUserPreferences = async () => {
      try {
        console.log("Fetching user preferences...");
        const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/user-preferences`);
        const data = await response.json();
        
        console.log("User preferences response:", data);
        if (data.preferences && data.preferences.useManagedService !== undefined) {
          console.log("Setting managed service state to:", data.preferences.useManagedService);
          uc.managedService.setIsEnabled(data.preferences.useManagedService);
        }
      } catch (error) {
        console.error("Error fetching user preferences:", error);
      }
    };

    fetchUserPreferences();
  }, [api.client, uc.managedService]);

  const loadHelpPanelContent = (content: React.SetStateAction<JSX.Element>) => {
    setToolsOpen(true);
    setToolsContent(content);
    appLayoutRef.current?.focusToolsClose();
  };

  const handleManagedServiceToggle = async (checked: boolean) => {
    try {
      console.log("Toggle changed to:", checked);
      
      // Update the local state first for immediate UI feedback
      uc.managedService.setIsEnabled(checked);
      
      // Call the API to update the user preferences
      await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/user-preferences`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          useManagedService: checked
        })
      });
      
      // Show success message
      setFlashbarItems([
        {
          type: "success",
          content: `Successfully ${checked ? "enabled" : "disabled"} managed service.`,
          dismissible: true,
          onDismiss: () => setFlashbarItems([]),
        },
      ]);
    } catch (error) {
      console.error("Error updating user preferences:", error);
      
      // Show error message
      setFlashbarItems([
        {
          type: "error",
          content: `Failed to ${checked ? "enable" : "disable"} managed service. Please try again.`,
          dismissible: true,
          onDismiss: () => setFlashbarItems([]),
        },
      ]);
      
      // Revert the local state
      uc.managedService.setIsEnabled(!checked);
    }
  };

  const settingsContent = (
    <SpaceBetween size="l">
      {flashbarItems.length > 0 && <Flashbar items={flashbarItems} />}
      
      <Container
        header={
          <Header variant="h2" description="Customize your user interface">
            Appearance
          </Header>
        }
      >
        <SpaceBetween size="l">
          <FormField label="Theme">
            <Toggle
              onChange={() => {
                uc.theme.switchThemeMode();
              }}
              checked={isDarkMode}
            >
              Dark mode
            </Toggle>
          </FormField>
        </SpaceBetween>
      </Container>

      <Container
        header={
          <Header variant="h2" description="Configure notification preferences">
            Notifications
          </Header>
        }
      >
        <SpaceBetween size="l">
          <FormField label="Email notifications">
            <Toggle>
              Receive email notifications
            </Toggle>
          </FormField>
          <FormField label="Alert notifications">
            <Toggle>
              Receive alert notifications
            </Toggle>
          </FormField>
        </SpaceBetween>
      </Container>

      <Container
        header={
          <Header variant="h2" description="Configure metrics collection">
            Metrics
          </Header>
        }
      >
        <SpaceBetween size="l">
          <Button>Enable Metrics</Button>
        </SpaceBetween>
      </Container>

      <Container
        header={
          <Header variant="h2" description="Configure logging settings">
            Logging
          </Header>
        }
      >
        <SpaceBetween size="l">
          <Button>Edit</Button>
        </SpaceBetween>
      </Container>

      <Container
        header={
          <Header variant="h2" description="Configure encryption settings">
            Encryption
          </Header>
        }
      >
        <SpaceBetween size="l">
          <Button>Edit</Button>
        </SpaceBetween>
      </Container>

      <Container
        header={
          <Header variant="h2" description="Configure managed service options">
            Managed Service
          </Header>
        }
      >
        <SpaceBetween size="l">
          <FormField>
            <Toggle
              onChange={({ detail }) => {
                handleManagedServiceToggle(detail.checked);
              }}
              checked={uc.managedService.isEnabled}
            >
              Enable managed service
            </Toggle>
          </FormField>
          <Box variant="p">
            When enabled, the application will use AWS IoT FleetWise to manage fleets. 
            When disabled, the application will use a custom implementation.
          </Box>
        </SpaceBetween>
      </Container>

      <Container
        header={
          <Header variant="h2" description="Manage your account settings">
            Account
          </Header>
        }
      >
        <SpaceBetween size="l">
          <Box>
            <SpaceBetween size="xs">
              <div>
                <strong>Demo Mode:</strong> {uc.demoMode.isDemoMode ? "Enabled" : "Disabled"}
              </div>
            </SpaceBetween>
          </Box>
          <Button>Update account information</Button>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );

  return (
    <>
      <header id="h">
        <TopNavigation
          identity={{
            href: '/',
            title: APP_TRADEMARK_NAME,
          }}
          utilities={[
            {
              type: 'button',
              iconName: 'settings',
              ariaLabel: 'Settings',
              title: 'Settings',
              onClick: () => navigate(UI_ROUTES.SETTINGS)
            },
            {
              type: 'menu-dropdown',
              text: auth.user?.email || auth.user?.username || 'Demo User',
              iconName: 'user-profile',
              items: [
                { id: 'preferences', text: 'Preferences' },
                { id: 'switchTheme', text: 'Switch Theme' },
                { id: 'signout', text: 'Sign Out' },
              ],
              onItemClick: (event) => {
                if (event.detail.id === 'signout') {
                  auth.logout();
                }
                if (event.detail.id === 'switchTheme') {
                  // Add theme switching logic here
                }
              }
            },
          ]}
        />
      </header>
      <div id="b" style={{ marginTop: '48px' }}>
        <HelpPanelProvider value={loadHelpPanelContent}>
          <AppLayout
        ref={appLayoutRef}
        contentType="form"
        navigationHide={true}
        breadcrumbs={
          <BreadcrumbGroup
            items={[
              { text: 'Home', href: '/' },
              { text: 'Settings', href: '/settings' }
            ]}
            expandAriaLabel="Show path"
            ariaLabel="Breadcrumbs"
            onFollow={(e) => {
              e.preventDefault();
              navigate(e.detail.href);
            }}
          />
        }
        toolsOpen={toolsOpen}
        tools={toolsContent}
        onToolsChange={({ detail }) => setToolsOpen(detail.open)}
        content={settingsContent}
        headerSelector="#header"
      />
    </HelpPanelProvider>
    </div>
    </>
  );
};

export default SettingsView;