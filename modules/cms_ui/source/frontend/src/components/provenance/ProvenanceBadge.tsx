// SPDX-License-Identifier: Apache-2.0

/**
 * <ProvenanceBadge> — surfaces the data-quality origin of a value.
 *
 * Design constraint (spec § D1): the badge is NOT a tooltip and NOT hover-only.
 * An unopened tooltip is an unlabelled value — the very defect this component
 * exists to prevent. The marker must be visible without user interaction.
 *
 * Two rendering modes driven by the `variant` prop:
 *   - "row"   (default) — compact inline chip for table cells / list rows.
 *   - "panel"           — larger labelled block for a whole-panel dataset header.
 *
 * The absent-provenance case ("unknown") renders a visible "provenance unknown"
 * marker, never nothing. Existing seeded rows lack the field, so this is a real
 * production path.
 *
 * Styling uses Cloudscape design tokens, not literal hex. The app has a
 * dark-mode theme switch — hardcoded colours render incorrectly under it.
 */

import React from "react";
import { Box } from "@cloudscape-design/components";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ProvenanceValue =
  | "simulated"
  | "measured"
  | "derived"
  | "reference"
  | null
  | undefined;

export interface ProvenanceBadgeProps {
  /** The provenance value. null/undefined → "provenance unknown" marker. */
  provenance: ProvenanceValue;
  /**
   * "row"   — compact inline chip, suitable for table cells (default).
   * "panel" — larger block header label, suitable for whole-panel datasets.
   */
  variant?: "row" | "panel";
  /** Optional additional className forwarded to the outer element. */
  className?: string;
}

// ---------------------------------------------------------------------------
// Per-value display config
// ---------------------------------------------------------------------------

interface BadgeConfig {
  /** Short label visible at all times (no interaction required). */
  label: string;
  /**
   * Background color CSS string using Cloudscape design tokens.
   * Format: var(--<token-name>, <fallback-hex>)
   *
   * Tokens chosen from the Cloudscape palette; the fallback hex is the
   * light-mode value and is used only when the CSS custom property is
   * unresolvable (e.g. in vitest/jsdom where stylesheets are not loaded).
   */
  background: string;
  /** Foreground/text color token. */
  color: string;
  /** aria-label for screen readers (includes the full meaning). */
  ariaLabel: string;
}

const BADGE_CONFIG: Record<string, BadgeConfig> = {
  simulated: {
    label: "Simulated",
    // Cloudscape "warning" tokens — amber/orange communicates synthetic origin
    background: "var(--color-background-status-warning-2rnlcm, #fdf3e0)",
    color: "var(--color-text-status-warning-6meo06, #d4a017)",
    ariaLabel: "Data provenance: simulated — produced by a seed script or simulator",
  },
  derived: {
    label: "Derived",
    // Cloudscape "info" tokens — blue communicates computed/secondary nature
    background: "var(--color-background-status-info-ennhxq, #f0f4ff)",
    color: "var(--color-text-status-info-2u3yw9, #0972d3)",
    ariaLabel: "Data provenance: derived — computed from other records; inherits weakest input",
  },
  reference: {
    label: "Reference",
    // Cloudscape "success" tokens — green communicates authoritative catalog data
    background: "var(--color-background-status-success-zv6me5, #ebf8f0)",
    color: "var(--color-text-status-success-ybmii8, #00802f)",
    ariaLabel: "Data provenance: reference — catalog or standards data (DTC definitions, NHTSA recalls)",
  },
  // "measured" is the baseline; no badge is shown for measured values.
  // "unknown" is the absent/null/undefined case — always rendered visibly.
  unknown: {
    label: "Provenance unknown",
    // Cloudscape "error" tokens — red communicates missing/unreliable origin
    background: "var(--color-background-status-error-6pum7p, #fff7f7)",
    color: "var(--color-text-status-error-ksqavh, #d13313)",
    ariaLabel: "Data provenance unknown — origin of this value is not recorded",
  },
} as const;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Renders a visible provenance marker for any non-`measured` value.
 *
 * Returns null only for `measured` (the quality baseline; no label needed).
 * Every other case — including absent/null/undefined — renders a badge.
 */
export const ProvenanceBadge: React.FC<ProvenanceBadgeProps> = ({
  provenance,
  variant = "row",
  className,
}) => {
  // "measured" is the quality baseline — no badge needed; the absence of a
  // badge communicates "this is real measured data".
  if (provenance === "measured") {
    return null;
  }

  const key = provenance == null ? "unknown" : provenance;
  const config = BADGE_CONFIG[key] ?? BADGE_CONFIG.unknown;

  const isPanel = variant === "panel";

  const containerStyle: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: "4px",
    // Sizing: panel mode is larger and block-level; row mode is compact inline
    padding: isPanel ? "4px 10px" : "2px 6px",
    borderRadius: "4px",
    background: config.background,
    color: config.color,
    // Use a border with the same color token at reduced opacity so the badge
    // reads in both light and dark modes without a separate border token.
    border: `1px solid currentColor`,
    fontSize: isPanel ? "14px" : "11px",
    fontWeight: isPanel ? 600 : 500,
    lineHeight: isPanel ? "20px" : "16px",
    whiteSpace: "nowrap",
    // Ensure the badge sits above sibling text when used inline
    verticalAlign: "middle",
  };

  return (
    <span
      className={className}
      style={containerStyle}
      aria-label={config.ariaLabel}
      data-testid={`provenance-badge-${key}`}
      data-provenance={key}
      data-variant={variant}
    >
      {/*
       * No icon-only rendering: text is always visible.
       * An icon-only badge is effectively a tooltip — it requires hover/focus
       * to convey meaning. The spec explicitly forbids that pattern.
       */}
      {isPanel ? (
        <Box variant="span" fontSize="body-s">
          <strong>Data: </strong>
          {config.label}
        </Box>
      ) : (
        config.label
      )}
    </span>
  );
};

export default ProvenanceBadge;
