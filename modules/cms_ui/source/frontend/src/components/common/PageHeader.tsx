// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { BreadcrumbGroup, BreadcrumbGroupProps, Button, SpaceBetween } from '@cloudscape-design/components';
import '../Header.css';

interface ButtonConfig {
  text: string;
  iconName?: string;
  variant?: 'normal' | 'primary' | 'link';
  onClick?: () => void;
}

interface PageHeaderProps {
  title: string;
  description?: string;
  breadcrumbs: BreadcrumbGroupProps.Item[];
  onBreadcrumbFollow?: (event: CustomEvent<BreadcrumbGroupProps.ClickDetail>) => void;
  helpIcon?: React.ReactNode;
  buttons?: ButtonConfig[];
}

export const PageHeader: React.FC<PageHeaderProps> = ({
  title,
  description,
  breadcrumbs,
  onBreadcrumbFollow,
  helpIcon,
  buttons
}) => {
  return (
    <>
      <div className="cms-header">
      {/* Breadcrumbs and Help Icon Row */}
      <div className="cms-header-content">
        <BreadcrumbGroup
          items={breadcrumbs}
          ariaLabel="Breadcrumbs"
          onFollow={onBreadcrumbFollow}
        />
        
        {/* Help Icon aligned with breadcrumbs */}
        {helpIcon && (
          <div className="page-header-help-icon page-header">
            {helpIcon}
          </div>
        )}
      </div>
      
      {/* Title Section - Full Width */}
      <div className="cms-header-title-section">
        {/* Title and Buttons Row */}
        <div className="cms-header-title-row" style={{
          marginBottom: description ? '8px' : '0'
        }}>
          <h1 className="cms-header-title">
            {title}
          </h1>
          
          {/* Buttons aligned with title */}
          {buttons && buttons.length > 0 && (
            <div className="page-header-actions">
              <SpaceBetween direction="horizontal" size="xs">
                {buttons.map((button, index) => (
                  <Button
                    key={index}
                    variant={button.variant || 'normal'}
                    iconName={button.iconName}
                    onClick={button.onClick}
                  >
                    {button.text}
                  </Button>
                ))}
              </SpaceBetween>
            </div>
          )}
        </div>
        
        {/* Description below title */}
        {description && (
          <p className="cms-header-description">
            {description}
          </p>
        )}
      </div>
    </div>
    </>
  );
};
