// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  Container,
  SpaceBetween,
  Box
} from '@cloudscape-design/components';

export default function SignalCatalogView() {
  return (
      <Container>
        <SpaceBetween size="l">
          <Box textAlign="center" padding="xxl">
            <Box variant="h2" margin={{ bottom: 'm' }}>
              Signal Catalog Content
            </Box>
            <Box variant="p" color="text-body-secondary">
              This page will contain signal catalog management functionality.
            </Box>
          </Box>
        </SpaceBetween>
      </Container>
  );
}
