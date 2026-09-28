// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import {
  Button,
  Header,
  HeaderProps,
  SpaceBetween,
} from "@cloudscape-design/components";
import { InfoLink } from "../../commons";

interface FullPageHeaderProps extends HeaderProps {
  title?: string;
  createButtonText?: string;
  extraActions?: React.ReactNode;
  selectedItemsCount: number;
  onInfoLinkClick?: () => void;
  onCreateClick?: () => void;
}

export function FullPageHeader({
  title = "Distributions",
  createButtonText = "Create distribution",
  extraActions = null,
  selectedItemsCount,
  onInfoLinkClick,
  onCreateClick,
  ...props
}: FullPageHeaderProps) {
  const isOnlyOneSelected = selectedItemsCount === 1;

  return (
    <Header
      variant="awsui-h1-sticky"
      info={onInfoLinkClick && <InfoLink onFollow={onInfoLinkClick} />}
      actions={
        <SpaceBetween size="xs" direction="horizontal">
          {extraActions}
          <Button
            data-testid="header-btn-view-details"
            disabled={!isOnlyOneSelected}
          >
            View details
          </Button>
          <Button data-testid="header-btn-edit" disabled={!isOnlyOneSelected}>
            Edit
          </Button>
          <Button
            data-testid="header-btn-delete"
            disabled={selectedItemsCount === 0}
          >
            Delete
          </Button>
          {onCreateClick && createButtonText && (
            <Button 
              data-testid="header-btn-create" 
              variant="primary"
              onClick={onCreateClick}
            >
              {createButtonText}
            </Button>
          )}
        </SpaceBetween>
      }
      {...props}
    >
      {title}
    </Header>
  );
}
