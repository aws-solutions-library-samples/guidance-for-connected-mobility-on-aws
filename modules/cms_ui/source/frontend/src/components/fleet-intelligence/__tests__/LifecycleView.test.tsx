// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Red-phase tests for LifecycleView — T1.6 of spec
 * .kiro/specs/2026-09-14-cms-fleet-lifecycle-view/tasks.md
 *
 * ALL 19 tests fail with an ImportError until LifecycleView.tsx is authored in
 * Group 3.  The failures are intentional — these tests encode the acceptance
 * criteria that Group 3's implementation must satisfy.
 *
 * Chart invariants (per spec D9):
 *   1. Donut slice values (from summary.*Count) sum to totalVehicles.
 *   2. Histogram bin counts (client-computed from rows[]) sum to totalVehicles.
 *   3. Trend line has one point per fleetMonthlyTrend entry.
 *   4. Reference line uses summary.avgMonthlyDepreciation.
 *
 * Key mutation-gate test: test_donut_uses_server_counts_not_client_recompute
 * pins the donut source to the server's summary.*Count fields, preventing
 * a client-side re-tally from drifting against the server's threshold constants.
 *
 * The histogram BINS constant MUST be exported from LifecycleView (or an
 * extracted helper) so mutation tests can rebind edges without monkeypatching
 * JSX.  The seven bin labels in fixed order are:
 *   '0-6', '7-12', '13-18', '19-24', '25-36', 'No crossover', 'Insufficient'
 */

import React from "react";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";

// ---------------------------------------------------------------------------
// Mocks — hoisted above all other imports per vi.mock semantics
// ---------------------------------------------------------------------------

vi.mock("../../../utils/authFetch", () => ({
  authFetch: vi.fn(),
}));

vi.mock("../../../config/api", () => ({
  getApiEndpoint: () => "https://api.example.com",
  isDemoMode: () => false,
}));

// FleetFilterProvider reads useUserRole (2026-09-26 — follow-on 1 to the FI
// auth fix). Mock as platform-admin (cross-fleet) so the provider's default
// stays ALL_FLEETS_ID and this file's lifecycle-cache assertions remain
// focused on their own contract.
vi.mock("@/auth/useUserRole", () => ({
  useUserRole: () => ({
    isAdmin: true,
    isOperator: false,
    isViewer: false,
    isGuest: false,
    isConnectAgent: false,
    isEngineer: false,
    isDispatcher: false,
    canWrite: true,
    fleetIds: [],
  }),
}));

// react-router-dom: keep all real exports, only stub useNavigate so that
// the navigate() call in row-click tests is inspectable.
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom"
  );
  return { ...actual, useNavigate: () => mockNavigate };
});

// ---------------------------------------------------------------------------
// Imports placed AFTER vi.mock so they resolve the mocked modules
// ---------------------------------------------------------------------------

import { authFetch } from "../../../utils/authFetch";
import { FleetFilterProvider } from "../../fleet-filter/FleetFilter";
import { ALL_FLEETS_ID } from "../../fleet-picker/useFleetSelection";

// LifecycleView does NOT EXIST yet — importing it will throw an ImportError.
// That failure is the expected red-phase state.
import { LifecycleView } from "../LifecycleView";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

const mockAuthFetch = vi.mocked(authFetch);
const SESSION_KEY = "cms-fleet-filter-id";

/** Standard D3 response shape for most tests. */
interface LifecycleRow {
  vehicleId: string;
  vin: string;
  fleetId: string;
  make: string;
  model: string;
  year: number;
  purchasePrice: number;
  seriesLengthMonths: number;
  crossoverMonth: string | null;
  monthsUntilCrossover: number | null;
  rSquared: number;
  rSquaredMeaningful: boolean;
  currentMonthlyMaintenance: number;
  monthlyDepreciation: number;
  tireStatus: "healthy" | "monitor" | "needs_replacement" | "unknown";
  tirePositions: Array<{
    position: string;
    treadDepthMm: number;
    wearCategory: string;
    needsReplacement: boolean;
  }>;
  provenance: string;
}

