// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for PM components — T4.2 of spec 2026-09-02-cms-fleet-intelligence-v1.
 *
 * Coverage:
 *   - PmScheduleList: due, overdue, upcoming items display correctly
 *   - Overdue boundary: item with overdue=true is classified correctly
 *   - PmScheduleForm: all three bases (mileage, engine_hours, calendar)
 *   - PmScheduleForm: submit POSTs to /pm/schedules
 *   - PmComplianceRollup: renders rate + KPIs with ProvenanceBadge
 *   - ProvenanceBadge is visible (not hover-only) in all components
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { PmScheduleList } from "./PmScheduleList";
import { PmScheduleForm } from "./PmScheduleForm";
import { PmComplianceRollup } from "./PmComplianceRollup";
import PmComplianceView from "./PmComplianceView";
import type { PmSchedule } from "./types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../../utils/authFetch", () => ({
  authFetch: vi.fn(),
}));

vi.mock("../../config/api", () => ({
  getApiEndpoint: () => "https://api.example.com",
}));

import { authFetch } from "../../utils/authFetch";
const mockAuthFetch = vi.mocked(authFetch);

function mockFetchSuccess(data: unknown) {
  mockAuthFetch.mockResolvedValueOnce({
    ok: true,
    json: () => Promise.resolve(data),
  } as unknown as Response);
}

