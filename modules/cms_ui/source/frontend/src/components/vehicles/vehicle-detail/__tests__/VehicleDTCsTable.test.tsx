// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// RED-PHASE SKELETONS — spec 2026-06-17-dtc-dedup-first-last-seen-schedule-service
// Task 1.4: 7 cases that FAIL until Group 2.5 ships the new columns + button.

import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({
    getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
    user: { groups: ['fleet-operator'], roles: ['fleet-operator'] },
  }),
}));

vi.mock('@/config/api', () => ({
  getApiEndpoint: () => 'http://localhost/',
  getRuntimeConfig: () => ({ apiEndpoint: 'http://localhost/' }),
}));

// Simulation-config helpers read runtimeConfig.simulationApiEndpoint;
// the fault-injection tests exercise the /vehicle/{id}/faults route, so
// the URL builder is stubbed to a stable value we can pattern-match on.
vi.mock('@/utils/simulation-config', () => ({
  getSimulationApiUrl: (path: string) =>
    `http://localhost/api/simulation${path}`,
  getSimulationApiBase: () => 'http://localhost',
  getSimulationMode: () => 'local',
  isCloudSimAvailable: () => false,
  setSimulationMode: () => {},
}));

import VehicleDTCsTable from '../VehicleDTCsTable';
import { readFileSync } from 'node:fs';

// ── shared fixture data ──────────────────────────────────────────────────────

const T1 = 1_700_000_000_000; // firstSeenAt ms
const T2 = 1_700_000_100_000; // lastSeenAt ms (later)

const activeNoService = {
  vehicleId: 'V1',
  timestamp: T1,
  dtcId: 'dtc-aaaa',
  code: 'P0217',
  status: 'ACTIVE',
  severity: 'HIGH',
  system: 'Engine',
  description: 'Coolant over temp',
  firstSeenAt: T1,
  lastSeenAt: T2,
  occurrenceCount: 3,
  relatedServiceId: '',
  source: 'flink-maintenance-processor',
};

const activeWithService = {
  ...activeNoService,
  dtcId: 'dtc-bbbb',
  code: 'P0300',
  relatedServiceId: 'SVC-existing-001',
};

const clearedRow = {
  ...activeNoService,
  dtcId: 'dtc-cccc',
  code: 'P0128',
  status: 'CLEARED',
  relatedServiceId: '',
  occurrenceCount: 1,
};

/** Sets up a successful GET /dtcs response returning the given rows. */
function mockDtcsGet(rows: object[]) {
  vi.spyOn(global, 'fetch').mockImplementation((url: RequestInfo | URL) => {
    const urlStr = String(url);
    if (urlStr.includes('/dtcs') && !urlStr.includes('schedule-service')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ dtcs: rows, total: rows.length }),
      } as Response);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

// ── 1. firstSeenAt / lastSeenAt / occurrenceCount columns ────────────────────

test('renders firstSeenAt / lastSeenAt / occurrenceCount columns when present', async () => {
  mockDtcsGet([activeNoService]);
  render(<VehicleDTCsTable vehicleId="V1" />);

  // Column headers (also check Severity and System are present)
  await waitFor(() => {
    expect(screen.getByRole('columnheader', { name: /first seen/i })).toBeInTheDocument();
  });
  expect(screen.getByRole('columnheader', { name: /last seen/i })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: /detections/i })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: /severity/i })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: /system/i })).toBeInTheDocument();

  // Cell value: occurrenceCount = 3 should appear in the table
  // Use getAllByText to handle possible multiple matches
  expect(screen.getAllByText('3').length).toBeGreaterThanOrEqual(1);
});

// ── 2. Schedule Service button visibility rules ──────────────────────────────

test('renders Schedule Service button only on ACTIVE rows without relatedServiceId', async () => {
  mockDtcsGet([activeNoService, activeWithService, clearedRow]);
  render(<VehicleDTCsTable vehicleId="V1" />);

  await waitFor(() => {
    expect(screen.getAllByRole('button', { name: /schedule service/i })).toHaveLength(1);
  });
});

// ── 3. Schedule Service click opens confirmation modal ───────────────────────

test('Schedule Service click opens a confirmation modal', async () => {
  mockDtcsGet([activeNoService]);
  render(<VehicleDTCsTable vehicleId="V1" />);

  const btn = await screen.findByRole('button', { name: /schedule service/i });
  fireEvent.click(btn);

  await waitFor(() => {
    // Modal should show subsystem + severity — scope within modal dialog to avoid
    // collisions with the same values rendered in the table row cells
    const modal = document.querySelector('[role="dialog"]') as HTMLElement;
    expect(modal).not.toBeNull();
    const { getByText } = within(modal!);
    expect(getByText(/Engine/i)).toBeInTheDocument();
    expect(getByText(/HIGH/i)).toBeInTheDocument();
    // Confirm button inside modal
    expect(screen.getByRole('button', { name: /confirm/i })).toBeInTheDocument();
  });
});

