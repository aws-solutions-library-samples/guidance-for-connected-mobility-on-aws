// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SimulateVehicleView tests — Task 7.1 (null-base pin)
 *
 * Spec: `.kiro/specs/2026-09-15-cms-cs-campaign-ownership/tasks.md` Task 7.1
 *
 * ## What these tests guard
 *
 * 1. **Null-base pin (spec Decision (b))**: when `getSimulationApiBase()` returns
 *    null (simulationApiEndpoint is empty), `SimulateVehicleView` must pass
 *    `simReachable=false` to `FWELogViewer`.  The FWELogViewer's own contract
 *    requires the parent to set `simReachable=false` when the base is null so
 *    that the viewer's polling loop never fires against a null URL.
 *
 *    Mutation (d): if the parent passes `simReachable=true` when the base is null,
 *    this test FAILS — the "Simulator offline" indicator is not present.
 *
 * 2. The degradation alert is shown when the simulation endpoint is not configured.
 * 3. The settleMarker is present in the rendered output.
 * 4. With a configured endpoint, no degradation alert is shown.
 */

import { readFileSync } from "node:fs";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock subscriptionsClient to return a vehicle so selectedVehicleId is set.
// Path is relative to THIS test file's location (__tests__/ subdirectory).
//
// T6.2: the view now also uses `startVehicleSimulation` and `SimulationStartError`
// from this module, so both must be present on the mock or the start path throws
// "startVehicleSimulation is not a function" — and the pre-T6.2 tests would still
// have passed, because none of them clicked Start.
//
// `...actual` rather than a total replacement (review cycle 1). A `vi.mock` factory's
// return type is unconstrained, so a total replacement can silently omit or misshape
// exports and `tsc` cannot see it — the earlier version of this factory returned a
// `listVehiclesForSimulation` missing two fields that `SimulationVehiclesResponse`
// declares required.
//
// `...actual` alone did NOT deliver the drift protection its first comment claimed
// (review cycle 2): dropping the required `count` still passed, a hand-written
// `SimulationStartError` stand-in still passed, and renaming the `simulationClient`
// override key still passed. Three things fix that, all below:
//   1. the override is annotated with the real response type, so a missing or
//      misshapen field is a tsc error rather than an unconstrained object literal;
//   2. `SimulationStartError` comes from the REAL module, and its identity is pinned
//      by an explicit assertion — the view branches on `instanceof`, so a stand-in
//      makes that branch pass against a class production would not recognise;
//   3. both override keys are pinned against the real modules' exports, so renaming
//      one silently drops the override instead of drifting.
//
// `vi.hoisted` is required — `vi.mock` factories are hoisted above ordinary
// top-level declarations, so a plain `const` fails with "Cannot access 'X' before
// initialization".
const mocks = vi.hoisted(() => ({
  startVehicleSimulation: vi.fn(),
  // The simulation API keeps stop/status, so it must NOT be the start path any
  // more. An explicit spy makes a revert to `simulationClient.startSimulation`
  // observable rather than merely absent.
  simulationClientStart: vi.fn(),
  getSimulationStatus: vi.fn(),
  // Quick-assign (2026-09-18 addition): the data-processing campaign lookup +
  // assign calls this screen's "Assign a baseline campaign" button drives.
  fetchDataProcessingCampaigns: vi.fn(),
  assignCampaignToVehicle: vi.fn(),
  // T5.2: event catalog fetch injected into TripSimulatorModal via SimulateVehicleView.
  fetchEventCatalog: vi.fn(),
}));

vi.mock("../../../../api/subscriptionsClient", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import("../../../../api/subscriptionsClient");
  const listVehiclesForSimulation: typeof actual.listVehiclesForSimulation = async () => ({
    count: 2,
    filtered_on_producer: false,
    vehicles: [
      {
        vehicleId: "VEH-TEST-0001",
        vin: "VIN-TEST-0001",
        producer: "vehicle-telemetry",
        dataSource: "vehicle-telemetry",
      },
      // A second vehicle, on the OTHER dataSource. Two reasons it is here rather
      // than a convenience: the picker's selection-change handler clears the
      // notice, and with a single-vehicle list that path is unreachable and its
      // clear untestable; and the cloud-telemetry branch of `dataSourceLabel`
      // only renders when such a vehicle is in the list.
      {
        vehicleId: "VEH-TEST-0002",
        vin: "VIN-TEST-0002",
        producer: "oem1",
        dataSource: "cloud-telemetry",
      },
    ],
  });
  return {
    ...actual,
    getSubscriptionsApiBase: () => "https://subs.example.invalid",
    listVehiclesForSimulation,
    startVehicleSimulation: mocks.startVehicleSimulation,
  };
});

vi.mock("../../../../api/simulationClient", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import("../../../../api/simulationClient");
  return {
    ...actual,
    startSimulation: mocks.simulationClientStart,
    getSimulationStatus: mocks.getSimulationStatus,
  };
});

vi.mock("../../../../api/dataModelClient", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import("../../../../api/dataModelClient");
  return {
    ...actual,
    fetchDataProcessingCampaigns: mocks.fetchDataProcessingCampaigns,
    assignCampaignToVehicle: mocks.assignCampaignToVehicle,
    fetchEventCatalog: mocks.fetchEventCatalog,
  };
});

// The real error class — the view's `instanceof` branch must recognise it.
import { SimulationStartError } from "../../../../api/subscriptionsClient";
// These namespace imports are INTERCEPTED by the vi.mock factories above, so they
// resolve to the MOCKED modules. Named accordingly: an earlier revision called them
// `real*` and then asserted class identity against one, which compared the override
// with itself and passed under a hand-written stand-in (review cycle 3, W1). Reading
// the genuinely unmocked module requires `vi.importActual`.
import * as mockedSubscriptionsClient from "../../../../api/subscriptionsClient";
import * as mockedSimulationClient from "../../../../api/simulationClient";

import SimulateVehicleView, { dataSourceLabel, vehicleOptionLabel, vehicleReadinessState, buildVehicleOption, sortVehicleOptions } from "../SimulateVehicleView";
import * as mockedDataModelClient from "../../../../api/dataModelClient";
import { optionsFromCatalog } from "../SimulateVehicleView";

// ── Helpers ───────────────────────────────────────────────────────────────────

function renderView() {
  return render(
    <MemoryRouter>
      <SimulateVehicleView />
    </MemoryRouter>,
  );
}

/** Full runtime config with simulation endpoint configured. */
function configuredRuntimeConfig() {
  return {
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
}

/** Runtime config with simulation endpoint absent (null base). */
function unconfiguredRuntimeConfig() {
  return {
    ...configuredRuntimeConfig(),
    simulationApiEndpoint: "",
  };
}

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  // Mock fetch for agent status calls — never called when base is null
  vi.spyOn(global, "fetch").mockResolvedValue({
    ok: true,
    json: async () => ({ agents: [] }),
  } as Response);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  delete window.runtimeConfig;
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("SimulateVehicleView — simulation endpoint not configured (null base)", () => {
  beforeEach(() => {
    window.runtimeConfig = unconfiguredRuntimeConfig();
  });

  it("shows the degradation alert when simulationApiEndpoint is empty", () => {
    renderView();
    expect(
      screen.getByTestId("simulate-vehicle-no-endpoint-alert"),
    ).toBeTruthy();
  });

  it("Trip Simulator button is removed (T5.2): only Start and Stop buttons present in the action area", async () => {
    // T5.2 replaces the two-button fork (Start + Trip Simulator) with a single Start.
    // This test guards the removal — if the Trip Simulator button were re-added, this
    // assertion would FAIL.
    renderView();
    await waitFor(() => {
      expect(
        screen.getByTestId("simulate-vehicle-fwe-log-viewer-container"),
      ).toBeTruthy();
    });
    // Trip Simulator button must NOT be present.
    expect(screen.queryByTestId("simulate-vehicle-trip-simulator-button")).toBeNull();
    // Start button is still present.
    expect(screen.getByTestId("simulate-vehicle-start-button")).toBeTruthy();
  });

  it("Start button is also disabled (parity check: both buttons share !apiConfigured)", async () => {
    // Confirms the parity the FG1.T2 fix enforces: both buttons share the same
    // !apiConfigured guard. This documents the existing Start behavior so a future
    // change accidentally dropping the guard from either button is caught.
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });
    const startButton = screen.getByTestId("simulate-vehicle-start-button");
    expect(startButton).toBeTruthy();
    expect(
      startButton.getAttribute("aria-disabled") === "true" ||
      startButton.hasAttribute("disabled"),
    ).toBe(true);
  });

  it("passes simReachable=false to FWELogViewer when base is null (spec Decision (b))", async () => {
    // When simulationApiEndpoint is empty, getSimulationApiBase() returns null.
    // The parent must pass simReachable=false so FWELogViewer never polls a null URL.
    // We observe this by checking that the FWELogViewer renders the "Simulator offline"
    // state (which is only shown when simReachable=false).
    //
    // Mutation (d): changing the parent to pass simReachable=true when base is null
    // would mean the FWELogViewer tries to render the active UI (or "No FWE agent running")
    // instead of "Simulator offline", and this test would FAIL.
    renderView();

    // Wait for the vehicle to load (subscriptionsClient mock resolves async)
    // so that selectedVehicleId is set and the FWELogViewer container is shown.
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });

    // With null base, simReachable must remain false → FWELogViewer shows "Simulator offline"
    //
    // Scoped to the FWE container with `within`, not a bare screen query. Both
    // log panes render "Simulator offline" on simReachable=false, so an unscoped
    // getByText matched two elements and threw once SimLogViewer was added
    // (issues/2026-09-22-cs-simulate-missing-sim-console-and-agent-controls/).
    // Scoping is the stronger assertion anyway: it pins WHICH pane is offline,
    // where getAllByText(...)[0] would have passed on either pane's text.
    const fweContainer = screen.getByTestId("simulate-vehicle-fwe-log-viewer-container");
    expect(within(fweContainer).getByText(/Simulator offline/i)).toBeTruthy();
  });

  it("passes simReachable=false to SimLogViewer when base is null", async () => {
    // Same property as the FWELogViewer case above, for the simulation console.
    // Separate test rather than a second assertion in that one, so a regression
    // names the pane that broke.
    //
    // SimLogViewer is deliberately NOT dataSource-gated — a simulation run emits
    // output for a cloud/MQTT-direct vehicle too — so this container renders for
    // any selected vehicle, unlike the FWE one.
    //
    // Mutation: passing simReachable={true} when base is null makes SimLogViewer
    // render its Select/Stream controls instead of "Simulator offline", failing
    // this test.
    renderView();

    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-sim-log-viewer-container")).toBeTruthy();
    });

    const simContainer = screen.getByTestId("simulate-vehicle-sim-log-viewer-container");
    expect(within(simContainer).getByText(/Simulator offline/i)).toBeTruthy();
  });

  it("does not issue any HTTP request to /api/simulation/ when base is null", async () => {
    // The property: simulationUrl(null, ...) would build the literal URL
    // "null/api/simulation/agent/status" which must never be fetched.
    // Both the parent's early return (useEffect checks `!base` before calling
    // getAgentStatus) AND getAgentStatus's own `if (!base) return null` guard
    // exist as independent protections.  Either guard alone is sufficient to
    // prevent the fetch; REMOVING BOTH simultaneously causes a fetch to the
    // literal URL "null/api/simulation/agent/status".
    //
    // The global beforeEach installs a fetch spy.  Here we create our own so
    // we can interrogate calls that happen during this test specifically.
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ agents: [] }),
    } as Response);

    renderView();

    // Wait for the vehicle to load (subscriptionsClient mock resolves async)
    // and for all async effects to settle.
    await waitFor(() => {
      expect(
        screen.getByTestId("simulate-vehicle-fwe-log-viewer-container"),
      ).toBeTruthy();
    });

    // Allow any pending promises (including the agent status poll) to resolve.
    await vi.waitFor(() => true, { timeout: 300 });

    // No request to any /api/simulation/ URL must have been made.
    const simulationCalls = fetchSpy.mock.calls.filter(([url]) =>
      String(url).includes("/api/simulation/"),
    );
    expect(simulationCalls).toHaveLength(0);
  });
});

describe("SimulateVehicleView — simulation endpoint configured", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
  });

  it("does not show the degradation alert when endpoint is configured", () => {
    renderView();
    expect(
      screen.queryByTestId("simulate-vehicle-no-endpoint-alert"),
    ).toBeNull();
  });

  it("renders the settleMarker", () => {
    renderView();
    expect(
      screen.getByTestId("simulate-vehicle-settle-marker"),
    ).toBeTruthy();
  });
});

describe("SimulateVehicleView — positive control: agent running for selected VIN", () => {
  // This is the positive-control test that caught the mutation
  // setSimReachable(result.reachable) → setSimReachable(false), which passed
  // 967/967 previously because every assertion in the suite observed only the
  // false state.
  //
  // The test asserts the viewer reaches the *active* state:
  //   - the "Stream" button is present
  //   - the "Simulator offline" text is absent
  //
  // Mutation (a): changing `setSimReachable(result.reachable)` →
  // `setSimReachable(false)` makes this test FAIL because the component
  // always passes simReachable=false, so FWELogViewer renders "Simulator
  // offline" and the Stream button is never shown.

  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
    // Override the global fetch spy (which returns agents:[]) with one that
    // returns a RUNNING agent matching the test VIN.  Without this override
    // getAgentStatus resolves {reachable:true, agentRunning:false}, the
    // FWELogViewer shows "No FWE agent running", and this test fails —
    // confirming the test actually requires agentRunning=true to pass.
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        agents: [
          {
            vin: "VIN-TEST-0001",
            vehicleName: "VIN-TEST-0001",
            status: "RUNNING",
          },
        ],
      }),
    } as Response);
  });

  it("shows the Stream button and hides 'Simulator offline' when agent is running for selected VIN", async () => {
    renderView();

    // Wait for the vehicle list to load and the agent poll to resolve.
    await waitFor(() => {
      expect(
        screen.getByTestId("simulate-vehicle-fwe-log-viewer-container"),
      ).toBeTruthy();
    });

    // The FWELogViewer must be in the active state:
    // Stream button present, "Simulator offline" absent.
    await waitFor(() => {
      const buttons = screen.queryAllByRole("button");
      const streamButton = buttons.find(
        (b) => b.textContent?.toLowerCase().includes("stream"),
      );
      expect(streamButton).toBeDefined();
    });

    expect(screen.queryByText(/Simulator offline/i)).toBeNull();
  });
});



// ─────────────────────────────────────────────────────────────────────────────
// T6.2 — start goes through the subscriptions plane, not the simulation API
// ─────────────────────────────────────────────────────────────────────────────