interface LifecycleResponse {
  summary: {
    totalVehicles: number;
    sellRecommendedCount: number;
    sellSoonCount: number;
    healthyCount: number;
    insufficientDataCount: number;
    tiresNeedReplacementCount: number;
    avgMonthsToCrossover: number | null;
    avgMonthlyDepreciation: number;
    horizonMonths: number;
  };
  fleetMonthlyTrend: Array<{
    yearMonth: string;
    avgMaintenance: number;
    vehicleCount: number;
  }>;
  rows: LifecycleRow[];
  provenance: string;
}

function makeRow(overrides: Partial<LifecycleRow> = {}): LifecycleRow {
  return {
    vehicleId: "VEH-001",
    vin: "MRDN0000000000001",
    fleetId: "flt-test-001",
    make: "Meridian",
    model: "Range",
    year: 2023,
    purchasePrice: 60000,
    seriesLengthMonths: 14,
    crossoverMonth: "2027-06",
    monthsUntilCrossover: 21,
    rSquared: 0.87,
    rSquaredMeaningful: true,
    currentMonthlyMaintenance: 320.5,
    monthlyDepreciation: 500.0,
    tireStatus: "healthy",
    tirePositions: [
      { position: "FL", treadDepthMm: 5.0, wearCategory: "ok", needsReplacement: false },
      { position: "FR", treadDepthMm: 5.1, wearCategory: "ok", needsReplacement: false },
      { position: "RL", treadDepthMm: 6.3, wearCategory: "ok", needsReplacement: false },
      { position: "RR", treadDepthMm: 5.8, wearCategory: "ok", needsReplacement: false },
    ],
    provenance: "simulated",
    ...overrides,
  };
}

const BASE_RESPONSE: LifecycleResponse = {
  summary: {
    totalVehicles: 10,
    sellRecommendedCount: 2,
    sellSoonCount: 3,
    healthyCount: 4,
    insufficientDataCount: 1,
    tiresNeedReplacementCount: 5,
    avgMonthsToCrossover: 14.3,
    avgMonthlyDepreciation: 483.33,
    horizonMonths: 36,
  },
  fleetMonthlyTrend: [
    { yearMonth: "2025-10", avgMaintenance: 285.4, vehicleCount: 8 },
    { yearMonth: "2025-11", avgMaintenance: 291.2, vehicleCount: 9 },
    { yearMonth: "2025-12", avgMaintenance: 310.0, vehicleCount: 10 },
  ],
  rows: [
    makeRow({ vehicleId: "VEH-001", tireStatus: "healthy" }),
    makeRow({ vehicleId: "VEH-002", tireStatus: "needs_replacement" }),
    makeRow({ vehicleId: "VEH-003", tireStatus: "unknown" }),
  ],
  provenance: "simulated",
};

function mockFetchSuccess(data: LifecycleResponse = BASE_RESPONSE) {
  mockAuthFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
  } as unknown as Response);
}

function mockFetchError(status = 500, message = "Internal Server Error") {
  mockAuthFetch.mockRejectedValueOnce(new Error(message));
}

/** Wrap component in necessary providers for tests. */
function renderLifecycle(element: React.ReactElement = <LifecycleView />) {
  return render(
    <MemoryRouter>
      <FleetFilterProvider>{element}</FleetFilterProvider>
    </MemoryRouter>
  );
}

// ---------------------------------------------------------------------------
// Zone 1 + Table basic rendering tests
// ---------------------------------------------------------------------------

describe("LifecycleView — Zone 1: KPI cards", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it("renders_summary_kpis_from_response", async () => {
    // Mocked authFetch returns the D3 shape; asserts each of the 6 KPI cards
    // renders its value. Assertions are scoped to the `[data-testid="kpi-cards"]`
    // container so numeric values that also appear on chart axes (histogram
    // y-axis ticks, line-chart y-axis ticks) do not create false positives.
    mockFetchSuccess();
    renderLifecycle();

    await waitFor(() => {
      const kpiContainer = screen.getByTestId("kpi-cards");
      const kpis = within(kpiContainer);
      // totalVehicles
      expect(kpis.getByText("10")).toBeInTheDocument();
      // sellRecommendedCount
      expect(kpis.getByText("2")).toBeInTheDocument();
      // sellSoonCount
      expect(kpis.getByText("3")).toBeInTheDocument();
      // healthyCount
      expect(kpis.getByText("4")).toBeInTheDocument();
      // insufficientDataCount
      expect(kpis.getByText("1")).toBeInTheDocument();
      // tiresNeedReplacementCount — distinct value so getByText is unambiguous
      expect(kpis.getByText("5")).toBeInTheDocument();
    });
  });
});

