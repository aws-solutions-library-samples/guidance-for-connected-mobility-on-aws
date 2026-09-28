// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * KpiCardGrid.test.tsx — tests for the KpiCardGrid component.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T3.1 AC4
 *
 * Key assertions:
 * - Grid always renders even when children count is 0 (no loading/error gate).
 * - Renders all children.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import KpiCardGrid from "../KpiCardGrid";
import KpiCard from "../KpiCard";

describe("KpiCardGrid", () => {
  it("renders all children", () => {
    render(
      <KpiCardGrid>
        <KpiCard label="Card 1" value="10" color="text-status-info" captions={[]} />
        <KpiCard label="Card 2" value="20" color="text-status-success" captions={[]} />
        <KpiCard label="Card 3" value="30" color="text-status-warning" captions={[]} />
      </KpiCardGrid>
    );

    expect(screen.getByText("Card 1")).toBeTruthy();
    expect(screen.getByText("Card 2")).toBeTruthy();
    expect(screen.getByText("Card 3")).toBeTruthy();
  });

  it("renders without throwing when children array is empty (no loading gate)", () => {
    // T3.1 constraint: DO NOT gate KpiCardGrid on !loading && !error.
    // Grid must always render; individual cards show 0 when data is absent.
    expect(() =>
      render(<KpiCardGrid>{[] as React.ReactNode}</KpiCardGrid>)
    ).not.toThrow();
  });

  it("renders a single child", () => {
    render(
      <KpiCardGrid>
        <KpiCard label="Only" value="1" color="text-status-info" captions={[]} />
      </KpiCardGrid>
    );
    expect(screen.getByText("Only")).toBeTruthy();
  });

  it("renders six children (Command Center tile count)", () => {
    const labels = [
      "Connectivity",
      "Software",
      "Quality",
      "Security",
      "Manufacturing",
      "Approvals",
    ];
    render(
      <KpiCardGrid>
        {labels.map((label) => (
          <KpiCard key={label} label={label} value="0" color="text-body-secondary" captions={[]} />
        ))}
      </KpiCardGrid>
    );

    for (const label of labels) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });
});
