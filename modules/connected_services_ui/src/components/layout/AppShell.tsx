// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * AppShell.tsx — top bar, seven-section side nav, simulated-data banner,
 * placeholder panel wrapper, and page chrome for the Connected Services portal.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md T3.3
 *
 * ## Layout contract
 *
 * <header id="h"> wraps TopNavigation and stays STICKY (not fixed), so
 * AppLayout's top edge naturally sits below it in flow.  AppLayout's
 * headerSelector="#h" reports the header height for sticky-scroll offsets; it
 * does NOT move AppLayout's top edge.  No spacer div is added or needed.
 *
 * ## Nav contract
 *
 * The side nav is built ENTIRELY from screenRegistry.getNavSections(). There is
 * no second nav list anywhere in the codebase. Placeholder entries appear in the
 * nav because spec D6 defines 'placeholder' as "renders in nav AND as a route"
 * (unlike DMS's 'deferred', which skips both).
 *
 * ## Constraints (from spec D9 / T3.3)
 *
 * - `#h` must be `position: sticky; top: 0` — see AppShell.css.
 * - `toolsHide` is always true — no tools panel.
 * - contentType is derived from the pathname so AppLayout can adjust spacing.
 * - PageHeader is rendered once, above children, using getPageConfig().
 */

import AppLayout, {
  AppLayoutProps,
} from "@cloudscape-design/components/app-layout";
import Input from "@cloudscape-design/components/input";
import SideNavigation, {
  SideNavigationProps,
} from "@cloudscape-design/components/side-navigation";
import TopNavigation from "@cloudscape-design/components/top-navigation";
import React, { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import "../../AppShell.css";

import { getNavSections, SCREEN_REGISTRY } from "../../screenRegistry";
import { getSession, signOut } from "../../auth";
import { getRuntimeConfig } from "../../env";
import PageHeader from "../commons/PageHeader";
import { getPageConfig } from "../commons/pageConfig";
import PlaceholderPanel from "../commons/PlaceholderPanel";

// ---------------------------------------------------------------------------
// Pending-approvals badge count
// ---------------------------------------------------------------------------

/**
 * Derive a pending-approvals count from the registry entries that correspond
 * to the two approval surfaces: stop-ship holds and software campaigns.
 * Currently a static simulated value — wired to the fixture screens once T4.1
 * and T5.3 land. A count of 0 does NOT suppress the badge; the badge is always
 * rendered per spec D5 (the Pending Approvals tile on Command Center carries
 * this count).
 */
const PENDING_APPROVALS_COUNT = 3; // simulated fixture value

// ---------------------------------------------------------------------------
// contentType map — AppLayout adjusts margins based on content shape
// ---------------------------------------------------------------------------

/** Derive the AppLayout contentType from the current pathname. */
function resolveContentType(pathname: string): AppLayoutProps.ContentType {
  // Table-heavy screens use 'table' to get tighter top/bottom spacing.
  const tablePaths = [
    "/connectivity/fleet-health",
    "/connectivity/subscriber-lookup",
    "/connectivity/rate-plans",
    "/software/campaigns",
    "/software/rollout",
    "/manufacturing/factory-registration",
    "/manufacturing/ownership-transfer",
    "/manufacturing/stop-ship",
    "/sales/feature-catalog",
    "/sales/connectivity-plans",
    "/sales/data-products",
    "/compliance/audit-log",
  ];
  if (tablePaths.includes(pathname) || pathname.startsWith("/connectivity/subscriber-lookup/")) {
    return "table";
  }
  // Dashboard-like overview screens.
  if (pathname === "/" || pathname === "/diagnostics/quality-signals") {
    return "dashboard";
  }
  return "default";
}

// ---------------------------------------------------------------------------
// Nav items — built entirely from the registry
// ---------------------------------------------------------------------------

/**
 * Convert registry sections into SideNavigation items.
 *
 * Each section becomes a `type: 'section'` item containing its entries as
 * `type: 'link'` items (tech.md § (c)).  The Command Center section has a
 * single entry (/), rendered as a top-level link rather than a section to
 * match the UX spec's nav treatment.
 */
function buildNavItems(): SideNavigationProps.Item[] {
  const sections = getNavSections();
  const items: SideNavigationProps.Item[] = [];

  for (const section of sections) {
    if (section.section === "command-center") {
      // Command Center is a single top-level link, not a collapsible section.
      for (const entry of section.entries) {
        items.push({ type: "link", text: entry.label, href: entry.path });
      }
    } else {
      items.push({
        type: "section",
        text: section.label,
        defaultExpanded: true,
        items: section.entries.map((entry) => ({
          type: "link" as const,
          text: entry.label,
          href: entry.path,
        })),
      });
    }
  }

  return items;
}

// Build once — the registry is static for this pass.
const NAV_ITEMS = buildNavItems();

// ---------------------------------------------------------------------------
// VIN search field
// ---------------------------------------------------------------------------

interface VinSearchFieldProps {
  onSearch: (vin: string) => void;
}

const VinSearchField: React.FC<VinSearchFieldProps> = ({ onSearch }) => {
  const [value, setValue] = useState("");

  const handleSubmit = (): void => {
    const trimmed = value.trim();
    if (trimmed) {
      onSearch(trimmed);
      setValue("");
    }
  };

  return (
    <Input
      value={value}
      onChange={({ detail }) => setValue(detail.value)}
      onKeyDown={({ detail }) => {
        if (detail.key === "Enter") handleSubmit();
      }}
      placeholder="Search by VIN…"
      ariaLabel="VIN search"
      data-testid="vin-search-input"
      type="search"
    />
  );
};

// ---------------------------------------------------------------------------
// AppShell
// ---------------------------------------------------------------------------

export interface AppShellProps {
  children: React.ReactNode;
}

const AppShell: React.FC<AppShellProps> = ({ children }) => {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [navigationOpen, setNavigationOpen] = useState(true);

  // Resolve page config from the current pathname.
  const pageConfig = getPageConfig(pathname);

  // Resolve the current registry entry (for placeholder detection).
  const currentEntry = SCREEN_REGISTRY.find((e) => {
    if (e.path === pathname) return true;
    // Resolve parameterised sub-routes.
    if (
      e.path === "/connectivity/subscriber-lookup" &&
      pathname.startsWith("/connectivity/subscriber-lookup/")
    )
      return true;
    if (
      e.path === "/software/workbench" &&
      pathname.startsWith("/software/workbench/")
    )
      return true;
    return false;
  });

  const isPlaceholder = currentEntry?.availability === "placeholder";

  // Navigate to subscriber-lookup detail for the entered VIN.
  const handleVinSearch = (vin: string): void => {
    void navigate(`/connectivity/subscriber-lookup/${encodeURIComponent(vin)}`);
  };

  return (
    <>
      {/* ── Top bar ─────────────────────────────────────────────────────── */}
      {/* #h must be sticky, not fixed — AppShell.css enforces this.
          No spacer div: sticky keeps the element in flow so AppLayout's top
          edge sits naturally below #h. */}
      <header id="h" data-testid="app-top-bar">
        <TopNavigation
          identity={{
            title: "Connected Services",
            href: "/",
            onFollow: (e) => {
              e.preventDefault();
              void navigate("/");
            },
          }}
          search={<VinSearchField onSearch={handleVinSearch} />}
          utilities={[
            {
              type: "button",
              text: `Pending (${PENDING_APPROVALS_COUNT})`,
              iconName: "notification",
              badge: PENDING_APPROVALS_COUNT > 0,
              ariaLabel: `${PENDING_APPROVALS_COUNT} pending approvals`,
              onClick: () => {
                void navigate("/manufacturing/stop-ship");
              },
            },
            {
              // Account menu carrying Sign out. Shape matches CMS's account dropdown
              // (`buildProfileItems.ts`): menu-dropdown, user-profile icon, email as the
              // trigger text, and Sign out carrying `lock-private`.
              //
              // NO PERSONA SWITCHER, deliberately — see decisions.md 2026-09-07. CMS
              // DELETED its persona switcher on 2026-09-02 (`buildProfileItems.ts` header,
              // `issues/2026-09-02-cms-persona-dropdown-removed/`) because it resolved
              // demo passwords from runtimeConfig and called login() — "a 'hop between
              // demo admin accounts' affordance no external visitor should see" once the
              // staging URL became publicly reachable. This portal became publicly
              // reachable on 2026-09-06, so that reasoning applies here directly.
              // DMS's switcher is a different, safe mechanism (credential-free role
              // emulation guarded on real platform-admin), but it needs roles to emulate:
              // this portal authorises on ONE group and has zero role-conditional
              // rendering across its 26 screens, so the control would change nothing.
              //
              // Only items with a real destination are listed. Preferences / Switch Theme
              // / Support are in CMS's dropdown because CMS has routes and URLs behind
              // them; adding them here would be dead menu items.
              //
              // `signOut()` itself shipped at T9.1 with ZERO callers — fully implemented,
              // unit-tested, and unreachable, so a signed-in user could not end their
              // session. Reported by the user 2026-09-07. An exported auth function with
              // no caller is not a feature, and no unit test can catch that.
              type: "menu-dropdown",
              text: getSession()?.alias ?? "Account",
              iconName: "user-profile",
              ariaLabel: "Account and sign out",
              items: [
                {
                  id: "signout",
                  text: "Sign out",
                  ariaLabel: "Sign out",
                  iconName: "lock-private",
                },
              ],
              onItemClick: ({ detail }) => {
                if (detail.id !== "signout") return;
                try {
                  // signOut() clears all five session keys, then redirects to the Hosted
                  // UI logout endpoint so the Cognito session is dropped too — otherwise
                  // Federate would silently re-authenticate on the next attempt and the
                  // user would appear never to have signed out.
                  signOut(getRuntimeConfig());
                } catch {
                  // Runtime config unavailable: still drop the local session and return to
                  // the sign-in screen. Fail toward signed-out rather than stranding the
                  // user in a session they cannot end.
                  sessionStorage.clear();
                  window.location.assign("/");
                }
              },
            },
          ]}
          i18nStrings={{
            overflowMenuTriggerText: "More",
            overflowMenuTitleText: "All",
          }}
        />
      </header>

      {/* ── AppLayout ────────────────────────────────────────────────────── */}
      <AppLayout
        data-testid="app-layout"
        headerSelector="#h"
        navigationWidth={280}
        toolsHide
        // `maxContentWidth` is DELIBERATELY UNSET — matching CMS's AppLayout config.
        //
        // It previously carried `Number.MAX_VALUE` to force the content panel edge-to-edge
        // (nav edge to scrollbar). That is a coherent look on its own, but it is mutually
        // exclusive with the CMS page-banner overlap, and the overlap is the look we want.
        //
        // Why they are mutually exclusive. AppLayout lays the page out as a CSS grid:
        //
        //   grid-template-columns: min-content
        //                          minmax(var(--awsui-content-gap-left), 1fr)
        //                          minmax(var(--awsui-min-content-width),
        //                                 var(--awsui-default-max-content-width))
        //                          minmax(var(--awsui-content-gap-right), 1fr)
        //                          min-content
        //
        // With `maxContentWidth` unset, the content column caps at Cloudscape's default
        // (1280px at >=1401px viewport for contentType default/dashboard; 100% for
        // table/cards), and the two `1fr` gap columns absorb everything left over —
        // 150-300px of empty space on each side on a wide monitor. `.cs-header`'s
        // full-bleed background spans that space, so when the banner pulls the next
        // element up into its padding band, dark banner is visible either side of that
        // element. THAT flank is what reads as the overlap.
        //
        // With `Number.MAX_VALUE`, the content column consumes the full width and the gap
        // columns collapse to their 24px minimum. The vertical pull still happens, but the
        // banner has a ~14px sliver to show in (Cloudscape's `Grid` spends 10px of it on
        // `margin-inline: calc(20px / -2)`), so it reads as "the banner is slightly
        // shorter" rather than as an overlap. Tuning the pull cannot fix that; the flank
        // IS the effect. See PageHeader.css § "The overlap band".
        //
        // Consequence to be aware of, and it is CMS's behaviour too: page width now varies
        // by `contentType`. `default`/`dashboard` screens get a capped centred column;
        // the 12 `table` screens in resolveContentType() stay full width.
        contentType={resolveContentType(pathname)}
        navigationOpen={navigationOpen}
        onNavigationChange={({ detail }) => setNavigationOpen(detail.open)}
        navigation={
          <SideNavigation
            data-testid="side-navigation"
            header={{ text: "Connected Services", href: "/" }}
            activeHref={pathname}
            items={NAV_ITEMS}
            onFollow={(e) => {
              e.preventDefault();
              void navigate(e.detail.href);
            }}
          />
        }
        content={
          <main data-testid="app-main-content">
            {/* Simulated-data notice removed from here 2026-09-05. It rendered on
                every one of the 26 screens; the statement now appears once on the
                sign-in screen, plus a compact top-bar indicator. See decisions.md. */}

            {/* ── Page chrome (breadcrumbs + title + description) ────────── */}
            <PageHeader
              title={pageConfig.title}
              description={pageConfig.description}
              breadcrumbs={pageConfig.breadcrumbs}
              onBreadcrumbFollow={(e) => {
                e.preventDefault();
                void navigate(e.detail.href);
              }}
            />

            {/* ── Screen content ────────────────────────────────────────── */}
            {/*
              Wrapped in a named plain block, deliberately, for two reasons.

              1. The banner's overlap rule used to select `.cs-header + *`, which
                 resolves to whatever element each screen happens to root with —
                 a Cloudscape SpaceBetween today, but that is a per-screen
                 implementation detail no rule should depend on. `.cs-page-body`
                 is a contract: exactly one element, always here, always a block.
              2. It matches CMS. CMS's AppLayout `content` is a plain <div>
                 holding the page header and then the routed view, so its
                 banner's negative bottom margin always adjoins an ordinary
                 block. CS screens root in SpaceBetween, so the same rule was
                 adjoining a flex container instead. Same shape now.

              Keep this a bare block: no SpaceBetween, no padding, no display
              change. Anything with its own box model here re-opens the question
              of what the banner is actually pulling up.
            */}
            <div className="cs-page-body">
              {isPlaceholder && currentEntry ? (
                <PlaceholderPanel
                  screenLabel={currentEntry.label}
                  settleMarker={currentEntry.settleMarker}
                />
              ) : (
                children
              )}
            </div>
          </main>
        }
      />
    </>
  );
};

export default AppShell;
