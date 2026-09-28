// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * AppShell.test.tsx — T3.3 shell tests.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T3.3
 *
 * Verify contract:
 *   `npx vitest run src/components/layout`
 *
 * Key assertion required by spec T3.3:
 *   "assert in a test that the nav item count equals getNavSections()'s total
 *   entry count, so a hardcoded second list fails"
 *
 * Additional assertions per AC1–AC6:
 *   AC1  <header id="h"> is rendered
 *   AC1  TopNavigation identity title is "Connected Services"
 *   AC1  VIN search input is present
 *   AC1  Pending-approvals badge utility is rendered
 *   AC2  Nav item count equals getNavSections() total entry count
 *   AC4  SimulatedDataBanner is rendered (non-dismissible: no dismiss button)
 *   AC5  PlaceholderPanel appears for placeholder-availability entries
 *   AC6  NotFound renders for unknown routes
 */

import { render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup } from "@testing-library/react";

import AppShell from "../AppShell";
import NotFound from "../NotFound";
import { SCREEN_REGISTRY, getNavSections } from "../../../screenRegistry";

// ---------------------------------------------------------------------------
// Helper — render AppShell at a given path via MemoryRouter
// ---------------------------------------------------------------------------

function renderShellAt(pathname: string, children?: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <Routes>
        <Route
          path="*"
          element={
            <AppShell>
              {children ?? (
                <div data-testid="shell-child">Shell child content</div>
              )}
            </AppShell>
          }
        />
      </Routes>
    </MemoryRouter>
  );
}

afterEach(cleanup);

// ---------------------------------------------------------------------------
// AC1 — header, identity, VIN search, pending-approvals badge
// ---------------------------------------------------------------------------

