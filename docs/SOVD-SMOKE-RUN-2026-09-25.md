# SOVD smoke — run record, 2026-09-25 (Diagnostics tab redesign)

Executed by: **operator (browser) + architect (agent)**, against **staging**, us-west-2.
Playbook: `docs/SOVD-SMOKE-PLAYBOOK.md`. This run closes task 5.1 of spec
`2026-09-24-cms-diagnostics-tab-ia-redesign`, on the builds from its Fix Groups 2–5.
Vehicle: `VEH-MRDN-0015` / vin `MRDN0000000000015`. Task 5.1 names `VEH-MRDN-0001`; 0015 was
used because its simulation was the one running.

Supersedes `docs/SOVD-SMOKE-RUN-2026-09-24.md` for the Diagnostics tab and Step 6.

## Verdict

**PASS.** Every task 5.1 check passed in the browser, and the dispatch reached DMS with
the right evidence. The run found one defect, the **View RO** link, which was fixed and
re-checked the same day. It also found a DMS page that works but shows too little, which
is a follow-on tracked in DMS spec `2026-09-25-dms-fleet-ro-page`.

| Check | Mode | Outcome |
|---|---|---|
| Layout: actions first, one scan button, no "unknown", no health claim while loading | operator, browser | **PASS** |
| Full scan updates the status bar | operator + agent | **PASS**: `read_dtcs` 21:51:58Z SUCCEEDED |
| INERT result shown without hunting | operator, browser | **PASS**: `lamp_self_check` 21:52:11Z, `marginal` |
| A run survives opening and closing the session dialog | operator, browser | **PASS** |
| Three routines in one session count once and read Complete | operator + agent | **PASS**: session `59dd1d8c…`, 23:08:39–23:09:16Z. `lamp_self_check` `marginal`, `pack_isolation_test` `in_spec`, `cell_balance_check` `out_of_spec` (expected on 0015) |
| STATIONARY rows disabled; prohibition once per group; routines in columns | operator, browser | **PASS** (after FG4) |
| Dispatch evidence carries the fault record (FG3) | operator + agent | **PASS**: see Step 6 |
| Dispatched session shows its service order and is read-only | operator, browser | **PASS** |
| **View RO** opens the repair order | operator, browser | **FAIL, then PASS**: see below |

## Step 6: dispatch to DMS

Dispatched from session `15673155…` to `dealer-atlanta` at 21:53:20Z. The operator did not
open DMS for this step; the agent checked the store:

- RO `47c2b8e7-0352-4b25-9b09-5ce6d41605ed` in `dms-staging-repair-orders`: `initiated_by=cms_booking`, status Draft, VIN `MRDN0000000000015`.
- `evidence.sessionId` equals the CMS session. `routinesRun` is `[lamp_self_check]`.
- `evidence.dtcs` is six plain strings (P0562, P0001, P0299, P0217, B1234, P0420). That is exactly 0015's ACTIVE fault record, with none of 0001's codes (B0001_FIRE absent).
- The CMS command row carries `dispatched_ro_id` = that RO.

This is the first recorded end-to-end dispatch since the 2026-09-24 run found Step 6 blocked
(`issues/2026-09-24-cms-dispatch-posts-to-unrouted-dms-path/`).

## Defects and findings

**View RO opened CMS's own 404 page.** The link was the CMS-relative
`/service?ro_id=…`, CMS never received the DMS origin, and DMS had no page on which a
fleet user could open a repair order by id. Fixed the same day:

- CMS Fix Group 5 builds the link on `runtimeConfig.dmsUiOrigin`.
- `make regenerate-runtime-config` now writes that key.
- DMS spec `2026-09-25-dms-fleet-ro-page` adds `/fleet/repair-orders/:roId`.

After both deploys the link opened the repair order. Issue:
`issues/2026-09-25-diagnostics-view-ro-link-404/`.

**"Not Authorized" on the DMS page was a leftover persona emulation, not a defect.** The
operator's DMS header read **Emulating: dealer-admin**, stored in the browser from an earlier
session. Emulating a dealer role replaces a `platform-admin`'s groups, and dealer roles cannot
open fleet pages. Choosing **View as: platform-admin (real)** cleared it. The denial page does
not say an emulation is active, which made this hard to spot.

**The DMS page works but shows little.** Operator: the detail should open in a modal over the
vehicle's repair orders, as the Service Lane's detail does, and it lacks the diagnostic detail.
The fleet API's narrow projection omits the dispatch evidence. The operator approved adding
the evidence to the fleet projection. Dealer-internal fields (technician, advisor, labour,
customer) stay dealer-only. Tracked as the next group of `2026-09-25-dms-fleet-ro-page`. The
CMS link's URL does not change.