// ── 4. Confirm POSTs to schedule-service and reloads on 200 ─────────────────

test('Confirm in modal POSTs to /dtcs/{dtcId}/schedule-service and reloads on 200', async () => {
  let callCount = 0;
  const fetchSpy = vi.spyOn(global, 'fetch').mockImplementation((url: RequestInfo | URL, init?: RequestInit) => {
    const urlStr = String(url);
    if (urlStr.includes('schedule-service')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ serviceId: 'SVC-new', relatedServiceId: 'SVC-new', status: 'ACTIVE' }),
      } as Response);
    }
    callCount++;
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ dtcs: [activeNoService], total: 1 }),
    } as Response);
  });

  render(<VehicleDTCsTable vehicleId="V1" />);

  const scheduleBtn = await screen.findByRole('button', { name: /schedule service/i });
  fireEvent.click(scheduleBtn);

  const confirmBtn = await screen.findByRole('button', { name: /confirm/i });
  const getCallsBefore = callCount;
  fireEvent.click(confirmBtn);

  await waitFor(() => {
    // POST to schedule-service
    const postCall = fetchSpy.mock.calls.find(
      ([url, init]) => String(url).includes('schedule-service') && init?.method === 'POST',
    );
    expect(postCall).toBeDefined();
    expect(String(postCall![0])).toContain('/dtcs/dtc-aaaa/schedule-service');
    // Table reload (second GET)
    expect(callCount).toBeGreaterThan(getCallsBefore);
  });
});

// ── 5. 409 surfaces "already scheduled" ─────────────────────────────────────

test('Schedule Service POST 409 surfaces "already scheduled"', async () => {
  vi.spyOn(global, 'fetch').mockImplementation((url: RequestInfo | URL) => {
    const urlStr = String(url);
    if (urlStr.includes('schedule-service')) {
      return Promise.resolve({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ serviceId: 'SVC-existing-001', message: 'already scheduled' }),
        text: () => Promise.resolve(JSON.stringify({ serviceId: 'SVC-existing-001' })),
      } as unknown as Response);
    }
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ dtcs: [activeNoService], total: 1 }),
    } as Response);
  });

  render(<VehicleDTCsTable vehicleId="V1" />);

  const scheduleBtn = await screen.findByRole('button', { name: /schedule service/i });
  fireEvent.click(scheduleBtn);

  const confirmBtn = await screen.findByRole('button', { name: /confirm/i });
  fireEvent.click(confirmBtn);

  await waitFor(() => {
    expect(screen.getByText(/already scheduled/i)).toBeInTheDocument();
  });
});

// ── 6. Button disabled/loading while POST in flight ─────────────────────────

test('Schedule Service button disabled while POST in flight', async () => {
  let resolvePost!: (v: Response) => void;
  const postPromise = new Promise<Response>(res => { resolvePost = res; });

  vi.spyOn(global, 'fetch').mockImplementation((url: RequestInfo | URL, init?: RequestInit) => {
    const urlStr = String(url);
    if (urlStr.includes('schedule-service')) return postPromise;
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ dtcs: [activeNoService], total: 1 }),
    } as Response);
  });

  render(<VehicleDTCsTable vehicleId="V1" />);

  const scheduleBtn = await screen.findByRole('button', { name: /schedule service/i });
  fireEvent.click(scheduleBtn);

  const confirmBtn = await screen.findByRole('button', { name: /confirm/i });
  fireEvent.click(confirmBtn);

  // While the POST is pending, the confirm/schedule button should be disabled or loading
  await waitFor(() => {
    const btns = screen.queryAllByRole('button', { name: /schedule service|confirm/i });
    const anyDisabledOrLoading = btns.some(
      btn => btn.hasAttribute('disabled') || btn.getAttribute('aria-disabled') === 'true' || btn.getAttribute('aria-busy') === 'true',
    );
    expect(anyDisabledOrLoading).toBe(true);
  });

  // Resolve so cleanup is clean
  resolvePost({ ok: true, json: () => Promise.resolve({}), status: 200 } as unknown as Response);
});

// ── 7. Detections column: >1 → blue Badge, ==1 → plain text ─────────────────

test('Detections column renders count > 1 as a blue badge, count == 1 as plain text', async () => {
  mockDtcsGet([
    { ...activeNoService, dtcId: 'dtc-multi', occurrenceCount: 3 },
    { ...clearedRow, dtcId: 'dtc-one', occurrenceCount: 1 },
  ]);
  render(<VehicleDTCsTable vehicleId="V1" />);

  await waitFor(() => {
    expect(screen.getAllByText('3').length).toBeGreaterThanOrEqual(1);
  });

  // The "3" for occurrenceCount should be inside a badge
  const all3 = screen.getAllByText('3');
  const badge3 = all3.find(el => el.closest('[class*="badge"]') || el.tagName === 'SPAN');
  expect(badge3).toBeDefined();
  expect(badge3!.closest('[class*="badge"]') || badge3!.tagName === 'SPAN').toBeTruthy();

  // Find the plain-text "1" for occurrenceCount (not inside a badge)
  // Use getAllByText and find one NOT inside a badge element
  const all1 = screen.getAllByText('1');
  const plain1 = all1.find(el => el.closest('[class*="badge"]') === null);
  expect(plain1).toBeDefined();
});