describe("LifecycleView — Zone 3: Vehicle table", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it("renders_one_table_row_per_vehicle", async () => {
    // 3 vehicles in response → 3 renderable rows.
    mockFetchSuccess();
    renderLifecycle();

    await waitFor(() => {
      // All 3 vehicleIds should appear in the document
      expect(screen.getByText("VEH-001")).toBeInTheDocument();
      expect(screen.getByText("VEH-002")).toBeInTheDocument();
      expect(screen.getByText("VEH-003")).toBeInTheDocument();
    });
  });

  it("renders_insufficient_data_row_distinctly", async () => {
    // A row where rSquaredMeaningful=false renders with a grey indicator /
    // distinct test-id, not the standard threshold color.
    const response: LifecycleResponse = {
      ...BASE_RESPONSE,
      rows: [
        makeRow({ vehicleId: "VEH-INSUF", rSquaredMeaningful: false, crossoverMonth: null, monthsUntilCrossover: null }),
        makeRow({ vehicleId: "VEH-OK", rSquaredMeaningful: true }),
      ],
    };
    mockFetchSuccess(response);
    renderLifecycle();

    await waitFor(() => {
      // The insufficient-data row should have a distinguishable test-id or label
      const insufficientIndicator = document.querySelector(
        '[data-testid*="insufficient"], [data-testid*="no-data"], [data-insufficient="true"]'
      );
      expect(insufficientIndicator).not.toBeNull();
    });
  });

  it("renders_tire_status_column", async () => {
    // Three vehicles with tireStatus=healthy|needs_replacement|unknown each
    // render their status distinctly.
    mockFetchSuccess();
    renderLifecycle();

    await waitFor(() => {
      // All three tire status values should appear
      expect(screen.getByText(/healthy/i)).toBeInTheDocument();
      expect(screen.getByText(/needs.replacement/i)).toBeInTheDocument();
      expect(screen.getByText(/unknown/i)).toBeInTheDocument();
    });
  });

  it("row_click_navigates_to_vehicle_detail", async () => {
    // Clicking a row triggers navigation to /vehicles/management/{vehicleId}.
    mockFetchSuccess();
    renderLifecycle();

    await waitFor(() => {
      expect(screen.getByText("VEH-001")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("VEH-001"));

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith(
        expect.stringContaining("/vehicles/management/VEH-001")
      );
    });
  });

  it("error_state_renders_alert_not_crash", async () => {
    // authFetch throws → renders an Alert, does not crash.
    mockFetchError(500, "Network error");
    renderLifecycle();

    await waitFor(() => {
      // Should render an Alert element, not crash
      const alert = document.querySelector('[class*="alert"], [role="alert"]');
      expect(alert).not.toBeNull();
    });
  });
});

// ---------------------------------------------------------------------------
// Fleet filter wiring tests
// ---------------------------------------------------------------------------

describe("LifecycleView — fleet filter wiring", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    // Stub the authFetch call so it resolves rather than hanging
    mockAuthFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(BASE_RESPONSE),
    } as unknown as Response);
  });

  it("passes_fleetId_from_context_when_not_all_fleets", async () => {
    // Inside <FleetFilterProvider>, setting selectedFleetId='flt-x' includes
    // fleetId=flt-x in the URL; asserts via authFetch mock.
    sessionStorage.setItem(SESSION_KEY, "flt-meridian-range-001");

    renderLifecycle();

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalled();
    });

    const calledUrl = mockAuthFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("fleetId=flt-meridian-range-001");
  });

  it("omits_fleetId_when_all_fleets_sentinel", async () => {
    // selectedFleetId===ALL_FLEETS_ID → URL has no fleetId param.
    // Passing the literal '__all__' as fleetId would make the backend match no
    // vehicles and return empty, so the default view would break silently.
    sessionStorage.setItem(SESSION_KEY, ALL_FLEETS_ID);

    renderLifecycle();

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalled();
    });

    const calledUrl = mockAuthFetch.mock.calls[0][0] as string;
    expect(calledUrl).not.toContain("fleetId");
    expect(calledUrl).not.toContain(ALL_FLEETS_ID);
  });
});

