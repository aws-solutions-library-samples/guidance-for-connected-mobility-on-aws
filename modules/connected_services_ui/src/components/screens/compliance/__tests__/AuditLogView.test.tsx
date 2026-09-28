// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * AuditLogView tests — T6.4
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.4
 *
 * ## What this file covers
 *
 * 1. Settle marker present in rendered output.
 * 2. Table renders with entries from the audit log fixture.
 * 3. Audit log sources events from COMMAND_CENTER_ACTIVITY (same event model).
 * 4. Each entry exposes timestamp, domain, description, and actor columns.
 * 5. TableNoMatchState shown when text filter matches nothing.
 * 6. Fixture integrity: every leaf carries a valid provenance marker.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import AuditLogView from "../AuditLogView";
import {
  AUDIT_LOG_ENTRIES,
  COMMAND_CENTER_ACTIVITY,
  type AuditLogEntry,
} from "../auditLog.fixture";
import { VALID_PROVENANCE_MARKERS } from "../../../../types";

afterEach(() => {
  cleanup();
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderAuditLog() {
  return render(
    <MemoryRouter initialEntries={["/compliance/audit-log"]}>
      <Routes>
        <Route path="/compliance/audit-log" element={<AuditLogView />} />
      </Routes>
    </MemoryRouter>,
  );
}

// ── 1. Settle marker ─────────────────────────────────────────────────────────

describe("settle marker", () => {
  it("renders the screen settleMarker in the output", () => {
    renderAuditLog();
    const marker = screen.getByTestId("audit-log-settle-marker");
    expect(marker).toBeTruthy();
    expect(marker.textContent).toContain("cs-settle-audit-log-event-stream-table");
  });
});

// ── 2. Table renders ──────────────────────────────────────────────────────────

describe("table renders", () => {
  it("renders the audit log table", () => {
    renderAuditLog();
    expect(screen.getByTestId("audit-log-table")).toBeTruthy();
  });

  it("renders the correct total row count in the header counter", () => {
    renderAuditLog();
    const total = AUDIT_LOG_ENTRIES.length;
    const header = screen.getByTestId("audit-log-table-header");
    expect(header.textContent).toContain(`(${total})`);
  });
});

// ── 3. Same event model as Command Center ─────────────────────────────────────

/**
 * Load-bearing: the Audit Log must use the same event shape as the Command Center
 * activity feed. One event model, two views.
 */
describe("one event model — same as Command Center", () => {
  it("AUDIT_LOG_ENTRIES length equals COMMAND_CENTER_ACTIVITY length", () => {
    // Each audit entry wraps exactly one activity event.
    expect(AUDIT_LOG_ENTRIES.length).toBe(COMMAND_CENTER_ACTIVITY.length);
  });

  it("AUDIT_LOG_ENTRIES[0] wraps COMMAND_CENTER_ACTIVITY[0] by reference", () => {
    // The wrapped event object is the exact same reference — not a copy.
    // This enforces the single-source contract.
    expect(AUDIT_LOG_ENTRIES[0].event).toBe(COMMAND_CENTER_ACTIVITY[0]);
  });

  it("every event id in AUDIT_LOG_ENTRIES matches the corresponding COMMAND_CENTER_ACTIVITY id", () => {
    AUDIT_LOG_ENTRIES.forEach((entry, index) => {
      expect(entry.event.id).toBe(COMMAND_CENTER_ACTIVITY[index].id);
    });
  });

  it("every event description in AUDIT_LOG_ENTRIES matches COMMAND_CENTER_ACTIVITY", () => {
    AUDIT_LOG_ENTRIES.forEach((entry, index) => {
      expect(entry.event.description).toBe(
        COMMAND_CENTER_ACTIVITY[index].description,
      );
    });
  });
});

// ── 4. Column content ────────────────────────────────────────────────────────

describe("column content", () => {
  it("renders the first event description in the table", () => {
    renderAuditLog();
    const firstEvent = COMMAND_CENTER_ACTIVITY[0];
    // Description value rendered by ProvenanceField as text
    expect(
      screen.getByText(firstEvent.description.value!),
    ).toBeTruthy();
  });

  it("renders the first event domain in the table", () => {
    renderAuditLog();
    const firstEvent = COMMAND_CENTER_ACTIVITY[0];
    expect(screen.getByText(firstEvent.domain.value!)).toBeTruthy();
  });

  it("renders the actor for the first audit entry", () => {
    renderAuditLog();
    const firstActor = AUDIT_LOG_ENTRIES[0].actor.value!;
    expect(screen.getByText(firstActor)).toBeTruthy();
  });

  it("renders the timestamp for the first audit entry", () => {
    renderAuditLog();
    const firstTimestamp = COMMAND_CENTER_ACTIVITY[0].timestamp.value!;
    expect(screen.getByText(firstTimestamp)).toBeTruthy();
  });
});

// ── 5. TableNoMatchState ──────────────────────────────────────────────────────

describe("TableNoMatchState", () => {
  it("renders 'No matches' when text filter matches nothing", async () => {
    const user = userEvent.setup();
    renderAuditLog();
    const table = screen.getByTestId("audit-log-table");
    const filterInput = within(table).getByRole("searchbox");
    await user.type(filterInput, "zzz_no_match_xyzzy");
    expect(screen.getByText("No matches")).toBeTruthy();
  });
});

// ── 6. Fixture integrity ──────────────────────────────────────────────────────

describe("fixture integrity", () => {
  const EXEMPT_KEYS = new Set(["id"]);

  function checkProvenanceDeep(obj: unknown, path: string): void {
    if (obj === null || typeof obj !== "object") return;
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      if (EXEMPT_KEYS.has(key)) continue;
      if (value !== null && typeof value === "object") {
        if ("provenance" in value) {
          const pv = value as { value: unknown; provenance: string };
          expect(
            VALID_PROVENANCE_MARKERS.has(pv.provenance),
            `${path}.${key} has invalid provenance "${pv.provenance}"`,
          ).toBe(true);
        } else {
          checkProvenanceDeep(value, `${path}.${key}`);
        }
      }
    }
  }

  it("every leaf in AUDIT_LOG_ENTRIES carries a valid provenance marker", () => {
    for (const entry of AUDIT_LOG_ENTRIES) {
      checkProvenanceDeep(
        { event: entry.event, actor: entry.actor },
        `AUDIT_LOG_ENTRIES[${entry.event.id}]`,
      );
    }
  });

  it("AUDIT_LOG_ENTRIES is not empty", () => {
    expect(AUDIT_LOG_ENTRIES.length).toBeGreaterThan(0);
  });

  it("every entry has an actor field with a non-null value", () => {
    for (const entry of AUDIT_LOG_ENTRIES) {
      expect(entry.actor.value).not.toBeNull();
    }
  });
});