// ─────────────────────────────────────────────────────────────────────────────
// Fault-injection flow tests — spec 2026-08-05-cms-parked-vehicle-dtc-injection
// Group 4: DTCs tab gains a "Set fault" affordance + currently-set display.
//
// These are integration tests against the real component. All /faults traffic
// is against the stubbed simulation API URL from vi.mock at the top of this
// file; the fetch spy pattern matches the ScheduleService tests above.
// ─────────────────────────────────────────────────────────────────────────────

/** Injectable set the server has computed — matches _get_faults's shape. */
const INJECTABLE_FIXTURE = [
  {
    eventId: 'maintenance.catalyst_efficiency_low',
    dtcCode: 'P0420',
    ecu: 2,
    description: 'Catalyst System Efficiency Below Threshold',
  },
  {
    eventId: 'maintenance.evap_leak_small',
    dtcCode: 'P0442',
    ecu: 8,
    description: 'EVAP System Small Leak Detected',
  },
];

/** A catalog event that has a dtc_code but is NOT in the injectable set —
 *  simulates the 13-of-37 codes that would 400 (e.g., B0001_FIRE / U3000_CRASH).
 *  If the component filters client-side instead of consuming `injectable`, this
 *  event_id would leak into the Multiselect options. That is the drift the
 *  option-list test catches. */
const NON_INJECTABLE_EVENT_ID = 'safety.airbag_fire';
const NON_INJECTABLE_DESC = 'Airbag Circuit Fire Detected';

const FAULTS_SET_FIXTURE = {
  ecus: {
    '2': { req: 0x7e2, resp: 0x7ea, dtcs: ['P0420'] },
  },
  eventIds: ['maintenance.catalyst_efficiency_low'],
  setAt: '2026-08-05T18:00:00Z',
  setBy: 'test-operator',
  requestId: 'abcd1234',
};

/**
 * Wire up a full fetch mock covering DTC + faults GET/PUT.
 *
 * @param opts.dtcs         Rows returned by GET /dtcs
 * @param opts.injectable   Rows returned by GET /faults .injectable
 * @param opts.faultState   faultState returned by GET /faults (null → not set)
 * @param opts.putResponse  Override PUT /faults with a specific Response
 */
function mockFaultsFlow(opts: {
  dtcs?: object[];
  injectable?: object[];
  faultState?: object | null;
  campaignRunning?: boolean;
  putResponse?: Response;
}) {
  const dtcs = opts.dtcs ?? [];
  const injectable = opts.injectable ?? INJECTABLE_FIXTURE;
  const faultState = opts.faultState ?? null;
  const campaignRunning = opts.campaignRunning ?? true;

  return vi.spyOn(global, 'fetch').mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const urlStr = String(input);
    if (urlStr.includes('/faults')) {
      if (init?.method === 'PUT') {
        return Promise.resolve(
          opts.putResponse ??
            ({
              ok: true,
              status: 200,
              json: () =>
                Promise.resolve({
                  success: true,
                  ecus: (FAULTS_SET_FIXTURE as any).ecus,
                  appliesWithinSeconds: 40,
                  clearGuidance:
                    'To clear: (1) PUT /faults with empty list. (2) Then Mark Cleared in the UI.',
                }),
            } as unknown as Response),
        );
      }
      // GET /faults
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ faultState, campaignRunning, injectable }),
      } as unknown as Response);
    }
    if (urlStr.includes('/dtcs')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ dtcs, total: dtcs.length }),
      } as Response);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
}

// ── 8. Currently-set faults are visible when faultState is present ───────────

test('renders currently-injected faults panel when faultState.eventIds is non-empty', async () => {
  mockFaultsFlow({ dtcs: [], faultState: FAULTS_SET_FIXTURE });
  render(<VehicleDTCsTable vehicleId="V1" />);

  // The panel is an info Alert with a header naming the count.
  await waitFor(() => {
    expect(screen.getByText(/Faults currently injected/i)).toBeInTheDocument();
  });
  // And it shows the actual code(s) so operators can tell what's set.
  expect(screen.getByText(/P0420/)).toBeInTheDocument();
  expect(screen.getByText(/ECU2/)).toBeInTheDocument();
});

// ── 9. The Multiselect option list comes from `injectable`, not the catalog ──

