// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Scope toggle and "All ADP vehicles" layout — T3.1 of spec
 * .kiro/specs/2026-09-25-cms-fi-adp-wide-lifecycle/tasks.md (§ D1, D3, D5, D7).
 *
 * Covers:
 *   - non-admins never see the toggle and never request scope=adp
 *   - admins get the toggle; ADP requests carry scope=adp and no fleetId
 *   - the scope is derived from the role, so losing admin mid-session drops ADP
 *   - the fleet picker lock is set in ADP scope and released on the way out
 *   - the D5 purchase-price assumption is stated beside the crossover figures
 *   - 503 renders "Rollup not computed yet" with the last computedAt, not an error
 *   - the drill-down is asserted through aria-expanded, not DOM presence
 */

import React from "react";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";

const { mockRole } = vi.hoisted(() => ({
  mockRole: {
    current: {
      isAdmin: true,
      isOperator: false,
      isViewer: false,
      isGuest: false,
      isConnectAgent: false,
      isEngineer: false,
      isDispatcher: false,
      canWrite: true,
      fleetIds: [] as string[],
    },
  },
}));

vi.mock("../../../utils/authFetch", () => ({
  authFetch: vi.fn(),
}));

vi.mock("../../../config/api", () => ({
  getApiEndpoint: () => "https://api.example.com",
  isDemoMode: () => false,
}));

vi.mock("@/auth/useUserRole", () => ({
  useUserRole: () => mockRole.current,
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

import { authFetch } from "../../../utils/authFetch";
import { FleetFilterProvider, useFleetFilter } from "../../fleet-filter/FleetFilter";
import { LifecycleView, ADP_SCOPE_PICKER_LOCK_REASON } from "../LifecycleView";

const mockAuthFetch = vi.mocked(authFetch);
const SESSION_KEY = "cms-fleet-filter-id";

const ADMIN = {
  isAdmin: true,
  isOperator: false,
  isViewer: false,
  isGuest: false,
  isConnectAgent: false,
  isEngineer: false,
  isDispatcher: false,
  canWrite: true,
  fleetIds: [] as string[],
};

const OPERATOR = {
  ...ADMIN,
  isAdmin: false,
  isOperator: true,
  fleetIds: ["fleet-a"],
};

const VIEWER = {
  ...ADMIN,
  isAdmin: false,
  isViewer: true,
  canWrite: false,
};

const ENGINEER = { ...ADMIN, isAdmin: false, isEngineer: true, canWrite: true };
const DISPATCHER = { ...ADMIN, isAdmin: false, isDispatcher: true, canWrite: false };
const GUEST = { ...ADMIN, isAdmin: false, isGuest: true, canWrite: false, fleetIds: ["fleet-a"] };

const CMS_BODY = {
  summary: {
    totalVehicles: 0,
    sellRecommendedCount: 0,
    sellSoonCount: 0,
    healthyCount: 0,
    insufficientDataCount: 0,
    tiresNeedReplacementCount: 0,
    avgMonthsToCrossover: null,
    avgMonthlyDepreciation: 0,
    horizonMonths: 36,
  },
  fleetMonthlyTrend: [],
  rows: [],
  provenance: "simulated",
};

const ADP_BODY = {
  scope: "adp",
  computedAt: "2026-09-26T12:08:00.123456+00:00",
  windowMonths: 36,
  horizonMonths: 36,
  assumptions: { purchasePriceUsd: 60000, straightLineLifeMonths: 120 },
  summary: {
    totalVehicles: 4669034,
    sellRecommendedCount: 5923,
    sellSoonCount: 2942,
    healthyCount: 3673577,
    insufficientDataCount: 986592,
    avgMonthsToCrossover: 26.8,
  },
  monthlyTrend: [
    { yearMonth: "2026-07", avgMaintenance: 210.5, vehicleCount: 1200 },
    { yearMonth: "2026-08", avgMaintenance: 220.25, vehicleCount: 1300 },
  ],
  cohorts: [
    {
      model: "Aurora",
      modelYear: 2021,
      vehicles: 1234,
      sellRecommendedCount: 11,
      sellSoonCount: 22,
      healthyCount: 1000,
      insufficientDataCount: 201,
      avgMonthlyMaintenance: 180.4,
      avgCostPerMile: null,
    },
  ],
  topCrossovers: [
    {
      vin: "MRDN0000000000042",
      model: "Aurora",
      modelYear: 2019,
      monthsUntilCrossover: 2,
      currentMonthlyMaintenance: 480,
      rSquared: 0.81,
      series: [
        { yearMonth: "2026-06", maintenanceCost: 410 },
        { yearMonth: "2026-07", maintenanceCost: 451 },
      ],
    },
  ],
  evidence: { queryExecutionIds: ["q1", "q2", "q3", "q4"] },
  provenance: "simulated",
};

const mkResponse = (status: number, body: unknown) =>
  ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (body === undefined) throw new SyntaxError("no body");
      return body;
    },
  }) as unknown as Response;

