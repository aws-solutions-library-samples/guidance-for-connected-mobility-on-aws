// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { ResetButton } from "./components/reset-button";
import { PageBanner } from "./components/page-banner";
import Board from "@cloudscape-design/board-components/board";
import { EmptyState } from "./components/empty-state";
import { boardI18nStrings } from "../../i18n-strings";
import { exportLayout, getBoardWidgets, getDefaultLayout } from "./widgets";
import React, { useContext, useEffect, useRef, useState } from "react";
import { useContainerQuery } from "@cloudscape-design/component-toolkit";
import { ConfigurableWidget } from "./components/configurable-widget";
import { StoredWidgetPlacement } from "./interfaces";
import { 
  Button, 
  SpaceBetween, 
  Box, 
  Container, 
  Header
} from "@cloudscape-design/components";
import { UserContext } from "../commons/UserContext";
import { FleetSelectionItem } from "../commons/fleet-selection";
import { AlertsFleetFilter, useAlertsFleetFilter } from '../commons/AlertsFleetFilter';
import { DashboardMetricsWithAggregation } from './DashboardMetricsWithAggregation';
import { UI_ROUTES } from "@/utils/constants";
import { useNavigate } from "react-router-dom";

interface ContentProps {
  layout: ReadonlyArray<StoredWidgetPlacement> | null;
  setLayout: (newLayout: ReadonlyArray<StoredWidgetPlacement>) => void;
  resetLayout: (newLayout: ReadonlyArray<StoredWidgetPlacement>) => void;
  setSplitPanelOpen: (newOpen: boolean) => void;
}

export function Content({
  layout,
  setLayout,
  resetLayout,
  setSplitPanelOpen,
}: ContentProps) {
  const [width, ref] = useContainerQuery((entry) => entry.contentBoxWidth);
  const itemsChanged = useRef(layout !== null);
  const uc = useContext(UserContext);
  const navigate = useNavigate();
  const [refreshInProgress, setRefreshInProgress] = useState(false);

  // Use the AlertsFleetFilter hook for consistent fleet filtering
  const {
    selectedFleet,
    selectedFleetName,
    handleFleetChange,
    isAllFleets
  } = useAlertsFleetFilter();

  useEffect(() => {
    if (itemsChanged.current || !width) {
      return;
    }
    resetLayout(getDefaultLayout(width));
  }, [resetLayout, width]);

  function handleLayoutChange(layout: ReadonlyArray<StoredWidgetPlacement>) {
    itemsChanged.current = true;
    setLayout(layout);
  }

  function handleResetLayout() {
    itemsChanged.current = false;
    resetLayout(getDefaultLayout(width!));
  }

  const handleRefresh = async () => {
    if (refreshInProgress) return;
    setRefreshInProgress(true);
    // Simulate refresh
    setTimeout(() => setRefreshInProgress(false), 1000);
  };

  return (
    <SpaceBetween size="l">

      {/* Enhanced Dashboard Metrics Component */}
      <DashboardMetricsWithAggregation />

      {/* Customizable Dashboard Section */}
      <Container 
        className="full-width-dashboard"
        header={
          <Header 
            variant="h2"
            description="Drag and drop widgets to customize your dashboard view"
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={handleResetLayout}>
                  Reset layout
                </Button>
                <Button
                  iconName="add-plus"
                  onClick={() => setSplitPanelOpen(true)}
                >
                  Add widget
                </Button>
              </SpaceBetween>
            }
          >
            Customizable Dashboard
          </Header>
        }
      >
        <PageBanner />
        <div ref={ref}>
          {selectedFleet && selectedFleet !== "all" ? (
            <Board
              empty={
                <EmptyState
                  title="No widgets"
                  description="There are no widgets on the dashboard."
                  verticalCenter={true}
                  action={
                    <SpaceBetween direction="horizontal" size="xs">
                      <Button onClick={handleResetLayout}>
                        Reset to default layout
                      </Button>
                      <Button
                        iconName="add-plus"
                        onClick={() => setSplitPanelOpen(true)}
                      >
                        Add widget
                      </Button>
                    </SpaceBetween>
                  }
                />
              }
              i18nStrings={boardI18nStrings}
              items={getBoardWidgets(layout ?? [])}
              onItemsChange={({ detail: { items } }) => {
                handleLayoutChange(exportLayout(items));
              }}
              renderItem={(item, actions) => {
                const Wrapper = item.data.provider ?? React.Fragment;
                return (
                  <Wrapper>
                    <ConfigurableWidget
                      config={item.data}
                      onRemove={actions.removeItem}
                    />
                  </Wrapper>
                );
              }}
            />
          ) : (
            <Box textAlign="center" padding={{ vertical: "xxl" }}>
              <SpaceBetween size="l" alignItems="center">
                <SpaceBetween size="m" alignItems="center">
                  <Box variant="h2" color="inherit">
                    {selectedFleet === "all" 
                      ? "All Fleets Overview" 
                      : "Welcome to Fleet Management"
                    }
                  </Box>
                  <Box variant="p" color="inherit">
                    {selectedFleet === "all"
                      ? "You are viewing aggregated data from all your fleets. Select a specific fleet above to access detailed widgets and customization options."
                      : "Get started by creating your first fleet to monitor vehicles, track performance, and analyze telemetry data."
                    }
                  </Box>
                </SpaceBetween>
                {selectedFleet !== "all" && (
                  <Button 
                    variant="primary" 
                    onClick={() => navigate(UI_ROUTES.FLEET_MANAGEMENT)}
                    iconName="add-plus"
                  >
                    Create Your First Fleet
                  </Button>
                )}
                <Box variant="small" color="text-body-secondary">
                  {selectedFleet === "all"
                    ? "Customizable widgets are available when viewing individual fleets"
                    : "Or select an existing fleet from the dropdown above if you have one"
                  }
                </Box>
              </SpaceBetween>
            </Box>
          )}
        </div>
      </Container>
    </SpaceBetween>
  );
}