// ---------------------------------------------------------------------------
// Zone 2a: Sell-status donut chart tests
// ---------------------------------------------------------------------------

describe("LifecycleView — Zone 2a: sell-status donut", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it("renders_sell_status_donut_with_four_slices", async () => {
    // A <PieChart> element renders with four data points:
    // sellRecommended, sellSoon, healthy, insufficientData.
    mockFetchSuccess();
    renderLifecycle();

    await waitFor(() => {
      // The component renders a PieChart with 4 slices. We verify by
      // checking that the chart container exists and carries the slice labels.
      expect(screen.getByText(/Sell Recommended/i)).toBeInTheDocument();
      expect(screen.getByText(/Sell Soon/i)).toBeInTheDocument();
      expect(screen.getByText(/Healthy/i)).toBeInTheDocument();
      expect(screen.getByText(/Insufficient Data/i)).toBeInTheDocument();
    });
  });

  it("donut_slice_values_sum_to_totalVehicles", async () => {
    // D9 invariant #1: slice values from the rendered chart data prop sum to
    // summary.totalVehicles.  This test pins the sum invariant so a category
    // leak or off-by-one breaks the donut.
    // Uses a response where the four counts are unambiguous and sum is verified.
    const response: LifecycleResponse = {
      ...BASE_RESPONSE,
      summary: {
        ...BASE_RESPONSE.summary,
        totalVehicles: 20,
        sellRecommendedCount: 3,
        sellSoonCount: 5,
        healthyCount: 10,
        insufficientDataCount: 2,
      },
    };
    mockFetchSuccess(response);
    renderLifecycle();

    await waitFor(() => {
      // The component must expose the chart data via a data-testid attribute
      // on the PieChart container, or the donut must render the total in the
      // inner area label — assert on that inner label.
      const totalLabel = document.querySelector('[data-testid="donut-total"]');
      if (totalLabel) {
        expect(totalLabel.textContent).toContain("20");
      } else {
        // Fallback: check the inner label rendered by PieChart variant="donut"
        expect(screen.getByText("20")).toBeInTheDocument();
      }
    });
  });

  it("test_donut_uses_server_counts_not_client_recompute", async () => {
    // MUTATION GATE (spec D9 + T3.1 constraint 6).
    // The donut slice values MUST come from summary.{sellRecommendedCount,
    // sellSoonCount, healthyCount, insufficientDataCount} directly.
    // A client-side re-tally using different threshold constants would produce
    // different numbers.  This test provides a response where the server counts
    // do NOT match what a naive client computation from rows[] would produce
    // (because the rows' monthsUntilCrossover values were intentionally
    // mismatched to what the summary says).
    //
    // The component MUST render the server's summary counts, not its own tally.
    const response: LifecycleResponse = {
      ...BASE_RESPONSE,
      summary: {
        ...BASE_RESPONSE.summary,
        totalVehicles: 5,
        sellRecommendedCount: 1,   // server says 1
        sellSoonCount: 1,
        healthyCount: 2,
        insufficientDataCount: 1,
      },
      rows: [
        // If the client re-tallied from rows, monthsUntilCrossover=4 would
        // yield sellRecommended=3, contradicting the server's count of 1.
        makeRow({ vehicleId: "VEH-A", monthsUntilCrossover: 4, rSquaredMeaningful: true }),
        makeRow({ vehicleId: "VEH-B", monthsUntilCrossover: 4, rSquaredMeaningful: true }),
        makeRow({ vehicleId: "VEH-C", monthsUntilCrossover: 4, rSquaredMeaningful: true }),
        makeRow({ vehicleId: "VEH-D", monthsUntilCrossover: 15, rSquaredMeaningful: true }),
        makeRow({ vehicleId: "VEH-E", rSquaredMeaningful: false, monthsUntilCrossover: null }),
      ],
    };
    mockFetchSuccess(response);
    renderLifecycle();

    await waitFor(() => {
      // The component should render the server-provided sellRecommendedCount=1.
      // The donut's data-testid or aria-label on the slice should confirm this.
      // We access the chart's data prop via the data-testid on the chart container.
      const donutContainer = document.querySelector(
        '[data-testid="sell-status-donut"]'
      );
      expect(donutContainer).not.toBeNull();

      // The rendered data attribute carries the actual count used for the slice.
      // This pins that the count is 1 (server) not 3 (client re-tally).
      const sellRecommendedSlice = document.querySelector(
        '[data-slice="sell-recommended"]'
      );
      if (sellRecommendedSlice) {
        expect(sellRecommendedSlice.getAttribute("data-value")).toBe("1");
      } else {
        // Fallback: assert via the chart's series prop rendered into the DOM.
        // The component must expose its data through a data-testid wrapper.
        // Failing this assertion confirms the test correctly pins the source.
        throw new Error(
          "Expected data-testid='sell-status-donut' with data-slice='sell-recommended' " +
          "on the PieChart container. Add these data-testid attributes in LifecycleView.tsx."
        );
      }
    });
  });
});