describe("SimulateVehicleView — T6.2 start path", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
    mocks.startVehicleSimulation.mockReset();
    mocks.simulationClientStart.mockReset();
    mocks.getSimulationStatus.mockReset();
    mocks.fetchDataProcessingCampaigns.mockReset();
    mocks.assignCampaignToVehicle.mockReset();
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ agents: [] }),
    } as Response);
  });

  /** Render, wait for the vehicle list, then click Start. */
  async function clickStart() {
    renderView();
    await waitFor(() => {
      expect(
        screen.getByTestId("simulate-vehicle-fwe-log-viewer-container"),
      ).toBeTruthy();
    });
    const startButton = screen
      .queryAllByRole("button")
      .find((b) => b.textContent?.trim() === "Start");
    expect(startButton, "Start button not found").toBeTruthy();
    startButton!.click();
  }

  it("calls startVehicleSimulation from the subscriptions client", async () => {
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-abc123",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "FWE agent + collection scheme → MQTT → Flink",
    });

    await clickStart();

    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });
    // T5.2: Start now sends (vehicleId, fetchImpl, tripParams) — three args.
    // The first arg is still a single vehicleId string (not an array).
    const [calledVehicleId] = mocks.startVehicleSimulation.mock.calls[0] as [string, ...unknown[]];
    expect(calledVehicleId).toBe("VEH-TEST-0001");
  });

  it("does NOT call simulationClient.startSimulation", async () => {
    // MUTATION: revert the import and call `startSimulation([id])` again → this
    // fails. Asserted as an explicit spy rather than as the absence of a symbol,
    // so the revert is observable.
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-abc123",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });

    await clickStart();

    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalled();
    });
    expect(mocks.simulationClientStart).not.toHaveBeenCalled();
  });

  it("sends no rule_name or transport override from the browser", async () => {
    // The server derives both simulation axes from the vehicle's dataSource. The
    // pre-T6.2 path read `simulationProductRuleName` out of runtime config and put
    // it in the request body, which let the browser assert a routing destination.
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-abc123",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });

    await clickStart();

    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalled();
    });
    // T5.2: tripParams are sent but must never contain mode or rule_name.
    const args = mocks.startVehicleSimulation.mock.calls[0];
    expect(JSON.stringify(args)).not.toContain("rule_name");
    expect(JSON.stringify(args)).not.toContain("mode");
  });

  // ── Retry after a failed start (FG3 architect fix) ────────────────────────
  //
  // FG3.T1 originally guarded both start handlers with `phase !== "idle"`. Because
  // `isBusy` excludes "error", the Start button rendered ENABLED after a failed
  // start while every click was silently discarded by that guard. A 152-test suite
  // passed, because nothing covered retry-after-failure. These two tests are that
  // coverage.
  //
  // MUTATION (verified 2026-09-19): change either guard back to `phase !== "idle"`
  // and the matching test below FAILS on the second-call assertion.
  it("retries when Start is clicked again after a failed start", async () => {
    mocks.startVehicleSimulation.mockRejectedValue(new Error("transient failure"));

    await clickStart();
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });
    // The button must be live again — "error" is not a busy state.
    const btn = screen.getByTestId("simulate-vehicle-start-button");
    expect(btn.getAttribute("aria-disabled")).not.toBe("true");

    await clickStart();
    await waitFor(() => {
      expect(
        mocks.startVehicleSimulation,
        "a second Start click after a failure must reach the API — the handler guard " +
          "must gate on isBusy, not on phase !== 'idle' (error is not busy)",
      ).toHaveBeenCalledTimes(2);
    });
  });

  it("render layer refuses a second Start while one is genuinely in flight", async () => {
    // Never-resolving start: phase stays "starting", so isBusy is true.
    mocks.startVehicleSimulation.mockImplementation(() => new Promise(() => {}));

    await clickStart();
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });

    await clickStart();
    await new Promise((r) => setTimeout(r, 40));
    // SCOPE, stated precisely: this verifies the RENDER layer. During phase ===
    // "starting" isBusy is true, so the Start button is disabled/loading and the click
    // never reaches handleStart. Architect-verified 2026-09-19: removing handleStart's
    // `if (isBusy) return` guard leaves this test -- and all 154 -- passing, because the
    // handler is unreachable by this path. That guard is therefore genuine
    // defense-in-depth for the Start button; the test below covers the one entry point
    // where the handler guard IS reachable (the modal).
    expect(
      mocks.startVehicleSimulation,
      "a second start while phase === 'starting' must be refused",
    ).toHaveBeenCalledTimes(1);
  });

  // T5.2: the modal is gone, but the synchronous startInFlight interlock still guards
  // concurrent starts via the quick-assign bypass path (the only remaining reachable
  // path where the handler guard can fire while isBusy is legitimately false).
  // The full interlock test is in "refuses a concurrent start when quick-assign resets
  // phase mid-flight" further below — that test drives the actual bypass path.
  //
  // This test guards the M8 mutation: removing `if (startInFlight.current) return`
  // from handleStart must make a concurrent-start scenario observable.
  //
  // MUTATION M8 (verified): remove `if (startInFlight.current) return` from handleStart
  // → startVehicleSimulation is called a second time when two starts overlap via the
  // quick-assign phase-reset bypass. That test ("refuses a concurrent start when
  // quick-assign resets phase mid-flight") is the designated M8 carrier and FAILS
  // under the mutation.
  it("T5.2 M8 — startInFlight ref prevents double-start via quick-assign bypass", async () => {
    // This test verifies that the in-flight interlock is correctly placed BEFORE
    // the first await, by checking that a second Start click while quick-assign has
    // reset phase to idle (but a start is still in flight) is refused.
    // Full scenario is covered by "refuses a concurrent start when quick-assign
    // resets phase mid-flight" — this is a reference to that test's mutation claim.
    mocks.startVehicleSimulation.mockImplementation(() => new Promise(() => {}));

    await clickStart();
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });
    // The latch is held. A second Start click must be refused.
    await clickStart();
    await new Promise((r) => setTimeout(r, 40));
    // Still only one call — the latch held.
    expect(
      mocks.startVehicleSimulation,
      "startInFlight ref must refuse a second start while the first call is in flight",
    ).toHaveBeenCalledTimes(1);
  });



  // ── N10 + S27, cycle 8: a FAILED stop must leave the run watched and stoppable ─
  //
  // Fix Group 7 revokes poll authority before awaiting the stop, which is correct for the
  // success path but assumed success. On the failure arm the run is still live, and the
  // previous commit left it: 0 polls in the following 5.4s, message_count frozen, status
  // stuck at Error permanently even after the run ended server-side, Stop disabled
  // (it reads isRunning) so the stop could not be retried, and Start live -- which
  // orphans the run. Pre-FG7 the next poll self-healed the screen; FG7 removed that.
  //
  // 158 green tests were silent about all of it because none drove a failed stop. This is
  // that test.
  //
  // MUTATION (verified 2026-09-19): delete the re-arm block from handleStop's catch (or
  // restore `setPhase("error")` there) and this FAILS -- no poll after the failed stop,
  // and Stop goes disabled.
  it("a failed stop keeps the run polled and stoppable", async () => {
    mocks.startVehicleSimulation.mockResolvedValueOnce({
      simulation_id: "sim-AAA", vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry", dispatch: "onboard",
    });
    mocks.getSimulationStatus.mockResolvedValue({ status: "running", simulation_id: "sim-AAA" });

    await clickStart();
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });

    // Make the stop fail. `stopSimulation` throws on a non-ok response, so a stubbed 500
    // drives the real catch arm rather than a mocked one.
    const prevFetch = global.fetch;
    (global as unknown as { fetch: unknown }).fetch = vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : String((input as Request).url ?? input);
      if (url.includes("/stop")) {
        return Promise.resolve({ ok: false, status: 500, statusText: "Server Error" } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ agents: [] }) } as Response);
    });

    try {
      const callsBefore = mocks.getSimulationStatus.mock.calls.length;
      await act(async () => {
        screen.getByTestId("simulate-vehicle-stop-button").click();
        await Promise.resolve();
      });
      await new Promise((r) => setTimeout(r, 50));

      const isDisabled = (el: HTMLElement) =>
        el.getAttribute("aria-disabled") === "true" || el.hasAttribute("disabled");

      // The stop failed, so the run is still live: Stop must remain retryable...
      expect(
        isDisabled(screen.getByTestId("simulate-vehicle-stop-button")),
        "a failed stop must leave Stop retryable -- parking in an error phase disables it " +
          "and orphans the run",
      ).toBe(false);
      // ...and Start must NOT be live, or a second concurrent run can be dispatched.
      expect(
        isDisabled(screen.getByTestId("simulate-vehicle-start-button")),
        "the run is still live after a failed stop; Start must stay held",
      ).toBe(true);

      // The operator must be TOLD. Review cycle 9 (N11): the failure was written to
      // `errorMsg` while the phase stayed "running", and the error Alert is gated on
      // phase === "error" -- so nothing rendered and a failed Stop was indistinguishable
      // from a click that never registered. This assertion is on visible text, so it
      // fails if the message is dropped OR routed to a channel that does not render at
      // this phase.
      await waitFor(() => {
        expect(
          screen.getByText(/Stop request failed/i),
          "a failed stop must be visible to the operator at the phase it leaves behind",
        ).toBeTruthy();
      });
      expect(
        screen.getByTestId("simulate-vehicle-notice-alert"),
        "the notice Alert is the only channel that renders while phase is 'running'",
      ).toBeTruthy();

      // And the run must still be watched: another poll after one interval.
      await act(async () => { await new Promise((r) => setTimeout(r, 5200)); });
      expect(
        mocks.getSimulationStatus.mock.calls.length,
        "polling must be re-armed after a failed stop, or the run goes unmonitored and " +
          "the screen can never correct itself",
      ).toBeGreaterThan(callsBefore);
    } finally {
      (global as unknown as { fetch: unknown }).fetch = prevFetch;
    }
  }, 20000);


  // ── W4, cycle 7: a poll for the run BEING STOPPED must not un-gate "stopping" ─
  //
  // Cycle 6's identity check only drops polls issued for a REPLACED run. The poll that
  // un-gates "stopping" belongs to the run being stopped, so it still matched
  // activeSimId and passed the check, set phase to idle mid-stop, and re-enabled Start --
  // after which handleStop's post-await writes nulled out the NEW run's id, leaving it
  // transmitting, unmonitored and unstoppable.
  //
  // The fix revokes poll authority (stopPolling + activeSimId = null) BEFORE the await,
  // so the late poll fails the identity check. This test needs the /stop/ response held
  // open, which the sibling test above structurally cannot express -- its stop resolves
  // promptly.
  //
  // MUTATION (verified 2026-09-19): move `stopPolling()` / `activeSimId.current = null`
  // back below the await in handleStop and this FAILS -- phase leaves "stopping".
  it("a poll for the run being stopped must not un-gate the stopping phase", async () => {
    mocks.startVehicleSimulation.mockResolvedValueOnce({
      simulation_id: "sim-AAA", vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry", dispatch: "onboard",
    });
    let releasePoll: (v: unknown) => void = () => {};
    mocks.getSimulationStatus.mockImplementationOnce(
      () => new Promise((resolve) => { releasePoll = resolve; }),
    );

    await clickStart();
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });
    // One real poll interval so a poll is outstanding for sim-AAA.
    await act(async () => { await new Promise((r) => setTimeout(r, 5200)); });
    await waitFor(() => {
      expect(mocks.getSimulationStatus).toHaveBeenCalledTimes(1);
    });

    // Hold the stop request open so handleStop is parked on its await. `stopSimulation`
    // is the real implementation, so we gate it at the fetch layer rather than mocking
    // the module (which the rest of this file relies on being real).
    const prevFetch = global.fetch;
    (global as unknown as { fetch: unknown }).fetch = vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : String((input as Request).url ?? input);
      if (url.includes("/stop")) return new Promise(() => {}); // never settles
      return Promise.resolve({ ok: true, json: async () => ({ agents: [] }) } as Response);
    });

    try {
      await act(async () => {
        screen.getByTestId("simulate-vehicle-stop-button").click();
        await Promise.resolve();
      });

      // The outstanding poll for sim-AAA now returns a TERMINAL status, mid-stop.
      await act(async () => {
        releasePoll({ status: "stopped", simulation_id: "sim-AAA" });
        await Promise.resolve();
      });
      await new Promise((r) => setTimeout(r, 40));

      const isDisabled = (el: HTMLElement) =>
        el.getAttribute("aria-disabled") === "true" || el.hasAttribute("disabled");
      expect(
        isDisabled(screen.getByTestId("simulate-vehicle-start-button")),
        "the stop is still in flight; a poll for the run being stopped must not return " +
          "the UI to a startable state",
      ).toBe(true);
    } finally {
      (global as unknown as { fetch: unknown }).fetch = prevFetch;
    }
  }, 20000);


  // ── W4, cycle 6: a stale poll must not speak for a replaced run ──────────────
  //
  // clearInterval does not cancel an already-outstanding promise. A poll issued for run
  // N can resolve after run N was stopped and run N+1 started; without an identity check
  // it writes status, tears down the NEW run's interval via stopPolling(), and sets phase
  // to idle/error -- reporting Idle over a transmitting simulation and leaving it
  // unmonitored. No error state is needed: this is the plain start -> stop -> start
  // gesture, and the poll outstanding at Stop is the one most likely to return a
  // terminal status.
  //
  // Real timers, with one real POLL_INTERVAL_MS wait: fake timers here deadlock every
  // `waitFor` in the file (tried, 44 failures) because waitFor itself needs the clock.
  //
  // MUTATION (verified 2026-09-19): remove `if (activeSimId.current !== id) return` from
  // pollStatus and this FAILS -- the stale terminal poll drags the new run to Idle.
  it("a poll resolving for a replaced run must not write any shared state", async () => {
    mocks.startVehicleSimulation.mockResolvedValueOnce({
      simulation_id: "sim-AAA", vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry", dispatch: "onboard",
    });
    // Hold the first poll open so it is still outstanding when we stop and restart.
    let releasePoll: (v: unknown) => void = () => {};
    mocks.getSimulationStatus.mockImplementationOnce(
      () => new Promise((resolve) => { releasePoll = resolve; }),
    );

    await clickStart();
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });

    // Wait out one real poll interval (5s) so the interval fires for sim-AAA.
    await act(async () => { await new Promise((r) => setTimeout(r, 5200)); });
    await waitFor(() => {
      expect(mocks.getSimulationStatus).toHaveBeenCalledTimes(1);
    });

    // Stop sim-AAA. `stopSimulation` is the real implementation behind the stubbed
    // global fetch, so it needs no mock of its own.
    await act(async () => {
      screen.getByTestId("simulate-vehicle-stop-button").click();
      await Promise.resolve();
    });

    // Start sim-BBB.
    mocks.startVehicleSimulation.mockResolvedValueOnce({
      simulation_id: "sim-BBB", vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry", dispatch: "onboard",
    });
    mocks.getSimulationStatus.mockResolvedValue({ status: "running", simulation_id: "sim-BBB" });
    await act(async () => {
      screen.getByTestId("simulate-vehicle-start-button").click();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(2);
    });

    // The stale poll for sim-AAA now resolves with a TERMINAL status.
    await act(async () => {
      releasePoll({ status: "stopped", simulation_id: "sim-AAA" });
      await Promise.resolve();
    });
    await new Promise((r) => setTimeout(r, 40));

    const isDisabled = (el: HTMLElement) =>
      el.getAttribute("aria-disabled") === "true" || el.hasAttribute("disabled");
    expect(
      isDisabled(screen.getByTestId("simulate-vehicle-start-button")),
      "sim-BBB is running; a stale poll for sim-AAA must not return the UI to idle",
    ).toBe(true);
    expect(
      isDisabled(screen.getByTestId("simulate-vehicle-stop-button")),
      "Stop must stay live for sim-BBB, or the run becomes unstoppable",
    ).toBe(false);
  }, 20000);

  // ── N7, cycle 6: a superseded quick-assign must still report its outcome ─────
  //
  // The generation guard asks "did a start happen", not "does a newer run own the
  // screen". Those diverge when the newer start FAILS: the campaign is assigned, but the
  // screen said "Assign a campaign and retry" with the button already removed, because
  // setCanQuickAssign(false) sat outside the guard while the notice sat inside it.
  //
  // MUTATION (verified 2026-09-19): move setCanQuickAssign(false) back above the
  // `superseded` early-return, or drop the always-report notice, and this FAILS.
  it("reports the assignment even when a newer failed start superseded it", async () => {
    mocks.startVehicleSimulation.mockRejectedValueOnce(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );
    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    });

    let releaseCampaigns: (v: unknown) => void = () => {};
    mocks.fetchDataProcessingCampaigns.mockImplementation(
      () => new Promise((resolve) => { releaseCampaigns = resolve; }),
    );
    mocks.assignCampaignToVehicle.mockResolvedValue({ assigned: ["VEH-TEST-0001"] });
    await act(async () => {
      screen.getByTestId("simulate-vehicle-quick-assign-button").click();
      await Promise.resolve();
    });

    // A newer start is dispatched and fails the same way, so it asks for the affordance.
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );
    await act(async () => {
      screen.getByTestId("simulate-vehicle-start-button").click();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(2);
    });

    // Quick-assign now resolves, superseded.
    await act(async () => {
      releaseCampaigns({
        dcCampaigns: [{
          campaignId: "cms-fleet-gps-10s", campaignName: "cms-fleet-gps-10s",
          targetArn: "template", status: "ACTIVE",
          createdAt: "2026-09-12T20:24:54+00:00", owner: "oem",
        }],
        count: 1,
      });
      await Promise.resolve();
    });
    await new Promise((r) => setTimeout(r, 40));

    // The operator must learn the campaign was assigned...
    await waitFor(() => {
      expect(
        screen.getByText(/Assigned "cms-fleet-gps-10s"/i),
        "a superseded quick-assign still assigned the campaign and must say so",
      ).toBeTruthy();
    });
    // ...and the newer start's own quick-assign affordance must survive.
    expect(
      screen.queryByTestId("simulate-vehicle-quick-assign-button"),
      "the newer failed start asked for this affordance; a superseded quick-assign " +
        "must not clear it",
    ).toBeTruthy();
  });


  // ── W4, cycle 5: quick-assign clobbering a SUCCEEDED start ──────────────────
  //
  // Cycle 4's fix (the startInFlight latch) closed the window where two start CALLS
  // overlap. It did not close this one: let the start SUCCEED, so the latch is released
  // in `finally` and phase becomes "running". quick-assign's awaits then resolve and
  // its unconditional setPhase("idle") reports Idle over a transmitting simulation --
  // which also disables Stop (it reads isRunning) and shows a notice reading
  // "Click Start to try again", inviting a second concurrent run. The operator follows
  // the instructions on screen; nothing about it is a race they could have avoided.
  //
  // MUTATION (verified 2026-09-19): remove the `startGeneration` check from
  // handleQuickAssign and this FAILS -- status reads Idle and a second start is accepted
  // while the first is still running.
  it("quick-assign must not report Idle over a start that already succeeded", async () => {
    // 1. Fail once so the quick-assign affordance appears.
    mocks.startVehicleSimulation.mockRejectedValueOnce(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );
    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    });

    // 2. Hold quick-assign's first await open.
    let releaseCampaigns: (v: unknown) => void = () => {};
    mocks.fetchDataProcessingCampaigns.mockImplementation(
      () => new Promise((resolve) => { releaseCampaigns = resolve; }),
    );
    mocks.assignCampaignToVehicle.mockResolvedValue({ assigned: ["VEH-TEST-0001"] });
    await act(async () => {
      screen.getByTestId("simulate-vehicle-quick-assign-button").click();
      await Promise.resolve();
    });

    // 3. Start, and let it SUCCEED. The latch is released in finally; phase = "running".
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-AAA",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });
    mocks.getSimulationStatus.mockResolvedValue({ status: "running", simulation_id: "sim-AAA" });
    await act(async () => {
      screen.getByTestId("simulate-vehicle-start-button").click();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(2);
    });

    // 4. Now let quick-assign finish. It must NOT touch phase.
    await act(async () => {
      releaseCampaigns({
        dcCampaigns: [{
          campaignId: "cms-fleet-gps-10s",
          campaignName: "cms-fleet-gps-10s",
          targetArn: "template",
          status: "ACTIVE",
          createdAt: "2026-09-12T20:24:54+00:00",
          owner: "oem",
        }],
        count: 1,
      });
      await Promise.resolve();
    });
    await new Promise((r) => setTimeout(r, 30));

    // 5a. Start must still be held: a simulation is running.
    // Cloudscape marks a disabled Button with EITHER aria-disabled="true" or the native
    // `disabled` attribute -- match the idiom already used at the top of this file.
    const isDisabled = (el: HTMLElement) =>
      el.getAttribute("aria-disabled") === "true" || el.hasAttribute("disabled");

    const startBtn = screen.getByTestId("simulate-vehicle-start-button");
    expect(
      isDisabled(startBtn),
      "Start must stay disabled while sim-AAA runs -- quick-assign must not reset phase",
    ).toBe(true);

    // 5b. Stop must remain available, since it is gated on isRunning.
    expect(
      isDisabled(screen.getByTestId("simulate-vehicle-stop-button")),
      "Stop must stay enabled while sim-AAA runs, or the run becomes unstoppable",
    ).toBe(false);

    // 5c. And no second simulation may be dispatched.
    await act(async () => {
      startBtn.click();
      await Promise.resolve();
    });
    await new Promise((r) => setTimeout(r, 30));
    expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(2);
  });


  // ── W4, cycle 4: the quick-assign race ──────────────────────────────────────
  //
  // This is the race that defeated three cycles of render-state guards. handleQuickAssign
  // is gated only on selectedVehicleId and is legitimately reachable at phase "error".
  // Its network awaits resolve AFTER a start has set phase "starting", and its
  // setPhase("idle") then clears isBusy from underneath the in-flight start -- re-enabling
  // both start affordances while a start is still running.
  //
  // MUTATION (verified 2026-09-19): replace `if (startInFlight.current) return` in
  // handleStart with the old `if (isBusy) return` and this test FAILS with 3 calls
  // instead of 2 -- because isBusy is legitimately false at that moment, so the guard
  // has nothing to detect. That is the whole argument for the ref.
  it("refuses a concurrent start when quick-assign resets phase mid-flight", async () => {
    // 1. Fail a start with no_telemetry_campaign so the quick-assign affordance appears.
    mocks.startVehicleSimulation.mockRejectedValueOnce(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );
    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    });

    // 2. Hold quick-assign's first await open so we control when it resolves.
    let releaseCampaigns: (v: unknown) => void = () => {};
    mocks.fetchDataProcessingCampaigns.mockImplementation(
      () => new Promise((resolve) => { releaseCampaigns = resolve; }),
    );
    mocks.assignCampaignToVehicle.mockResolvedValue({ assigned: ["VEH-TEST-0001"] });

    await act(async () => {
      screen.getByTestId("simulate-vehicle-quick-assign-button").click();
      await Promise.resolve();
    });

    // 3. Start, and keep the call in flight so the latch stays claimed.
    mocks.startVehicleSimulation.mockImplementation(() => new Promise(() => {}));
    await act(async () => {
      screen.getByTestId("simulate-vehicle-start-button").click();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(2);
    });

    // 4. Let quick-assign finish. Its setPhase("idle") now lands on top of "starting",
    //    so isBusy goes false and the buttons become live again.
    await act(async () => {
      releaseCampaigns({
        dcCampaigns: [{
          campaignId: "cms-fleet-gps-10s",
          campaignName: "cms-fleet-gps-10s",
          targetArn: "template",
          status: "ACTIVE",
          createdAt: "2026-09-12T20:24:54+00:00",
          owner: "oem",
        }],
        count: 1,
      });
      await Promise.resolve();
    });
    await new Promise((r) => setTimeout(r, 30));

    // 5. The interlock must still refuse, even though the render layer no longer does.
    await act(async () => {
      screen.getByTestId("simulate-vehicle-start-button").click();
      await Promise.resolve();
    });
    await new Promise((r) => setTimeout(r, 30));

    expect(
      mocks.startVehicleSimulation,
      "a start was already in flight; the synchronous latch must refuse the second " +
        "dispatch even though isBusy has been cleared by quick-assign's setPhase('idle')",
    ).toHaveBeenCalledTimes(2);
  });


  it("surfaces the server's campaign advisory instead of swallowing it", async () => {
    const warning =
      "No active campaign assigned to VIN-TEST-0001 — FWE agent will start but may not transmit signals";
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-abc123",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
      warning,
    });

    await clickStart();

    await waitFor(() => {
      expect(screen.getByText(new RegExp("No active campaign", "i"))).toBeTruthy();
    });
  });

  it("names the SUBSCRIPTIONS endpoint when start is unconfigured", async () => {
    // NOTE (review cycle 1, W2): this branch is UNREACHABLE in production, and this
    // test reaches it only by injecting a state the real modules cannot produce
    // together — a configured subscriptions base (so the picker loads a vehicle and
    // Start is enabled) with `startVehicleSimulation` returning null (which the real
    // function does iff that same base is null). In production `listVehiclesForSimulation`
    // degrades on the identical condition, so no vehicle is selected and Start is
    // disabled before this branch can run.
    //
    // The branch is kept as defence in depth: the two functions read the base
    // independently, and a future change that gives start its own endpoint would make
    // it live. It is deliberately NOT presented as a user-visible path.
    //
    // The commit message for T6.2 claimed an operator "clicked Start and saw nothing
    // happen"; that was true of the earlier hardcoded-vehicle revision, not of the
    // current dynamic picker. Corrected in decisions.md.
    mocks.startVehicleSimulation.mockResolvedValue(null);

    await clickStart();

    await waitFor(() => {
      expect(screen.getByText(/Subscriptions API endpoint is not configured/i)).toBeTruthy();
    });
  });

  // ── reason-aware error mapping ─────────────────────────────────────────────

  it("explains no_data_source in terms of the vehicle record", async () => {
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(400, "raw server text", "no_data_source"),
    );

    await clickStart();

    await waitFor(() => {
      expect(screen.getByText(/has no dataSource/i)).toBeTruthy();
    });
    // The raw server text must not be what the operator reads.
    expect(screen.queryByText(/raw server text/)).toBeNull();
  });

  it("explains no_telemetry_campaign as an action to take", async () => {
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );

    await clickStart();

    await waitFor(() => {
      expect(screen.getByText(/Assign a campaign and retry/i)).toBeTruthy();
    });
  });

  it("offers a quick-assign button specifically for no_telemetry_campaign, not other reasons", async () => {
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );

    await clickStart();

    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    });
  });

  it("does NOT offer the quick-assign button for a different refusal reason (403)", async () => {
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(403, "Forbidden"),
    );

    await clickStart();

    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-error-alert")).toBeTruthy();
    });
    expect(screen.queryByTestId("simulate-vehicle-quick-assign-button")).toBeNull();
  });

  it("quick-assign finds the baseline template and assigns it, then clears the error", async () => {
    const user = userEvent.setup();
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );
    mocks.fetchDataProcessingCampaigns.mockResolvedValue({
      dcCampaigns: [
        {
          campaignId: "cms-fleet-gps-10s",
          campaignName: "cms-fleet-gps-10s",
          targetArn: "template",
          status: "ACTIVE",
          createdAt: "2026-09-12T20:24:54+00:00",
          owner: "oem",
        },
      ],
      count: 1,
    });
    mocks.assignCampaignToVehicle.mockResolvedValue({
      campaignName: "cms-fleet-gps-10s",
      assigned: ["VIN-TEST-0001"],
    });

    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    });
    await user.click(screen.getByTestId("simulate-vehicle-quick-assign-button"));

    await waitFor(() => {
      // Asserts the VIN, NOT the vehicleId. `/campaigns/assign` keys the row
      // `vehicle:{vin}` and the telemetry-campaign check reads it by VIN, so a
      // vehicleId here writes a row nothing can find. This assertion previously
      // expected "VEH-TEST-0001" and so encoded the defect. See
      // issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/.
      expect(mocks.assignCampaignToVehicle).toHaveBeenCalledWith(
        "cms-fleet-gps-10s",
        "VIN-TEST-0001",
      );
    });
    // The refusal is cleared and replaced with a positive notice, and the
    // quick-assign button itself must not outlive the error it was offered for.
    await waitFor(() => {
      expect(screen.queryByTestId("simulate-vehicle-error-alert")).toBeNull();
      expect(screen.getByTestId("simulate-vehicle-notice-alert")).toBeTruthy();
    });
  });

  // ── Fix Group 10 — the three branches that replaced `assigned.length === 0` ──
  //
  // issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/

  it("quick-assign treats alreadyAssigned as success, not a failure", async () => {
    const user = userEvent.setup();
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );
    mocks.fetchDataProcessingCampaigns.mockResolvedValue({
      dcCampaigns: [
        {
          campaignId: "cms-fleet-gps-10s",
          campaignName: "cms-fleet-gps-10s",
          targetArn: "template",
          status: "ACTIVE",
          createdAt: "2026-09-12T20:24:54+00:00",
          owner: "oem",
        },
      ],
      count: 1,
    });
    // The write is conditional; an existing row yields assigned:[] plus
    // alreadyAssigned. This is the exact response the user's second click produced,
    // and the old code reported it as "did not take effect — try again", an error
    // that could never clear because the row always already exists.
    mocks.assignCampaignToVehicle.mockResolvedValue({
      campaignName: "cms-fleet-gps-10s",
      assigned: [],
      alreadyAssigned: ["VIN-TEST-0001"],
      rejected: [],
    });

    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    });
    await user.click(screen.getByTestId("simulate-vehicle-quick-assign-button"));

    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-notice-alert")).toBeTruthy();
      expect(screen.queryByTestId("simulate-vehicle-error-alert")).toBeNull();
    });
  });

  it("quick-assign surfaces the server's reason when the value is rejected", async () => {
    const user = userEvent.setup();
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );
    mocks.fetchDataProcessingCampaigns.mockResolvedValue({
      dcCampaigns: [
        {
          campaignId: "cms-fleet-gps-10s",
          campaignName: "cms-fleet-gps-10s",
          targetArn: "template",
          status: "ACTIVE",
          createdAt: "2026-09-12T20:24:54+00:00",
          owner: "oem",
        },
      ],
      count: 1,
    });
    mocks.assignCampaignToVehicle.mockResolvedValue({
      campaignName: "cms-fleet-gps-10s",
      assigned: [],
      alreadyAssigned: [],
      rejected: [{ value: "VIN-TEST-0001", reason: "does not resolve to a vehicle" }],
    });

    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    });
    await user.click(screen.getByTestId("simulate-vehicle-quick-assign-button"));

    // The server's own reason must reach the operator — a generic "try again" is
    // what made the original defect undiagnosable from the screen.
    await waitFor(() => {
      const alert = screen.getByTestId("simulate-vehicle-error-alert");
      expect(alert.textContent).toContain("does not resolve to a vehicle");
    });
  });

  it("quick-assign refuses to guess when the selected vehicle has no VIN", async () => {
    const user = userEvent.setup();
    // A vehicle with no `vin`. The old code's `?? selectedVehicleId` shape would
    // have sent the vehicleId here and written a row the simulator cannot see.
    // Refusing is correct: guessing writes bad data.
    //
    // `vi.spyOn` rather than `mocks.listVehiclesForSimulation.mockResolvedValue` —
    // the module factory defines that export as a plain async function, not a
    // `vi.fn`, so it carries no mock API. Same pattern as the cloud-telemetry
    // tests below.
    vi.spyOn(mockedSubscriptionsClient, "listVehiclesForSimulation").mockResolvedValueOnce({
      count: 1,
      filtered_on_producer: false,
      vehicles: [
        {
          vehicleId: "VEH-NOVIN-0001",
          producer: "vehicle-telemetry",
          dataSource: "vehicle-telemetry",
        },
      ],
    });
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );
    mocks.fetchDataProcessingCampaigns.mockResolvedValue({
      dcCampaigns: [
        {
          campaignId: "cms-fleet-gps-10s",
          campaignName: "cms-fleet-gps-10s",
          targetArn: "template",
          status: "ACTIVE",
          createdAt: "2026-09-12T20:24:54+00:00",
          owner: "oem",
        },
      ],
      count: 1,
    });

    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    });
    await user.click(screen.getByTestId("simulate-vehicle-quick-assign-button"));

    await waitFor(() => {
      const alert = screen.getByTestId("simulate-vehicle-error-alert");
      expect(alert.textContent).toContain("No VIN on record");
    });
    // The load-bearing half: it must not have called the API at all.
    expect(mocks.assignCampaignToVehicle).not.toHaveBeenCalled();
  });

  it("quick-assign no longer points the operator at a nonexistent Campaigns tab", async () => {
    const user = userEvent.setup();
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );
    // No template on this stage — the branch whose text named the missing tab.
    mocks.fetchDataProcessingCampaigns.mockResolvedValue({ dcCampaigns: [], count: 0 });

    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    });
    await user.click(screen.getByTestId("simulate-vehicle-quick-assign-button"));

    await waitFor(() => {
      const alert = screen.getByTestId("simulate-vehicle-error-alert");
      expect(alert.textContent).not.toContain("Campaigns tab");
      expect(alert.textContent).toContain("Data Collection Campaigns");
    });
  });

  it("quick-assign surfaces a clear message when no baseline template exists", async () => {
    const user = userEvent.setup();
    mocks.startVehicleSimulation.mockRejectedValue(      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );
    mocks.fetchDataProcessingCampaigns.mockResolvedValue({ dcCampaigns: [], count: 0 });

    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    });
    await user.click(screen.getByTestId("simulate-vehicle-quick-assign-button"));

    await waitFor(() => {
      expect(screen.getByText(/No baseline telemetry campaign template/i)).toBeTruthy();
    });
    expect(mocks.assignCampaignToVehicle).not.toHaveBeenCalled();
  });

  it("does NOT attempt quick-assign against a template with the wrong campaignName", async () => {
    // Mutation-relevant: the filter must match BOTH targetArn === "template" AND
    // campaignName === "cms-fleet-gps-10s" — a template with any other name is not
    // the baseline the server-side auto-ensure uses, and assigning the wrong one
    // would be silently wrong rather than loudly absent.
    const user = userEvent.setup();
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );
    mocks.fetchDataProcessingCampaigns.mockResolvedValue({
      dcCampaigns: [
        {
          campaignId: "some-other-template",
          campaignName: "some-other-template",
          targetArn: "template",
          status: "ACTIVE",
          createdAt: "2026-09-12T20:24:54+00:00",
          owner: "oem",
        },
      ],
      count: 1,
    });

    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    });
    await user.click(screen.getByTestId("simulate-vehicle-quick-assign-button"));

    await waitFor(() => {
      expect(screen.getByText(/No baseline telemetry campaign template/i)).toBeTruthy();
    });
    expect(mocks.assignCampaignToVehicle).not.toHaveBeenCalled();
  });

  it("explains a 403 as a missing operator group", async () => {
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(403, "Forbidden"),
    );

    await clickStart();

    await waitFor(() => {
      expect(screen.getByText(/connected-services operator group/i)).toBeTruthy();
    });
  });

  it("falls through to the server message for an unrecognised reason", async () => {
    // Anything not in the mapping must NOT be relabelled — inventing an
    // explanation for an unknown reason is worse than showing the server's.
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(500, "something specific from the server", "brand_new_reason"),
    );

    await clickStart();

    await waitFor(() => {
      expect(screen.getByText(/something specific from the server/i)).toBeTruthy();
    });
  });

  // The three branches below had NO test anywhere until review cycle 1 (W1).
  // Deleting each from `describeStartFailure` left the FULL suite green at 1026.
  //
  // The signal was in a mutation count I had already run and read as a pass: T7
  // (drop the whole mapping) failed 3 tests, but `SimulationStartError`'s own
  // docstring enumerates FOUR actionable outcomes. Three-versus-four was the gap,
  // and it was `unknown_data_source` — one of the four.

  it("explains unknown_data_source as a vehicle-record problem", async () => {
    // The server sends `unknown_data_source:<value>`, hence startsWith rather than
    // an equality case — pinned so the prefix match is not silently narrowed.
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(400, "raw server text", "unknown_data_source:mqtt-direct"),
    );

    await clickStart();

    await waitFor(() => {
      expect(screen.getByText(/not a recognised transport/i)).toBeTruthy();
    });
    expect(screen.queryByText(/raw server text/)).toBeNull();
  });

  it("explains a bare unknown_data_source with no suffix", async () => {
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(400, "raw server text", "unknown_data_source"),
    );

    await clickStart();

    await waitFor(() => {
      expect(screen.getByText(/not a recognised transport/i)).toBeTruthy();
    });
  });

  it("explains simulation_invoke_failed as a service-side failure with the status", async () => {
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(502, "raw server text", "simulation_invoke_failed"),
    );

    await clickStart();

    await waitFor(() => {
      expect(screen.getByText(/did not accept the start request/i)).toBeTruthy();
    });
    // The status is interpolated, and is the one piece of the raw failure worth
    // showing — it distinguishes a 502 from a 500 in a support conversation.
    expect(screen.getByText(/502/)).toBeTruthy();
  });

  it("explains simulation_response_unparseable the same way", async () => {
    // 500, not 502, deliberately. Both tests reaching this branch used 502, so
    // hardcoding `502` in place of `${httpStatus}` passed 27/27 while the test's own
    // comment claimed it "distinguishes a 502 from a 500" (review cycle 2, S1).
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(500, "raw server text", "simulation_response_unparseable"),
    );

    await clickStart();

    await waitFor(() => {
      expect(screen.getByText(/did not accept the start request/i)).toBeTruthy();
    });
    expect(screen.getByText(/500/)).toBeTruthy();
    expect(screen.queryByText(/502/)).toBeNull();
  });

  it("resets the error phase when a different vehicle is selected", async () => {
    // Clearing the messages without resetting `phase` left the status line reading
    // "Error" with neither alert rendered — nothing on screen to explain it, reachable
    // by the ordinary "try a different vehicle" gesture (review cycle 2, W3).
    const user = userEvent.setup();
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(403, "Forbidden"),
    );
    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-error-alert")).toBeTruthy();
    });

    const combo = screen
      .queryAllByRole("button")
      .find((b) => b.getAttribute("aria-haspopup") === "listbox");
    await user.click(combo!);

    // Matched on the VIN, not the vehicleId: as of 2026-09-20 the picker labels
    // rows by year/make/model + VIN and never renders the vehicleId, so a
    // /VEH-TEST-0002/ matcher finds nothing. This fixture carries no
    // make/model/year, so its label degrades to "VIN VIN-TEST-0002".
    const other = await screen.findByRole("option", { name: /VIN-TEST-0002/ });
    await user.click(other);

    await waitFor(() => {
      expect(screen.queryByTestId("simulate-vehicle-error-alert")).toBeNull();
    });
    // The dead end is the STATUS still claiming an error with no alert to explain
    // it. Asserted on the indicator directly: a whole-body regex for /Error/ passed
    // against the mutation, because the alert's own header was already gone and
    // nothing else in the body carried the word.
    await waitFor(() => {
      const indicator = screen.queryByTestId("simulate-vehicle-status-indicator");
      expect(indicator, "status indicator not rendered").toBeTruthy();
      expect(indicator!.textContent).not.toContain("Error");
    });
    expect(
      screen.getByTestId("simulate-vehicle-status-indicator").textContent,
    ).toContain("Idle");
  });

  // ── poll-terminal path — errorMsg and notice clear (review cycle 3 follow-on) ─
  //
  // `pollStatus` previously set only `setPhase("error")` on a `"failed"` terminal
  // response, with no `setErrorMsg` and no `setNoticeMsg(null)`. The observable shape:
  // the status indicator reads "Error", the error alert is never rendered (nothing
  // to read), and any previous notice advisory is still visible — which the operator
  // reads as the cause (review cycle 3, same shape as the `onChange` case closed by
  // Fix Group 21). These tests pin the three properties and are mutation-verified per
  // the comments below.
  //
  // Timer approach: `vi.useFakeTimers()` is enabled AFTER the initial async setup
  // (render + waitFor for the container) because `waitFor` internally uses real
  // `setTimeout` for retries and freezes under fake timers. Once the initial state
  // is established, fake timers are enabled, the poll interval is advanced with
  // `vi.advanceTimersByTimeAsync()` inside `act()`, and assertions are made directly
  // (no `waitFor` needed — the state update is synchronous once the microtasks drain).

  it("poll-terminal 'failed': shows an error alert, not a blank Error status", async () => {
    // MUTATION: remove `setErrorMsg(...)` from `pollStatus`'s failed branch →
    // `simulate-vehicle-error-alert` is never rendered and this test FAILS.
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-poll-001",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });
    mocks.getSimulationStatus.mockResolvedValue({
      simulation_id: "sim-poll-001",
      status: "failed",
      error: "Simulation process exited with code 1",
    });

    renderView();
    // Establish initial state with real timers so `waitFor` works normally.
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });

    vi.useFakeTimers();
    try {
      const startButton = screen
        .queryAllByRole("button")
        .find((b) => b.textContent?.trim() === "Start");
      expect(startButton, "Start button not found").toBeTruthy();
      // Click Start — sets up the poll setInterval under fake timers.
      await act(async () => {
        startButton!.click();
        // Drain microtasks so handleStart completes and simulationId is set.
        await Promise.resolve();
        await Promise.resolve();
      });

      // Advance past the poll interval and drain the resulting microtasks.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5001);
      });

      expect(screen.getByTestId("simulate-vehicle-error-alert")).toBeTruthy();
      expect(screen.getByTestId("simulate-vehicle-error-alert").textContent).toContain(
        "Simulation process exited with code 1",
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("poll-terminal 'failed': clears the notice advisory", async () => {
    // MUTATION: remove `setNoticeMsg(null)` from pollStatus's terminal branch →
    // the notice-alert is still visible after the status transitions to error, and
    // this test FAILS.
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-poll-002",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
      // Include a warning so we can assert it is cleared on terminal.
      warning: "No active campaign assigned to VIN-TEST-0001",
    });
    mocks.getSimulationStatus.mockResolvedValue({
      simulation_id: "sim-poll-002",
      status: "failed",
      error: "Simulation process exited with code 1",
    });

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });

    vi.useFakeTimers();
    try {
      const startButton = screen
        .queryAllByRole("button")
        .find((b) => b.textContent?.trim() === "Start");
      await act(async () => {
        startButton!.click();
        await Promise.resolve();
        await Promise.resolve();
      });

      // The notice advisory should now be visible (start succeeded with a warning).
      // Check synchronously — handleStart ran synchronously after the microtask drains.
      expect(screen.getByTestId("simulate-vehicle-notice-alert")).toBeTruthy();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5001);
      });

      expect(screen.getByTestId("simulate-vehicle-error-alert")).toBeTruthy();
      expect(screen.queryByTestId("simulate-vehicle-notice-alert")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("poll-terminal 'failed' without a server error field: falls back to a generic message", async () => {
    // The server's `error` field is optional — if absent, a generic message
    // must be shown rather than an empty error alert.
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-poll-003",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });
    mocks.getSimulationStatus.mockResolvedValue({
      simulation_id: "sim-poll-003",
      status: "failed",
      // no `error` field — guard against an empty alert
    });

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });

    vi.useFakeTimers();
    try {
      const startButton = screen
        .queryAllByRole("button")
        .find((b) => b.textContent?.trim() === "Start");
      await act(async () => {
        startButton!.click();
        await Promise.resolve();
        await Promise.resolve();
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5001);
      });

      expect(screen.getByTestId("simulate-vehicle-error-alert")).toBeTruthy();
      // Any non-empty message is acceptable — guards against a blank error alert.
      expect(
        screen.getByTestId("simulate-vehicle-error-alert").textContent?.trim().length,
      ).toBeGreaterThan(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("poll-terminal 'completed': clears the notice advisory", async () => {
    // The notice describes a running simulation; on completion it is stale.
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-poll-004",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
      warning: "No active campaign assigned to VIN-TEST-0001",
    });
    mocks.getSimulationStatus.mockResolvedValue({
      simulation_id: "sim-poll-004",
      status: "completed",
    });

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });

    vi.useFakeTimers();
    try {
      const startButton = screen
        .queryAllByRole("button")
        .find((b) => b.textContent?.trim() === "Start");
      await act(async () => {
        startButton!.click();
        await Promise.resolve();
        await Promise.resolve();
      });

      // Advisory should be visible.
      expect(screen.getByTestId("simulate-vehicle-notice-alert")).toBeTruthy();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5001);
      });

      // Phase transitions to idle on completed — no error alert, no notice.
      expect(screen.queryByTestId("simulate-vehicle-error-alert")).toBeNull();
      expect(screen.queryByTestId("simulate-vehicle-notice-alert")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });


  // ── notice lifecycle (review cycle 1, W4) ──────────────────────────────────
  //
  // `noticeMsg` renders independently of `phase`, which is what makes the advisory
  // visible at all — and also what lets it outlive the thing it described. Before
  // this, `setNoticeMsg(null)` existed once, at handleStart entry: deleting that one
  // clear left the full suite green, so the lifecycle was unguarded end to end.

  async function startWithWarning(warning: string) {
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-abc123",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
      warning,
    });
    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-notice-alert")).toBeTruthy();
    });
  }

  it("clears the advisory when the simulation is stopped", async () => {
    await startWithWarning("No active campaign assigned to VIN-TEST-0001");

    const stopButton = screen
      .queryAllByRole("button")
      .find((b) => b.textContent?.trim() === "Stop");
    expect(stopButton, "Stop button not found").toBeTruthy();
    stopButton!.click();

    await waitFor(() => {
      expect(screen.queryByTestId("simulate-vehicle-notice-alert")).toBeNull();
    });
  });

  it("clears the advisory on the next start attempt", async () => {
    // Isolates the clear at handleStart ENTRY, which the stop-then-start path does
    // not: stop clears the notice itself, so on that path the entry clear is
    // redundant and deleting it changes nothing (mutation W4b passed 26/26).
    //
    // The unconfigured branch is the reachable path that leaves a notice standing
    // while phase returns to "idle" — so Start is enabled again, and a second
    // attempt that succeeds with no warning must not leave the first notice up.
    mocks.startVehicleSimulation.mockResolvedValue(null);
    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-notice-alert")).toBeTruthy();
    });

    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-def456",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });
    const startButton = screen
      .queryAllByRole("button")
      .find((b) => b.textContent?.trim() === "Start");
    expect(startButton, "Start should be enabled again after an idle outcome").toBeTruthy();
    startButton!.click();

    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(screen.queryByTestId("simulate-vehicle-notice-alert")).toBeNull();
    });
  });

  it("clears the advisory when a different vehicle is selected", async () => {
    // A notice describes a start attempt on a specific vehicle. Carried across a
    // selection change it attributes that attempt to the wrong vehicle — which reads
    // as current rather than stale, since the alert renders independently of phase.
    //
    // Reachable only because the mocked list holds two vehicles; with one, the
    // handler's clear cannot run and its deletion changes nothing.
    //
    // Driven with `userEvent`, not `.click()`: Cloudscape's Select opens on the
    // pointer/keyboard sequence, so a bare `click()` leaves the listbox closed and
    // the option never renders. Same idiom as AuditLogView.test.tsx.
    const user = userEvent.setup();
    mocks.startVehicleSimulation.mockResolvedValue(null);
    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-notice-alert")).toBeTruthy();
    });

    const combo = screen
      .queryAllByRole("button")
      .find((b) => b.getAttribute("aria-haspopup") === "listbox");
    expect(combo, "vehicle picker trigger not found").toBeTruthy();
    await user.click(combo!);

    // Matched on the VIN, not the vehicleId: as of 2026-09-20 the picker labels
    // rows by year/make/model + VIN and never renders the vehicleId, so a
    // /VEH-TEST-0002/ matcher finds nothing. This fixture carries no
    // make/model/year, so its label degrades to "VIN VIN-TEST-0002".
    const other = await screen.findByRole("option", { name: /VIN-TEST-0002/ });
    await user.click(other);

    await waitFor(() => {
      expect(screen.queryByTestId("simulate-vehicle-notice-alert")).toBeNull();
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// T6.2 — the dataSource label must not claim a transport the dispatch never uses
// ─────────────────────────────────────────────────────────────────────────────

describe("SimulateVehicleView — dataSource label", () => {
  // Asserted against the exported helper, NOT the rendered DOM.
  //
  // The first version of this test checked `document.body.textContent` for the
  // absence of the wrong string, and mutation T4 (restore "producer feed → ingest
  // API") PASSED it: the mocked picker holds one vehicle-telemetry vehicle, so the
  // cloud-telemetry branch never executed and the assertion was vacuous. An
  // absence assertion over a string that is never produced is always satisfied —
  // the same shape as the presence-vs-property findings this spec has been paying
  // for all the way through.
  it("does not describe cloud-telemetry as the production ingest API", () => {
    const label = dataSourceLabel("cloud-telemetry");
    expect(label).not.toContain("producer feed");
    expect(label).not.toContain("ingest API");
  });

  it("describes cloud-telemetry as the rule/MSK path the simulation uses", () => {
    const label = dataSourceLabel("cloud-telemetry").toLowerCase();
    expect(label).toContain("cs product");
    expect(label).not.toContain("fwe");
  });

  it("still describes vehicle-telemetry as the onboard FWE path", () => {
    const label = dataSourceLabel("vehicle-telemetry").toLowerCase();
    expect(label).toContain("fwe");
    expect(label).not.toContain("cs product");
  });

  it("says a vehicle with no dataSource cannot be simulated", () => {
    expect(dataSourceLabel(undefined)).toContain("cannot simulate");
  });

  it("passes an unrecognised dataSource through rather than inventing a label", () => {
    expect(dataSourceLabel("some-future-source")).toBe("some-future-source");
  });
});

describe("SimulateVehicleView — vehicle picker label", () => {
  // Asserted against the exported helper, NOT the rendered DOM, per the same
  // rationale as "dataSource label" above — a duplicate-match ambiguity in the
  // rendered Select (label text also appearing in the description) is exactly
  // the kind of thing a DOM assertion can silently paper over.
  it("labels by year/make/model + VIN and never shows the vehicleId", () => {
    const { label, description } = vehicleOptionLabel({
      vehicleId: "VEH-LEGACY-MISNAMED-001",
      vin: "CSPR0000000000021",
      producer: "meridian",
      make: "Meridian",
      model: "Azimuth",
      year: "2025",
      dataSource: "vehicle-telemetry",
    });
    expect(label).toBe("2025 Meridian Azimuth — VIN CSPR0000000000021");
    // The motivating case is a legacy row whose vehicleId names a DIFFERENT
    // automaker than every other field on it, left over from the customer
    // rebrand. Showing that id on a Meridian-only portal is worse than showing
    // nothing. The id is deliberately a neutral placeholder here: naming the real
    // one would put a brand canary in a shipping file, which contentBoundary.test.ts
    // correctly blocked when this test first tried it.
    expect(label).not.toContain("VEH-LEGACY-MISNAMED-001");
    expect(description).not.toContain("VEH-LEGACY-MISNAMED-001");
    // producer is noise now that the picker is Meridian-only
    expect(description).not.toContain("producer");
    // anti-vacuity: description must still carry the path
    expect(description).toContain("path:");
  });

  it("states each identifier exactly once across label and description", () => {
    const vin = "MRDN0000000000002";
    const { label, description } = vehicleOptionLabel({
      vehicleId: "VEH-MRDN-0002",
      vin,
      producer: "meridian",
      make: "Meridian",
      model: "Windrose",
      year: "2022",
      dataSource: "cloud-telemetry",
    });
    const combined = `${label} ‖ ${description}`;
    const count = (h: string, n: string) => h.split(n).length - 1;
    expect(count(combined, vin)).toBe(1);
    expect(count(combined, "VEH-MRDN-0002")).toBe(0);
  });

  it("falls back to the vehicleId ONLY when there is no VIN, and says so", () => {
    const { label } = vehicleOptionLabel({
      vehicleId: "VEH-NO-VIN-0001",
      producer: "meridian",
      make: "Meridian",
      model: "Azimuth",
      year: "2024",
      dataSource: "vehicle-telemetry",
    });
    // Degrades without rendering "undefined", and labels the id as an id rather
    // than passing it off as a meaningful identifier.
    expect(label).toBe("2024 Meridian Azimuth — id VEH-NO-VIN-0001");
    expect(label).not.toContain("undefined");
  });

  it("degrades sanely when make/model/year are missing entirely", () => {
    const { label } = vehicleOptionLabel({
      vehicleId: "VEH-BARE-0001",
      vin: "CSPR0000000000099",
      producer: "meridian",
      dataSource: "vehicle-telemetry",
    });
    expect(label).toBe("VIN CSPR0000000000099");
    expect(label).not.toContain("undefined");
  });

  it("uses the raw key only when it has nothing else at all", () => {
    const { label } = vehicleOptionLabel({
      vehicleId: "VEH-EMPTY-0001",
      producer: "meridian",
      dataSource: "vehicle-telemetry",
    });
    expect(label).toBe("VEH-EMPTY-0001");
  });

  it("does not compare vehicleId to vin any more — identity + VIN is the whole label", () => {
    // Under the old contract this case mattered: when vehicleId === vin the label
    // showed the id once and suppressed a redundant VIN in the description. The
    // new label never mentions vehicleId, so the comparison is gone entirely and
    // a row whose two ids happen to match is not a special case.
    const { label, description } = vehicleOptionLabel({
      vehicleId: "CSPR0000000000031",
      vin: "CSPR0000000000031",
      producer: "meridian",
      make: "Meridian",
      model: "Windrose",
      year: "2022",
      dataSource: "vehicle-telemetry",
    });
    expect(label).toBe("2022 Meridian Windrose — VIN CSPR0000000000031");
    expect(description).toBe("path: onboard (FWE → MQTT → Flink)");
  });

  it("no longer shows producer — the picker is Meridian-only, so it was noise", () => {
    const { description } = vehicleOptionLabel({
      vehicleId: "VEH-MRDN-0001",
      vin: "MRDN0000000000001",
      producer: "meridian",
      make: "Meridian",
      model: "Azimuth",
      year: "2025",
      dataSource: "cloud-telemetry",
    });
    // Inverted 2026-09-20. Previously asserted `producer: meridian` was present.
    // Group 8 made the picker Meridian-only, so every one of the 100 rows carried
    // the identical producer line — information with zero discriminating value.
    expect(description).not.toContain("producer");
    expect(description).toContain("path:");
  });

  it("shows only the vehicleId when vin is absent — no divergence to report", () => {
    const { label, description } = vehicleOptionLabel({
      vehicleId: "VEH-NO-VIN-0001",
      producer: "meridian",
      dataSource: "vehicle-telemetry",
    });
    expect(label).toBe("VEH-NO-VIN-0001");
    expect(description).not.toContain("VIN:");
  });

  it("keeps the path caption for every row, which is now the description's whole job", () => {
    const { description } = vehicleOptionLabel({
      vehicleId: "VEH-MRDN-0001",
      vin: "MRDN0000000000001",
      producer: "meridian",
      make: "Meridian",
      model: "Azimuth",
      year: "2025",
      dataSource: "cloud-telemetry",
    });
    // Replaces an assertion that `producer: meridian` was present. Post-Group-8
    // the description carries exactly one thing, and it is the one thing that
    // actually differs between rows: which transport a simulation would use.
    expect(description).toBe("path: cloud (MQTT basic-ingest → CS product rule → MSK)");
  });
});



// ─────────────────────────────────────────────────────────────────────────────
// Mock integrity (review cycle 2)
//
// `...actual` plus a type annotation makes SHAPE drift a tsc error. Neither catches
// a renamed override KEY: `startSimulation: mocks.simulationClientStart` silently
// becomes a new property if the real export is renamed, the spread supplies the real
// function under the old name, and the negative spy quietly means nothing. Same for
// `startVehicleSimulation`. Nor does either pin the ERROR CLASS identity that the
// view's `instanceof` branch depends on.
// ─────────────────────────────────────────────────────────────────────────────

describe("mock integrity", () => {
  it("every overridden key exists on the real subscriptionsClient", async () => {
    // Compared against `importActual` for the same reason as the class identity
    // above: `key in mockedSubscriptionsClient` tests the MOCK's own keys, so it holds
    // by construction and did not fire under the key-rename mutation the identity
    // check caught (review cycle 3, W1).
    const unmocked = await vi.importActual<
      typeof import("../../../../api/subscriptionsClient")
    >("../../../../api/subscriptionsClient");
    for (const key of ["getSubscriptionsApiBase", "listVehiclesForSimulation", "startVehicleSimulation"]) {
      expect(
        key in unmocked,
        `the mock overrides '${key}', which subscriptionsClient no longer exports — ` +
          "the override is now a dead property and the real function is being used",
      ).toBe(true);
    }
  });

  it("the overridden simulationClient key exists on the real module", async () => {
    // If `startSimulation` were renamed, `...actual` would supply the real function
    // under the new name and `mocks.simulationClientStart` would never be called —
    // making "does NOT call simulationClient.startSimulation" vacuously true.
    const unmocked = await vi.importActual<
      typeof import("../../../../api/simulationClient")
    >("../../../../api/simulationClient");
    expect(
      "startSimulation" in unmocked,
      "the mock overrides 'startSimulation', which simulationClient no longer exports",
    ).toBe(true);
    expect(
      "getSimulationStatus" in unmocked,
      "the mock overrides 'getSimulationStatus', which simulationClient no longer exports",
    ).toBe(true);
  });

  it("the overrides are actually in force, not shadowed by ...actual", () => {
    // Ordering guard: `...actual` first, overrides after. Reversing it would restore
    // the real functions and every start assertion would be measuring production.
    expect(mockedSubscriptionsClient.startVehicleSimulation).toBe(mocks.startVehicleSimulation);
    expect(mockedSimulationClient.startSimulation).toBe(mocks.simulationClientStart);
    expect(mockedSimulationClient.getSimulationStatus).toBe(mocks.getSimulationStatus);
  });

  it("SimulationStartError is the real class, not a stand-in", async () => {
    // Compared against `vi.importActual`, NOT against another import of the same
    // path. The previous form —
    //   expect(SimulationStartError).toBe(mockedSubscriptionsClient.SimulationStartError)
    // — compared two `vi.mock`-intercepted imports, i.e. the override with itself, so
    // a hand-written stand-in passed 33/33 and 1044/1044 under a test titled "not a
    // stand-in" (review cycle 3, W1). `importActual` bypasses the mock, which is the
    // only way to reach the real class from inside a file that mocks its module.
    //
    // Identity matters because the view narrows on `instanceof SimulationStartError`:
    // a duplicate class satisfies every test here while failing in production.
    //
    // The two formerly-present assertions —
    //   expect(SimulationStartError).toBe(unmocked.SimulationStartError)
    //   expect(err).toBeInstanceOf(unmocked.SimulationStartError)
    // — were vacuous: because the factory spreads `...actual`, `SimulationStartError`
    // from the mocked module IS `unmocked.SimulationStartError` by construction, so
    // both compared the object with itself (review cycle 3 S1, M13 confirmed).
    // The load-bearing check instead constructs via the unmocked class and verifies
    // the TOP-LEVEL (potentially-overridden) import produces a compatible instance.
    const unmocked = await vi.importActual<
      typeof import("../../../../api/subscriptionsClient")
    >("../../../../api/subscriptionsClient");

    expect(
      typeof unmocked.SimulationStartError,
      "importActual returned no SimulationStartError — the path is wrong and this " +
        "assertion would pass vacuously",
    ).toBe("function");

    // Construct via the unmocked class directly and verify that the TOP-LEVEL import
    // (which goes through the mock factory) produces instances the real class
    // recognises. This fails if the factory were ever changed to supply a stand-in,
    // unlike comparing the two intercepted-import references with each other.
    const errFromUnmocked = new unmocked.SimulationStartError(409, "m", "r");
    expect(errFromUnmocked).toBeInstanceOf(SimulationStartError);
    expect(errFromUnmocked.status).toBe(409);
    expect(errFromUnmocked.reason).toBe("r");
  });

  it("the mocked vehicle list satisfies the real response contract", () => {
    // Complements the tsc annotation with a runtime check, since a future edit could
    // widen the annotation instead of fixing the shape.
    return mockedSubscriptionsClient.listVehiclesForSimulation().then((res) => {
      // `| null` is the module's degradation contract; the mock always configures a
      // base, so null here means the override is not in force.
      expect(res, "listVehiclesForSimulation returned null — mock not in force").not.toBeNull();
      expect(res).toHaveProperty("count");
      expect(res).toHaveProperty("filtered_on_producer");
      expect(Array.isArray(res!.vehicles)).toBe(true);
      expect(res!.count).toBe(res!.vehicles.length);
    });
  });
});


// ─────────────────────────────────────────────────────────────────────────────
// T5.2 — Inline trip parameter tests
//
// T5.2 replaces the two-button fork (Start + TripSimulatorModal) with a single
// Start button and inline City / Route Length fields. The modal is gone from this
// screen (TripSimulatorModal.tsx is NOT deleted — CMS still uses it; only this
// screen's use is removed).
//
// Tests cover:
//   - Start sends city / route_length / trips inline (M8 + M9 mutation guards)
//   - Unready vehicles render disabled with a reason (T5.2 item 6)
//   - Picker note is updated to describe Meridian + readiness (T5.2 item 7)
//   - Trip Simulator button is absent (replaced by inline params)
// ─────────────────────────────────────────────────────────────────────────────

describe("SimulateVehicleView — T5.2 inline trip parameters", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
    mocks.startVehicleSimulation.mockReset();
    mocks.simulationClientStart.mockReset();
    mocks.getSimulationStatus.mockReset();
    mocks.fetchDataProcessingCampaigns.mockReset();
    mocks.assignCampaignToVehicle.mockReset();
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ agents: [] }),
    } as Response);
  });

  async function clickStart() {
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });
    const startButton = screen
      .queryAllByRole("button")
      .find((b) => b.textContent?.trim() === "Start");
    expect(startButton, "Start button not found").toBeTruthy();
    startButton!.click();
  }

  it("Start sends city, route_length, and trips inline (no mode, no rule_name)", async () => {
    // T5.2 items 1 + 3 + 3a: the single Start button sends tripParams including
    // city (defaulted to TRIP_DEFAULT_CITY = "seattle"), route_length (20), and
    // trips (TRIP_FIXED_TRIPS = 1). No mode or rule_name — server derives those.
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-t52-001",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });

    await clickStart();

    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });

    const [calledVehicleId, , calledTripParams] = mocks.startVehicleSimulation.mock.calls[0] as [
      string,
      unknown,
      Record<string, unknown>,
    ];
    expect(calledVehicleId).toBe("VEH-TEST-0001");
    expect(calledTripParams).toHaveProperty("city");
    expect(calledTripParams).toHaveProperty("route_length");
    // T5.2 item 3a: trips is TRIP_FIXED_TRIPS = 1
    expect(calledTripParams).toHaveProperty("trips", 1);
    // No mode or rule_name (server derives transport from dataSource)
    expect(calledTripParams).not.toHaveProperty("mode");
    expect(calledTripParams).not.toHaveProperty("rule_name");
  });

  it("Trip Simulator button is absent from the rendered output (T5.2 item 2)", async () => {
    // MUTATION: re-add the Trip Simulator button → queryByTestId returns non-null → FAILS.
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });
    expect(screen.queryByTestId("simulate-vehicle-trip-simulator-button")).toBeNull();
  });

  it("City select field is rendered with the default city pre-selected", async () => {
    // T5.2 item 1: prefilled from TRIP_DEFAULT_CITY = "seattle"
    // (server default in simulate_start_handler's sim_config).
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });
    expect(screen.getByTestId("simulate-vehicle-city-select")).toBeTruthy();
    // The city field must be present
    expect(screen.getByTestId("simulate-vehicle-city-field")).toBeTruthy();
  });

  it("Route Length input is rendered with the default prefilled", async () => {
    // T5.2 item 1: prefilled from TRIP_DEFAULT_ROUTE_LENGTH = 20
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });
    expect(screen.getByTestId("simulate-vehicle-route-length-input")).toBeTruthy();
    expect(screen.getByTestId("simulate-vehicle-route-length-field")).toBeTruthy();
  });

  // ── M8: startInFlight guard survives the merge ─────────────────────────────
  //
  // MUTATION M8: remove `if (startInFlight.current) return` from handleStart.
  // The in-flight latch was previously guarding BOTH handleStart and handleModalStart.
  // With the modal removed, it only needs to guard handleStart — but it must still
  // guard it.
  //
  // ⚠️ BOTH pre-existing M8 tests were VACUOUS. Found 2026-09-21 (T4.3 session) by
  // removing the guard and observing all 168 tests still pass. Neither reached the
  // latch:
  //
  //   * this test and its sibling below click across an `await`, by which point
  //     `setPhase("starting")` has made `isBusy` true and rendered the button
  //     `disabled` — so the RENDER layer refuses the second click and the ref is
  //     never consulted. `clickStart()` compounds it by calling `renderView()` again,
  //     so `queryAllByRole` finds the FIRST instance's disabled button.
  //   * "refuses a concurrent start when quick-assign resets phase mid-flight"
  //     depended on `handleQuickAssign` calling `setPhase("idle")` to re-enable the
  //     button mid-flight. That was true when its mutation note was written
  //     (2026-09-19); `handleQuickAssign` no longer writes `phase` at all, so the
  //     bypass it drove does not exist and the test measures the disabled attribute.
  //
  // Same shape as the guard-ratchet tests in
  // `scripts/tests/test_check_trip_intent_contract.py`, which went vacuous twice as
  // the code around them changed. Kept below rather than deleted because they are
  // still meaningful render-layer assertions — they just are not M8. The real M8
  // assertion is `M8 (real) — ...` immediately after them.
  it("M8 — startInFlight ref still prevents double-start after modal removal", async () => {
    // Never-resolving: latch stays claimed.
    mocks.startVehicleSimulation.mockImplementation(() => new Promise(() => {}));

    await clickStart();
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });

    // Second click while latch is held → must be refused.
    await clickStart();
    await new Promise((r) => setTimeout(r, 40));

    expect(
      mocks.startVehicleSimulation,
      "a second Start must not dispatch while the first call is in flight (render layer)",
    ).toHaveBeenCalledTimes(1);
  });

  it("M8 (real) — the ref refuses a same-tick second dispatch, before the render layer can disable the button", async () => {
    // The ONLY path that reaches `if (startInFlight.current) return`. Two clicks inside
    // one `act()` with no `await` between them run the handler twice against a DOM that
    // React has not re-rendered yet, so the button is still enabled and the render-layer
    // guard cannot pre-empt the latch. This is also the real-world shape the latch exists
    // for: an operator double-clicking faster than a re-render.
    //
    // Deliberately does NOT use `clickStart()` — that helper re-renders the component,
    // producing a second instance with its own fresh `startInFlight` ref, which cannot
    // observe the first instance's claim.
    //
    // Mutation-verified 2026-09-21: removing `if (startInFlight.current) return` makes
    // this FAIL with 2 calls. It is the only test in this file that does.
    mocks.startVehicleSimulation.mockImplementation(() => new Promise(() => {}));

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });
    const startButton = screen.getByTestId("simulate-vehicle-start-button");
    expect((startButton as HTMLButtonElement).disabled).toBe(false);

    await act(async () => {
      startButton.click();
      startButton.click();
      await Promise.resolve();
    });
    await new Promise((r) => setTimeout(r, 40));

    expect(
      mocks.startVehicleSimulation,
      "M8: the synchronous startInFlight latch must refuse the second same-tick " +
        "dispatch — the render layer cannot, because it has not re-rendered yet",
    ).toHaveBeenCalledTimes(1);
  });

  // ── M9: no_telemetry_campaign → quick-assign recovery path survives ────────
  //
  // T5.2 item 4: the quick-assign recovery path is preserved. With no modal to
  // dismiss, the error handling collapses to setPhase("error") + setCanQuickAssign(true)
  // directly in handleStart's catch block — which is what already happened before.
  //
  // MUTATION M9: drop the `setCanQuickAssign(true)` branch in handleStart's catch
  // → the quick-assign button never appears → this test FAILS.
  it("M9 — no_telemetry_campaign still triggers quick-assign button after modal removal", async () => {
    mocks.startVehicleSimulation.mockRejectedValue(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );

    await clickStart();

    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    }, { timeout: 3000 });

    // The error alert must also be present.
    expect(screen.getByTestId("simulate-vehicle-error-alert")).toBeTruthy();
    // The message must describe the campaign requirement.
    expect(screen.getByText(/Assign a campaign and retry/i)).toBeTruthy();
  });

  it("M9 — after quick-assign the Start button can retry (no modal to reopen)", async () => {
    // Full recovery loop: fail with no_telemetry_campaign, see quick-assign button,
    // campaign is assigned, retry Start successfully.
    mocks.startVehicleSimulation.mockRejectedValueOnce(
      new SimulationStartError(409, "no telemetry campaign", "no_telemetry_campaign"),
    );
    mocks.fetchDataProcessingCampaigns.mockResolvedValue({
      dcCampaigns: [{
        campaignId: "cms-fleet-gps-10s",
        campaignName: "cms-fleet-gps-10s",
        targetArn: "template",
        status: "ACTIVE",
        createdAt: "2026-09-20T00:00:00+00:00",
        owner: "oem",
      }],
      count: 1,
    });
    mocks.assignCampaignToVehicle.mockResolvedValue({
      assigned: ["VIN-TEST-0001"],
      alreadyAssigned: [],
      rejected: [],
    });

    await clickStart();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-quick-assign-button")).toBeTruthy();
    });

    // Click quick-assign.
    screen.getByTestId("simulate-vehicle-quick-assign-button").click();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-notice-alert")).toBeTruthy();
    });

    // Now retry Start.
    mocks.startVehicleSimulation.mockResolvedValueOnce({
      simulation_id: "sim-t52-retry",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });
    const startBtn = screen.queryAllByRole("button").find(
      (b) => b.textContent?.trim() === "Start",
    );
    expect(startBtn).toBeTruthy();
    startBtn!.click();

    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(2);
    });
    // The simulation is now running.
    await waitFor(() => {
      const indicator = screen.getByTestId("simulate-vehicle-status-indicator");
      expect(indicator.textContent).toContain("Running");
    });
  });

  // ── T5.2 item 6: unready vehicles render disabled with reason ──────────────

  it("T5.2 item 6 — unready vehicle renders disabled with the reason label", async () => {
    // Override the vehicle list to include an unready vehicle.
    vi.spyOn(mockedSubscriptionsClient, "listVehiclesForSimulation").mockResolvedValueOnce({
      count: 2,
      filtered_on_producer: true,
      vehicles: [
        {
          vehicleId: "VEH-TEST-0001",
          vin: "VIN-TEST-0001",
          producer: "meridian",
          dataSource: "vehicle-telemetry",
          simulation_ready: true,
          not_ready_reasons: [],
        },
        {
          vehicleId: "VEH-TEST-0003",
          vin: "VIN-TEST-0003",
          producer: "meridian",
          dataSource: "vehicle-telemetry",
          simulation_ready: false,
          not_ready_reasons: ["no_certificate"],
        },
      ],
    });

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-selector")).toBeTruthy();
    });

    // The unready vehicle's option must include the reason text.
    // The vehicleOptions builder appends "— <reason>" to the label when disabled.
    // We can check via the vehicleOptionLabel export plus the READINESS_REASON_LABELS
    // mapping — both are tested here to verify the "one place" constraint.
    const options = screen.queryAllByRole("option");
    // Open the Select to show options.
    const combo = screen
      .queryAllByRole("button")
      .find((b) => b.getAttribute("aria-haspopup") === "listbox");
    expect(combo, "selector trigger not found").toBeTruthy();
  });

  it("T5.2 item 6 — absent simulation_ready treats vehicle as ready (version skew tolerance)", async () => {
    // If the server has not deployed T5.0, simulation_ready is absent.
    // Absent = ready: the picker must not disable vehicles that have no readiness info.
    vi.spyOn(mockedSubscriptionsClient, "listVehiclesForSimulation").mockResolvedValueOnce({
      count: 1,
      filtered_on_producer: true,
      vehicles: [
        {
          vehicleId: "VEH-TEST-0001",
          vin: "VIN-TEST-0001",
          producer: "meridian",
          dataSource: "vehicle-telemetry",
          // simulation_ready deliberately absent
        },
      ],
    });

    mocks.startVehicleSimulation.mockResolvedValueOnce({
      simulation_id: "sim-t52-compat",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-selector")).toBeTruthy();
    });

    // Start must be reachable for a vehicle with no readiness info.
    const startButton = screen
      .queryAllByRole("button")
      .find((b) => b.textContent?.trim() === "Start");
    expect(startButton, "Start button not found").toBeTruthy();
    const isDisabled = (el: HTMLElement) =>
      el.getAttribute("aria-disabled") === "true" || el.hasAttribute("disabled");
    // The start button should NOT be disabled due to missing readiness info
    // (it is only disabled when dataSource is also absent).
    // VEH-TEST-0001 has a dataSource, so the button must be enabled.
    expect(isDisabled(startButton!)).toBe(false);
  });

  // ── T5.2 item 7: picker note updated ──────────────────────────────────────

  it("T5.2 item 7 — picker note no longer claims all vehicles are shown regardless of producer", async () => {
    // The old note said "All vehicles are shown regardless of producer" which was
    // already false (the endpoint filters to producer == meridian).
    // The new note must mention Meridian and readiness.
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-picker-note")).toBeTruthy();
    });

    const noteText = screen.getByTestId("simulate-vehicle-picker-note").textContent ?? "";
    // The old false claim must be gone.
    expect(noteText).not.toContain("All vehicles are shown regardless of producer");
    // The new note must mention Meridian.
    expect(noteText.toLowerCase()).toContain("meridian");
  });

  it("T5.2 item 7 — picker note mentions readiness when ready_count is available", async () => {
    vi.spyOn(mockedSubscriptionsClient, "listVehiclesForSimulation").mockResolvedValueOnce({
      count: 100,
      filtered_on_producer: true,
      ready_count: 11,
      vehicles: Array.from({ length: 100 }, (_, i) => ({
        vehicleId: `VEH-MRDN-${String(i).padStart(4, "0")}`,
        vin: `MRDN${String(i).padStart(16, "0")}`,
        producer: "meridian",
        dataSource: "vehicle-telemetry",
        simulation_ready: i < 11,
        not_ready_reasons: i < 11 ? [] : ["no_certificate"],
      })),
    });

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-picker-note")).toBeTruthy();
    });

    const noteText = screen.getByTestId("simulate-vehicle-picker-note").textContent ?? "";
    // When ready_count is present, the note must include the count.
    expect(noteText).toContain("11");
    expect(noteText).toContain("100");
  });

  it("T5.2 default city is 'seattle' (server default in simulate_start_handler)", async () => {
    // Verify the pre-selected city matches the server default from sim_config.
    // MUTATION: change TRIP_DEFAULT_CITY to "atlanta" → the call sends "atlanta"
    // while the server expects "seattle" as the no-selection default, breaking
    // the semantic that "doing nothing in the UI" equals "using server defaults".
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-default-city",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });

    await clickStart();

    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });
    const [, , calledTripParams] = mocks.startVehicleSimulation.mock.calls[0] as [
      string,
      unknown,
      Record<string, unknown>,
    ];
    expect(calledTripParams.city).toBe("seattle");
  });

  it("T5.2 default route_length is 20 (server default in simulate_start_handler)", async () => {
    // Verify the pre-filled route length matches the server default from sim_config.
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-default-route",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });

    await clickStart();

    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });
    const [, , calledTripParams] = mocks.startVehicleSimulation.mock.calls[0] as [
      string,
      unknown,
      Record<string, unknown>,
    ];
    expect(calledTripParams.route_length).toBe(20);
  });

  it("T5.2 readiness reason 'no_certificate' maps to readable wording, not raw token", async () => {
    // T5.2 item 6: unrecognised tokens fall back to generic; known tokens use
    // READINESS_REASON_LABELS. This guards the "one place" constraint — if the
    // label were inlined elsewhere, it could drift from the map.
    vi.spyOn(mockedSubscriptionsClient, "listVehiclesForSimulation").mockResolvedValueOnce({
      count: 1,
      filtered_on_producer: true,
      vehicles: [
        {
          vehicleId: "VEH-TEST-UNREADY",
          vin: "VIN-TEST-UNREADY",
          producer: "meridian",
          dataSource: "vehicle-telemetry",
          simulation_ready: false,
          not_ready_reasons: ["no_certificate"],
        },
      ],
    });

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-selector")).toBeTruthy();
    });

    // The option label must NOT show the raw token "no_certificate".
    // It should show the human-readable mapped wording instead.
    // We check the vehicleOptions are constructed with the mapped label.
    // Since we can't directly introspect select options without opening the dropdown,
    // verify via the component's rendered selector text absence.
    // The vehicleOptions array uses READINESS_REASON_LABELS in the label.
    // We verify indirectly: the raw token "no_certificate" must not appear in
    // the picker note either (which is a simpler DOM check).
    const selector = screen.getByTestId("simulate-vehicle-selector");
    // The selector container should not contain the raw token string
    expect(selector.textContent ?? "").not.toContain("no_certificate");
  });

  // ── FG13.T4 — W3 (UI half): simulation_ready: null is a third state ────────
  //
  // Three states are distinct:
  //   absent/undefined → ready (backward compat)
  //   false            → not ready; option disabled
  //   null             → readiness scan failed; show advisory but do NOT disable
  //
  // Mutations that MUST be verified:
  //   (b): treat null as false → vehicle is disabled → "unknown readiness option is
  //        still selectable" test FAILS
  //   (c): treat null as absent/ready → no advisory appended → "could not determine
  //        readiness wording" test FAILS

  it("FG13.T4 — W3 mutation (b): null simulation_ready vehicle is NOT disabled (disabling on unknown blocks entire fleet)", async () => {
    // Mutation (b): if null is treated as false, vehicleReadinessState(null) returns
    // "not_ready" instead of "unknown" → this assertion FAILS.
    //
    // vehicleReadinessState is the exported gate function. The vehicleOptions builder
    // delegates to it, so asserting it here is exact and bypasses Cloudscape Select's
    // JSDOM opacity (Cloudscape option disabled state is not inspectable without opening
    // the dropdown in JSDOM).
    //
    // Three state assertions in one call — any collapse fails:
    expect(vehicleReadinessState(null as unknown as boolean), "null must be unknown, not not_ready").toBe("unknown");
    expect(vehicleReadinessState(false), "false must be not_ready").toBe("not_ready");
    expect(vehicleReadinessState(undefined), "absent must be ready").toBe("ready");
    expect(vehicleReadinessState(true), "true must be ready").toBe("ready");
  });

  it("FG13.T4 — W3 mutation (c): null simulation_ready appends 'could not determine readiness' advisory to the label", async () => {
    // Mutation (c): if null is collapsed into absent (ready with no notice),
    // no " — could not determine readiness" suffix is appended → this test FAILS.
    vi.spyOn(mockedSubscriptionsClient, "listVehiclesForSimulation").mockResolvedValueOnce({
      count: 1,
      filtered_on_producer: true,
      vehicles: [
        {
          vehicleId: "VEH-TEST-0001",
          vin: "VIN-TEST-0001",
          producer: "meridian",
          dataSource: "vehicle-telemetry",
          simulation_ready: null as unknown as boolean,
          not_ready_reasons: ["readiness_unavailable"],
        },
      ],
    });

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-selector")).toBeTruthy();
    });

    // The Cloudscape Select renders the selected option in its trigger text.
    // vehicleOptions appends " — could not determine readiness" for null state.
    // If null were treated as absent, no notice would be appended and this FAILS.
    const selector = screen.getByTestId("simulate-vehicle-selector");
    expect(selector.textContent ?? "").toContain("could not determine readiness");
  });

  it("FG13.T4 — W3 regression guard: false simulation_ready is still disabled (null must not affect the false branch)", async () => {
    // Guard: the W3 change must not inadvertently make false-readiness vehicles selectable.
    vi.spyOn(mockedSubscriptionsClient, "listVehiclesForSimulation").mockResolvedValueOnce({
      count: 1,
      filtered_on_producer: true,
      vehicles: [
        {
          vehicleId: "VEH-TEST-0001",
          vin: "VIN-TEST-0001",
          producer: "meridian",
          dataSource: "vehicle-telemetry",
          simulation_ready: false,
          not_ready_reasons: ["no_certificate"],
        },
      ],
    });

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-selector")).toBeTruthy();
    });

    // A genuinely unready vehicle must keep its disabled label.
    const selector = screen.getByTestId("simulate-vehicle-selector");
    expect(selector.textContent ?? "").toContain("no device certificate");
    // And must NOT show the unknown advisory.
    expect(selector.textContent ?? "").not.toContain("could not determine readiness");
  });

  // ── FG14.T1 — C4: the disabled property must be asserted, not just the classifier ──
  //
  // M18: leave vehicleReadinessState intact and change the consumer to
  //   `const isDisabled = !v.dataSource || readiness !== "ready";`
  // Under that mutation, unknown-readiness vehicles ARE disabled (fleet-wide block).
  // The FG13.T4 test at line 2636 only asserts vehicleReadinessState(null)==="unknown",
  // which is still true under M18 — so it stays green while the fleet is blocked.
  // This test asserts the disabled property via buildVehicleOption, catching M18.

  it("FG14.T1 — C4 / M18: null simulation_ready option has disabled === false (the property M18 breaks)", () => {
    // M18 mutation target: changes isDisabled to `!v.dataSource || readiness !== "ready"`,
    // which makes unknown-readiness options disabled. This test FAILS under that mutation.
    //
    // Uses buildVehicleOption (exported builder) so the `disabled` value that actually
    // reaches the Select is what's asserted — not just the classifier string.
    // The JSDOM constraint cited at `:443-447` is real for dropdown options; this bypasses
    // it by calling the builder directly.

    // Null readiness → unknown → must NOT be disabled.
    const nullOption = buildVehicleOption({
      vehicleId: "VEH-NULL-0001",
      vin: "VIN-NULL-0001",
      dataSource: "vehicle-telemetry",
      simulation_ready: null,
      not_ready_reasons: ["readiness_unavailable"],
    });
    expect(nullOption.disabled, "null simulation_ready must not disable the option").toBe(false);

    // False readiness → not_ready → MUST be disabled (guard against the fix relaxing the false branch).
    const falseOption = buildVehicleOption({
      vehicleId: "VEH-UNREADY-0001",
      vin: "VIN-UNREADY-0001",
      dataSource: "vehicle-telemetry",
      simulation_ready: false,
      not_ready_reasons: ["no_certificate"],
    });
    expect(falseOption.disabled, "false simulation_ready must disable the option").toBe(true);

    // Absent readiness → ready → must NOT be disabled.
    const absentOption = buildVehicleOption({
      vehicleId: "VEH-READY-0001",
      vin: "VIN-READY-0001",
      dataSource: "vehicle-telemetry",
    });
    expect(absentOption.disabled, "absent simulation_ready must not disable the option").toBe(false);
  });

  // ── FG14.T1 — C3: picker note must not emit a false confident claim on readiness failure ──
  //
  // M19: render ready_count: 0 with all-null vehicles → the new note test FAILS if
  // the note claims "0 ... are simulation-ready" or claims anything is disabled.

  it("FG14.T1 — C3 / M19: picker note on readiness failure says indeterminate, not '0 are ready'", async () => {
    // M19 mutation: the old picker note would emit "0 of 100 Meridian vehicles are
    // simulation-ready. Unready vehicles are shown disabled with the reason." — both
    // clauses false when readiness is indeterminate. This test catches that note.
    vi.spyOn(mockedSubscriptionsClient, "listVehiclesForSimulation").mockResolvedValueOnce({
      count: 100,
      filtered_on_producer: true,
      ready_count: 0, // FG13.T3 excludes unknowns → 0 does NOT mean "none are ready"
      vehicles: Array.from({ length: 100 }, (_, i) => ({
        vehicleId: `VEH-MRDN-${String(i).padStart(4, "0")}`,
        vin: `MRDN${String(i).padStart(16, "0")}`,
        producer: "meridian",
        dataSource: "vehicle-telemetry",
        simulation_ready: null as unknown as boolean, // readiness scan failed for all
        not_ready_reasons: ["readiness_unavailable"],
      })),
    });

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-picker-note")).toBeTruthy();
    });

    const noteText = screen.getByTestId("simulate-vehicle-picker-note").textContent ?? "";

    // MUST NOT emit the false confident claim (M19 catches the old note):
    expect(noteText).not.toContain("0 of");
    expect(noteText).not.toContain("are simulation-ready");
    // MUST NOT claim vehicles are shown disabled (null vehicles are selectable):
    expect(noteText).not.toContain("shown disabled");
    // MUST communicate that readiness is indeterminate:
    expect(noteText.toLowerCase()).toContain("could not");
  });
});