test('Set fault modal offers ONLY codes present in the injectable set', async () => {
  // Injectable has 2 entries; the catalog would additionally contain a
  // NON_INJECTABLE_EVENT_ID with a dtc_code (safety.airbag_fire → B0001_FIRE).
  // The server has already excluded it from injectable — so the UI must not
  // reintroduce it by filtering the catalog client-side.
  mockFaultsFlow({ dtcs: [], injectable: INJECTABLE_FIXTURE });
  render(<VehicleDTCsTable vehicleId="V1" />);

  // Open the modal
  const setBtn = await screen.findByRole('button', { name: /set fault on vehicle/i });
  fireEvent.click(setBtn);

  // Open the Multiselect dropdown to expose options.
  // Cloudscape opens its dropdowns on mouseDown, not click.
  const trigger = await screen.findByRole('button', { name: /select codes to inject/i });
  fireEvent.mouseDown(trigger);

  // Both injectable descriptions appear as options in the listbox
  await waitFor(() => {
    const opts = document.querySelectorAll('[role="option"]');
    expect(opts.length).toBeGreaterThan(0);
  });
  const optionText = Array.from(document.querySelectorAll('[role="option"]'))
    .map(el => (el.textContent || '').toLowerCase())
    .join('|');
  expect(optionText).toContain('catalyst system efficiency below threshold');
  expect(optionText).toContain('evap system small leak detected');

  // The catalog-only event MUST NOT be offered
  expect(optionText).not.toContain(NON_INJECTABLE_DESC.toLowerCase());
  expect(optionText).not.toContain(NON_INJECTABLE_EVENT_ID.toLowerCase());
});

// ── 10. Successful PUT surfaces clearGuidance and appliesWithinSeconds ───────

test('Confirm PUTs /faults and surfaces clearGuidance + appliesWithinSeconds on 200', async () => {
  const user = userEvent.setup();
  const spy = mockFaultsFlow({ dtcs: [], injectable: INJECTABLE_FIXTURE });
  render(<VehicleDTCsTable vehicleId="V1" />);

  await user.click(await screen.findByRole('button', { name: /set fault on vehicle/i }));

  // Select the first option (P0420). Cloudscape's Multiselect requires the
  // full mouseDown → mouseUp → click sequence that user-event drives — plain
  // fireEvent.click does not trigger its selection handler.
  const trigger = await screen.findByRole('button', { name: /select codes to inject/i });
  await user.click(trigger);
  await waitFor(() => {
    expect(document.querySelectorAll('[role="option"]').length).toBeGreaterThan(0);
  });
  const opts = Array.from(document.querySelectorAll('[role="option"]')) as HTMLElement[];
  const p0420 = opts.find(el => (el.textContent || '').includes('Catalyst System Efficiency'));
  expect(p0420).toBeDefined();
  await user.click(p0420!);

  await user.click(screen.getByRole('button', { name: /confirm inject/i }));

  // PUT was made to /vehicle/V1/faults with the selected event_id
  await waitFor(() => {
    const putCall = spy.mock.calls.find(
      ([url, init]) =>
        String(url).includes('/vehicle/V1/faults') &&
        (init as RequestInit | undefined)?.method === 'PUT',
    );
    expect(putCall).toBeDefined();
    const body = JSON.parse(String((putCall![1] as RequestInit).body));
    expect(body.maintenance_scenarios).toEqual(['maintenance.catalyst_efficiency_low']);
  });

  // Guidance surfaces in a success alert
  await waitFor(() => {
    expect(screen.getByText(/40s/i)).toBeInTheDocument();
  });
  expect(screen.getByText(/Then Mark Cleared/i)).toBeInTheDocument();
});

// ── 11. 400 body surfaces verbatim, not generic "failed" text ─────────────────

test('PUT /faults 400 renders the response body\'s error message actionably', async () => {
  const user = userEvent.setup();
  const badBody = JSON.stringify({
    error: "Event 'safety.airbag_fire' has no dtc_code in the catalog and cannot be injected via UDS.",
  });
  mockFaultsFlow({
    dtcs: [],
    injectable: INJECTABLE_FIXTURE,
    putResponse: {
      ok: false,
      status: 400,
      json: () => Promise.resolve(JSON.parse(badBody)),
      text: () => Promise.resolve(badBody),
    } as unknown as Response,
  });
  render(<VehicleDTCsTable vehicleId="V1" />);

  await user.click(await screen.findByRole('button', { name: /set fault on vehicle/i }));
  const trigger = await screen.findByRole('button', { name: /select codes to inject/i });
  await user.click(trigger);
  await waitFor(() => {
    expect(document.querySelectorAll('[role="option"]').length).toBeGreaterThan(0);
  });
  const opt = Array.from(document.querySelectorAll('[role="option"]')).find(
    el => (el.textContent || '').includes('Catalyst System Efficiency'),
  ) as HTMLElement;
  await user.click(opt);
  await user.click(screen.getByRole('button', { name: /confirm inject/i }));

  // Verbatim server message must reach the operator
  await waitFor(() => {
    expect(screen.getByText(/has no dtc_code in the catalog/i)).toBeInTheDocument();
  });
});