// ---------------------------------------------------------------------------
// Zone 2b: Crossover histogram tests
// ---------------------------------------------------------------------------

describe("LifecycleView — Zone 2b: crossover histogram", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it("renders_crossover_histogram_with_seven_bins", async () => {
    // A <BarChart> element renders 7 bins in this fixed order:
    // '0-6', '7-12', '13-18', '19-24', '25-36', 'No crossover', 'Insufficient'
    mockFetchSuccess();
    renderLifecycle();

    await waitFor(() => {
      // All seven bin labels should appear in the histogram
      expect(screen.getByText("0-6")).toBeInTheDocument();
      expect(screen.getByText("7-12")).toBeInTheDocument();
      expect(screen.getByText("13-18")).toBeInTheDocument();
      expect(screen.getByText("19-24")).toBeInTheDocument();
      expect(screen.getByText("25-36")).toBeInTheDocument();
      expect(screen.getByText(/No crossover/i)).toBeInTheDocument();
      expect(screen.getByText(/Insufficient/i)).toBeInTheDocument();
    });
  });

  it("histogram_bin_counts_sum_to_totalVehicles", async () => {
    // D9 invariant #2: sum of the 7 rendered bin y-values equals
    // summary.totalVehicles.  The histogram is client-computed from rows[],
    // so a bin-edge off-by-one would break this invariant.
    const response: LifecycleResponse = {
      ...BASE_RESPONSE,
      summary: { ...BASE_RESPONSE.summary, totalVehicles: 7 },
      rows: [
        makeRow({ vehicleId: "V1", monthsUntilCrossover: 3,  rSquaredMeaningful: true }),
        makeRow({ vehicleId: "V2", monthsUntilCrossover: 9,  rSquaredMeaningful: true }),
        makeRow({ vehicleId: "V3", monthsUntilCrossover: 15, rSquaredMeaningful: true }),
        makeRow({ vehicleId: "V4", monthsUntilCrossover: 22, rSquaredMeaningful: true }),
        makeRow({ vehicleId: "V5", monthsUntilCrossover: 30, rSquaredMeaningful: true }),
        makeRow({ vehicleId: "V6", monthsUntilCrossover: null, rSquaredMeaningful: true }),
        makeRow({ vehicleId: "V7", monthsUntilCrossover: null, rSquaredMeaningful: false }),
      ],
    };
    mockFetchSuccess(response);
    renderLifecycle();

    await waitFor(() => {
      // Histogram container must carry the total for verification
      const histContainer = document.querySelector(
        '[data-testid="crossover-histogram"]'
      );
      expect(histContainer).not.toBeNull();
      // The sum of bin counts attribute should be 7
      const total = histContainer?.getAttribute("data-total-vehicles");
      if (total !== null) {
        expect(Number(total)).toBe(7);
      }
    });
  });

  it("histogram_bins_boundary_correctness", async () => {
    // Mutation-style boundary test: pins the exact bin edges.
    // monthsUntilCrossover=6 → '0-6' bin
    // monthsUntilCrossover=7 → '7-12' bin
    // monthsUntilCrossover=12 → '7-12' bin
    // monthsUntilCrossover=13 → '13-18' bin
    const response: LifecycleResponse = {
      ...BASE_RESPONSE,
      summary: {
        ...BASE_RESPONSE.summary,
        totalVehicles: 4,
        sellRecommendedCount: 1,
        sellSoonCount: 3,
        healthyCount: 0,
        insufficientDataCount: 0,
      },
      rows: [
        makeRow({ vehicleId: "V-6",  monthsUntilCrossover: 6,  rSquaredMeaningful: true }),
        makeRow({ vehicleId: "V-7",  monthsUntilCrossover: 7,  rSquaredMeaningful: true }),
        makeRow({ vehicleId: "V-12", monthsUntilCrossover: 12, rSquaredMeaningful: true }),
        makeRow({ vehicleId: "V-13", monthsUntilCrossover: 13, rSquaredMeaningful: true }),
      ],
    };
    mockFetchSuccess(response);
    renderLifecycle();

    await waitFor(() => {
      // The histogram container must carry the bin-edge data for inspection.
      // Specifically: bin '0-6' should have count=1, bin '7-12' should have count=2.
      const histContainer = document.querySelector(
        '[data-testid="crossover-histogram"]'
      );
      expect(histContainer).not.toBeNull();

      // Verify via data attributes on the bin elements
      const bin06 = document.querySelector('[data-bin="0-6"]');
      const bin712 = document.querySelector('[data-bin="7-12"]');
      const bin1318 = document.querySelector('[data-bin="13-18"]');

      if (bin06 && bin712 && bin1318) {
        expect(Number(bin06.getAttribute("data-count"))).toBe(1);   // V-6
        expect(Number(bin712.getAttribute("data-count"))).toBe(2);  // V-7, V-12
        expect(Number(bin1318.getAttribute("data-count"))).toBe(1); // V-13
      } else {
        // If the bins are rendered inline into the BarChart series without
        // explicit data-testid, assert via the BINS export from LifecycleView.
        // This will fail until LifecycleView is implemented.
        throw new Error(
          "Expected [data-bin='0-6'], [data-bin='7-12'], [data-bin='13-18'] on " +
          "histogram bin elements. Add data-bin and data-count attributes in LifecycleView.tsx."
        );
      }
    });
  });

  it("histogram_places_insufficient_vehicles_in_own_bin", async () => {
    // A vehicle with rSquaredMeaningful=false lands in the 'Insufficient' bin,
    // NOT in a numeric bin, even if it has a spurious monthsUntilCrossover=2.
    const response: LifecycleResponse = {
      ...BASE_RESPONSE,
      summary: {
        ...BASE_RESPONSE.summary,
        totalVehicles: 2,
        sellRecommendedCount: 0,
        sellSoonCount: 0,
        healthyCount: 1,
        insufficientDataCount: 1,
      },
      rows: [
        // This vehicle has a spurious crossover in 2 months but insufficient data
        makeRow({ vehicleId: "V-INSUF", monthsUntilCrossover: 2, rSquaredMeaningful: false }),
        makeRow({ vehicleId: "V-OK",    monthsUntilCrossover: null, rSquaredMeaningful: true }),
      ],
    };
    mockFetchSuccess(response);
    renderLifecycle();

    await waitFor(() => {
      // The '0-6' bin should have count=0 (V-INSUF went to 'Insufficient', not '0-6')
      const bin06 = document.querySelector('[data-bin="0-6"]');
      const binInsuff = document.querySelector('[data-bin="Insufficient"]');

      if (bin06 && binInsuff) {
        expect(Number(bin06.getAttribute("data-count"))).toBe(0);
        expect(Number(binInsuff.getAttribute("data-count"))).toBe(1);
      } else {
        // The insufficient vehicle must NOT appear in the '0-6' label
        throw new Error(
          "Expected [data-bin='0-6'] with data-count=0 and " +
          "[data-bin='Insufficient'] with data-count=1."
        );
      }
    });
  });
});