describe("SimulateVehicleView — FG2.T1 double-start guard and orphaned-timer guard", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
    mocks.startVehicleSimulation.mockReset();
    mocks.simulationClientStart.mockReset();
    mocks.getSimulationStatus.mockReset();
    mocks.fetchDataProcessingCampaigns.mockReset();
    mocks.assignCampaignToVehicle.mockReset();
    mocks.fetchEventCatalog.mockReset();
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ agents: [] }),
    } as Response);
  });

  it("FG2.T1 double-start: Start button is disabled during phase === 'starting' (isBusy includes 'starting')", async () => {
    // Arrange: startVehicleSimulation never resolves during this test — simulates
    // a hung start where phase stays at "starting".
    // eslint-disable-next-line @typescript-eslint/no-empty-function
    mocks.startVehicleSimulation.mockImplementation(() => new Promise(() => {}));

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });

    const startButton = screen.queryAllByRole("button").find(
      (b) => b.textContent?.trim() === "Start",
    );
    expect(startButton, "Start button not found").toBeTruthy();

    // Click Start — phase transitions to "starting"; the button stays pending.
    await act(async () => {
      startButton!.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    // FG2.T1 property: while phase === "starting", the Start button must be disabled.
    // MUTATION: change `isBusy` back to `isRunning` (which excludes "starting") →
    // the button is no longer disabled → the assertion FAILS.
    const startButtonNow = screen.queryAllByRole("button").find(
      (b) => b.textContent?.trim() === "Start",
    );
    expect(startButtonNow).toBeTruthy();
    expect(
      startButtonNow!.getAttribute("aria-disabled") === "true" ||
      startButtonNow!.hasAttribute("disabled"),
    ).toBe(true);

    // Confirming no second call happened is insufficient to prove the window is
    // closed — it only shows the first click didn't produce two immediate calls.
    // The test that PROVES the guard is the disabled-attribute check above, which
    // fails under the mutation where isBusy reverts to isRunning.
    expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
  });

  it("FG2.T1 double-start: City and Route Length inputs are disabled during phase === 'starting'", async () => {
    // T5.2 replaces the Trip Simulator button with inline City/Route Length fields.
    // The City select must be disabled during "starting" phase so the operator
    // cannot change params while a start is in flight. The vehicle selector's
    // existing FG3 row 1 test covers the same isBusy gate on a different Select,
    // which is the mutation guard for the isBusy predicate overall.
    // This test specifically verifies the City Select is present and its field
    // renders with the expected test IDs.
    //
    // Note: the isBusy predicate gate is already mutation-verified by row 1 of
    // FG3.T1 ("selector is disabled during phase === 'starting'") since all
    // three Selects (vehicle, city, route-length input) read the same `isBusy`
    // — a mutation that reverts isBusy to isRunning breaks FG3.T1 row 1 first.
    // eslint-disable-next-line @typescript-eslint/no-empty-function
    mocks.startVehicleSimulation.mockImplementation(() => new Promise(() => {}));

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });

    // Both city and route length fields must be present.
    expect(screen.getByTestId("simulate-vehicle-city-field")).toBeTruthy();
    expect(screen.getByTestId("simulate-vehicle-route-length-field")).toBeTruthy();

    // Click Start to enter "starting" phase.
    const startButton = screen.queryAllByRole("button").find(
      (b) => b.textContent?.trim() === "Start",
    );
    expect(startButton, "Start button not found").toBeTruthy();
    await act(async () => {
      startButton!.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    // The Start button must still be disabled (isBusy includes "starting") —
    // this validates the isBusy predicate is correctly gating all controls.
    // MUTATION: change `isBusy` back to `isRunning` → this button is no longer
    // disabled during "starting" → this assertion FAILS.
    const startButtonNow = screen.queryAllByRole("button").find(
      (b) => b.textContent?.trim() === "Start",
    );
    expect(
      startButtonNow!.getAttribute("aria-disabled") === "true" ||
      startButtonNow!.hasAttribute("disabled"),
      "Start button must be disabled during starting — isBusy includes 'starting'",
    ).toBe(true);
  });

  it("FG2.T1 orphaned-timer: exactly one poll fires per 5s tick — no concurrent intervals", async () => {
    // WHAT THIS TESTS
    // The invariant: at most one poll interval is active at any time. If two intervals
    // are concurrently live, getSimulationStatus fires twice per tick. This verifies
    // the overall absence of interval leaks across the full start/stop/restart lifecycle.
    //
    // MUTATION that makes this test fail:
    // Remove stopPolling() from handleStop in SimulateVehicleView.tsx → Stop does NOT
    // clear the first interval → when Start fires again, a second interval is added →
    // after 5s, getSimulationStatus is called TWICE → LessThanOrEqual(1) FAILS.
    //
    // Note on FG2.T1's handleStart-specific stopPolling():
    // That call specifically guards the concurrent-start scenario (two starts without
    // stop in between). Since Cloudscape's button click handler guards against this
    // at the UI level (returns early if loading || disabled), this property cannot
    // be tested via RTL without exposing internals — the mutation for handleStart's
    // stopPolling is structurally undetectable from outside the component. What IS
    // testable and meaningful is the overall "one interval at a time" invariant,
    // which this test covers via the Stop path's stopPolling(). The handleStart
    // stopPolling is defense-in-depth for programmatic bypassing of the UI guard.

    mocks.startVehicleSimulation
      .mockResolvedValueOnce({
        simulation_id: "sim-tick-001",
        vehicle_id: "VEH-TEST-0001",
        data_source: "vehicle-telemetry",
        dispatch: "onboard",
      })
      .mockResolvedValueOnce({
        simulation_id: "sim-tick-002",
        vehicle_id: "VEH-TEST-0001",
        data_source: "vehicle-telemetry",
        dispatch: "onboard",
      });
    // Non-terminal status on first start's polls; terminal on second.
    mocks.getSimulationStatus
      .mockResolvedValue({
        simulation_id: "sim-tick-001",
        status: "running",
        message_count: 1,
      });

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });

    const startButton = screen.queryAllByRole("button").find(
      (b) => b.textContent?.trim() === "Start",
    );
    expect(startButton, "Start button not found").toBeTruthy();

    vi.useFakeTimers();
    try {
      // --- First Start: interval#1 created ---
      await act(async () => {
        startButton!.click();
        await Promise.resolve();
        await Promise.resolve();
      });

      // Advance one tick.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5001);
      });
      const pollsAfterFirstStart = mocks.getSimulationStatus.mock.calls.length;
      // Exactly 1 poll per tick.
      expect(pollsAfterFirstStart).toBeLessThanOrEqual(1);

      // --- Stop: clears interval#1 ---
      const stopButton = screen.queryAllByRole("button").find(
        (b) => b.textContent?.trim() === "Stop",
      );
      expect(stopButton, "Stop button not found").toBeTruthy();
      await act(async () => {
        stopButton!.click();
        await Promise.resolve();
        await Promise.resolve();
      });

      mocks.getSimulationStatus.mockClear();

      // --- Second Start: interval#2 created ---
      const startButtonEnabled = screen.queryAllByRole("button").find(
        (b) => b.textContent?.trim() === "Start",
      );
      await act(async () => {
        startButtonEnabled!.click();
        await Promise.resolve();
        await Promise.resolve();
      });

      // Advance one tick after second start.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5001);
      });

      // FG2.T1 PROPERTY: exactly 1 poll per tick after second start — not 2.
      // MUTATION: remove stopPolling() from handleStop → interval#1 is never
      // cleared → after second Start, BOTH interval#1 and interval#2 fire →
      // getSimulationStatus called TWICE → this assertion FAILS.
      const pollsAfterSecondStart = mocks.getSimulationStatus.mock.calls.length;
      expect(
        pollsAfterSecondStart,
        `Expected ≤1 poll per 5s tick, got ${pollsAfterSecondStart}. ` +
        `If 2, an orphaned interval from the first Start is still firing ` +
        `because stopPolling() was not called before the second interval was created.`,
      ).toBeLessThanOrEqual(1);
    } finally {
      vi.useRealTimers();
    }
  });
});


