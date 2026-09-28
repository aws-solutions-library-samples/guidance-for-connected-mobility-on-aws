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
  onEditClick?: () => void;
  onDeleteClick?: () => void;
}

export function FullPageHeader({
  title = "Distributions",
  createButtonText = "Create distribution",
  extraActions = null,
  selectedItemsCount,
  onInfoLinkClick,
  onCreateClick,
  onEditClick,
  onDeleteClick,
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
            data-testid="header-btn-edit" 
            disabled={!isOnlyOneSelected}
            onClick={onEditClick}
          >
            Edit
          </Button>
          <Button
            data-testid="header-btn-delete"
            disabled={selectedItemsCount === 0}
            onClick={onDeleteClick}
          >
            Delete
          </Button>
          {onCreateClick && createButtonText && (
            <Button 
              data-testid="header-btn-create" 
              variant="primary"
              onClick={(e) => {
                console.log('🔥 CREATE BUTTON CLICKED!', e);
                console.log('🔥 onCreateClick handler:', onCreateClick);
                if (onCreateClick) {
                  onCreateClick();
                } else {
                  console.log('🔥 NO onCreateClick handler provided!');
                }
              }}
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