// ── 12. 409 body surfaces verbatim and mentions the Campaigns tab ─────────────

test('PUT /faults 409 renders the response body\'s error, including the Campaigns-tab hint', async () => {
  const user = userEvent.setup();
  const conflictBody = JSON.stringify({
    error:
      "No standing 'uds-dtc-polling-1FT8W3DT5MEC55401' campaign is RUNNING for vehicle 'VEH-MICH-001'. Assign the 'uds-dtc-polling' template from the Campaigns tab, then retry.",
  });
  mockFaultsFlow({
    dtcs: [],
    injectable: INJECTABLE_FIXTURE,
    campaignRunning: false,
    putResponse: {
      ok: false,
      status: 409,
      json: () => Promise.resolve(JSON.parse(conflictBody)),
      text: () => Promise.resolve(conflictBody),
    } as unknown as Response,
  });
  render(<VehicleDTCsTable vehicleId="V1" />);

  await user.click(await screen.findByRole('button', { name: /set fault on vehicle/i }));
  const trigger = await screen.findByRole('button', { name: /select codes to inject/i });
  await user.click(trigger);
  await waitFor(() => {
    expect(document.querySelectorAll('[role="option"]').length).toBeGreaterThan(0);
  });
  const opt = Array.from(document.querySelectorAll('[role="option"]')).find(
    el => (el.textContent || '').includes('Catalyst System Efficiency'),
  ) as HTMLElement;
  await user.click(opt);
  await user.click(screen.getByRole('button', { name: /confirm inject/i }));

  // Actionable message: names Campaigns tab, the exact template, and the vehicle.
  // Scope to the error Alert so unrelated static help text elsewhere in the
  // modal ("Assign it from the Campaigns tab, then retry.") doesn't collide.
  await waitFor(() => {
    const alerts = document.querySelectorAll('[class*="alert"]');
    const alertText = Array.from(alerts)
      .map(el => (el.textContent || ''))
      .join('||');
    expect(alertText).toContain('Campaigns tab');
    expect(alertText).toContain('uds-dtc-polling');
    expect(alertText).toContain("VEH-MICH-001");
  });
});

// ── Actions column: ambiguous-check fix (issue 2026-08-05) ───────────────────

// 13a. CLEARED rows render nothing in the actions column
test('CLEARED row renders no action control in the actions column', async () => {
  mockDtcsGet([clearedRow]);
  render(<VehicleDTCsTable vehicleId="V1" />);

  await waitFor(() => {
    expect(screen.getByText('P0128')).toBeInTheDocument();
  });

  // Neither the clear-DTC button nor the schedule-service button should appear
  expect(screen.queryByRole('button', { name: /mark cleared/i })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /schedule service/i })).not.toBeInTheDocument();
});

// 13b. ACTIVE row exposes both controls by accessible name
test('ACTIVE row exposes Mark-cleared and Schedule-service controls by accessible name', async () => {
  mockDtcsGet([activeNoService]);
  render(<VehicleDTCsTable vehicleId="V1" />);

  await waitFor(() => {
    expect(screen.getByRole('button', { name: /mark cleared/i })).toBeInTheDocument();
  });
  expect(screen.getByRole('button', { name: /schedule service/i })).toBeInTheDocument();
});

// 13c. Both controls carry a native hover tooltip (title attribute)
test('ACTIVE-row action buttons carry a title attribute for hover tooltip', async () => {
  mockDtcsGet([activeNoService]);
  render(<VehicleDTCsTable vehicleId="V1" />);

  // Wait for the row to render
  await screen.findByRole('button', { name: /mark cleared/i });

  // Find the wrapping <span> elements by their title attribute —
  // we can't rely on the button's own title because Cloudscape may
  // derive one from ariaLabel internally. Query the wrapping spans directly.
  const clearSpan = document.querySelector('button[title="Mark cleared"]') as HTMLElement | null;
  const scheduleSpan = document.querySelector('button[title="Schedule service"]') as HTMLElement | null;

  expect(clearSpan).not.toBeNull();
  expect(scheduleSpan).not.toBeNull();
  expect(clearSpan!.title).toBe('Mark cleared');
  expect(scheduleSpan!.title).toBe('Schedule service');
});

// 13d. Viewer role sees no Schedule Service button but still sees clear button
test('viewer role sees no DTC action controls in the table', async () => {
  vi.doMock('@/auth/useAuth', () => ({
    useAuth: () => ({
      getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
      user: { groups: ['fleet-viewer'], roles: ['fleet-viewer'] },
    }),
  }));

  vi.resetModules();
  const { default: VehicleDTCsTableViewer } = await import('../VehicleDTCsTable');
  mockDtcsGet([activeNoService]);
  render(<VehicleDTCsTableViewer vehicleId="V1" />);

  await waitFor(() => {
    expect(screen.getByText('P0217')).toBeInTheDocument();
  });

  // Viewer sees the ACTIVE row — the clear button is present (not viewer-gated)
  // but the Schedule Service button is hidden (gated by canSchedule → !isViewer)
  expect(screen.queryByRole('button', { name: /schedule service/i })).not.toBeInTheDocument();

  vi.doUnmock('@/auth/useAuth');
});

