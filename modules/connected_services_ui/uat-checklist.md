# Connected Services Portal v2 — UAT checklist

Written 2026-09-05 at the close of the v2 build. Spec:
`.kiro/specs/2026-09-04-cms-connected-services-portal-v2/`

> This file replaces an earlier auto-generated draft that cited routes which do not exist
> in this application (`/inventory`, `/command-center`, route ids `R001`/`R002`), a
> session-seed one-liner calling a function only available inside the test harness, and a
> Computed-styles command that returns inline styles and would always come back empty.
> Every route, testid and command below was read from source.

## Nothing was deployed

**No deploy happened.** The CS portal domain (`<cs-portal-domain>`) still serves the v1
bundle — two stub pages, not the 26 screens described here.

Sign-in is fully implemented but its callback origin is not registered on the shared
Cognito user-pool client, so a real Federate round-trip fails at the Hosted UI with a
`redirect_uri` mismatch. That was a deliberate stop, not an oversight: registering it
requires a `cms-staging-ui` deploy, and that stack currently carries 53 unrelated
resource changes from a concurrent session — a new `fleet-intelligence` API surface plus
removal of `AdminAddUserToGroup`/`AdminUpdateUserAttributes` from the provisioning role.
Shipping those as a side effect of a one-line auth change was not a call to make on
another session's behalf. See T9.3 in `tasks.md`.

**It unblocks itself.** `GUARD_CTX_FLAGS` already passes
`connectedServicesUiCallbackOrigin` on every CMS deploy, and `ui_stack.py` now consumes
it, so the next legitimate `cms-staging-ui` deploy registers the callback with no further
code change.

So everything below is **local** UAT, via `npm start`.

## Step 1 — start it and get a session

```bash
cd modules/connected_services_ui && npm start      # serves on :5178
```

You will land on the sign-in screen: `getSession()` finds no token. The Federate button
renders but goes nowhere locally, because the dev server's `/runtime-config.js` carries
deliberately non-functional placeholder credentials.

To walk the screens, paste this into the browser console and reload. It mints a token
with the shape `getSession()` requires — future `exp`, `email`, and the
`connected-services` group:

```javascript
(() => {
  const b64 = o => btoa(JSON.stringify(o)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=/g,'');
  const tok = b64({alg:'RS256',typ:'JWT'}) + '.' + b64({
    email: 'uat@example.com',
    'cognito:groups': ['connected-services'],
    exp: Math.floor(Date.now()/1000) + 86400
  }) + '.local-uat-not-a-real-signature';
  sessionStorage.setItem('idToken', tok);
  location.reload();
})()
```

This is a **local affordance only**. The signature is fake; it grants nothing anywhere
deployed. It exists because the real sign-in path cannot complete until the callback is
registered.

To test the fail-closed paths instead: `sessionStorage.clear()` → sign-in screen;
seed the same token with `exp` in the past → sign-in screen; change the group to
something else → explicit `Unauthorized` page, never a blank one.

## Step 2 — the DOM dump, please do this first

The one thing I cannot get without you. In DevTools console:

```javascript
document.querySelector('main').outerHTML
```

and then, with the page header selected in Elements, the **Computed** panel values for:

```javascript
getComputedStyle(document.querySelector('.cs-header'))
  .getPropertyValue('margin-left')   // repeat for margin-right, margin-top, padding
```

Use `getComputedStyle`, not `.style.cssText` — the latter returns only inline styles and
will be empty here, since these come from a stylesheet class.

**Why first rather than fifth.** `PageHeader.css` cancels `AppLayout`'s content gaps with
literal fallbacks — `calc(-1 * 24px)` horizontal, `calc(-1 * 12px)` top — because
Cloudscape hashes its CSS custom-property names per build, so DMS's verified token names
resolve to nothing at this module's version (`components@3.0.1354` vs DMS's `3.0.839`).
Nine rounds of reading source converged on nothing in DMS; one pasted Computed value
solved it in about a minute. If the banner bleeds into the nav, leaves a light strip
above itself, or the KPI cards do not overlap its lower edge, the pasted values are what
fixes it.

## Step 3 — the six-hop Goal-3 walk

This is the spec's primary demonstrable journey and the gate Group 4 was defined by. It
passes as an automated test through the real router; what you are checking is that it
also *reads* correctly to a human.

| Hop | Do this | Expect |
|---|---|---|
| 1 | From Command Center (`/`), click the degraded-connectivity tile | Fleet Health, **already filtered to degraded** (`?state=degraded`), reduced row count |
| 2 | Click the first VIN row | Subscriber Lookup detail for that VIN |
| 3 | Confirm the **"Why is this vehicle offline?"** root-cause panel is present, then click "View software history for this VIN" | Diagnosis Workbench, scoped to that VIN |
| 4 | Click the back control | Subscriber Lookup detail again |
| 5 | Click **Deny All Services**, type the VIN exactly to enable Confirm, confirm | Stays on Subscriber Lookup; a toast appears |
| 6 | Look at the Connectivity State card | Shows "Denied — all services" for that VIN |

At hop 5, check that Confirm stays **disabled** until the typed VIN matches exactly —
one character off must not enable it.

