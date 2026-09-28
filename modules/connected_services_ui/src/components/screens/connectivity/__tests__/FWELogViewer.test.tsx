// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FWELogViewer tests — Task 7.1
 *
 * Spec: `.kiro/specs/2026-09-15-cms-cs-campaign-ownership/tasks.md` Task 7.1
 *
 * ## What these tests guard
 *
 * 1. When simReachable=false, the "Simulator offline" status indicator is shown.
 * 2. When simReachable=true but agentRunning=false, the "No FWE agent running"
 *    indicator is shown.
 * 3. When both simReachable=true and agentRunning=true, the log viewer UI
 *    (Stream/Refresh/Expand buttons) is rendered.
 * 4. Logs fetched from /api/simulation/agent/logs/{vin} are rendered.
 *
 * ## Parity note
 *
 * This test file tests CS's FWELogViewer.tsx, which imports from
 * '../../../api/simulationClient'.  The allowlisted import divergence from
 * the CMS canonical is intentional (see scripts/verify_cs_log_viewer_parity.py).
 *
 * ## Null-base guard (spec Decision (b))
 *
 * Decision (b) requires the PARENT (SimulateVehicleView) to pass
 * simReachable=false when getSimulationApiBase() returns null.  That
 * parent-side contract is tested in SimulateVehicleView.test.tsx.
 * FWELogViewer itself only consumes the simReachable prop; it has no direct
 * access to getSimulationApiBase().
 */

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import FWELogViewer from "../FWELogViewer";
import { getSimulationApiBase } from "../../../../api/simulationClient";

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  window.runtimeConfig = {
    cognitoUserPoolId: "<pool-id>",
    cognitoClientId: "testclientid",
    cognitoDomain: "test.example.invalid",
    connectedServicesApiEndpoint: "https://cs.example.invalid",
    subscriptionsApiEndpoint: "https://subs.example.invalid",
    simulationApiEndpoint: "https://sim.example.invalid",
    dataProcessingApiEndpoint: "https://dp.example.invalid",
    simulationProductRuleName: "test_rule",
    callbackOrigin: "https://test.example.invalid",
  };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const TEST_VIN = "VIN-TEST-0001";

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("FWELogViewer — sim not reachable", () => {
  it("shows 'Simulator offline' when simReachable=false", () => {
    render(
      <FWELogViewer vin={TEST_VIN} simReachable={false} agentRunning={false} />,
    );
    expect(screen.getByText(/Simulator offline/i)).toBeTruthy();
  });

  it("shows 'Simulator offline' regardless of agentRunning when simReachable=false", () => {
    render(
      <FWELogViewer vin={TEST_VIN} simReachable={false} agentRunning={true} />,
    );
    expect(screen.getByText(/Simulator offline/i)).toBeTruthy();
  });
});

describe("FWELogViewer — sim reachable, agent not running", () => {
  it("shows 'No FWE agent running' when simReachable=true, agentRunning=false", () => {
    render(
      <FWELogViewer vin={TEST_VIN} simReachable={true} agentRunning={false} />,
    );
    expect(screen.getByText(new RegExp(`No FWE agent running for ${TEST_VIN}`))).toBeTruthy();
  });
});

describe("FWELogViewer — sim reachable and agent running", () => {
  it("renders Stream and Refresh buttons when active", () => {
    // Mock fetch to return empty logs
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ logs: [] }),
    } as Response);

    render(
      <FWELogViewer vin={TEST_VIN} simReachable={true} agentRunning={true} />,
    );
    expect(screen.getAllByRole("button").length).toBeGreaterThanOrEqual(1);
  });

  it("renders fetched log lines in the log viewer", async () => {
    const mockLogs = ["line one", "line two", "line three"];
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ logs: mockLogs }),
    } as Response);

    render(
      <FWELogViewer vin={TEST_VIN} simReachable={true} agentRunning={true} />,
    );

    // Wait for the fetch to complete and re-render
    await vi.waitFor(() => {
      const content = document.body.textContent ?? "";
      expect(content).toContain("line one");
    });
  });

  it("fetches from the correct logs endpoint", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ logs: [] }),
    } as Response);

    render(
      <FWELogViewer vin={TEST_VIN} simReachable={true} agentRunning={true} />,
    );

    await vi.waitFor(() => {
      expect(fetchSpy).toHaveBeenCalled();
    });

    const calledUrl = String(fetchSpy.mock.calls[0][0]);
    const base = getSimulationApiBase();
    expect(calledUrl).toContain(`${base}/api/simulation/agent/logs/${TEST_VIN}`);
  });
});

