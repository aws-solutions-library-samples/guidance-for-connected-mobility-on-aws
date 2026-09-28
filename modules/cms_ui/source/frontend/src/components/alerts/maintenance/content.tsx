// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useContext, useState } from "react";
import {
  Button,
  SpaceBetween,
} from "@cloudscape-design/components";
import { PageBanner } from "@/components/dashboard/components/page-banner";
import { DashboardHeader } from "../header";
import { UserContext } from "@/components/commons/UserContext";
import { MaintenanceAlertsContent } from "./MaintenanceAlertsContent";

interface ContentProps {}

export function Content({}: ContentProps) {
  const [refreshInProgress, setRefreshInProgress] = useState(false);

  function handleRefresh(): void {
    setRefreshInProgress(true);
    // Simulate refresh
    setTimeout(() => {
      setRefreshInProgress(false);
    }, 1000);
  }

  return (
    <SpaceBetween size="m">
      <DashboardHeader
        title="Maintenance Alerts"
        actions={
          <SpaceBetween size="xs" direction="horizontal">
            <Button
              iconName="refresh"
              onClick={() => handleRefresh()}
              disabled={refreshInProgress}
              disabledReason="Refresh in progress..."
            >
              Refresh
            </Button>
            <Button iconName="add-plus" onClick={() => {}}>
              Create Alert
            </Button>
          </SpaceBetween>
        }
      />
      <PageBanner />
      
      {/* Use the new enhanced MaintenanceAlertsContent */}
      <MaintenanceAlertsContent />
    </SpaceBetween>
  );
}