The denial is client-side state only. Reload and it is gone. That is by design for this
pass; nothing persists and no API is called.

## Step 4 — walk all 23 routes

Seven nav sections. Each screen should render real content with visible provenance
markers on displayed values — no blank panels, no "not built yet" text except where
noted.

| # | Section | Screen | Path | Expect |
|---|---|---|---|---|
| 1 | — | Command Center | `/` | 3×2 tile grid + cross-domain activity feed; every tile and feed item clicks through |
| 2 | Connectivity | Fleet Health | `/connectivity/fleet-health` | US/Germany/India summary cards over a filterable, sortable, paginated table |
| 3 | Connectivity | Subscriber Lookup | `/connectivity/subscriber-lookup` | Search field + recent lookups; a VIN opens the 4-card detail |
| 4 | Connectivity | Policy & Control | `/connectivity/policy` | APN / traffic priority / geo-fencing / QoS authoring. Geo-fencing is a named market-boundary list — **no map, no coordinates** |
| 5 | Connectivity | Rate Plans | `/connectivity/rate-plans` | Plan catalogue; the Self-Managed ↔ Amazon Managed toggle relabels **only** the billing-owner column |
| 6 | Software | Signal Detection | `/software/signals` | Card feed, newest first, each with confidence + `computed_at` + evidence chips |
| 7 | Software | Diagnosis Workbench | `/software/workbench` | Six-step stepper. **Approval is its own step**, not a modal, and needs a rationale before Approve enables |
| 8 | Software | Campaigns | `/software/campaigns` | Software + recall campaigns with a type badge; recall rows show **aggregate completion only** |
| 9 | Software | Rollout Monitor | `/software/rollout` | Staged progress canary→10%→50%→100%, fault-rate delta, halt threshold rendered as a number |
| 10 | Software | Security Monitor | `/software/security` | **Placeholder by decision** — states in words that R155 monitoring is not built. Correct, not a bug |
| 11 | Population Diagnostics | Fault Patterns | `/diagnostics/fault-patterns` | Population fault-signature table, rows click through to the Workbench |
| 12 | Population Diagnostics | Quality Signals | `/diagnostics/quality-signals` | Trend panel over pre-computed artifacts, showing confidence + `computed_at` |
| 13 | Manufacturing | Build & Order | `/manufacturing/build-order` | Generic production-sequencing table |
| 14 | Manufacturing | Factory Registration | `/manufacturing/factory-registration` | VIN form → generated ICCID/IMSI/profile preview, **each labelled simulated** → confirm appends to the log |
| 15 | Manufacturing | Homologation | `/manufacturing/homologation` | Per-market, per-variant type-approval status with RXSWIN references |
| 16 | Manufacturing | Ownership Transfer | `/manufacturing/ownership-transfer` | Transfer form with a subscription-carryover choice whose effect is stated in plain language |
| 17 | Manufacturing | Stop-Ship / Stop-Sale | `/manufacturing/stop-ship` | Criterion input with live match counts against **two** pools (production, in-transit — not dealer lots); Place Hold needs a named person **and** role |
| 18 | Sales | Feature Catalog | `/sales/feature-catalog` | Card grid; **toggling a feature visibly moves the revenue figure** — this is the most concrete interaction in the portal |
| 19 | Sales | Connectivity Plans | `/sales/connectivity-plans` | Subscription lifecycle (active/expired/pending renewal) + renewal trend; cross-links to Rate Plans |
| 20 | Sales | Data Products | `/sales/data-products` | Outbound telemetry feeds with **generic** partner labels and consent indicators |
| 21 | Compliance | Market Posture | `/compliance/market-posture` | Three market cards: sovereignty, roaming, homologation count, emissions, TCU tiers |
| 22 | Compliance | Consent & Privacy | `/compliance/consent` | Consent-state table + data-subject-request log. "location" is a consent **category name**, never a coordinate |
| 23 | Compliance | Audit Log | `/compliance/audit-log` | Reverse-chronological consequential actions. Reads the **same** event model as the Command Center feed |

Two legacy paths should redirect and preserve any query string: `/fleet-health` →
`/connectivity/fleet-health`, `/subscriber-lookup` → `/connectivity/subscriber-lookup`.

## What I did not verify, and cannot

- **Anything visual.** Per `agent-capabilities.md`, browser-rendered UAT needs you. Every
  layout, spacing and styling judgement in this build is unverified.
- **A real Federate round-trip.** Blocked on the callback registration above. What is
  verified is that the shipped bundle constructs the correct authorize URL and that
  `getSession()` fails closed on absent, expired and malformed tokens.
- **Deployed behaviour of any kind.** Nothing was deployed.

## Open by decision, not by omission

- `issues/2026-09-04-connected-services-no-auth-integration/` — the *stub* it describes is
  now resolved (Federate sign-in is implemented); what remains open is the callback
  registration, which is T9.3, not this issue.
- `issues/2026-09-04-connected-services-no-cloudfront-gate/` — the distribution has no
  edge auth gate and no WAF. Accepted deliberately as spec R1. Worth revisiting before any
  real backend is wired, since today the portal serves only fixtures.