/** Route authFetch by URL: scope=adp → adpResponse, anything else → CMS body. */
const routeFetch = (adpResponse: () => Response) => {
  mockAuthFetch.mockImplementation(async (url: RequestInfo | URL) => {
    const u = String(url);
    if (u.includes("scope=adp")) return adpResponse();
    return mkResponse(200, CMS_BODY);
  });
};

const requestedUrls = () => mockAuthFetch.mock.calls.map((c) => String(c[0]));

/** Renders the lock reason so tests can observe the picker lock. */
const LockProbe: React.FC = () => {
  const { pickerLockReason } = useFleetFilter();
  return <span data-testid="lock-probe">{pickerLockReason ?? ""}</span>;
};

const renderView = () =>
  render(
    <MemoryRouter>
      <FleetFilterProvider>
        <LockProbe />
        <LifecycleView />
      </FleetFilterProvider>
    </MemoryRouter>
  );

const toggleButton = (label: string) => {
  const toggle = screen.getByTestId("lifecycle-scope-toggle");
  return within(toggle).getByRole("button", { name: label });
};

const selectAdp = async () => {
  fireEvent.click(toggleButton("All ADP vehicles"));
  await waitFor(() => expect(requestedUrls().some((u) => u.includes("scope=adp"))).toBe(true));
};

beforeEach(() => {
  mockAuthFetch.mockReset();
  sessionStorage.clear();
  mockRole.current = { ...ADMIN };
});

// ---------------------------------------------------------------------------
// Role gate (D1, D7)
// ---------------------------------------------------------------------------

describe("scope toggle role gate", () => {
  it.each([
    ["fleet-operator", OPERATOR],
    ["fleet-viewer", VIEWER],
    ["product-engineer (canWrite, not admin)", ENGINEER],
    ["dispatcher", DISPATCHER],
    ["fleet-guest", GUEST],
  ])("a %s never sees the toggle and never requests scope=adp", async (_name, role) => {
    mockRole.current = { ...role };
    routeFetch(() => mkResponse(200, ADP_BODY));
    renderView();
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled());

    expect(screen.queryByTestId("lifecycle-scope-toggle")).toBeNull();
    expect(screen.queryByText("All ADP vehicles")).toBeNull();
    expect(requestedUrls().every((u) => !u.includes("scope="))).toBe(true);
  });

  it("an admin sees both scope options, CMS fleets selected by default", async () => {
    routeFetch(() => mkResponse(200, ADP_BODY));
    renderView();
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled());

    expect(toggleButton("CMS fleets")).toBeTruthy();
    expect(toggleButton("All ADP vehicles")).toBeTruthy();
    expect(requestedUrls().every((u) => !u.includes("scope=adp"))).toBe(true);
  });

  it("losing the admin role while ADP is shown returns to the CMS view", async () => {
    routeFetch(() => mkResponse(200, ADP_BODY));
    const view = renderView();
    await selectAdp();
    await screen.findByTestId("adp-as-of");

    mockRole.current = { ...OPERATOR };
    view.rerender(
      <MemoryRouter>
        <FleetFilterProvider>
          <LockProbe />
          <LifecycleView />
        </FleetFilterProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.queryByTestId("adp-as-of")).toBeNull());
    expect(screen.queryByTestId("lifecycle-scope-toggle")).toBeNull();
    expect(screen.getByTestId("lock-probe").textContent).toBe("");
  });
});

// ---------------------------------------------------------------------------
// ADP request and the fleet filter (D1, D7)
// ---------------------------------------------------------------------------