function mockFetchError(status = 500) {
  mockAuthFetch.mockResolvedValueOnce({
    ok: false,
    status,
    json: () => Promise.resolve({}),
  } as unknown as Response);
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const MILEAGE_SCHEDULE: PmSchedule = {
  scheduleId: "sched-001",
  vehicleId: "VEH-001",
  fleetId: "fleet-1",
  taskCode: "PM-OIL-0001",
  taskDescription: "Oil change",
  basis: "mileage",
  intervalValue: 5000,
  nextDueValue: 55000,
  nextDueDate: "2025-12-01",
  lastPerformedMileage: 50000,
  currentMileage: 54200,
  overdue: false,
  daysUntilDue: 30,
  provenance: "simulated",
};

const ENGINE_HOURS_SCHEDULE: PmSchedule = {
  scheduleId: "sched-002",
  vehicleId: "VEH-002",
  taskCode: "PM-FILTER-0001",
  taskDescription: "Air filter replacement",
  basis: "engine_hours",
  intervalValue: 250,
  nextDueValue: 1500,
  nextDueDate: "2025-11-15",
  lastPerformedEngineHours: 1250,
  currentEngineHours: 1490,
  overdue: false,
  daysUntilDue: 5,
  provenance: "simulated",
};

const OVERDUE_SCHEDULE: PmSchedule = {
  scheduleId: "sched-003",
  vehicleId: "VEH-003",
  taskCode: "PM-BRAKE-0001",
  taskDescription: "Brake inspection",
  basis: "calendar",
  intervalValue: 90,
  nextDueValue: 90,
  nextDueDate: "2025-09-01",
  lastPerformedAt: "2025-06-01",
  overdue: true,
  daysUntilDue: -15,
  provenance: "simulated",
};

// ---------------------------------------------------------------------------
// PmScheduleList tests
// ---------------------------------------------------------------------------

describe("PmScheduleList — rendering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders mileage-based schedule row", async () => {
    mockFetchSuccess({ data: [MILEAGE_SCHEDULE] });
    render(<PmScheduleList />);

    await waitFor(() => {
      expect(screen.getByText("VEH-001")).toBeInTheDocument();
      expect(screen.getByText("Oil change")).toBeInTheDocument();
    });
  });

  it("renders engine_hours schedule row", async () => {
    mockFetchSuccess({ data: [ENGINE_HOURS_SCHEDULE] });
    render(<PmScheduleList />);

    await waitFor(() => {
      expect(screen.getByText("VEH-002")).toBeInTheDocument();
      expect(screen.getByText("Air filter replacement")).toBeInTheDocument();
    });
  });

  it("renders all three schedule rows", async () => {
    mockFetchSuccess({
      data: [MILEAGE_SCHEDULE, ENGINE_HOURS_SCHEDULE, OVERDUE_SCHEDULE],
    });
    render(<PmScheduleList />);

    await waitFor(() => {
      expect(screen.getByText("VEH-001")).toBeInTheDocument();
      expect(screen.getByText("VEH-002")).toBeInTheDocument();
      expect(screen.getByText("VEH-003")).toBeInTheDocument();
    });
  });

  it("calls the pm/schedules endpoint", async () => {
    mockFetchSuccess({ data: [] });
    render(<PmScheduleList />);

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalled();
    });
    const calledUrl = (mockAuthFetch.mock.calls[0][0] as string);
    expect(calledUrl).toContain("/api/v1/fleet-intelligence/pm/schedules");
  });

  it("surfaces an error alert on API failure", async () => {
    mockFetchError(503);
    render(<PmScheduleList />);

    await waitFor(() => {
      expect(
        screen.getByText("Failed to load PM schedules")
      ).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// PmScheduleList — overdue boundary
// ---------------------------------------------------------------------------

describe("PmScheduleList — overdue boundary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows 'Overdue' status for a schedule with overdue=true", async () => {
    mockFetchSuccess({ data: [OVERDUE_SCHEDULE] });
    render(<PmScheduleList />);

    await waitFor(() => {
      expect(screen.getByText(/Overdue/i)).toBeInTheDocument();
    });
  });

  it("shows 'Due soon' status for a schedule due within 7 days", async () => {
    mockFetchSuccess({ data: [ENGINE_HOURS_SCHEDULE] });
    // ENGINE_HOURS_SCHEDULE has daysUntilDue=5
    render(<PmScheduleList />);

    await waitFor(() => {
      expect(screen.getByText(/Due soon/i)).toBeInTheDocument();
    });
  });

  it("shows 'Upcoming' for a schedule with 30+ days until due", async () => {
    mockFetchSuccess({ data: [MILEAGE_SCHEDULE] });
    // MILEAGE_SCHEDULE has daysUntilDue=30
    render(<PmScheduleList />);

    await waitFor(() => {
      expect(screen.getByText(/Upcoming/i)).toBeInTheDocument();
    });
  });

  it("ProvenanceBadge is visible in each row (not hover-only)", async () => {
    mockFetchSuccess({ data: [OVERDUE_SCHEDULE] });
    render(<PmScheduleList />);

    await waitFor(() => {
      const badges = document.querySelectorAll(
        '[data-testid="provenance-badge-simulated"]'
      );
      expect(badges.length).toBeGreaterThan(0);
      badges.forEach((b) => {
        expect(b.textContent).toContain("Simulated");
      });
    });
  });
});

// ---------------------------------------------------------------------------
// PmScheduleForm — three bases
// ---------------------------------------------------------------------------

describe("PmScheduleForm — all three bases", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the basis selector with all three options", () => {
    render(<PmScheduleForm />);

    // The Select shows the current selected option
    expect(screen.getByTestId("pm-form-basis")).toBeInTheDocument();
  });

  it("shows mileage last-performed field when basis is mileage (default)", () => {
    render(<PmScheduleForm />);

    // Default basis is mileage — last mileage field should appear
    expect(screen.getByTestId("pm-form-last-mileage")).toBeInTheDocument();
  });

  it("renders vehicle ID field", () => {
    render(<PmScheduleForm />);
    expect(screen.getByTestId("pm-form-vehicle-id")).toBeInTheDocument();
  });

  it("renders task code field", () => {
    render(<PmScheduleForm />);
    expect(screen.getByTestId("pm-form-task-code")).toBeInTheDocument();
  });

  it("renders interval field", () => {
    render(<PmScheduleForm />);
    expect(screen.getByTestId("pm-form-interval")).toBeInTheDocument();
  });

  it("validates required fields before submit", async () => {
    render(<PmScheduleForm />);

    // Click save without filling required fields
    const saveBtn = screen.getByTestId("pm-form-save");
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByText("Vehicle ID is required")).toBeInTheDocument();
    });
  });

  it("POSTs to /pm/schedules on valid submit", async () => {
    mockAuthFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ scheduleId: "new-sched", vehicleId: "VEH-007" }),
    } as unknown as Response);

    const onSave = vi.fn();
    render(<PmScheduleForm onSave={onSave} />);

    // Fill required fields
    fireEvent.change(
      screen.getByTestId("pm-form-vehicle-id").querySelector("input")!,
      { target: { value: "VEH-007" } }
    );
    fireEvent.change(
      screen.getByTestId("pm-form-task-code").querySelector("input")!,
      { target: { value: "PM-OIL-0001" } }
    );
    fireEvent.change(
      screen.getByTestId("pm-form-interval").querySelector("input")!,
      { target: { value: "5000" } }
    );

    fireEvent.click(screen.getByTestId("pm-form-save"));

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/fleet-intelligence/pm/schedules"),
        expect.objectContaining({ method: "POST" })
      );
    });
  });

  it("calls onSave callback after successful POST", async () => {
    mockAuthFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ scheduleId: "new-sched" }),
    } as unknown as Response);

    const onSave = vi.fn();
    render(<PmScheduleForm onSave={onSave} />);

    // Fill and submit
    fireEvent.change(
      screen.getByTestId("pm-form-vehicle-id").querySelector("input")!,
      { target: { value: "VEH-007" } }
    );
    fireEvent.change(
      screen.getByTestId("pm-form-task-code").querySelector("input")!,
      { target: { value: "PM-OIL-0001" } }
    );
    fireEvent.change(
      screen.getByTestId("pm-form-interval").querySelector("input")!,
      { target: { value: "5000" } }
    );
    fireEvent.click(screen.getByTestId("pm-form-save"));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledTimes(1);
    });
  });

  it("calls onCancel callback when cancel is clicked", () => {
    const onCancel = vi.fn();
    render(<PmScheduleForm onCancel={onCancel} />);

    fireEvent.click(screen.getByTestId("pm-form-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// PmComplianceRollup tests
// ---------------------------------------------------------------------------

describe("PmComplianceRollup — rendering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders compliance rate as progress bar", async () => {
    mockFetchSuccess({
      fleetId: "fleet-1",
      window: { start: "2025-09-01", end: "2025-09-30" },
      total: 20,
      compliant: 18,
      non_compliant: 2,
      rate: 0.9,
      provenance: "simulated",
    });

    render(<PmComplianceRollup />);

    await waitFor(() => {
      expect(screen.getByTestId("compliance-progress")).toBeInTheDocument();
    });
  });

  it("renders the total scheduled count", async () => {
    mockFetchSuccess({
      window: { start: "2025-09-01", end: "2025-09-30" },
      total: 20,
      compliant: 18,
      non_compliant: 2,
      rate: 0.9,
      provenance: "simulated",
    });

    render(<PmComplianceRollup />);

    await waitFor(() => {
      expect(
        screen.getByTestId("compliance-total-scheduled")
      ).toHaveTextContent("20");
    });
  });

  it("renders the overdue count", async () => {
    mockFetchSuccess({
      window: { start: "2025-09-01", end: "2025-09-30" },
      total: 20,
      compliant: 17,
      non_compliant: 3,
      rate: 0.85,
      provenance: "simulated",
    });

    render(<PmComplianceRollup />);

    await waitFor(() => {
      expect(
        screen.getByTestId("compliance-overdue-count")
      ).toHaveTextContent("3");
    });
  });

  it("renders ProvenanceBadge for simulated data at panel level", async () => {
    mockFetchSuccess({
      window: { start: "2025-09-01", end: "2025-09-30" },
      total: 10,
      compliant: 8,
      non_compliant: 2,
      rate: 0.8,
      provenance: "simulated",
    });

    render(<PmComplianceRollup />);

    await waitFor(() => {
      const badge = document.querySelector(
        '[data-testid="provenance-badge-simulated"][data-variant="panel"]'
      );
      expect(badge).not.toBeNull();
      expect(badge!.textContent).toContain("Simulated");
    });
  });

  it("surfaces an error alert on API failure", async () => {
    mockFetchError(500);
    render(<PmComplianceRollup />);

    await waitFor(() => {
      expect(
        screen.getByText("Failed to load compliance data")
      ).toBeInTheDocument();
    });
  });

  it("calls the pm/compliance endpoint", async () => {
    mockFetchSuccess({
      window: { start: "2025-09-01", end: "2025-09-30" },
      total: 5,
      compliant: 4,
      non_compliant: 1,
      rate: 0.8,
      provenance: "simulated",
    });

    render(<PmComplianceRollup />);

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalled();
    });
    const calledUrl = (mockAuthFetch.mock.calls[0][0] as string);
    expect(calledUrl).toContain("/api/v1/fleet-intelligence/pm/compliance");
  });
});