describe("AC1 — top bar", () => {
  it("renders <header id='h'>", () => {
    renderShellAt("/");
    const header = document.getElementById("h");
    expect(header, "<header id='h'> must be present").not.toBeNull();
    expect(header!.tagName.toLowerCase()).toBe("header");
  });

  it("renders TopNavigation identity title 'Connected Services'", () => {
    renderShellAt("/");
    // TopNavigation renders the identity title multiple times (virtual + real
    // variants for responsive sizing) — use getAllByText.
    const matches = screen.getAllByText("Connected Services");
    expect(matches.length).toBeGreaterThan(0);
  });

  it("renders the VIN search input", () => {
    renderShellAt("/");
    // TopNavigation duplicates the search slot for virtual + real variants.
    // Use getAllByTestId and confirm at least one is present.
    const searchInputs = screen.getAllByTestId("vin-search-input");
    expect(searchInputs.length).toBeGreaterThan(0);
  });

  it("renders the pending-approvals utility with badge text", () => {
    renderShellAt("/");
    // The utility button aria-label contains "pending approvals".
    // TopNavigation also duplicates utilities for virtual/real rendering.
    const pendingEls = screen.getAllByText(/pending/i);
    expect(pendingEls.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// AC2 — nav item count equals getNavSections() total entry count
// ---------------------------------------------------------------------------

describe("AC2 — nav item count matches registry (hardcoded second list would fail)", () => {
  it("total link items in <nav> equals the total entries across all sections", () => {
    renderShellAt("/connectivity/fleet-health");

    // Count the total entries across all seven sections.
    const expectedCount = getNavSections().reduce(
      (acc, section) => acc + section.entries.length,
      0
    );

    // All 23 entries are in the registry.
    expect(expectedCount).toBe(SCREEN_REGISTRY.length);

    // Count rendered nav links. SideNavigation renders each link as an <a>
    // inside the nav landmark. We query the nav landmark and count its links.
    // We use getAllByRole('navigation') → pick the SideNavigation one.
    // Use querySelector on the container because Cloudscape's nav has no
    // accessible name we can rely on.
    const navEl = document.querySelector("[data-testid='app-main-content']")
      ?.closest("[data-testid='app-layout']")
      // AppLayout does not surface a test-id for the nav pane; query the nav
      // landmark directly.
      ?? document.body;

    // Find all <a> elements inside the side-navigation.
    // Cloudscape's SideNavigation renders each link item as an <a>.
    const sideNavEl = document.querySelector("[data-testid='side-navigation']");
    expect(sideNavEl, "SideNavigation must be in the DOM").not.toBeNull();

    const navLinks = sideNavEl!.querySelectorAll("a");
    // The SideNavigation header also has an <a>; subtract it.
    // The header link text is "Connected Services" (the identity/header prop).
    const headerLinks = Array.from(navLinks).filter(
      (a) => a.textContent?.trim() === "Connected Services"
    );
    const screenLinks = navLinks.length - headerLinks.length;

    // The screen link count must equal the registry entry count.
    // A hardcoded second list would inflate this number above expectedCount.
    expect(
      screenLinks,
      `Nav link count (${screenLinks}) must equal registry entry count (${expectedCount}). ` +
        `A hardcoded second nav list would produce a higher count and fail this assertion.`
    ).toBe(expectedCount);
  });

  it("getNavSections() and SCREEN_REGISTRY are the sole source — no second registry", () => {
    // If a second nav list existed, the nav section count would exceed the registry's.
    // This test confirms the two counts are in sync.
    const sections = getNavSections();
    const totalFromSections = sections.reduce(
      (acc, s) => acc + s.entries.length,
      0
    );
    expect(totalFromSections).toBe(SCREEN_REGISTRY.length);
  });
});

// ---------------------------------------------------------------------------
// Simulated-data disclosure — amended 2026-09-05
//
// T3.3 AC4 originally required a non-dismissible SimulatedDataBanner inside the
// content area. It rendered on all 26 screens and, together with 227 per-value
// badges, made the portal unreadable, so the banner was removed 2026-09-05.
//
// A compact top-bar chip was added in its place. That was an UNREQUESTED deviation:
// the user's instruction was "remove all the simulated stuff … maybe on the login page
// one time", and a standing chip on all 26 screens is not that. It was flagged as such
// in decisions.md when added, and the user asked for it gone on 2026-09-07.
//
// So the disclosure now appears EXACTLY ONCE, on the sign-in screen (SignIn.tsx,
// data-testid="simulated-data-notice"), which is what was asked for. These tests pin
// that shape: nothing in the shell, and no chip in the top bar.
//
// Honesty of the data itself does not rest on any of this. It is carried by the
// provenance contract that stayed: ProvenanceValue<T>, assertProvenance()'s runtime
// throw, the fixture guard, the render guard, and the `-simulated` testid suffixes.
// The badge was always the least load-bearing part.
// ---------------------------------------------------------------------------

describe("simulated-data disclosure", () => {
  it("no longer renders the full-width banner inside the content area", () => {
    renderShellAt("/");
    expect(screen.queryByTestId("simulated-data-banner")).toBeNull();
  });

  it("does NOT put a standing simulated-data chip in the top bar", () => {
    renderShellAt("/");
    const header = document.querySelector("#h");
    expect(header, "top bar must render").toBeTruthy();
    expect(
      header?.textContent?.toLowerCase().includes("simulated data") ?? false,
      "the top bar must NOT carry a simulated-data chip — the disclosure belongs on " +
        "the sign-in screen only, per the user's 2026-09-07 instruction. An earlier " +
        "revision of this test asserted the opposite, encoding an unrequested " +
        "deviation as a requirement."
    ).toBe(false);
  });

  it("renders no dismissible controls in the top bar", () => {
    renderShellAt("/");
    const header = document.querySelector("#h");
    const dismissables = Array.from(header?.querySelectorAll("button") ?? []).filter(
      (btn) => btn.getAttribute("aria-label")?.toLowerCase().includes("dismiss") ?? false
    );
    expect(dismissables.length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// AC5 — PlaceholderPanel renders for placeholder entries
// ---------------------------------------------------------------------------

describe("AC5 — PlaceholderPanel for placeholder-availability screens", () => {
  it("renders PlaceholderPanel for the security-monitor placeholder route", () => {
    renderShellAt("/software/security");
    // PlaceholderPanel has data-testid="placeholder-panel"
    const panel = screen.getByTestId("placeholder-panel");
    expect(panel).toBeTruthy();
  });

  it("PlaceholderPanel carries the screen's settleMarker", () => {
    renderShellAt("/software/security");
    const securityEntry = SCREEN_REGISTRY.find(
      (e) => e.path === "/software/security"
    )!;
    // The settleMarker is rendered as text in a hidden span.
    expect(screen.getByText(securityEntry.settleMarker)).toBeTruthy();
  });

  it("names the screen label in the placeholder panel heading", () => {
    renderShellAt("/software/security");
    const securityEntry = SCREEN_REGISTRY.find(
      (e) => e.path === "/software/security"
    )!;
    // The panel heading is an h2 inside PlaceholderPanel.
    // (h1 is the PageHeader title; h2 is the PlaceholderPanel header)
    const heading = screen.getByRole("heading", {
      level: 2,
      name: securityEntry.label,
    });
    expect(heading).toBeTruthy();
  });

  it("does NOT render PlaceholderPanel for an active route", () => {
    renderShellAt("/connectivity/fleet-health");
    // PlaceholderPanel should not be rendered for active screens.
    const panel = document.querySelector("[data-testid='placeholder-panel']");
    expect(panel, "PlaceholderPanel must not appear for active routes").toBeNull();
  });

  it("renders children for active routes instead of PlaceholderPanel", () => {
    renderShellAt(
      "/connectivity/fleet-health",
      <div data-testid="active-child">Active screen content</div>
    );
    expect(screen.getByTestId("active-child")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// AC6 — NotFound renders standalone
// ---------------------------------------------------------------------------

describe("AC6 — NotFound", () => {
  it("renders the not-found view when mounted standalone", () => {
    render(
      <MemoryRouter initialEntries={["/totally-unknown-path"]}>
        <Routes>
          <Route path="*" element={<NotFound />} />
        </Routes>
      </MemoryRouter>
    );
    expect(screen.getByTestId("not-found-view")).toBeTruthy();
  });

  it("not-found view contains guidance text", () => {
    render(
      <MemoryRouter initialEntries={["/another-unknown"]}>
        <Routes>
          <Route path="*" element={<NotFound />} />
        </Routes>
      </MemoryRouter>
    );
    expect(screen.getByText(/page not found/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// AppLayout props — headerSelector, navigationWidth, toolsHide
// ---------------------------------------------------------------------------

describe("AppLayout wiring", () => {
  it("renders the AppLayout element", () => {
    renderShellAt("/");
    expect(screen.getByTestId("app-layout")).toBeTruthy();
  });

  it("renders <main> inside AppLayout content", () => {
    renderShellAt("/");
    expect(screen.getByTestId("app-main-content")).toBeTruthy();
  });

  it("PageHeader title is derived from pageConfig for the current path", () => {
    renderShellAt("/connectivity/fleet-health");
    // getPageConfig("/connectivity/fleet-health") returns title "Fleet Health"
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.textContent).toBe("Fleet Health");
  });

  it("Command Center path renders 'Command Center' as the page title", () => {
    renderShellAt("/");
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.textContent).toBe("Command Center");
  });
});
