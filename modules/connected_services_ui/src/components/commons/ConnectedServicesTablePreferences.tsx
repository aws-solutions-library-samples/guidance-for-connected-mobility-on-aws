// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ConnectedServicesTablePreferences — wraps Cloudscape CollectionPreferences
 * with standard Connected Services defaults.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T3.1 AC4
 * Reference: docs/tech.md § (g) — CollectionPreferencesProps verified against
 *   @cloudscape-design/components@3.0.1354
 *   node_modules/@cloudscape-design/components/collection-preferences/interfaces.d.ts
 *
 * ## Page-size options
 *
 * Default set: 25 / 50 / 100 (exported as DEFAULT_PAGE_SIZE_OPTIONS).
 *
 * ## Column visibility
 *
 * Uses `contentDisplayPreference` (NOT the deprecated `visibleContentPreference`).
 * Source: docs/tech.md § (g): "contentDisplayPreference — recommended for tables
 * (replaces visibleContentPreference)".
 *
 * When no `visibleContentOptions` are supplied only page-size is shown.
 *
 * ## Zero-import constraint (T2.5)
 *
 * Zero imports from *.fixture.ts, any API client, fetch/XHR, or ../screens/.
 */

import React from "react";
import CollectionPreferences from "@cloudscape-design/components/collection-preferences";
import type { CollectionPreferencesProps } from "@cloudscape-design/components/collection-preferences";

// ── Page-size options ─────────────────────────────────────────────────────────

/**
 * Standard Connected Services page-size options.
 * Exported so call sites can reference the same constant.
 */
export const DEFAULT_PAGE_SIZE_OPTIONS: CollectionPreferencesProps.PageSizeOption[] = [
  { value: 25, label: "25" },
  { value: 50, label: "50" },
  { value: 100, label: "100" },
];

// ── Preferences shape ─────────────────────────────────────────────────────────

/**
 * Minimal preferences state managed by ConnectedServicesTablePreferences.
 * Mirrors the subset of CollectionPreferencesProps.Preferences we use.
 */
export interface ConnectedServicesPreferences {
  pageSize?: number;
  contentDisplay?: Array<{ id: string; visible: boolean }>;
}

// ── ContentDisplayOption type ─────────────────────────────────────────────────

/**
 * A single option for the contentDisplayPreference panel.
 * Shape from docs/tech.md § (g) — CollectionPreferencesProps.ContentDisplayOption.
 */
export interface ContentDisplayOption {
  id: string;
  label: string;
  alwaysVisible?: boolean;
}

// ── Props ─────────────────────────────────────────────────────────────────────

export interface ConnectedServicesTablePreferencesProps {
  /** Current preferences state. */
  preferences: ConnectedServicesPreferences;

  /**
   * Called when the user confirms changes.
   * Receives the new preferences.
   */
  onConfirm: (preferences: ConnectedServicesPreferences) => void;

  /**
   * Column-visibility options for contentDisplayPreference.
   * When supplied: page-size + column-visibility controls rendered.
   * When absent: page-size only.
   */
  visibleContentOptions?: ContentDisplayOption[];

  /**
   * Human-readable resource name for the preferences panel title.
   * E.g. "vehicles" → "Preferences (vehicles)".
   */
  resourceName: string;
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * ConnectedServicesTablePreferences wraps CollectionPreferences with
 * standard Connected Services defaults.
 *
 * Uses contentDisplayPreference (not the deprecated visibleContentPreference)
 * when column visibility options are provided. See docs/tech.md § (g).
 */
export function ConnectedServicesTablePreferences({
  preferences,
  onConfirm,
  visibleContentOptions,
  resourceName,
}: ConnectedServicesTablePreferencesProps): React.ReactElement {
  const pageSizePreference: CollectionPreferencesProps.PageSizePreference = {
    title: "Page size",
    options: DEFAULT_PAGE_SIZE_OPTIONS,
  };

  const contentDisplayPreference:
    | CollectionPreferencesProps.ContentDisplayPreference
    | undefined =
    visibleContentOptions && visibleContentOptions.length > 0
      ? {
          title: "Column display",
          description: "Select the columns to display",
          options: visibleContentOptions,
        }
      : undefined;

  return (
    <CollectionPreferences
      title={`Preferences (${resourceName})`}
      confirmLabel="Confirm"
      cancelLabel="Cancel"
      preferences={preferences}
      onConfirm={({ detail }) =>
        onConfirm({
          pageSize: detail.pageSize,
          contentDisplay: detail.contentDisplay
            ? [...detail.contentDisplay]
            : undefined,
        })
      }
      pageSizePreference={pageSizePreference}
      contentDisplayPreference={contentDisplayPreference}
    />
  );
}