describe("ADP scope request and fleet filter", () => {
  it("requests exactly ?scope=adp with no fleetId, even with a fleet selected", async () => {
    sessionStorage.setItem(SESSION_KEY, "fleet-a");
    routeFetch(() => mkResponse(200, ADP_BODY));
    renderView();
    await waitFor(() =>
      expect(requestedUrls().some((u) => u.includes("fleetId=fleet-a"))).toBe(true)
    );

    await selectAdp();

    const adpUrls = requestedUrls().filter((u) => u.includes("scope=adp"));
    expect(adpUrls).toEqual([
      "https://api.example.com/api/v1/fleet-intelligence/lifecycle?scope=adp",
    ]);
  });

  it("locks the fleet picker in ADP scope and releases it back in CMS scope", async () => {
    routeFetch(() => mkResponse(200, ADP_BODY));
    renderView();
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled());
    expect(screen.getByTestId("lock-probe").textContent).toBe("");

    await selectAdp();
    await waitFor(() =>
      expect(screen.getByTestId("lock-probe").textContent).toBe(ADP_SCOPE_PICKER_LOCK_REASON)
    );

    fireEvent.click(toggleButton("CMS fleets"));
    await waitFor(() => expect(screen.getByTestId("lock-probe").textContent).toBe(""));
  });

  it("releases the fleet picker lock when the page unmounts", async () => {
    routeFetch(() => mkResponse(200, ADP_BODY));
    const Harness: React.FC<{ show: boolean }> = ({ show }) => (
      <MemoryRouter>
        <FleetFilterProvider>
          <LockProbe />
          {show && <LifecycleView />}
        </FleetFilterProvider>
      </MemoryRouter>
    );
    const view = render(<Harness show />);
    await selectAdp();
    await waitFor(() =>
      expect(screen.getByTestId("lock-probe").textContent).toBe(ADP_SCOPE_PICKER_LOCK_REASON)
    );

    view.rerender(<Harness show={false} />);
    await waitFor(() => expect(screen.getByTestId("lock-probe").textContent).toBe(""));
  });
});

// ---------------------------------------------------------------------------
// ADP layout (D5, D7)
// ---------------------------------------------------------------------------

describe("ADP layout", () => {
  it("shows as-of, bucket counts, cohorts and top-N from the server payload", async () => {
    routeFetch(() => mkResponse(200, ADP_BODY));
    renderView();
    await selectAdp();

    expect((await screen.findByTestId("adp-as-of")).textContent).toBe(
      "As of 2026-09-26 12:08 UTC"
    );
    const kpi = (id: string) => screen.getByTestId(id).textContent;
    expect(kpi("adp-kpi-total")).toBe("4,669,034");
    expect(kpi("adp-kpi-sell-recommended")).toBe("5,923");
    expect(kpi("adp-kpi-sell-soon")).toBe("2,942");
    expect(kpi("adp-kpi-healthy")).toBe("3,673,577");
    expect(kpi("adp-kpi-insufficient")).toBe("986,592");
    expect(kpi("adp-kpi-avg-months")).toBe("26.8");
    // Each value sits in the item carrying its label (dd after its dt).
    const labelOf = (id: string) =>
      screen.getByTestId(id).closest("dd")!.previousElementSibling!.textContent;
    expect(labelOf("adp-kpi-total")).toBe("Vehicles");
    expect(labelOf("adp-kpi-sell-recommended")).toBe("Sell recommended (≤ 6 mo)");
    expect(labelOf("adp-kpi-sell-soon")).toBe("Sell soon (7–12 mo)");
    expect(labelOf("adp-kpi-healthy")).toBe("Good standing");
    expect(labelOf("adp-kpi-insufficient")).toBe("Not enough history");
    expect(labelOf("adp-kpi-avg-months")).toBe("Avg months to crossover");
    expect(screen.getByTestId("adp-trend").getAttribute("data-point-count")).toBe("2");
    expect(screen.getByTestId("adp-trend").getAttribute("data-threshold")).toBe("500");

    // Cohort row, cells in column order.
    const cohortCells = within(screen.getByText("1,234").closest("tr")!)
      .getAllByRole("cell")
      .map((c) => c.textContent);
    expect(cohortCells).toEqual(["Aurora", "2021", "1,234", "11", "22", "1,000", "201", "$180", "—"]);

    // Top-N row, cells in column order: VIN, model, year, months, maintenance, R², series.
    const topCells = within(screen.getByText("MRDN0000000000042").closest("tr")!)
      .getAllByRole("cell")
      .map((c) => c.textContent);
    expect(topCells.slice(0, 6)).toEqual(["MRDN0000000000042", "Aurora", "2019", "2", "$480", "0.81"]);
  });

  it("states the purchase-price assumption beside the summary and the top-N list", async () => {
    routeFetch(() => mkResponse(200, ADP_BODY));
    renderView();
    await selectAdp();

    const expected =
      "Crossover assumes a $60,000 purchase price, depreciated straight-line over 120 months ($500/mo). ADP has no vehicle price.";
    expect((await screen.findByTestId("adp-assumption-summary")).textContent).toBe(expected);
    expect(screen.getByTestId("adp-assumption-top-n").textContent).toBe(expected);
  });

  it("builds the assumption from the response, not a constant", async () => {
    routeFetch(() =>
      mkResponse(200, {
        ...ADP_BODY,
        // Price, life and the monthly figure all differ from the 60,000 / 120 /
        // $500 defaults, so hardcoding any one of them fails.
        assumptions: { purchasePriceUsd: 54000, straightLineLifeMonths: 90 },
      })
    );
    renderView();
    await selectAdp();

    expect((await screen.findByTestId("adp-assumption-summary")).textContent).toContain(
      "$54,000 purchase price, depreciated straight-line over 90 months ($600/mo)"
    );
    expect(screen.getByTestId("adp-trend").getAttribute("data-threshold")).toBe("600");
  });

  it("drill-down: aria-expanded follows the View series button and shows the cached series", async () => {
    routeFetch(() => mkResponse(200, ADP_BODY));
    renderView();
    await selectAdp();

    const button = await screen.findByRole("button", {
      name: "Show maintenance series for MRDN0000000000042",
    });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    const sectionHeader = () =>
      screen.getByRole("button", { name: /^Maintenance series/ });
    expect(sectionHeader().getAttribute("aria-expanded")).toBe("false");

    const scrolled: Element[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this);
    } as typeof Element.prototype.scrollIntoView;
    try {
      fireEvent.click(button);
      await waitFor(() => expect(button.getAttribute("aria-expanded")).toBe("true"));
      // The section is brought into view (it sits below up to 100 top-N rows).
      await waitFor(() => expect(scrolled).toContain(screen.getByTestId("adp-drilldown")));
    } finally {
      Element.prototype.scrollIntoView = original;
    }
    expect(sectionHeader().getAttribute("aria-expanded")).toBe("true");
    expect(within(screen.getByTestId("adp-drilldown-series")).getByText("2026-07")).toBeTruthy();
    expect(within(screen.getByTestId("adp-drilldown-series")).getByText("$451")).toBeTruthy();
    expect(screen.getByTestId("adp-assumption-drilldown").textContent).toContain(
      "$60,000 purchase price"
    );

    fireEvent.click(button);
    await waitFor(() => expect(button.getAttribute("aria-expanded")).toBe("false"));
    expect(sectionHeader().getAttribute("aria-expanded")).toBe("false");
  });
});