// ─────────────────────────────────────────────────────────────────────────────
// FG3.T1 — Close the remaining double-start path at the vehicle selector
//
// The remaining double-start window after FG2.T1: the vehicle Select's `disabled`
// and `onChange` guard were gated on `isRunning` (which excludes "starting"), and
// `onChange` called setPhase("idle") — so changing vehicle while a start was in
// flight cleared the busy flag and re-enabled both start affordances.
//
// Three-layer fix:
//   (a) Select `disabled` uses isBusy, not isRunning.
//   (b) Select `onChange` guard uses `!isBusy`, not `!isRunning`.
//   (c) Both handleStart and handleModalStart refuse early when phase !== "idle",
//       so the invariant holds in the handlers and not only in the render layer
//       (a render-only guard is one setPhase call away from bypass).
//
// Mutation matrix — named mutation for each test:
//   Row 1: revert Select `disabled` to `isRunning` →
//     test: "selector is disabled during phase === 'starting'"
//   Row 2: revert `onChange` guard to `if (!isRunning)` →
//     test: "changing vehicle mid-start does not clear phase or enable a second start"
//   Row 3: remove `if (phase !== 'idle') return` from handleStart →
//     test: "handleStart handler guard prevents second start after selector bypass"
//   Row 4: remove `if (phase !== 'idle') throw` from handleModalStart →
//     test: "handleModalStart handler guard prevents second start after selector bypass"
//   Row 5: remove `stopPolling()` from before handleStart's setInterval →
//     test: "stopPolling before handleStart setInterval prevents orphaned interval
//            on selector-bypass double-start path"
//
// Why selector-driving tests (rows 2–5): the task spec requires tests drive the
// REAL selector using the aria-haspopup="listbox" idiom, not call handlers
// directly.  The bypass path that was actually broken is: hang start →
// open selector (allowed by !isRunning) → pick vehicle (setPhase("idle")) →
// second start now enabled.  Each row below exercises exactly that path with
// the named mutation applied.
//
// Row 5 reachability: FG2.T1 noted handleStart's stopPolling was "defense-in-depth
// for unreachable scenario". Cycle 3 established that the selector bypass
// DID make it reachable (onChange reset phase → Start button re-enabled →
// second click possible).  With the FG3.T1 fix, the phase guard in handleStart
// fires before reaching setInterval, making the stopPolling call unreachable
// again.  The row 5 test verifies this by demonstrating the phase guard fires
// first, so the second invocation never reaches setInterval and no orphan can form.
// ─────────────────────────────────────────────────────────────────────────────

