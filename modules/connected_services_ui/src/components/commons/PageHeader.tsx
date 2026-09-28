// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PageHeader.tsx — Connected Services portal page banner.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md D9
 * Modelled on: DMS frontend/src/components/commons/PageHeader.tsx (read-only)
 *
 * Renders a dark banner (breadcrumbs, h1 title, optional description, optional
 * actions slot) that sits inside AppLayout's `content` slot, to the right of
 * the SideNavigation. Individual views do NOT render their own <Header>.
 *
 * ## Contained banner — no viewport escape
 *
 * This component uses a CONTAINED layout per spec D9. AppLayout's content
 * column is already to the right of the 280px nav; a plain block element fills
 * that column exactly. No `width:100vw; left:50%; margin-left:-50vw` escape —
 * the arithmetic that requires was the "~100px bleed into the nav" defect in
 * DMS's early rounds.
 *
 * AppLayout's three content gaps are cancelled with `calc(-1 * 24px)` and
 * `calc(-1 * 12px)` as LITERAL FALLBACK VALUES, not hashed Cloudscape custom-
 * property names. This module is on `@cloudscape-design/components@3.0.1354`;
 * DMS is on `3.0.839`. Cloudscape hashes custom-property names per build, so
 * copying DMS's `-g964ok` / `-7l52k3` suffixes is a silent no-op here — the
 * specific failure that cost DMS three of its nine rounds. T7.3 refines the
 * token references from a live Computed-styles read.
 *
 * ## Export contract
 *
 * Default export: the PageHeader component.
 * Named export: PageHeaderProps type (for AppShell and tests).
 *
 * ## No API imports
 *
 * This file imports nothing from any API client, fixture, or fetch surface.
 */

import React from "react";
import BreadcrumbGroup, {
  BreadcrumbGroupProps,
} from "@cloudscape-design/components/breadcrumb-group";
import "./PageHeader.css";

// ── Prop types ─────────────────────────────────────────────────────────────────

export interface PageHeaderProps {
  /** Page title rendered as an h1. */
  title: string;
  /** Optional description rendered below the title. */
  description?: string;
  /** Breadcrumb trail. Pass an empty array to render no breadcrumbs. */
  breadcrumbs: BreadcrumbGroupProps.Item[];
  /** Optional handler for breadcrumb link clicks. */
  onBreadcrumbFollow?: (event: CustomEvent<BreadcrumbGroupProps.ClickDetail>) => void;
  /** Optional page-level action slot rendered beside the title. */
  actions?: React.ReactNode;
}

// ── Component ──────────────────────────────────────────────────────────────────

const PageHeader: React.FC<PageHeaderProps> = ({
  title,
  description,
  breadcrumbs,
  onBreadcrumbFollow,
  actions,
}) => {
  return (
    <div className="cs-header">
      {/* Breadcrumb row */}
      {breadcrumbs.length > 0 && (
        <div className="cs-header-breadcrumbs">
          <BreadcrumbGroup
            items={breadcrumbs}
            ariaLabel="Breadcrumbs"
            onFollow={onBreadcrumbFollow}
          />
        </div>
      )}

      {/* Title + optional actions */}
      <div className="cs-header-title-section">
        <div
          className="cs-header-title-row"
          style={{ marginBottom: description ? "8px" : "0" }}
        >
          <h1 className="cs-header-title">{title}</h1>
          {actions && <div className="cs-header-actions">{actions}</div>}
        </div>

        {/* Optional description */}
        {description && <p className="cs-header-description">{description}</p>}
      </div>
    </div>
  );
};

export default PageHeader;