// ---------------------------------------------------------------------------
// 503: rollup not computed yet (D3, D7)
// ---------------------------------------------------------------------------

describe("ADP 503", () => {
  it("renders 'Rollup not computed yet' with the last computedAt, not an error banner", async () => {
    routeFetch(() =>
      mkResponse(503, { error: "rollup not yet computed", computedAt: "2026-09-24T09:30:00+00:00" })
    );
    renderView();
    await selectAdp();

    const panel = await screen.findByTestId("adp-rollup-not-computed");
    expect(within(panel).getByText("Rollup not computed yet")).toBeTruthy();
    expect(panel.textContent).toContain("Last computed 2026-09-24 09:30 UTC");
    expect(screen.queryByText(/Failed to load/)).toBeNull();
  });

  it("renders the not-computed state when the Lambda's 503 carries no computedAt", async () => {
    routeFetch(() => mkResponse(503, { error: "rollup not yet computed" }));
    renderView();
    await selectAdp();

    const panel = await screen.findByTestId("adp-rollup-not-computed");
    expect(within(panel).getByText("Rollup not computed yet")).toBeTruthy();
    expect(panel.textContent).toContain("No previous run is recorded");
    expect(screen.queryByText(/Failed to load/)).toBeNull();
  });

  it.each([
    ["no body", undefined],
    ["another error value", { error: "Service Unavailable" }],
  ])("a 503 without the Lambda's marker (%s) is an outage, not 'not computed'", async (_n, body) => {
    routeFetch(() => mkResponse(503, body));
    renderView();
    await selectAdp();

    expect(await screen.findByText("Failed to load the All ADP vehicles rollup")).toBeTruthy();
    expect(screen.getByText("Service unavailable (HTTP 503)")).toBeTruthy();
    expect(screen.queryByTestId("adp-rollup-not-computed")).toBeNull();
  });

  it("a 200 with an unexpected shape shows the error state instead of crashing", async () => {
    routeFetch(() => mkResponse(200, { ...ADP_BODY, assumptions: undefined }));
    renderView();
    await selectAdp();

    expect(await screen.findByText("The rollup response has an unexpected shape.")).toBeTruthy();
  });

  it("a non-503 failure is an error, not the not-computed state", async () => {
    routeFetch(() => mkResponse(500, { error: "boom" }));
    renderView();
    await selectAdp();

    expect(await screen.findByText("Failed to load the All ADP vehicles rollup")).toBeTruthy();
    expect(screen.queryByTestId("adp-rollup-not-computed")).toBeNull();
  });

  it("a 403 names the platform-admin requirement", async () => {
    routeFetch(() => mkResponse(403, { error: "forbidden" }));
    renderView();
    await selectAdp();

    expect(
      await screen.findByText("The All ADP vehicles view needs the platform-admin role.")
    ).toBeTruthy();
  });
});