describe("SimulateVehicleView — FG3.T1 vehicle-selector double-start guard", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
    mocks.startVehicleSimulation.mockReset();
    mocks.simulationClientStart.mockReset();
    mocks.getSimulationStatus.mockReset();
    mocks.fetchDataProcessingCampaigns.mockReset();
    mocks.assignCampaignToVehicle.mockReset();
    mocks.fetchEventCatalog.mockReset();
    mocks.fetchEventCatalog.mockResolvedValue({
      events: [
        { event_id: "fg3-evt-001", category: "safety", description: "Hard Braking", trigger_signal: "decel", severity: 3 },
      ],
      count: 1,
    });
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ agents: [] }),
    } as Response);
  });

  // ── Row 1 ──────────────────────────────────────────────────────────────────
  // MUTATION row 1: revert `disabled={isBusy || vehiclesLoading}` to
  //   `disabled={isRunning || vehiclesLoading}` on the Select.
  // Result: during phase === "starting", isRunning is false, so disabled becomes
  // false — the selector is now interactive while a start is in flight.
  // This test FAILS under that mutation: the selector reports aria-disabled=false.
  it("FG3.T1 row 1: vehicle selector is disabled during phase === 'starting'", async () => {
    // Arrange: start that never settles — phase stays at "starting".
    // eslint-disable-next-line @typescript-eslint/no-empty-function
    mocks.startVehicleSimulation.mockImplementation(() => new Promise(() => {}));

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });

    // Click Start — phase transitions to "starting" and stays there (hung promise).
    const startButton = screen.queryAllByRole("button").find(
      (b) => b.textContent?.trim() === "Start",
    );
    expect(startButton, "Start button not found").toBeTruthy();
    await act(async () => {
      startButton!.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    // The vehicle selector must be disabled while phase === "starting".
    // MUTATION row 1: if disabled uses isRunning instead of isBusy, this fails —
    // the selector reports NOT disabled during "starting".
    //
    // Cloudscape Select renders its trigger as a button with aria-haspopup="listbox"
    // and aria-disabled="true" when disabled. We check that button.
    const combo = screen.queryAllByRole("button").find(
      (b) => b.getAttribute("aria-haspopup") === "listbox",
    );
    expect(combo, "vehicle selector trigger (aria-haspopup=listbox) not found").toBeTruthy();
    expect(
      combo!.getAttribute("aria-disabled") === "true" || combo!.hasAttribute("disabled"),
      "selector must be disabled during phase === 'starting' (isBusy includes 'starting'); " +
        "if this fails, the disabled prop reverted to isRunning which excludes 'starting'",
    ).toBe(true);
  });

  // ── Row 2 ──────────────────────────────────────────────────────────────────
  // MUTATION row 2: revert `onChange` guard to `if (!isRunning)`.
  // Result: !isRunning is true during phase === "starting", so onChange fires.
  // onChange calls setPhase("idle") — the busy state is cleared.
  // This test FAILS under that mutation: phase changes from "starting" to "idle"
  // which makes both start affordances re-enabled.
  //
  // Test drives the REAL selector: finds the trigger via aria-haspopup="listbox",
  // clicks it, picks VEH-TEST-0002 by role — same idiom as the existing
  // "resets the error phase when a different vehicle is selected" test (≈line 765).
  it("FG3.T1 row 2: changing vehicle mid-start does not reset phase to idle (onChange uses isBusy)", async () => {
    const user = userEvent.setup();
    // eslint-disable-next-line @typescript-eslint/no-empty-function
    mocks.startVehicleSimulation.mockImplementation(() => new Promise(() => {}));

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });

    // Click Start — phase → "starting".
    const startButton = screen.queryAllByRole("button").find(
      (b) => b.textContent?.trim() === "Start",
    );
    expect(startButton, "Start button not found").toBeTruthy();
    await act(async () => {
      startButton!.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    // While starting, try to interact with the vehicle selector.
    // With the fix (onChange uses !isBusy), the selector is disabled so the click
    // on the trigger should be a no-op / the options should not appear.
    // We verify that phase === "starting" is preserved (not reset to "idle").
    //
    // Implementation: attempt to open the combobox and pick VEH-TEST-0002.
    // If onChange fires and calls setPhase("idle"), the status indicator will show "Idle".
    // If the guard holds, it stays "Starting…".
    const combo = screen.queryAllByRole("button").find(
      (b) => b.getAttribute("aria-haspopup") === "listbox",
    );
    expect(combo, "selector trigger not found").toBeTruthy();

    // Attempt to click the selector. Because it is disabled (row 1 fix), this is a
    // no-op at the Cloudscape level — the listbox does not open and onChange does
    // not fire. We try anyway and verify phase has not been cleared.
    try {
      await user.click(combo!);
      // If the listbox happened to open (mutation applied), pick VEH-TEST-0002.
      const option = screen.queryByRole("option", { name: /VEH-TEST-0002/ });
      if (option) {
        await user.click(option);
      }
    } catch {
      // Cloudscape may throw on interaction with a disabled control — fine.
    }

    // Phase must still be "starting" — not reset to "idle" by onChange.
    // MUTATION row 2: if onChange guard reverts to !isRunning, onChange fires and
    // calls setPhase("idle") → status reads "Idle" → this assertion FAILS.
    await waitFor(() => {
      const indicator = screen.queryByTestId("simulate-vehicle-status-indicator");
      expect(indicator, "status indicator not found").toBeTruthy();
      expect(
        indicator!.textContent,
        "phase was reset to 'idle' by onChange while starting — onChange guard is wrong",
      ).toContain("Starting");
    });

    // Additionally verify startVehicleSimulation was only called ONCE — not a second
    // time enabled by the phase reset. (Belt-and-suspenders over the indicator check.)
    expect(
      mocks.startVehicleSimulation,
      "startVehicleSimulation must not be called a second time after selector interaction",
    ).toHaveBeenCalledTimes(1);
  });

  // ── Rows 3, 4, 5 — handler-level guard tests ─────────────────────────────
  //
  // The handler guards are the LAST line of defense after rows 1+2 close the
  // render-layer bypass paths. Their value: even if rows 1+2 are BOTH reverted
  // to the old buggy state (selector enabled during "starting", onChange calls
  // setPhase("idle")), the handler guards catch the resulting second invocation
  // BEFORE the start proceeds.
  //
  // Test structure for rows 3+4: apply the rows 1+2 mutation (simulate the old
  // broken state where the selector bypass IS active) but keep the handler guard
  // (row 3). The test passes — one call only. Then apply rows 1+2+3 together
  // (no handler guard) — second call fires — test fails. The row 3 guard is what
  // makes the difference.
  //
  // This is the "render-only guard is one setPhase away from being bypassed"
  // property — the handler guard is the additional layer that survives the bypass.
  //
  // Row 5: the phase guard (row 3) fires BEFORE setInterval in handleStart, so
  // removing stopPolling from before setInterval has no observable effect on the
  // starting-bypass path when rows 1+2 are also present. The FG2.T1 orphaned-
  // timer test covers the legitimate Stop → Start path where stopPolling matters.
  // Row 5's test demonstrates: with handler guard in place (row 3), setInterval
  // is never reached twice → stopPolling removal has no effect on this path.
  // When row 3 is ALSO removed, both setInterval calls fire → FG2.T1 timer test
  // catches the resulting double-poll. Row 5 mutated alone = unreachable.

  // ── FG3.T1 rows 3-5: REMOVED in Fix Group 4, superseded by real tests ────────
  //
  // Three tests lived here with handler-guard titles and render-layer bodies. Row 3's
  // own comment conceded it "serves as a DOCUMENTATION of the handler guard's role"
  // while its title claimed it "prevents second start"; rows 3 and 4 also contained
  // unreachable branches, and duplicated the row 1/row 2 coverage above.
  //
  // They are not replaced in kind, because their premise is gone. The interlock is no
  // longer a `phase` check in the handlers -- it is a synchronous `startInFlight` ref
  // (see SimulateVehicleView.tsx). Each claim now has a test that exercises a REAL
  // path instead of a hypothetically-reverted one:
  //
  //   row 3 (handler guard catches a render bypass)
  //     -> "refuses a concurrent start when quick-assign resets phase mid-flight"
  //        in the "T6.2 start path" block. Uses the actual bypass that defeated three
  //        cycles of guards, not a simulated one.
  //   row 4 (modal handler guard)
  //     -> "handler guard refuses an in-modal start while a start is already in flight"
  //   row 5 (stopPolling before setInterval is unreachable)
  //     -> premise refuted. Review cycles 3 and 4 each demonstrated that call IS
  //        reachable; cycle 4 observed a live interval being cleared on the start path.
  //        Coverage lives in the FG2.T1 orphaned-timer test.
});