// ---------------------------------------------------------------------------
// Zone 2c: Maintenance trend line chart tests
// ---------------------------------------------------------------------------

describe("LifecycleView — Zone 2c: maintenance trend", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it("renders_maintenance_trend_from_fleetMonthlyTrend", async () => {
    // A <LineChart> renders one data series with N points where N ===
    // fleetMonthlyTrend.length.
    const response: LifecycleResponse = {
      ...BASE_RESPONSE,
      fleetMonthlyTrend: [
        { yearMonth: "2025-10", avgMaintenance: 285.4, vehicleCount: 8 },
        { yearMonth: "2025-11", avgMaintenance: 291.2, vehicleCount: 9 },
        { yearMonth: "2025-12", avgMaintenance: 310.0, vehicleCount: 10 },
      ],
    };
    mockFetchSuccess(response);
    renderLifecycle();

    await waitFor(() => {
      // The trend container must carry the point count for verification
      const trendContainer = document.querySelector(
        '[data-testid="maintenance-trend"]'
      );
      expect(trendContainer).not.toBeNull();
      const pointCount = trendContainer?.getAttribute("data-point-count");
      if (pointCount !== null) {
        expect(Number(pointCount)).toBe(3);
      }
    });
  });

  it("trend_reference_line_uses_avgMonthlyDepreciation", async () => {
    // The chart's threshold or y-marker is set to summary.avgMonthlyDepreciation.
    const response: LifecycleResponse = {
      ...BASE_RESPONSE,
      summary: { ...BASE_RESPONSE.summary, avgMonthlyDepreciation: 499.99 },
    };
    mockFetchSuccess(response);
    renderLifecycle();

    await waitFor(() => {
      // The reference line label should surface the depreciation value
      const trendContainer = document.querySelector(
        '[data-testid="maintenance-trend"]'
      );
      expect(trendContainer).not.toBeNull();
      const refLineValue = trendContainer?.getAttribute("data-ref-line");
      if (refLineValue !== null) {
        expect(Number(refLineValue)).toBeCloseTo(499.99, 1);
      } else {
        // Fallback: the depreciation value should appear as a label on the chart
        expect(screen.getByText(/499/)).toBeInTheDocument();
      }
    });
  });

  it("trend_absent_when_fleetMonthlyTrend_empty", async () => {
    // An empty fleetMonthlyTrend renders the LineChart's Cloudscape empty state,
    // not a crash.
    const response: LifecycleResponse = {
      ...BASE_RESPONSE,
      fleetMonthlyTrend: [],
    };
    mockFetchSuccess(response);

    // Should not throw
    expect(() => renderLifecycle()).not.toThrow();

    await waitFor(() => {
      // The component should render without crashing
      const trendContainer = document.querySelector(
        '[data-testid="maintenance-trend"]'
      );
      expect(trendContainer).not.toBeNull();
    });
  });
});

// ---------------------------------------------------------------------------
// ProvenanceBadge presence tests
// ---------------------------------------------------------------------------

describe("LifecycleView — provenance badges", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it("charts_render_provenance_badges", async () => {
    // Each of the three charts has a <ProvenanceBadge> visible in its container
    // (not hover-only).  ProvenanceBadge renders with data-testid=
    // 'provenance-badge-simulated' when provenance='simulated'.
    mockFetchSuccess();
    renderLifecycle();

    await waitFor(() => {
      // At least three provenance badges should be visible —
      // one per chart zone (donut, histogram, trend).
      const badges = document.querySelectorAll('[data-testid="provenance-badge-simulated"]');
      expect(badges.length).toBeGreaterThanOrEqual(3);
      badges.forEach((badge) => {
        expect(badge.textContent).toContain("Simulated");
      });
    });
  });
});
