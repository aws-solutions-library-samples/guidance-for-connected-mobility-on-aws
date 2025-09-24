// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Button,
  Box,
  BreadcrumbGroup,
} from '@cloudscape-design/components';
import { HelpPanelProvider } from '../commons';
import { useNavigate } from 'react-router-dom';

const NotFoundHeader = () => (
  <div>
    <h3>Page Not Found</h3>
    <p>The page you're looking for doesn't exist.</p>
  </div>
);

const loadHelpPanelContent = (toolsContent: React.SetStateAction<React.ReactNode>) => {
  toolsContent(<NotFoundHeader />);
};

export default function NotFound() {
  const navigate = useNavigate();

  return (
    <HelpPanelProvider value={loadHelpPanelContent}>
      <BreadcrumbGroup
        items={[
          { text: 'Home', href: '/' },
          { text: 'Page Not Found' }
        ]}
      />
      <Container
        header={
          <Header
            variant="h1"
            description="The page you requested could not be found."
          >
            Page Not Found
          </Header>
        }
      >
        <SpaceBetween size="m">
          <Box textAlign="center">
            <SpaceBetween size="m">
              <div>
                <h2>404</h2>
                <p>Sorry, the page you are looking for doesn't exist.</p>
              </div>
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => navigate(-1)}>Go Back</Button>
                <Button variant="primary" onClick={() => navigate('/')}>
                  Go to Home
                </Button>
              </SpaceBetween>
            </SpaceBetween>
          </Box>
        </SpaceBetween>
      </Container>
    </HelpPanelProvider>
  );
}