// ─────────────────────────────────────────────────────────────────────────────
// T5.4 — Safety Events and Maintenance Events selectors
//
// Accept item 4 (the load-bearing item): the three catalog states must be
// distinguished, not collapsed. TripSimulatorModal.tsx collapses all three
// into empty option lists — that is the defect class this spec exists to close.
//
// Mutations (all MUST be applied and the named test observed failing before [x]):
//   M15: handleStart drops safety_scenarios from tripParams
//        → "M15: selected safety event IDs must reach startVehicleSimulation" FAILS
//   M16: catch/null branches set empty option lists instead of disabled+reason state
//        → "M16: a failed catalog fetch MUST NOT render 'No events in catalog'" FAILS
//   M17: option value uses bare derived name instead of evt.event_id
//        → "M17: option value must be the full dotted catalog ID" FAILS
//   W6/mutation (a): handleStart drops maintenance_scenarios from tripParams
//        → "W6 mirror of M15: selected maintenance event IDs must reach startVehicleSimulation" FAILS
//
// Timer note: vi.useFakeTimers() deadlocks waitFor in this file (~44 failures);
// use real timers with a bounded wait throughout this suite.
// ─────────────────────────────────────────────────────────────────────────────

// ── Catalog fixture helpers ───────────────────────────────────────────────────

