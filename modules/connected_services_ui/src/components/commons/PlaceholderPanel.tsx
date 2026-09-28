// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PlaceholderPanel.tsx — renders the `availability: 'placeholder'` state.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md T3.3 AC5
 *
 * Displays:
 *   - The screen name
 *   - A plain statement that the screen is not built in this pass
 *   - The screen's settleMarker as a data attribute (so P1 can detect it in content)
 *
 * Constraints:
 *   - The settleMarker is embedded in the rendered DOM so the registry-completeness
 *     guard P1 can find it inside <main> after stripping <nav>.
 *   - No fixture import, no API import (T2.5 / commonsIsolated guard).
 *
 * ## No API imports
 */

import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import React from "react";

export interface PlaceholderPanelProps {
  /** Screen label shown as the panel heading. */
  screenLabel: string;
  /**
   * Unique settle marker for this screen. Embedded as a data attribute so
   * the P1 guard can find it in the content area after stripping <nav>.
   */
  settleMarker: string;
}

const PlaceholderPanel: React.FC<PlaceholderPanelProps> = ({
  screenLabel,
  settleMarker,
}) => (
  <Box
    padding="xxl"
    data-testid="placeholder-panel"
    // The settleMarker must appear in the rendered body. We render it as a
    // data attribute AND as a visually-hidden span so that textContent-based
    // assertions can find it without relying on attribute queries.
  >
    {/* settleMarker embedded in a hidden span for guard detection via textContent */}
    <span
      aria-hidden="true"
      style={{ display: "none" }}
      data-settle-marker={settleMarker}
    >
      {settleMarker}
    </span>

    <Header variant="h2">{screenLabel}</Header>

    <Box variant="p" color="text-body-secondary" margin={{ top: "s" }}>
      This screen is not yet available in the current release. It will be
      implemented in a future sprint.
    </Box>
  </Box>
);

export default PlaceholderPanel;
