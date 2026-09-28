// SPDX-License-Identifier: Apache-2.0

/**
 * Unit tests for <ProvenanceBadge> — T2.2 of spec 2026-09-02-cms-fleet-intelligence-v1.
 *
 * Coverage requirements (from tasks.md T2.2 Accept):
 *   - All four provenance values: simulated, measured, derived, reference
 *   - Absent/null/undefined → visible "provenance unknown" marker (NOT nothing)
 *   - Row-level and panel-level rendering
 *
 * Key invariants (from spec § D1 and T2.2 Constraints):
 *   - "measured" returns null (no badge; it is the quality baseline)
 *   - All other values produce a visible, always-on text marker
 *   - No tooltip/hover-only — the text must be in the DOM, not in an attribute
 *   - Cloudscape tokens in styles, not literal hex
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ProvenanceBadge } from "./ProvenanceBadge";
import type { ProvenanceValue } from "./ProvenanceBadge";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderBadge(provenance: ProvenanceValue, variant?: "row" | "panel") {
  const { container } = render(
    <ProvenanceBadge provenance={provenance} variant={variant} />
  );
  return container;
}

// ---------------------------------------------------------------------------
// "measured" — no badge
// ---------------------------------------------------------------------------

describe("measured provenance", () => {
  it("renders nothing for 'measured' — it is the quality baseline, no label needed", () => {
    const { container } = render(<ProvenanceBadge provenance="measured" />);
    // The component returns null, so the container is empty
    expect(container.firstChild).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// "simulated" — visible label in both modes
// ---------------------------------------------------------------------------

describe("simulated provenance", () => {
  it("renders a visible 'Simulated' label in row mode (default)", () => {
    renderBadge("simulated");
    expect(screen.getByText("Simulated")).toBeInTheDocument();
  });

  it("text is directly in the DOM, not only in aria attributes (not tooltip-only)", () => {
    renderBadge("simulated");
    const el = screen.getByText("Simulated");
    // The label must be a real rendered text node, not hidden behind aria/title
    expect(el).toBeVisible();
  });

  it("has the correct data-testid for targeting", () => {
    renderBadge("simulated");
    expect(screen.getByTestId("provenance-badge-simulated")).toBeInTheDocument();
  });

  it("has an aria-label describing provenance for screen readers", () => {
    renderBadge("simulated");
    const badge = screen.getByTestId("provenance-badge-simulated");
    expect(badge.getAttribute("aria-label")).toMatch(/simulated/i);
  });

  it("uses Cloudscape warning tokens (not literal hex) for background", () => {
    renderBadge("simulated");
    const badge = screen.getByTestId("provenance-badge-simulated");
    const bg = badge.style.background;
    // Must use var(--...) token syntax, not a bare hex value
    expect(bg).toMatch(/var\(--color-background-status-warning/);
  });

  it("uses Cloudscape warning tokens for text color", () => {
    renderBadge("simulated");
    const badge = screen.getByTestId("provenance-badge-simulated");
    const col = badge.style.color;
    expect(col).toMatch(/var\(--color-text-status-warning/);
  });

  it("renders 'Simulated' in panel mode with data-variant='panel'", () => {
    renderBadge("simulated", "panel");
    const badge = screen.getByTestId("provenance-badge-simulated");
    expect(badge.getAttribute("data-variant")).toBe("panel");
    // In panel mode the text is wrapped but still present
    expect(badge.textContent).toContain("Simulated");
  });

  it("panel mode renders 'Data:' prefix label for context", () => {
    renderBadge("simulated", "panel");
    const badge = screen.getByTestId("provenance-badge-simulated");
    expect(badge.textContent).toContain("Data:");
  });

  it("row mode does NOT render 'Data:' prefix (compact)", () => {
    renderBadge("simulated", "row");
    const badge = screen.getByTestId("provenance-badge-simulated");
    expect(badge.textContent).not.toContain("Data:");
  });
});

// ---------------------------------------------------------------------------
// "derived" — visible label
// ---------------------------------------------------------------------------

describe("derived provenance", () => {
  it("renders a visible 'Derived' label in row mode", () => {
    renderBadge("derived");
    expect(screen.getByText("Derived")).toBeInTheDocument();
  });

  it("uses Cloudscape info tokens for background", () => {
    renderBadge("derived");
    const badge = screen.getByTestId("provenance-badge-derived");
    expect(badge.style.background).toMatch(/var\(--color-background-status-info/);
  });

  it("uses Cloudscape info tokens for text color", () => {
    renderBadge("derived");
    const badge = screen.getByTestId("provenance-badge-derived");
    expect(badge.style.color).toMatch(/var\(--color-text-status-info/);
  });

  it("renders in panel mode", () => {
    renderBadge("derived", "panel");
    expect(screen.getByTestId("provenance-badge-derived")).toBeInTheDocument();
    expect(screen.getByTestId("provenance-badge-derived").textContent).toContain("Derived");
  });
});

// ---------------------------------------------------------------------------
// "reference" — visible label
// ---------------------------------------------------------------------------

describe("reference provenance", () => {
  it("renders a visible 'Reference' label in row mode", () => {
    renderBadge("reference");
    expect(screen.getByText("Reference")).toBeInTheDocument();
  });

  it("uses Cloudscape success tokens for background", () => {
    renderBadge("reference");
    const badge = screen.getByTestId("provenance-badge-reference");
    expect(badge.style.background).toMatch(/var\(--color-background-status-success/);
  });

  it("uses Cloudscape success tokens for text color", () => {
    renderBadge("reference");
    const badge = screen.getByTestId("provenance-badge-reference");
    expect(badge.style.color).toMatch(/var\(--color-text-status-success/);
  });

  it("renders in panel mode", () => {
    renderBadge("reference", "panel");
    expect(screen.getByTestId("provenance-badge-reference")).toBeInTheDocument();
    expect(screen.getByTestId("provenance-badge-reference").textContent).toContain("Reference");
  });
});

// ---------------------------------------------------------------------------
// Absent / null / undefined — MUST render "provenance unknown" (not nothing)
//
// This case is real and will ship: existing seeded rows lack the field.
// Rendering nothing would be an unlabelled value — the defect this component
// exists to prevent.
// ---------------------------------------------------------------------------

describe("absent provenance (null/undefined)", () => {
  it("renders a visible 'Provenance unknown' marker for null — NOT nothing", () => {
    renderBadge(null);
    const badge = screen.getByTestId("provenance-badge-unknown");
    expect(badge).toBeInTheDocument();
  });

  it("shows 'Provenance unknown' text for null", () => {
    renderBadge(null);
    expect(screen.getByText("Provenance unknown")).toBeInTheDocument();
  });

  it("renders a visible 'Provenance unknown' marker for undefined — NOT nothing", () => {
    renderBadge(undefined);
    const badge = screen.getByTestId("provenance-badge-unknown");
    expect(badge).toBeInTheDocument();
  });

  it("shows 'Provenance unknown' text for undefined", () => {
    renderBadge(undefined);
    expect(screen.getByText("Provenance unknown")).toBeInTheDocument();
  });

  it("uses Cloudscape error tokens for background (communicates missing origin)", () => {
    renderBadge(null);
    const badge = screen.getByTestId("provenance-badge-unknown");
    expect(badge.style.background).toMatch(/var\(--color-background-status-error/);
  });

  it("uses Cloudscape error tokens for text color", () => {
    renderBadge(null);
    const badge = screen.getByTestId("provenance-badge-unknown");
    expect(badge.style.color).toMatch(/var\(--color-text-status-error/);
  });

  it("has an aria-label for screen readers", () => {
    renderBadge(null);
    const badge = screen.getByTestId("provenance-badge-unknown");
    expect(badge.getAttribute("aria-label")).toMatch(/unknown/i);
  });

  it("panel mode: shows 'Provenance unknown' text with Data: prefix", () => {
    renderBadge(null, "panel");
    const badge = screen.getByTestId("provenance-badge-unknown");
    expect(badge.textContent).toContain("Provenance unknown");
    expect(badge.textContent).toContain("Data:");
  });
});

// ---------------------------------------------------------------------------
// Panel vs row mode: structural distinction
// ---------------------------------------------------------------------------

describe("panel vs row mode", () => {
  it("row mode has data-variant='row'", () => {
    renderBadge("simulated", "row");
    expect(screen.getByTestId("provenance-badge-simulated").getAttribute("data-variant")).toBe("row");
  });

  it("panel mode has data-variant='panel'", () => {
    renderBadge("simulated", "panel");
    expect(screen.getByTestId("provenance-badge-simulated").getAttribute("data-variant")).toBe("panel");
  });

  it("default variant is 'row'", () => {
    renderBadge("derived");
    expect(screen.getByTestId("provenance-badge-derived").getAttribute("data-variant")).toBe("row");
  });

  it("panel mode badge has larger font size than row mode", () => {
    const { unmount: unmount1 } = render(
      <ProvenanceBadge provenance="simulated" variant="panel" />
    );
    const panelBadge = screen.getByTestId("provenance-badge-simulated");
    const panelFontSize = panelBadge.style.fontSize;
    unmount1();

    render(<ProvenanceBadge provenance="simulated" variant="row" />);
    const rowBadge = screen.getByTestId("provenance-badge-simulated");
    const rowFontSize = rowBadge.style.fontSize;

    // Panel should be larger than row
    expect(panelFontSize).not.toBe(rowFontSize);
    // Specifically: panel=14px, row=11px
    expect(panelFontSize).toBe("14px");
    expect(rowFontSize).toBe("11px");
  });
});

// ---------------------------------------------------------------------------
// data-provenance attribute — enables automated labelling audit
//
// The D1 guard tests enumerate unlabelled values by scanning the rendered DOM
// for values lacking this attribute. It must be present for every non-measured
// provenance value.
// ---------------------------------------------------------------------------

describe("data-provenance attribute for automated audit", () => {
  const cases: Array<[ProvenanceValue, string]> = [
    ["simulated", "simulated"],
    ["derived", "derived"],
    ["reference", "reference"],
    [null, "unknown"],
    [undefined, "unknown"],
  ];

  it.each(cases)(
    "provenance=%s renders data-provenance='%s'",
    (provenance, expectedAttr) => {
      renderBadge(provenance);
      const badge = screen.getByTestId(`provenance-badge-${expectedAttr}`);
      expect(badge.getAttribute("data-provenance")).toBe(expectedAttr);
    }
  );
});
