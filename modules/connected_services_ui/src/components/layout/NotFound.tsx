// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * NotFound.tsx — SPA catchall for unknown routes.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md T3.3 AC6
 *
 * Extracted from App.tsx into its own file so that App.tsx can import it
 * cleanly and tests can assert its standalone render.
 */

import Box from "@cloudscape-design/components/box";
import React from "react";

const NotFound: React.FC = () => (
  <Box padding="xxl" data-testid="not-found-view">
    <p>Page not found. Use the navigation to return to a valid route.</p>
  </Box>
);

export default NotFound;