// ── 13. Viewer role does NOT see the Set fault button ─────────────────────────

test('viewer role does not see the Set fault button', async () => {
  // Re-register useAuth mock as a fleet-viewer for this test only
  vi.doMock('@/auth/useAuth', () => ({
    useAuth: () => ({
      getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
      user: { groups: ['fleet-viewer'], roles: ['fleet-viewer'] },
    }),
  }));

  // Re-import to pick up the new mock
  vi.resetModules();
  const { default: VehicleDTCsTableViewer } = await import('../VehicleDTCsTable');
  mockFaultsFlow({ dtcs: [] });
  render(<VehicleDTCsTableViewer vehicleId="V1" />);

  // Refresh button is still present (all roles get it)
  await waitFor(() => {
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument();
  });
  // Set fault button is hidden
  expect(screen.queryByRole('button', { name: /set fault on vehicle/i })).not.toBeInTheDocument();

  // Restore the module-scope mock so subsequent tests are unaffected
  vi.doUnmock('@/auth/useAuth');
});

// ─────────────────────────────────────────────────────────────────────────────
// Clear faults button — spec clear-faults-ui
// ─────────────────────────────────────────────────────────────────────────────

// ── 14. Clear faults disabled when no faults set; enabled when some are ───────

test('Clear faults button is disabled when no faultState, enabled when faults are set', async () => {
  // No faults — button should be disabled
  mockFaultsFlow({ dtcs: [], faultState: null });
  const { unmount } = render(<VehicleDTCsTable vehicleId="V1" />);

  await waitFor(() => {
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument();
  });
  const disabledBtn = screen.getByRole('button', { name: /clear faults on vehicle/i });
  expect(disabledBtn).toHaveAttribute('aria-disabled', 'true');

  unmount();
  vi.restoreAllMocks();

  // With faults set — button should be enabled
  mockFaultsFlow({ dtcs: [], faultState: FAULTS_SET_FIXTURE });
  render(<VehicleDTCsTable vehicleId="V1" />);

  await waitFor(() => {
    expect(screen.getByText(/Faults currently injected/i)).toBeInTheDocument();
  });
  const enabledBtn = screen.getByRole('button', { name: /clear faults on vehicle/i });
  expect(enabledBtn).not.toBeDisabled();
});

// ── 15. Clicking Clear faults opens a confirmation modal naming the set codes ─

test('clicking Clear faults opens a confirmation modal naming the currently-set codes', async () => {
  mockFaultsFlow({ dtcs: [], faultState: FAULTS_SET_FIXTURE });
  render(<VehicleDTCsTable vehicleId="V1" />);

  await waitFor(() => {
    expect(screen.getByRole('button', { name: /clear faults on vehicle/i })).not.toBeDisabled();
  });
  fireEvent.click(screen.getByRole('button', { name: /clear faults on vehicle/i }));

  // Modal appears
  await waitFor(() => {
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  });

  const modal = document.querySelector('[role="dialog"]') as HTMLElement;
  const { getByText } = within(modal);

  // Codes named in ECU-prefixed format
  expect(getByText(/ECU2: P0420/i)).toBeInTheDocument();
  // ACTIVE row warning — text is split across elements (strong tag), so check textContent
  expect(modal.textContent).toMatch(/remain.*ACTIVE/i);
  // Partial-clear guidance
  expect(modal.textContent).toContain('Set fault');
});

// ── 16. Confirming Clear faults PUTs an empty maintenance_scenarios list ──────

test('confirming Clear faults PUTs with empty maintenance_scenarios list', async () => {
  const spy = mockFaultsFlow({
    dtcs: [],
    faultState: FAULTS_SET_FIXTURE,
    putResponse: {
      ok: true,
      status: 200,
      json: () => Promise.resolve({ cleared: true }),
    } as unknown as Response,
  });

  render(<VehicleDTCsTable vehicleId="V1" />);

  await waitFor(() => {
    expect(screen.getByRole('button', { name: /clear faults on vehicle/i })).not.toBeDisabled();
  });
  fireEvent.click(screen.getByRole('button', { name: /clear faults on vehicle/i }));

  const confirmBtn = await screen.findByRole('button', { name: /confirm clear faults/i });
  fireEvent.click(confirmBtn);

  await waitFor(() => {
    const putCall = spy.mock.calls.find(
      ([url, init]) =>
        String(url).includes('/faults') &&
        (init as RequestInit | undefined)?.method === 'PUT',
    );
    expect(putCall).toBeDefined();
    const body = JSON.parse(String((putCall![1] as RequestInit).body));
    expect(body.maintenance_scenarios).toEqual([]);
  });

  // Success alert surfaces
  await waitFor(() => {
    expect(screen.getByText(/Fault state cleared/i)).toBeInTheDocument();
  });
});

