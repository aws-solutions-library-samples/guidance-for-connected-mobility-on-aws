// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, SetStateAction } from "react";
import { AppLayout, BreadcrumbGroup, TopNavigation } from "@cloudscape-design/components";
import { useNavigate } from 'react-router-dom';
import { FormFull, FormHeader } from "./components/form";
import { HelpPanel } from "@cloudscape-design/components";
import { ExternalLinkGroup } from "../../commons";
import { UI_ROUTES } from "@/utils/constants";

export function AssociateVehiclesInfoPanel() {
  return (
    <HelpPanel
      header={<h2>Fleet</h2>}
      footer={
        <ExternalLinkGroup
          items={[{ href: "#", text: "User Guide for CMS" }]}
        />
      }
    >
      <p>Associate vehicles to fleet help...TODO</p>
    </HelpPanel>
  );
}

export function AssociateVehiclesPage() {
  const navigate = useNavigate();
  const [toolsIndex, setToolsIndex] = useState(0);
  const [toolsOpen, setToolsOpen] = useState(false);

  const loadHelpPanelContent: any = (content: SetStateAction<number>): any => {
    setToolsIndex(content);
    setToolsOpen(true);
  };

  return (
    <>
      <header id="h">
        <TopNavigation
          identity={{
            href: '/',
            title: 'Connected Mobility Fleet Management Console',
          }}
          utilities={[
            {
              type: 'button',
              iconName: 'settings',
              ariaLabel: 'Settings',
              title: 'Settings',
            },
            {
              type: 'menu-dropdown',
              text: 'User',
              iconName: 'user-profile',
              items: [
                { id: 'profile', text: 'Profile' },
                { id: 'signout', text: 'Sign Out' },
              ],
            },
          ]}
        />
      </header>
      <div id="b">
        <AppLayout
          contentType="form"
          headerVariant="high-contrast"
          breadcrumbs={
            <BreadcrumbGroup
              items={[
                { text: 'Home', href: '/' },
                { text: 'Fleet Management', href: '/fleets' },
                { text: 'Associate Vehicles', href: '/fleets/associate-vehicles' }
              ]}
              expandAriaLabel="Show path"
              ariaLabel="Breadcrumbs"
              onFollow={(e) => {
                e.preventDefault();
                navigate(e.detail.href);
              }}
            />
          }
          content={
            <FormFull
              loadHelpPanelContent={loadHelpPanelContent}
              header={<FormHeader loadHelpPanelContent={loadHelpPanelContent} />}
            />
          }
          navigationHide={true}
          tools={<AssociateVehiclesInfoPanel />}
          toolsOpen={toolsOpen}
          onToolsChange={({ detail }) => setToolsOpen(detail.open)}
        />
      </div>
    </>
  );
}
