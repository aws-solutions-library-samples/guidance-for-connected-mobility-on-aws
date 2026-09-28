// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * KpiCard — one KPI metric tile for the page-level summary strip.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T3.1 AC4
 * Reference: CMS modules/cms_ui/source/frontend/src/components/VehiclesPage.tsx:37-57
 *
 * ## Design
 *
 * Follows the CMS VehiclesPage.tsx pattern exactly:
 *
 *   <Container header={<Header variant="h2">{label}</Header>}>
 *     <Box variant="h1" color={color}>{value}</Box>
 *     {captions.map(c => (
 *       <div key={c}><Box variant="small" color="text-body-secondary">{c}</Box></div>
 *     ))}
 *   </Container>
 *
 * ## Color constraint
 *
 * `color` MUST be a Cloudscape semantic token from:
 *   text-status-info | text-status-success | text-status-warning |
 *   text-status-error | text-body-secondary
 *
 * No raw hex, no inline style={{ color: ... }}, no px font sizes.
 * Cloudscape resolves semantic tokens to CSS custom properties internally.
 *
 * ## Zero-import constraint (T2.5)
 *
 * Zero imports from *.fixture.ts, any API client, fetch/XHR, or ../screens/.
 */

import React from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Box from "@cloudscape-design/components/box";

// ── Color token type ──────────────────────────────────────────────────────────

/**
 * Accepted Cloudscape semantic color tokens for the `color` prop.
 */
export type KpiColorToken =
  | "text-status-info"
  | "text-status-success"
  | "text-status-warning"
  | "text-status-error"
  | "text-body-secondary";

// ── Props ─────────────────────────────────────────────────────────────────────

export interface KpiCardProps {
  /** Card title rendered inside a Header variant="h2". */
  label: string;

  /**
   * Metric value rendered inside Box variant="h1" with the semantic color token.
   * Pass the stringified count — e.g. String(items.length).
   * Counts derive from arrays that are `[]` in every data state and render "0" cleanly.
   */
  value: string;

  /**
   * Cloudscape semantic color token for the value element.
   * Must be one of the KpiColorToken values.
   */
  color: KpiColorToken;

  /**
   * Zero or more captions rendered below the value as
   * Box variant="small" elements.
   */
  captions: string[];
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * KpiCard renders one metric tile following the CMS VehiclesPage.tsx pattern:
 * Container with h2 header, h1 value in a semantic color, small captions.
 *
 * The component has no loading/error gates — it always renders.
 * Counts come from arrays that default to [] so "0" is a valid, clean render.
 * This follows the T3.1 constraint: "Do NOT gate KpiCardGrid on !loading && !error."
 */
export default function KpiCard({
  label,
  value,
  color,
  captions,
}: KpiCardProps): React.ReactElement {
  return (
    <Container header={<Header variant="h2">{label}</Header>}>
      <Box variant="h1" color={color}>
        {value}
      </Box>
      {captions.map((caption) => (
        <div key={caption}>
          <Box variant="small" color="text-body-secondary">
            {caption}
          </Box>
        </div>
      ))}
    </Container>
  );
}