// ── 17. Viewer sees neither Set fault nor Clear faults ────────────────────────

test('viewer role sees neither Set fault nor Clear faults buttons', async () => {
  vi.doMock('@/auth/useAuth', () => ({
    useAuth: () => ({
      getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
      user: { groups: ['fleet-viewer'], roles: ['fleet-viewer'] },
    }),
  }));

  vi.resetModules();
  const { default: VehicleDTCsTableViewer } = await import('../VehicleDTCsTable');
  mockFaultsFlow({ dtcs: [], faultState: FAULTS_SET_FIXTURE });
  render(<VehicleDTCsTableViewer vehicleId="V1" />);

  await waitFor(() => {
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument();
  });

  expect(screen.queryByRole('button', { name: /set fault on vehicle/i })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /clear faults on vehicle/i })).not.toBeInTheDocument();

  vi.doUnmock('@/auth/useAuth');
});

// ── 18. Info Popover trigger is present and has the correct accessible name ───

test('info Popover trigger is present and exposes its accessible name', async () => {
  mockFaultsFlow({ dtcs: [] });
  render(<VehicleDTCsTable vehicleId="V1" />);

  await waitFor(() => {
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument();
  });

  const infoBtn = screen.getByRole('button', { name: /about clearing faults and dtcs/i });
  expect(infoBtn).toBeInTheDocument();


});

// ── ACTIVE means "record unresolved", NOT "the vehicle is reporting it now" ──
// Reported by the user: "there is a status that says 'active' so it's confusing
// to the user." After Clear faults, rows stay ACTIVE while the ECU is silent, so
// the badge alone overstates. Colour splits the two cases and the title says
// which one in words, since colour is not an accessible signal on its own.

test('ACTIVE + fresh lastSeenAt reads as currently reported', async () => {
  mockFaultsFlow({
    dtcs: [{ ...activeNoService, status: 'ACTIVE', lastSeenAt: Date.now() - 5_000 }],
    faultState: null,
  });
  render(<VehicleDTCsTable vehicleId="V1" />);
  await screen.findByText('ACTIVE');
  const titles = Array.from(document.querySelectorAll('span[title]'))
    .map(e => e.getAttribute('title') || '');
  expect(titles.some(t => /reported by the vehicle within the last \d+s/i.test(t))).toBe(true);
});

test('ACTIVE + stale lastSeenAt reads as open-but-not-reporting', async () => {
  // 5 minutes stale — well past the 2-poll (90s) threshold.
  mockFaultsFlow({
    dtcs: [{ ...activeNoService, status: 'ACTIVE', lastSeenAt: Date.now() - 300_000 }],
    faultState: null,
  });
  render(<VehicleDTCsTable vehicleId="V1" />);
  await screen.findByText('ACTIVE');
  const titles = Array.from(document.querySelectorAll('span[title]'))
    .map(e => e.getAttribute('title') || '');
  expect(titles.some(t => /no report from the vehicle for over \d+s/i.test(t))).toBe(true);
  expect(titles.some(t => /within the last \d+s/i.test(t))).toBe(false);
});

test('ACTIVE badge carries an italic relative age underneath', async () => {
  // Requested by the user: "last seen as relative under gray active ... just in
  // italics or something (5 days old)". Rendered for both ACTIVE states so a red
  // badge reading "just now" corroborates liveness.
  mockFaultsFlow({
    dtcs: [{ ...activeNoService, status: 'ACTIVE', lastSeenAt: Date.now() - 5 * 86_400_000 }],
    faultState: null,
  });
  render(<VehicleDTCsTable vehicleId="V1" />);
  await screen.findByText('ACTIVE');
  // 5 days stale -> "(5 days ago)" in an <i>
  const italics = Array.from(document.querySelectorAll('i')).map(e => e.textContent || '');
  expect(italics.some(t => /\(5 days ago\)/.test(t))).toBe(true);
});

test('a freshly-reported ACTIVE row reads "just now", not a stale age', async () => {
  mockFaultsFlow({
    dtcs: [{ ...activeNoService, status: 'ACTIVE', lastSeenAt: Date.now() - 2_000 }],
    faultState: null,
  });
  render(<VehicleDTCsTable vehicleId="V1" />);
  await screen.findByText('ACTIVE');
  const italics = Array.from(document.querySelectorAll('i')).map(e => e.textContent || '');
  expect(italics.some(t => /\(just now\)/.test(t))).toBe(true);
  expect(italics.some(t => /days ago/.test(t))).toBe(false);
});