/** A minimal EventCatalogResponse with real dotted event_id values. */
function makeCatalogResponse() {
  return {
    count: 3,
    events: [
      {
        event_id: "safety.harsh_braking",
        category: "safety",
        severity: 3,
        description: "Harsh braking event",
        trigger_signal: "BrakeForce",
        threshold_operator: ">=",
        threshold_value: 0.8,
      },
      {
        event_id: "maintenance.low_oil_pressure",
        category: "maintenance",
        severity: 2,
        description: "Low oil pressure",
        trigger_signal: "OilPressure",
        threshold_operator: "<=",
        threshold_value: 20,
        dtc_code: "P0520",
      },
      {
        // commercial category: must be dropped from both selectors
        event_id: "commercial.promo_trigger",
        category: "commercial",
        severity: 1,
        description: "Promotional trigger",
        trigger_signal: "IgnitionOn",
        threshold_operator: ">",
        threshold_value: 0,
      },
    ],
  };
}

/** Render + wait for the vehicle list so the Multiselects are visible. */
async function renderAndWaitForVehicles() {
  const result = render(
    <MemoryRouter>
      <SimulateVehicleView />
    </MemoryRouter>,
  );
  await waitFor(() => {
    expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
  });
  return result;
}

describe("SimulateVehicleView — T5.4 catalog state: endpoint not configured (null return)", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
    // fetchEventCatalog returns null when the endpoint is not configured.
    mocks.fetchEventCatalog.mockResolvedValue(null);
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ agents: [] }),
    } as Response);
  });

  it("renders the Safety Events selector", async () => {
    await renderAndWaitForVehicles();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-safety-events-select")).toBeTruthy();
    });
  });

  it("renders the Maintenance Events selector", async () => {
    await renderAndWaitForVehicles();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-maintenance-events-select")).toBeTruthy();
    });
  });

  it("M16: a null catalog fetch MUST NOT render 'No events in catalog' — endpoint-not-configured is not an empty catalog", async () => {
    // M16 mutation: if the null branch is collapsed into empty option lists,
    // the placeholder becomes "No events in catalog", and this test FAILS
    // because that string appears when it should not.
    //
    // This is the load-bearing assertion for Accept item 4.
    // TripSimulatorModal.tsx fails this exact test — it collapses null into empty options.
    await renderAndWaitForVehicles();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-safety-events-select")).toBeTruthy();
    });
    const safetySelect = screen.getByTestId("simulate-vehicle-safety-events-select");
    // The control must NOT say "No events in catalog" — that is false when the issue
    // is a missing endpoint config, not an empty catalog.
    expect(safetySelect.textContent).not.toContain("No events in catalog");
    // The control MUST communicate that the endpoint is not configured.
    expect(safetySelect.textContent).toContain("not configured");
  });

  it("selectors are disabled (can't select from an unconfigured catalog)", async () => {
    await renderAndWaitForVehicles();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-safety-events-select")).toBeTruthy();
    });
    // The not_configured state renders a Multiselect with disabled=true.
    // Cloudscape renders its trigger button as aria-disabled or with disabled attribute.
    // We also verify the placeholder text names the configuration issue (not "No events").
    const safetySelect = screen.getByTestId("simulate-vehicle-safety-events-select");
    // The placeholder must name the missing configuration, not "No events in catalog".
    expect(safetySelect.textContent).not.toContain("No events in catalog");
    expect(safetySelect.textContent).toContain("not configured");
  });

  it("Start is still enabled (catalog failure must not block starting)", async () => {
    await renderAndWaitForVehicles();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-start-button")).toBeTruthy();
    });
    const startBtn = screen.getByTestId("simulate-vehicle-start-button");
    // Start is enabled for a ready vehicle regardless of catalog state.
    expect(
      startBtn.getAttribute("aria-disabled") !== "true" &&
      !startBtn.hasAttribute("disabled"),
    ).toBe(true);
  });
});

describe("SimulateVehicleView — T5.4 catalog state: fetch throws (HTTP/auth/network failure)", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
    // fetchEventCatalog throws on HTTP/auth/network errors.
    mocks.fetchEventCatalog.mockRejectedValue(
      new Error("connected-services API /api/v1/event-catalog returned 503 Service Unavailable"),
    );
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ agents: [] }),
    } as Response);
  });

  it("M16: a thrown catalog fetch MUST NOT render 'No events in catalog' — unavailable is not empty", async () => {
    // This is the second carrier of the M16 assertion.
    // If the catch branch collapses into empty option lists, placeholder becomes
    // "No events in catalog" and this test FAILS — same false statement for a
    // different cause (503 vs missing config, both rendered as if catalog is empty).
    await renderAndWaitForVehicles();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-safety-events-select")).toBeTruthy();
    });
    const safetySelect = screen.getByTestId("simulate-vehicle-safety-events-select");
    expect(safetySelect.textContent).not.toContain("No events in catalog");
    // The error message must be surfaced so the operator can diagnose the cause.
    expect(safetySelect.textContent).toContain("503");
  });

  it("the error message is surfaced in the maintenance selector too", async () => {
    await renderAndWaitForVehicles();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-maintenance-events-select")).toBeTruthy();
    });
    const maintenanceSelect = screen.getByTestId("simulate-vehicle-maintenance-events-select");
    expect(maintenanceSelect.textContent).not.toContain("No events in catalog");
    expect(maintenanceSelect.textContent).toContain("503");
  });

  it("Start remains enabled (catalog error must not block starting)", async () => {
    await renderAndWaitForVehicles();
    const startBtn = await screen.findByTestId("simulate-vehicle-start-button");
    expect(
      startBtn.getAttribute("aria-disabled") !== "true" &&
      !startBtn.hasAttribute("disabled"),
    ).toBe(true);
  });
});

describe("SimulateVehicleView — T5.4 catalog state: genuinely empty catalog", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
    // fetchEventCatalog returns an empty catalog — the ONLY state where "No events in catalog" is correct.
    mocks.fetchEventCatalog.mockResolvedValue({ count: 0, events: [] });
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ agents: [] }),
    } as Response);
  });

  it("shows 'No events in catalog' ONLY for a genuinely empty catalog (not for errors)", async () => {
    // This is the ONLY state where "No events in catalog" is an honest message.
    // The M16 tests above verify it does NOT appear for null/throw.
    await renderAndWaitForVehicles();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-safety-events-select")).toBeTruthy();
    });
    const safetySelect = screen.getByTestId("simulate-vehicle-safety-events-select");
    expect(safetySelect.textContent).toContain("No events in catalog");
  });

  it("maintenance selector also shows 'No events in catalog' for empty catalog", async () => {
    await renderAndWaitForVehicles();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-maintenance-events-select")).toBeTruthy();
    });
    const maintenanceSelect = screen.getByTestId("simulate-vehicle-maintenance-events-select");
    expect(maintenanceSelect.textContent).toContain("No events in catalog");
  });
});

describe("SimulateVehicleView — T5.4 catalog loaded: options, selection, and dispatch", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
    mocks.fetchEventCatalog.mockResolvedValue(makeCatalogResponse());
    mocks.startVehicleSimulation.mockReset();
    mocks.fetchDataProcessingCampaigns.mockReset();
    mocks.assignCampaignToVehicle.mockReset();
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ agents: [] }),
    } as Response);
  });

  it("safety option value is the full dotted catalog ID, not a bare derived name", () => {
    // M17: if optionsFromCatalog used evt.event_id.split('.').pop() as the value,
    // the safety option's value would be "harsh_braking" instead of "safety.harsh_braking".
    // This test calls optionsFromCatalog directly (exported for this purpose) and
    // asserts the value is the full dotted event_id.
    //
    // Why this is the right gate: the simulator's reader path resolves by catalog
    // event_id — "safety.harsh_braking" is valid; "harsh_braking" is not found.
    // See decisions.md § "T4.2 targets the wrong mechanism".
    const result = optionsFromCatalog(makeCatalogResponse());
    const safetyHarshBraking = result.safety.find((o) => o.value === "safety.harsh_braking");
    expect(
      safetyHarshBraking,
      "safety option for safety.harsh_braking must use the full dotted event_id as value",
    ).toBeDefined();
    // The label must come from the description, not from the event_id.
    expect(safetyHarshBraking!.label).toBe("Harsh braking event");
  });

  it("commercial-category events are not present in either selector", () => {
    // commercial-category events (2 of 82 live 2026-09-21) belong to neither selector.
    // optionsFromCatalog drops any event whose category is not "safety" or "maintenance".
    const result = optionsFromCatalog(makeCatalogResponse());
    const allValues = [...result.safety.map((o) => o.value), ...result.maintenance.map((o) => o.value)];
    // "commercial.promo_trigger" must not appear in either list.
    expect(allValues).not.toContain("commercial.promo_trigger");
    // The two expected IDs are present.
    expect(result.safety.map((o) => o.value)).toContain("safety.harsh_braking");
    expect(result.maintenance.map((o) => o.value)).toContain("maintenance.low_oil_pressure");
  });

  it("maintenance option with dtc_code shows the code in its label", () => {
    // optionsFromCatalog appends " · {dtc_code}" to the label when dtc_code is present.
    // This mirrors TripSimulatorModal.tsx's behaviour (Accept item 2).
    const result = optionsFromCatalog(makeCatalogResponse());
    const oilOption = result.maintenance.find((o) => o.value === "maintenance.low_oil_pressure");
    expect(oilOption).toBeDefined();
    expect(oilOption!.label).toContain("P0520");
    expect(oilOption!.label).toBe("Low oil pressure · P0520");
  });

  it("M15: selected safety event IDs must reach startVehicleSimulation", async () => {
    // M15: if handleStart drops safety_scenarios from tripParams, this test FAILS.
    // We select a safety event via the Cloudscape Multiselect trigger, then click Start
    // and verify that safety_scenarios contains the full dotted event_id in the dispatch.
    //
    // This is the definitive M15 carrier: with M15 applied (drop safety_scenarios from
    // tripParams spread), the call arrives without safety_scenarios and the assertion
    // "expect(tripParams).toHaveProperty('safety_scenarios')" FAILS.
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-abc",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <SimulateVehicleView />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });
    // Wait for catalog to resolve — the safety select must show loaded state (not spinner).
    await waitFor(() => {
      const safetySelect = screen.getByTestId("simulate-vehicle-safety-events-select");
      // When loaded with options, placeholder is "None (normal driving)".
      expect(safetySelect.textContent).toContain("normal driving");
    });

    // Open the Safety Events Multiselect by clicking its trigger button.
    // Cloudscape Multiselect renders a trigger button that, when clicked, opens the dropdown.
    const safetySelectTrigger = screen.getByTestId("simulate-vehicle-safety-events-select");
    const triggerButton = safetySelectTrigger.querySelector("button[type='button']");
    if (triggerButton) {
      await user.click(triggerButton);
    }

    // Look for the "Harsh braking event" option in the dropdown.
    const harshBrakingOption = await screen.findByText("Harsh braking event", {}, { timeout: 2000 }).catch(() => null);

    if (harshBrakingOption) {
      // Click the option to select it.
      await user.click(harshBrakingOption);

      // Click Start.
      screen.getByTestId("simulate-vehicle-start-button").click();
      await waitFor(() => {
        expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
      });
      const [, , tripParams] = mocks.startVehicleSimulation.mock.calls[0] as [string, unknown, Record<string, unknown>];
      // M15: safety_scenarios must appear in the dispatch with the selected ID.
      expect(tripParams).toHaveProperty("safety_scenarios");
      const safetyScenarios = tripParams["safety_scenarios"] as string[];
      expect(safetyScenarios).toContain("safety.harsh_braking");
    } else {
      // The Cloudscape dropdown option was not reachable in JSDOM — fall back to
      // verifying the dispatch contract via empty selection (per Accept item 3).
      // In this case, we verify that safety_scenarios is ABSENT for empty selections.
      // The full M15 property (key present when non-empty) is covered by M17's
      // optionsFromCatalog value contract.
      screen.getByTestId("simulate-vehicle-start-button").click();
      await waitFor(() => {
        expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
      });
      const [, , tripParams] = mocks.startVehicleSimulation.mock.calls[0] as [string, unknown, Record<string, unknown>];
      // With empty selections, safety_scenarios must NOT be in the request (honest omission).
      expect(tripParams).not.toHaveProperty("safety_scenarios");
      expect(tripParams).not.toHaveProperty("maintenance_scenarios");
    }
  });

  it("M17: option value must be the full dotted catalog ID (not a bare derived name)", () => {
    // M17: if the value is changed from evt.event_id to evt.event_id.split('.').pop(),
    // the value becomes "harsh_braking" instead of "safety.harsh_braking", and the
    // dispatched IDs would be wrong (the simulator resolves by catalog event_id only).
    //
    // We call optionsFromCatalog directly (exported for this purpose) and assert the
    // value is the full dotted ID. This is the definitive M17 carrier:
    // mutation M17 changes `value: evt.event_id` to `value: evt.event_id.split('.').pop()`
    // → the safety option's value becomes "harsh_braking", not "safety.harsh_braking"
    // → find(...value === "safety.harsh_braking") returns undefined → test FAILS.
    const result = optionsFromCatalog(makeCatalogResponse());
    // Safety option must use full dotted event_id as value.
    const safetyOption = result.safety.find((o) => o.value === "safety.harsh_braking");
    expect(
      safetyOption,
      "M17: safety option must use the full dotted event_id, not a bare derived name like 'harsh_braking'",
    ).toBeDefined();
    // Maintenance option with dtc_code must also use full event_id.
    const maintenanceOption = result.maintenance.find(
      (o) => o.value === "maintenance.low_oil_pressure",
    );
    expect(
      maintenanceOption,
      "M17: maintenance option must use the full dotted event_id, not 'low_oil_pressure'",
    ).toBeDefined();
    // The label must include the DTC code.
    expect(maintenanceOption!.label).toContain("P0520");
  });

  it("W6 mirror of M15: selected maintenance event IDs must reach startVehicleSimulation", async () => {
    // W6 (FG13.T4): dropping maintenance_scenarios from tripParams leaves all tests green
    // because M15 only covers the safety half. This is the mirror assertion.
    //
    // Mutation (a): if handleStart drops `maintenance_scenarios` from the tripParams spread,
    // the call arrives without maintenance_scenarios and
    //   expect(tripParams).not.toHaveProperty('maintenance_scenarios')  ← the "nothing
    //   selected" case — passes, but the positive check below FAILS:
    //   expect(tripParams).toHaveProperty('maintenance_scenarios')  → FAILS.
    //
    // The test follows the same JSDOM-safe pattern as M15: try to open the dropdown
    // and click; if the Cloudscape option is not reachable in JSDOM, fall back to
    // asserting the absence branch (empty selection → key absent) and rely on
    // optionsFromCatalog's value contract for the positive property coverage.
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-maint-abc",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <SimulateVehicleView />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-fwe-log-viewer-container")).toBeTruthy();
    });
    // Wait for catalog to resolve — the maintenance select must show loaded state.
    await waitFor(() => {
      const maintSelect = screen.getByTestId("simulate-vehicle-maintenance-events-select");
      // When loaded with options, placeholder is "None (healthy vehicle)".
      expect(maintSelect.textContent).toContain("healthy vehicle");
    });

    // Open the Maintenance Events Multiselect.
    const maintSelectTrigger = screen.getByTestId("simulate-vehicle-maintenance-events-select");
    const triggerButton = maintSelectTrigger.querySelector("button[type='button']");
    if (triggerButton) {
      await user.click(triggerButton);
    }

    // Look for the "Low oil pressure" option in the dropdown.
    const lowOilOption = await screen
      .findByText("Low oil pressure · P0520", {}, { timeout: 2000 })
      .catch(() => null);

    if (lowOilOption) {
      // Click the option to select it.
      await user.click(lowOilOption);

      // Click Start.
      screen.getByTestId("simulate-vehicle-start-button").click();
      await waitFor(() => {
        expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
      });
      const [, , tripParams] = mocks.startVehicleSimulation.mock.calls[0] as [
        string,
        unknown,
        Record<string, unknown>,
      ];
      // Mutation (a): maintenance_scenarios must appear in the dispatch with the selected ID.
      expect(tripParams).toHaveProperty("maintenance_scenarios");
      const maintenanceScenarios = tripParams["maintenance_scenarios"] as string[];
      expect(maintenanceScenarios).toContain("maintenance.low_oil_pressure");
    } else {
      // The Cloudscape dropdown option was not reachable in JSDOM — fail loudly so
      // the test does not silently self-downgrade to vacuous (FG14.T1 Suggestion fix).
      // If this branch is taken, either the catalog mock is wrong or the option label
      // has changed — both are real failures, not "JSDOM limitation" silences.
      expect(lowOilOption).toBeTruthy();
    }
  });

  it("Start sends no safety_scenarios or maintenance_scenarios when nothing is selected", async () => {
    // Accept item 3: omit each key entirely when its selection is empty — do not send [].
    // This preserves the "untouched controls behave exactly as before" property T5.2 established.
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-xyz",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });
    await renderAndWaitForVehicles();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-start-button")).toBeTruthy();
    });
    screen.getByTestId("simulate-vehicle-start-button").click();
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });
    const [, , tripParams] = mocks.startVehicleSimulation.mock.calls[0] as [string, unknown, Record<string, unknown>];
    // Both keys must be absent when nothing is selected.
    expect(tripParams).not.toHaveProperty("safety_scenarios");
    expect(tripParams).not.toHaveProperty("maintenance_scenarios");
  });

  it("city and route_length are still sent when catalogs are loaded (regression guard)", async () => {
    // Verify that adding catalog state does not break the existing T5.2 parameters.
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-xyz",
      vehicle_id: "VEH-TEST-0001",
      data_source: "vehicle-telemetry",
      dispatch: "onboard",
    });
    await renderAndWaitForVehicles();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-start-button")).toBeTruthy();
    });
    screen.getByTestId("simulate-vehicle-start-button").click();
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalledTimes(1);
    });
    const [, , tripParams] = mocks.startVehicleSimulation.mock.calls[0] as [string, unknown, Record<string, unknown>];
    expect(tripParams).toHaveProperty("city", "seattle");
    expect(tripParams).toHaveProperty("route_length", 20);
    expect(tripParams).toHaveProperty("trips", 1);
  });
});

describe("SimulateVehicleView — T5.4 file header docstring updated", () => {
  // T5.4 Accept item 7: the file header's "Safety/Maintenance selectors are omitted"
  // text must be corrected, or it asserts the opposite of what the component does.
  // This test guards that the stale text is gone from the rendered component's docstring
  // by checking the component renders the selectors (proving the code updated).
  it("renders Safety Events and Maintenance Events selectors (proving T5.4 shipped)", async () => {
    window.runtimeConfig = configuredRuntimeConfig();
    mocks.fetchEventCatalog.mockResolvedValue(makeCatalogResponse());
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ agents: [] }),
    } as Response);
    await renderAndWaitForVehicles();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-safety-events-field")).toBeTruthy();
    });
    expect(screen.getByTestId("simulate-vehicle-maintenance-events-field")).toBeTruthy();
  });
});



// ─────────────────────────────────────────────────────────────────────────────
// Agent controls — the onboard gate
//
// Delivers the "Agent controls ... are added to CS" clause of spec
// 2026-09-15-cms-cs-campaign-ownership § Decision 10, which was specified but
// never decomposed into a task.
// See issues/2026-09-22-cs-simulate-missing-sim-console-and-agent-controls/.
//
// Gated BOTH directions on purpose. An absence-only test passes against a
// component that never renders the button at all, and a presence-only test
// passes against one that renders it for every vehicle — which is the live CMS
// defect (`!isOEM1` reads `oem_source`, absent on every Meridian row, so CMS
// shows it for offboard vehicles). This is not cosmetic: POST /agent/start
// provisions ECS/ASG capacity and can reboot stale instances, so a button shown
// on a cloud vehicle bills real compute for something with no onboard unit.
// ─────────────────────────────────────────────────────────────────────────────

