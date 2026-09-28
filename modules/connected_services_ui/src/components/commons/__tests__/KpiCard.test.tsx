// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * KpiCard.test.tsx — tests for the KpiCard component.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T3.1 AC4
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import KpiCard from "../KpiCard";

describe("KpiCard", () => {
  it("renders the label as a heading", () => {
    render(
      <KpiCard label="Connected" value="42" color="text-status-success" captions={[]} />
    );
    expect(screen.getByText("Connected")).toBeTruthy();
  });

  it("renders the value", () => {
    render(
      <KpiCard label="Degraded" value="7" color="text-status-warning" captions={[]} />
    );
    expect(screen.getByText("7")).toBeTruthy();
  });

  it("renders captions", () => {
    render(
      <KpiCard
        label="Unreachable"
        value="3"
        color="text-status-error"
        captions={["of 50 total", "in US market"]}
      />
    );
    expect(screen.getByText("of 50 total")).toBeTruthy();
    expect(screen.getByText("in US market")).toBeTruthy();
  });

  it("renders '0' cleanly when value is zero (no loading gate needed)", () => {
    // T3.1 constraint: counts from [] arrays default to 0, must render cleanly.
    render(
      <KpiCard label="NTN Fallback" value="0" color="text-body-secondary" captions={[]} />
    );
    expect(screen.getByText("0")).toBeTruthy();
  });
});