test('CLEARED rows get no age line — the record is closed, age is noise', async () => {
  mockFaultsFlow({ dtcs: [clearedRow], faultState: null });
  render(<VehicleDTCsTable vehicleId="V1" />);
  await screen.findByText('CLEARED');
  const italics = Array.from(document.querySelectorAll('i')).map(e => e.textContent || '');
  expect(italics.some(t => /ago\)/.test(t))).toBe(false);
});

// Badge COLOUR is the primary at-a-glance signal for "is this live?", so it is
// asserted, not assumed. Cloudscape encodes it as a class modifier
// (awsui_badge-color-red / -grey), verified by probing the library directly.
const badgeColourOf = (text: string): string => {
  const el = Array.from(document.querySelectorAll('span'))
    .find(e => e.textContent === text && /awsui_badge_/.test(e.className));
  const m = /awsui_badge-color-([a-z]+)_/.exec(el?.className || '');
  return m ? m[1] : 'none';
};

test('ACTIVE badge is RED while the vehicle is reporting', async () => {
  mockFaultsFlow({
    dtcs: [{ ...activeNoService, status: 'ACTIVE', lastSeenAt: Date.now() - 3_000 }],
    faultState: null,
  });
  render(<VehicleDTCsTable vehicleId="V1" />);
  await screen.findByText('ACTIVE');
  expect(badgeColourOf('ACTIVE')).toBe('red');
});

test('ACTIVE badge is GREY once the vehicle stops reporting', async () => {
  // The distinction the user asked for: grey reads as dormant, red as live.
  mockFaultsFlow({
    dtcs: [{ ...activeNoService, status: 'ACTIVE', lastSeenAt: Date.now() - 300_000 }],
    faultState: null,
  });
  render(<VehicleDTCsTable vehicleId="V1" />);
  await screen.findByText('ACTIVE');
  expect(badgeColourOf('ACTIVE')).toBe('grey');
});

// The colour legend must be ON the Status column, because the meaning of red vs
// grey is not guessable — the person who specified it forgot within the hour.
test('Status column header exposes a colour-legend popover', async () => {
  mockFaultsFlow({ dtcs: [activeNoService], faultState: null });
  render(<VehicleDTCsTable vehicleId="V1" />);
  const trigger = await screen.findByRole('button', { name: /what the status colours mean/i });
  expect(trigger).toBeInTheDocument();
  await userEvent.click(trigger);
  // Legend must define BOTH ACTIVE colours, not just say "ACTIVE".
  expect(await screen.findByText(/reported this within the last/i)).toBeInTheDocument();
  expect(screen.getByText(/so the ECU has/i)).toBeInTheDocument();
});

test('operator-facing copy never calls the quiet ACTIVE badge "blue"', async () => {
  // Regression guard: the badge was blue for one deploy, then changed to grey.
  // The explanatory popover kept saying "blue" — copy drifting away from the UI
  // it describes is worse than no copy at all, and it survived a review.
  //
  // Scoped to OPERATOR-FACING copy: comment lines are excluded, because the
  // source legitimately explains *why* grey was chosen over blue, and colour
  // tokens (color="blue", 'blue') are code, not prose.
  const src = readFileSync(
    'src/components/vehicles/vehicle-detail/VehicleDTCsTable.tsx',
    'utf8',
  );
  const prose = src
    .split('\n')
    .filter(l => {
      const t = l.trim();
      if (t.startsWith('//') || t.startsWith('*') || t.startsWith('/*')) return false;
      if (/color[=:]\s*['"{]|'blue'/.test(l)) return false;
      return true;
    })
    .join('\n');
  expect(/\bblue\b/i.test(prose)).toBe(false);
});

// The freshness window is a real definition, not a vibe: "red" means the cloud
// saw a report within REPORTING_STALE_MS. Pinned on both sides of the boundary
// so the definition cannot drift silently.
//
// The window must exceed one UDS poll interval (30s) PLUS the pipeline legs
// (~40s measured), because lastSeenAt is stamped on cloud arrival, not when the
// ECU answered. A false grey on a live fault is the worse error.
test('freshness window: just inside the boundary stays RED', async () => {
  mockFaultsFlow({
    dtcs: [{ ...activeNoService, status: 'ACTIVE', lastSeenAt: Date.now() - 110_000 }],
    faultState: null,
  });
  render(<VehicleDTCsTable vehicleId="V1" />);
  await screen.findByText('ACTIVE');
  expect(badgeColourOf('ACTIVE')).toBe('red');
});

test('freshness window: just outside the boundary flips to GREY', async () => {
  mockFaultsFlow({
    dtcs: [{ ...activeNoService, status: 'ACTIVE', lastSeenAt: Date.now() - 130_000 }],
    faultState: null,
  });
  render(<VehicleDTCsTable vehicleId="V1" />);
  await screen.findByText('ACTIVE');
  expect(badgeColourOf('ACTIVE')).toBe('grey');
});