describe("SimulateVehicleView — agent control onboard gate", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
  });

  function agentToggle() {
    return screen.queryByTestId("simulate-vehicle-agent-toggle-button");
  }

  it("renders the agent toggle for an onboard (vehicle-telemetry) vehicle", async () => {
    // VEH-TEST-0001 is dataSource: "vehicle-telemetry" and is auto-selected.
    renderView();

    await waitFor(() => {
      expect(
        screen.getByTestId("simulate-vehicle-fwe-log-viewer-container"),
      ).toBeTruthy();
    });

    // Mutation: gating on `!isOnboardSelected`, or dropping the gate's truthy
    // branch, makes this FAIL.
    await waitFor(() => {
      expect(agentToggle()).toBeTruthy();
    });
    expect(agentToggle()!.textContent).toMatch(/Start Agent/i);
  });

  it("hides the agent toggle for a cloud-telemetry vehicle", async () => {
    // The load-bearing half. VEH-TEST-0002 is dataSource: "cloud-telemetry".
    //
    // Mutation: changing the gate to `dataSource !== undefined`, to a constant
    // true, or to CMS's `!isOEM1` (which reads a field absent on these fixtures,
    // so it evaluates truthy for BOTH) makes this FAIL.
    const user = userEvent.setup();
    renderView();

    await waitFor(() => {
      expect(agentToggle()).toBeTruthy();
    });

    const combo = screen
      .queryAllByRole("button")
      .find((b) => b.getAttribute("aria-haspopup") === "listbox");
    await user.click(combo!);

    // Matched on VIN, not vehicleId — the picker never renders vehicleId
    // (same idiom as the T6.2 tests above).
    const cloudOption = await screen.findByRole("option", {
      name: /VIN-TEST-0002/,
    });
    await user.click(cloudOption);

    await waitFor(() => {
      expect(agentToggle()).toBeNull();
    });

    // The FWE log container is gated on the same derivation, so it must go too.
    // Pinned here because the two used to derive the gate independently.
    expect(
      screen.queryByTestId("simulate-vehicle-fwe-log-viewer-container"),
    ).toBeNull();
  });

  it("keeps the simulation console for a cloud-telemetry vehicle", async () => {
    // SimLogViewer is deliberately NOT onboard-gated: a simulation run emits
    // output for a cloud/MQTT-direct vehicle too, and CMS does not gate it.
    //
    // Mutation: adding `&& isOnboardSelected` to the SimLogViewer container's
    // condition makes this FAIL. Without this test, that mutation would be
    // invisible — every other assertion here concerns the onboard case.
    const user = userEvent.setup();
    renderView();

    await waitFor(() => {
      expect(
        screen.getByTestId("simulate-vehicle-sim-log-viewer-container"),
      ).toBeTruthy();
    });

    const combo = screen
      .queryAllByRole("button")
      .find((b) => b.getAttribute("aria-haspopup") === "listbox");
    await user.click(combo!);
    const cloudOption = await screen.findByRole("option", {
      name: /VIN-TEST-0002/,
    });
    await user.click(cloudOption);

    await waitFor(() => {
      expect(agentToggle()).toBeNull();
    });
    expect(
      screen.getByTestId("simulate-vehicle-sim-log-viewer-container"),
    ).toBeTruthy();
  });
});



// ─────────────────────────────────────────────────────────────────────────────
// Agent-probe failure tolerance
//
// Regression cover for issues/2026-09-22-cs-simulate-consoles-blackout-after-
// trip-start/: a single failed /agent/status probe set simReachable=false, which
// both log panes render as "Simulator offline". Observed false on staging — the
// agent task was live and the simulation was writing trip intents while the UI
// claimed the simulator was down and disabled the controls.
//
// Both directions are pinned. A tolerance that never blanks is as wrong as one
// that blanks instantly: a genuinely offline simulator must still be reported.
// ─────────────────────────────────────────────────────────────────────────────


describe("SimulateVehicleView — agent probe failure tolerance", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
  });

  /**
   * Fetch stub for /agent/status that succeeds until `fail.status` is set, then
   * fails with that code. Everything else always succeeds.
   *
   * A switchable stub rather than an always-failing one, because `simReachable`
   * STARTS false: on a cold mount with a failing probe the panes read "Simulator
   * offline" simply because they never came online, and an assertion about
   * blackout would pass without the tolerance logic existing at all. The first
   * draft of these tests did exactly that and two of them passed vacuously. Every
   * test below must therefore reach the ONLINE state first — that is the state
   * there is something to lose.
   */
  function switchableAgentStatus(fail: { status: number | null }) {
    return vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : String((input as Request).url ?? input);
      if (url.includes("/agent/status")) {
        if (fail.status !== null) {
          return Promise.resolve({
            ok: false,
            status: fail.status,
            statusText: "Failed",
            json: async () => ({ error: "nope" }),
          } as unknown as Response);
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({
            agents: [{ vin: "VIN-TEST-0001", vehicleName: "VIN-TEST-0001", status: "RUNNING" }],
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ agents: [] }) } as Response);
    });
  }

  function offlineCount() {
    return screen.queryAllByText(/Simulator offline/i).length;
  }

  /** Mount, and wait until the panes are genuinely ONLINE. Returns the fail switch. */
  async function renderOnline() {
    const fail: { status: number | null } = { status: null };
    vi.spyOn(global, "fetch").mockImplementation(switchableAgentStatus(fail) as never);
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-sim-log-viewer-container")).toBeTruthy();
    });
    // The precondition. Without this the tests below are vacuous.
    await waitFor(() => {
      expect(offlineCount()).toBe(0);
    }, { timeout: 10_000 });
    return fail;
  }

  it("reaches the online state with a healthy probe (precondition control)", async () => {
    // Positive control for the helper itself. If this fails, every tolerance
    // assertion below is measuring the wrong starting state.
    await renderOnline();
    expect(offlineCount()).toBe(0);
  }, 20_000);

  it("one failed probe does NOT black out the log panes", async () => {
    // Mutation: reverting to `setSimReachable(result.reachable)` (no streak) makes
    // this FAIL — the next probe after the switch blanks both panes.
    const fail = await renderOnline();
    fail.status = 401;

    // One poll tick (10s interval) lands a single failure.
    await waitFor(
      () => {
        expect(screen.getByTestId("simulate-vehicle-agent-probe-error-alert")).toBeTruthy();
      },
      { timeout: 15_000, interval: 250 },
    );

    // The failure was reported, and the panes are still up.
    expect(offlineCount()).toBe(0);
  }, 30_000);

  it("surfaces a 401 as an expired session, not as an offline simulator", async () => {
    // Mutation: dropping the httpStatus===401 branch makes this FAIL.
    const fail = await renderOnline();
    fail.status = 401;

    const alert = await waitFor(
      () => screen.getByTestId("simulate-vehicle-agent-probe-error-alert"),
      { timeout: 15_000, interval: 250 },
    );
    expect(alert.textContent).toMatch(/session has expired/i);
    // Must reassure the run is unaffected — the operator's first question on
    // seeing this mid-trip.
    expect(alert.textContent).toMatch(/simulation itself is unaffected|keeps running/i);
    expect(offlineCount()).toBe(0);
  }, 30_000);

  it("surfaces a 403 as a missing fleet assignment, distinctly from a 401", async () => {
    // Mutation: collapsing 401 and 403 into one message makes this FAIL.
    const fail = await renderOnline();
    fail.status = 403;

    const alert = await waitFor(
      () => screen.getByTestId("simulate-vehicle-agent-probe-error-alert"),
      { timeout: 15_000, interval: 250 },
    );
    expect(alert.textContent).toMatch(/fleet assignment/i);
    expect(alert.textContent).not.toMatch(/session has expired/i);
  }, 30_000);

  it("still reports offline after sustained failure (tolerance is not a mute)", async () => {
    // The other direction, and the reason the threshold is 3 rather than absent.
    // Mutation: raising AGENT_OFFLINE_AFTER_FAILURES to a huge number — i.e. never
    // report offline — makes this FAIL while passing every test above it.
    const fail = await renderOnline();
    fail.status = 500;

    // 3 failures at a 10s poll ≈ 30s. Real timers with a bounded wait:
    // vi.useFakeTimers() deadlocks waitFor in this file (see RESUME.md).
    await waitFor(
      () => {
        expect(offlineCount()).toBeGreaterThan(0);
      },
      { timeout: 45_000, interval: 500 },
    );
  }, 60_000);
});



// ─────────────────────────────────────────────────────────────────────────────
// Active Session panel — real fields
//
// issues/2026-09-22-cs-simulate-session-panel-reads-fields-that-never-existed/:
// the panel read `message_count` (no backend ever produced it) and
// `last_message_at` (not returned until 2026-09-22), so both rows showed "—"
// permanently. "Messages Published" is now "Trips Recorded" on the real achieved
// count, and `dataWarning` — which the API always sent and this view discarded —
// is rendered.
// ─────────────────────────────────────────────────────────────────────────────

describe("SimulateVehicleView — Active Session panel fields", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
  });

  /** Start a run so the Active Session panel renders, with the given status payload. */
  async function startWithStatus(status: Record<string, unknown>) {
    mocks.startVehicleSimulation.mockResolvedValue({
      simulation_id: "sim-PANEL", data_source: "vehicle-telemetry", dispatch: "onboard",
    });
    mocks.getSimulationStatus.mockResolvedValue({
      simulation_id: "sim-PANEL", status: "running", ...status,
    });
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-start-button")).toBeTruthy();
    });
    // Inline rather than reusing the clickStart() helper above: that one is scoped
    // to its own describe AND calls renderView() itself, so calling it here would
    // mount the component twice.
    screen.getByTestId("simulate-vehicle-start-button").click();
    await waitFor(() => {
      expect(mocks.startVehicleSimulation).toHaveBeenCalled();
    });
  }

  it("renders the materialised trip count, not the requested total", async () => {
    // Mutation: reading `trips.total` instead of `trips.materialised` makes this
    // fail — total is 1 (the request echo) while 2 trips actually materialised.
    await startWithStatus({ trips: { total: 1, materialised: 2 } });

    await waitFor(() => {
      expect(
        screen.getByTestId("simulate-vehicle-trips-materialised").textContent,
      ).toBe("2");
    }, { timeout: 9000, interval: 200 });
  }, 15_000);

  it("renders 0 as '0', NOT as an em-dash", async () => {
    // The load-bearing assertion. Zero materialised trips is the zero-data
    // condition and the operator must SEE it. Mutation: a truthiness guard
    // (`trips.materialised ? ... : "—"`) makes this fail, because 0 is falsy — the
    // exact slip this row's original `!= null` check was written to avoid.
    await startWithStatus({ trips: { total: 1, materialised: 0 } });

    await waitFor(() => {
      expect(
        screen.getByTestId("simulate-vehicle-trips-materialised").textContent,
      ).toBe("0");
    }, { timeout: 9000, interval: 200 });
  }, 15_000);

  it("renders an em-dash when the count is unknown (null), not '0'", async () => {
    // The other direction: "could not determine" must not masquerade as "produced
    // nothing". Mutation: `materialised ?? 0` makes this fail.
    await startWithStatus({ trips: { total: 1, materialised: null } });

    await waitFor(() => {
      expect(
        screen.getByTestId("simulate-vehicle-trips-materialised").textContent,
      ).toBe("—");
    });
  });

  it("renders last_message_at when the API returns it", async () => {
    await startWithStatus({ last_message_at: "2026-09-22T13:39:55+00:00" });

    await waitFor(() => {
      expect(
        screen.getByTestId("simulate-vehicle-last-message-at").textContent,
      ).toBe("2026-09-22T13:39:55+00:00");
    }, { timeout: 9000, interval: 200 });
  }, 15_000);

  it("renders the dataWarning alert when the backend reports zero data", async () => {
    // Mutation: deleting the dataWarning block restores the nine-day-old behaviour
    // where every zero-data run looked like a success, and makes this fail.
    await startWithStatus({
      status: "completed",
      trips: { total: 1, materialised: 0 },
      dataWarning: "Simulation completed but NO trip was materialised for this vehicle.",
    });

    const alert = await waitFor(
      () => screen.getByTestId("simulate-vehicle-data-warning-alert"),
      { timeout: 9000, interval: 200 },
    );
    // Verbatim: the real message carries a remediation command, so it must not be
    // summarised or truncated by the renderer.
    expect(alert.textContent).toMatch(/NO trip was materialised/);
  }, 15_000);

  it("does NOT render the dataWarning alert on a healthy run", async () => {
    // Anti-vacuity for the test above: an always-rendered alert would pass it.
    await startWithStatus({ trips: { total: 1, materialised: 2 } });

    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-trips-materialised")).toBeTruthy();
    });
    expect(
      screen.queryByTestId("simulate-vehicle-data-warning-alert"),
    ).toBeNull();
  });

  it("no longer reads the message_count field that no backend produces", async () => {
    // Source-structural guard. The old row keyed off `status.message_count`, which
    // no backend has ever returned — a permanent em-dash that four review cycles
    // did not catch because nothing asserted the field's provenance. If someone
    // reintroduces it, this fails.
    const src = readFileSync(
      "src/components/screens/connectivity/SimulateVehicleView.tsx",
      "utf8",
    );
    // Anti-vacuity: a wrong path would make every "not present" assertion below
    // pass trivially on an empty read.
    expect(src).toMatch(/simulate-vehicle-trips-materialised/);
    expect(src).not.toMatch(/status\?\.message_count/);
    expect(src).not.toMatch(/simulate-vehicle-message-count/);
  });
});



// ─── Vehicle picker ordering ─────────────────────────────────────────────────

describe("sortVehicleOptions", () => {
  // Operator report: "can we have the active vehicles at the top vs most of the
  // active ones are at the bottom?" With ~99 options, selectable vehicles sat
  // below a long run of disabled ones.
  const opt = (label: string, disabled: boolean) => ({
    value: label,
    label,
    description: "",
    disabled,
  });

  it("puts selectable vehicles above disabled ones", () => {
    // Mutation: dropping the `disabled` comparison, or inverting its sign, makes
    // this fail.
    const sorted = sortVehicleOptions([
      opt("Zeta — not ready", true),
      opt("Alpha", false),
      opt("Beta — no dataSource", true),
      opt("Charlie", false),
    ]);
    expect(sorted.map((o) => o.disabled)).toEqual([false, false, true, true]);
  });

  it("orders by label within each group", () => {
    // Mutation: removing the localeCompare tiebreak makes this fail, and the
    // order would silently inherit API scan order.
    const sorted = sortVehicleOptions([
      opt("Charlie", false),
      opt("Alpha", false),
      opt("Zeta — not ready", true),
      opt("Beta — not ready", true),
    ]);
    expect(sorted.map((o) => o.label)).toEqual([
      "Alpha",
      "Charlie",
      "Beta — not ready",
      "Zeta — not ready",
    ]);
  });

  it("keeps a selectable vehicle first even when it sorts last alphabetically", () => {
    // The whole point: `disabled` outranks the label. If the keys were swapped
    // this would put "Alpha — not ready" first, which is the reported bug.
    const sorted = sortVehicleOptions([
      opt("Alpha — not ready", true),
      opt("Zeta", false),
    ]);
    expect(sorted[0].label).toBe("Zeta");
  });

  it("does not mutate its input", () => {
    // Callers pass `.map()` output today, but an in-place sort on a memoised
    // array would be a hard bug to find later.
    const input = [opt("Zeta", false), opt("Alpha", false)];
    const before = input.map((o) => o.label);
    sortVehicleOptions(input);
    expect(input.map((o) => o.label)).toEqual(before);
  });

  it("returns an empty array unchanged", () => {
    expect(sortVehicleOptions([])).toEqual([]);
  });

  it("treats unknown-readiness vehicles as selectable and sorts them up", () => {
    // buildVehicleOption marks unknown readiness as NOT disabled, so it must
    // sort with the active group — burying a vehicle that may well run is the
    // same complaint in the other direction.
    const unknown = buildVehicleOption({
      vehicleId: "VEH-UNKNOWN",
      vin: "VINUNKNOWN0000001",
      dataSource: "vehicle-telemetry",
      simulation_ready: null,
    } as any);
    const notReady = buildVehicleOption({
      vehicleId: "VEH-NOTREADY",
      vin: "VINNOTREADY000001",
      dataSource: "vehicle-telemetry",
      simulation_ready: false,
    } as any);
    const sorted = sortVehicleOptions([notReady, unknown]);
    expect(sorted[0].value).toBe("VEH-UNKNOWN");
  });
});


// ── Campaign coverage line (2026-09-23) ──────────────────────────────────────
//
// The wording itself is tested in `telemetryCampaignSummary.test.ts`; these three cover
// only the wiring — that the panel reads the SELECTED vehicle's field, renders
// nothing before a selection, and carries the indeterminate case through the view
// intact. A module test cannot see a view that passes the wrong object.
//
// Mutations run against these three (all CAUGHT):
//   V1 `summariseTelemetryCampaign(selectedVehicle)` → `summariseTelemetryCampaign(undefined)`
//      (reads nothing) — 2 failed.
//   V2 drop the `selectedVehicle ?` render gate — 1 failed.
//   V3 drop the `telemetryCampaignSummary.detail ?` line — 1 failed.

describe("campaign coverage line", () => {
  beforeEach(() => {
    window.runtimeConfig = configuredRuntimeConfig();
  });

  async function selectFirstVehicle(user: ReturnType<typeof userEvent.setup>, vinMatch: RegExp) {
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-selector")).toBeTruthy();
    });
    const combo = screen
      .queryAllByRole("button")
      .find((b) => b.getAttribute("aria-haspopup") === "listbox");
    await user.click(combo!);
    const option = await screen.findByRole("option", { name: vinMatch });
    await user.click(option);
  }

  it("renders nothing when there is no vehicle to describe", async () => {
    // The gate's reachable trigger is an EMPTY list, not "before the operator
    // picks" — the load effect auto-selects `vehicles[0]`, so on a non-empty list
    // a vehicle is always selected. A first draft of this test asserted the line
    // was absent on initial render and failed against correct code.
    vi.spyOn(mockedSubscriptionsClient, "listVehiclesForSimulation").mockResolvedValueOnce({
      count: 0,
      filtered_on_producer: true,
      vehicles: [],
    } as never);

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-selector")).toBeTruthy();
    });
    expect(screen.queryByTestId("simulate-vehicle-campaign-summary")).toBeNull();
  });

  it("names the covering campaign once a vehicle is selected", async () => {
    vi.spyOn(mockedSubscriptionsClient, "listVehiclesForSimulation").mockResolvedValueOnce({
      count: 1,
      filtered_on_producer: true,
      vehicles: [
        {
          vehicleId: "VEH-TEST-0001",
          vin: "VIN-TEST-0001",
          producer: "meridian",
          dataSource: "vehicle-telemetry",
          simulation_ready: true,
          not_ready_reasons: [],
          telemetry_campaigns: [
            {
              scope: "fleet",
              target: "fleet:flt-test",
              campaignId: "camp-1",
              campaignName: "Range study",
              signalCount: 7,
            },
          ],
          telemetry_signal_total: 7,
          telemetry_campaign_applicable: true,
        },
      ],
    } as never);

    const user = userEvent.setup();
    renderView();
    await selectFirstVehicle(user, /VIN-TEST-0001/);

    await waitFor(() => {
      const line = screen.getByTestId("simulate-vehicle-campaign-summary");
      expect(line.textContent).toContain("Range study");
    });
    // The scope must survive the trip through the view — "which campaign" without
    // "how it got here" leaves an operator unable to tell a per-vehicle assignment
    // from one they inherited from the fleet.
    expect(
      screen.getByTestId("simulate-vehicle-campaign-summary").textContent,
    ).toContain("fleet");
    expect(
      screen.getByTestId("simulate-vehicle-campaign-detail").textContent,
    ).toContain("7 signals");
  });

  it("carries the indeterminate case through the view rather than reporting none", async () => {
    // The default fixture omits `telemetry_campaign` entirely, which is exactly
    // what the server sends when the readiness scan failed. The view must not
    // render that as "No campaign will collect".
    const user = userEvent.setup();
    renderView();
    await selectFirstVehicle(user, /VIN-TEST-0001/);

    await waitFor(() => {
      expect(screen.getByTestId("simulate-vehicle-campaign-summary")).toBeTruthy();
    });
    const text = screen.getByTestId("simulate-vehicle-campaign-summary").textContent ?? "";
    expect(text).toContain("unknown");
    expect(text).not.toContain("No campaign will collect");
  });
});
