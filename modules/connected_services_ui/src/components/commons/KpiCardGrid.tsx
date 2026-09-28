// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * KpiCardGrid — responsive grid wrapper for KpiCard tiles.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T3.1 AC4
 * Reference: CMS modules/cms_ui/source/frontend/src/components/VehiclesPage.tsx:37-40
 *
 * ## Design
 *
 * Follows the CMS VehiclesPage pattern:
 *   <Grid gridDefinition={[{ colspan: N }, ...]}>
 *     {children}
 *   </Grid>
 *
 * where each entry in `gridDefinition` corresponds to one child. The grid spans
 * 12 columns; each child receives `colspan = Math.floor(12 / count)`.
 *
 *   6 children → colspan 2  (6-column layout for Command Center tiles)
 *   4 children → colspan 3  (canonical CMS 4-column layout)
 *   3 children → colspan 4
 *   2 children → colspan 6
 *   1 child    → colspan 12
 *
 * ## No loading/error gate (T3.1 Constraints — hard-won DMS lesson)
 *
 * DO NOT add `if (!loading && !error) { ... }` around the grid render.
 * DMS did, and a 401 made the entire KPI strip vanish so the page read as
 * broken rather than showing zeros. The correct pattern is:
 *   - Arrays default to [] in every data state.
 *   - String(array.length) === "0" — a clean, honest render.
 *   - The grid always renders; individual cards show 0 when data is absent.
 *
 * ## Constraints
 *
 * - MUST use Cloudscape <Grid gridDefinition={...}>, NOT <div style={{display:'grid'}}>.
 * - NO hardcoded grid-template-columns with px values.
 * - NO inline color styles (all colors delegated to KpiCard).
 *
 * ## Zero-import constraint (T2.5)
 *
 * Zero imports from *.fixture.ts, any API client, fetch/XHR, or ../screens/.
 */

import React from "react";
import Grid from "@cloudscape-design/components/grid";

// ── Props ─────────────────────────────────────────────────────────────────────

export interface KpiCardGridProps {
  /** KpiCard elements (or any React children) to lay out in the grid. */
  children: React.ReactNode;
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * KpiCardGrid wraps KpiCard children in a Cloudscape Grid, distributing
 * 12 columns evenly across however many children are provided.
 *
 * The grid always renders — no loading/error gate. Counts from fixture arrays
 * default to 0 cleanly. See the "No loading/error gate" section above.
 */
export default function KpiCardGrid({
  children,
}: KpiCardGridProps): React.ReactElement {
  const childArray = React.Children.toArray(children);
  const count = Math.max(childArray.length, 1);
  const colspan = Math.floor(12 / count);

  const gridDefinition = childArray.map(() => ({ colspan }));

  return <Grid gridDefinition={gridDefinition}>{children}</Grid>;
}