// ---------------------------------------------------------------------------
// PmComplianceView — completion contract (review Cycle 4 regression)
//
// The first implementation POSTed {completedAt: <ISO datetime>} with no
// vehicleId, via bare fetch() + sessionStorage. The handler
// (services/fleet_intelligence/index.py::_handle_pm_complete) requires
// vehicleId (the table partition key) and an ISO-8601 *date* `completedDate`,
// and 400s without them — so every completion would have failed silently.
// These tests pin the corrected contract and the visible-error behaviour.
// ---------------------------------------------------------------------------

describe("PmComplianceView — completion request contract", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default fallback: the list remounts after a successful completion
    // (listKey bump) and fires an extra fetch. Without a default, that call
    // returns undefined and throws "Cannot read properties of undefined
    // (reading 'then')" as an unhandled error beside otherwise-green tests.
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ data: [], provenance: "simulated" }),
    } as unknown as Response);
  });

  it("sends vehicleId and an ISO date completedDate via authFetch", async () => {
    // Rollup fetch, then list fetch
    mockFetchSuccess({ window: { start: "2026-09-01", end: "2026-09-30" }, total: 1, compliant: 1, non_compliant: 0, rate: 1.0, provenance: "simulated" });
    mockFetchSuccess({ data: [MILEAGE_SCHEDULE] });
    render(<PmComplianceView />);

    const completeBtn = await screen.findByText("Complete");
    mockAuthFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ scheduleId: MILEAGE_SCHEDULE.scheduleId }),
    } as unknown as Response);
    fireEvent.click(completeBtn);

    await waitFor(() => {
      const completionCall = mockAuthFetch.mock.calls.find(([url]) =>
        String(url).includes("/complete")
      );
      expect(completionCall).toBeTruthy();
      const body = JSON.parse(String((completionCall![1] as RequestInit).body));
      // vehicleId is required — it is the pm_schedules partition key
      expect(body.vehicleId).toBe(MILEAGE_SCHEDULE.vehicleId);
      // completedDate must be a bare ISO date (YYYY-MM-DD), not a datetime,
      // because the handler parses it with date.fromisoformat()
      expect(body.completedDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      // the pre-fix field name must be gone
      expect(body.completedAt).toBeUndefined();
    });
  });

  it("surfaces a visible error when the completion POST fails", async () => {
    mockFetchSuccess({ window: { start: "2026-09-01", end: "2026-09-30" }, total: 1, compliant: 0, non_compliant: 1, rate: 0.0, provenance: "simulated" });
    mockFetchSuccess({ data: [MILEAGE_SCHEDULE] });
    render(<PmComplianceView />);

    const completeBtn = await screen.findByText("Complete");
    mockAuthFetch.mockResolvedValueOnce({ ok: false, status: 400 } as unknown as Response);
    fireEvent.click(completeBtn);

    // A swallowed error would leave the operator believing the PM was recorded.
    await waitFor(() => {
      expect(
        screen.getByText(/could not record the pm completion/i)
      ).toBeInTheDocument();
    });
  });
});
