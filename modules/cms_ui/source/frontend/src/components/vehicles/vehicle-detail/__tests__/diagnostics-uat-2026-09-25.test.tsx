// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Guards for the first browser UAT of the Diagnostics tab redesign.
// Issue: issues/2026-09-25-diagnostics-ia-uat-defects/.
//
// Every fixture is a real commands-API row captured from staging
// (utils/__tests__/__fixtures__/liveSovdRows.ts). The defects this file guards
// against all passed their earlier tests because those tests used invented
// row shapes.

import React from 'react';
import { render, screen, fireEvent, act, within, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

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

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

vi.mock('@/hooks/useDealerOptions', () => ({
  useDealerOptions: () => ({ options: [], status: 'finished', errorMessage: null }),
  dealerPlaceholder: () => 'Select a service centre',
}));

import VehicleDiagnosticsPanel from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';
import type { SessionCommandEntry } from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';
import * as authFetchModule from '@/utils/authFetch';
import { LIVE_SOVD_ROWS } from '@/utils/__tests__/__fixtures__/liveSovdRows';

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const live = (r: Record<string, unknown>) => clone(r) as SessionCommandEntry;

const VEHICLE_ID = 'VEH-MRDN-0001';

beforeEach(() => {
  vi.mocked(authFetchModule.authFetch).mockReset();
  vi.mocked(authFetchModule.authFetch).mockReturnValue(undefined as unknown as Promise<Response>);
});

function renderWithHistory(rows: SessionCommandEntry[]) {
  return render(
    <VehicleDiagnosticsPanel
      vehicleId={VEHICLE_ID}
      connectionStatus="connected"
      catalog={[]}
      priorSessionCommands={rows}
      sessionCommands={[]}
    />,
  );
}

describe('health strip reads the latest scan from command history', () => {
  it('shows the live scan: no open faults, a last-scan age, and a scoped healthy verdict', () => {
    renderWithHistory([live(LIVE_SOVD_ROWS.scan), live(LIVE_SOVD_ROWS.sessionRoutine)]);
    expect(screen.getByTestId('health-strip-faults')).toHaveTextContent('No open faults');
    expect(screen.getByTestId('health-strip-last-scan')).toHaveTextContent(/Last scan: \d+ (day|hr|min)/);
    expect(screen.getByTestId('health-strip-recommended-action')).toHaveTextContent(
      'No issues found in the last scan.',
    );
  });

  it('counts DTCs from response.components when the scan found some', () => {
    const scan = clone(LIVE_SOVD_ROWS.scan) as { response: { components: Record<string, { dtcs: unknown[] }> } };
    scan.response.components.ECU_BRAKE.dtcs = [{ code: 'C1234', status: 'active' }];
    renderWithHistory([scan as unknown as SessionCommandEntry]);
    expect(screen.getByTestId('health-strip-faults')).toHaveTextContent('1 open fault');
    expect(screen.getByTestId('health-strip-recommended-action')).not.toHaveTextContent(/No issues found/i);
  });

  it('with history loaded but no scan in it: says so, and makes NO health claim', () => {
    renderWithHistory([live(LIVE_SOVD_ROWS.sessionRoutine)]);
    expect(screen.getByTestId('health-strip-faults')).toHaveTextContent('Not scanned yet');
    expect(screen.getByTestId('health-strip-last-scan')).toHaveTextContent('Last scan: none found');
    const action = screen.getByTestId('health-strip-recommended-action');
    expect(action).not.toHaveTextContent(/No issues found/i);
    expect(action).toHaveTextContent('Run a scan');
  });

  it('while history is still loading: says it is checking, and makes NO health claim', () => {
    // No priorSessionCommands prop and a fetch that never answers.
    vi.mocked(authFetchModule.authFetch).mockReturnValue(new Promise<Response>(() => {}));
    render(<VehicleDiagnosticsPanel vehicleId={VEHICLE_ID} connectionStatus="connected" catalog={[]} />);
    expect(screen.getByTestId('health-strip-faults')).toHaveTextContent('Checking');
    expect(screen.getByTestId('health-strip-recommended-action')).not.toHaveTextContent(/No issues found/i);
  });

  it('never renders the bare word "unknown" in the strip', () => {
    renderWithHistory([live(LIVE_SOVD_ROWS.sessionRoutine)]);
    expect(screen.getByTestId('diagnostics-health-strip').textContent ?? '').not.toMatch(/\bunknown\b/i);
  });
});

describe('sessions table groups live rows by session_id and shows start times', () => {
  it('lists the sessioned run and the unsessioned run as two rows, both with a Started time', () => {
    renderWithHistory([live(LIVE_SOVD_ROWS.sessionRoutine), live(LIVE_SOVD_ROWS.unsessionedRoutine)]);
    const table = screen.getByTestId('diagnostic-sessions-table');
    expect(within(table).getByTestId('session-view-button-2cf1b8de-9df7-467d-a0d8-e2c867e3d935')).toBeDefined();
    const bodyRows = table.querySelectorAll('tbody tr');
    expect(bodyRows.length).toBe(2);
    for (const tr of Array.from(bodyRows)) {
      const startedCell = tr.querySelector('td');
      expect(startedCell?.textContent?.trim()).not.toBe('—');
      expect(startedCell?.textContent?.trim()).not.toBe('');
    }
  });
});

describe('layout: actions before history', () => {
  it('renders the scan button and self-tests above the sessions table and DTC history', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId={VEHICLE_ID}
        connectionStatus="connected"
        catalog={[]}
        priorSessionCommands={[live(LIVE_SOVD_ROWS.sessionRoutine)]}
        sessionCommands={[]}
        routines={[{ routineId: 'lamp_self_check', safetyClass: 'INERT', invocable: true, precondition: '', reason: '' }]}
      />,
    );
    const scan = screen.getByTestId('health-strip-run-scan-button');
    const selfTests = screen.getByText('Self-tests you can run here');
    const sessions = screen.getByTestId('diagnostic-sessions-table');
    // The DTC section's header text also appears inside VehicleDTCsTable; the
    // first occurrence in document order is the section header.
    const dtcs = screen.getAllByText('Diagnostic Trouble Codes')[0];
    const follows = (a: Element, b: Element) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(follows(scan, selfTests)).toBe(true);
    expect(follows(selfTests, sessions)).toBe(true);
    expect(follows(sessions, dtcs)).toBe(true);
  });
});

describe('session detail opens in a modal and shows THAT session\'s rows', () => {
  it('opens the selected session, not the current mount\'s session', async () => {
    renderWithHistory([live(LIVE_SOVD_ROWS.sessionRoutine), live(LIVE_SOVD_ROWS.unsessionedRoutine)]);
    await act(async () => {
      fireEvent.click(screen.getByTestId('session-view-button-2cf1b8de-9df7-467d-a0d8-e2c867e3d935'));
    });
    const detail = screen.getByTestId('session-detail-view');
    expect(within(detail).getByTestId(`routine-result-entry-${LIVE_SOVD_ROWS.sessionRoutine.commandId}`)).toBeDefined();
    expect(
      within(detail).queryByTestId(`routine-result-entry-${LIVE_SOVD_ROWS.unsessionedRoutine.commandId}`),
    ).toBeNull();
    // The page stays under the modal.
    expect(screen.getByTestId('health-strip-run-scan-button')).toBeDefined();
  });
});


// ── Review FG2 cycle 1 guards ─────────────────────────────────────────────────

const okJson = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, clone() { return this; }, json: async () => body } as unknown as Response);

describe('C1 — a scan whose results were not stored makes no health claim', () => {
  it('says the results are not available, never "No open faults" / "No issues found"', () => {
    const scan = clone(LIVE_SOVD_ROWS.scan) as Record<string, unknown>;
    delete scan.response;
    scan.s3Key = 'sovd-responses/example.json';
    renderWithHistory([scan as SessionCommandEntry]);
    expect(screen.getByTestId('health-strip-faults')).toHaveTextContent('Fault results not available');
    const action = screen.getByTestId('health-strip-recommended-action');
    expect(action).not.toHaveTextContent(/No issues found/i);
    expect(action).toHaveTextContent('too large to show here');
  });

  it('a PARTIAL scan is scoped to the ECUs that answered, and is not called healthy', () => {
    const scan = clone(LIVE_SOVD_ROWS.scan);
    scan.status = 'PARTIAL';
    renderWithHistory([scan as SessionCommandEntry]);
    expect(screen.getByTestId('health-strip-faults')).toHaveTextContent(/No open faults in the \d+ ECUs that answered/);
    expect(screen.getByTestId('health-strip-recommended-action')).not.toHaveTextContent(/No issues found/i);
  });
});

describe('W1 — without an event catalog, found DTCs are named, not called "Undetermined"', () => {
  it('names the codes and says severity is not available', () => {
    const scan = clone(LIVE_SOVD_ROWS.scan) as { response: { components: Record<string, { dtcs: unknown[] }> } };
    scan.response.components.ECU_ENGINE.dtcs = [{ code: 'P0300', status: 'active' }];
    render(
      <VehicleDiagnosticsPanel
        vehicleId={VEHICLE_ID}
        connectionStatus="connected"
        priorSessionCommands={[scan as unknown as SessionCommandEntry]}
        sessionCommands={[]}
      />,
    );
    const action = screen.getByTestId('health-strip-recommended-action');
    expect(action).toHaveTextContent('1 fault code found in the last scan: P0300.');
    expect(action).toHaveTextContent("Severity isn't available right now.");
    expect(action).not.toHaveTextContent(/Undetermined|No issues found/i);
  });
});

describe('W5 — history loading transitions', () => {
  it('loads history when the tab opens, without waiting for the poll interval', async () => {
    vi.mocked(authFetchModule.authFetch).mockImplementation(((url: string) =>
      url.includes('limit=100') ? okJson({ commands: [live(LIVE_SOVD_ROWS.scan)], count: 1 }) : undefined
    ) as unknown as typeof authFetchModule.authFetch);
    render(<VehicleDiagnosticsPanel vehicleId={VEHICLE_ID} connectionStatus="connected" catalog={[]} />);
    // Real timers and no timer advance: only a mount-time fetch can load this.
    await waitFor(() =>
      expect(screen.getByTestId('health-strip-faults')).toHaveTextContent('No open faults'),
    );
  });

  it('shows "unavailable" when the history fetch fails, and makes no health claim', async () => {
    vi.mocked(authFetchModule.authFetch).mockImplementation(((url: string) =>
      url.includes('limit=100')
        ? Promise.resolve({ ok: false, status: 500, json: async () => ({}) } as unknown as Response)
        : undefined
    ) as unknown as typeof authFetchModule.authFetch);
    render(<VehicleDiagnosticsPanel vehicleId={VEHICLE_ID} connectionStatus="connected" catalog={[]} />);
    await waitFor(() =>
      expect(screen.getByTestId('health-strip-faults')).toHaveTextContent('Fault codes unavailable'),
    );
    expect(screen.getByTestId('health-strip-recommended-action')).not.toHaveTextContent(/No issues found/i);
  });
});

describe('W5 — the unsessioned bucket opens with only unsessioned rows', () => {
  it('excludes sessioned rows', async () => {
    renderWithHistory([live(LIVE_SOVD_ROWS.sessionRoutine), live(LIVE_SOVD_ROWS.unsessionedRoutine)]);
    await act(async () => {
      fireEvent.click(screen.getByTestId('session-view-button-__unsessioned__'));
    });
    const detail = screen.getByTestId('session-detail-view');
    expect(within(detail).getByTestId(`routine-result-entry-${LIVE_SOVD_ROWS.unsessionedRoutine.commandId}`)).toBeDefined();
    expect(within(detail).queryByTestId(`routine-result-entry-${LIVE_SOVD_ROWS.sessionRoutine.commandId}`)).toBeNull();
  });
});

describe('W3 — an earlier session does not borrow this visit\'s notes box', () => {
  it('shows the not-current note instead of an editable notes box', async () => {
    renderWithHistory([live(LIVE_SOVD_ROWS.sessionRoutine)]);
    await act(async () => {
      fireEvent.click(screen.getByTestId('session-view-button-2cf1b8de-9df7-467d-a0d8-e2c867e3d935'));
    });
    const detail = screen.getByTestId('session-detail-view');
    expect(within(detail).queryByTestId('evidence-notes-textarea')).toBeNull();
    expect(within(detail).getByTestId('evidence-notes-not-current')).toBeDefined();
  });
});

describe('dispatch evidence carries the latest scan\'s DTCs', () => {
  it('summarises the scan DTC in the dispatch modal', async () => {
    const scan = clone(LIVE_SOVD_ROWS.scan) as { response: { components: Record<string, { dtcs: unknown[] }> } };
    scan.response.components.ECU_ENGINE.dtcs = [{ code: 'P0300', status: 'active' }];
    render(
      <VehicleDiagnosticsPanel
        vehicleId={VEHICLE_ID}
        connectionStatus="connected"
        vin="MRDN0000000000001"
        callerGroups={['fleet-operator']}
        catalog={[]}
        priorSessionCommands={[scan as unknown as SessionCommandEntry]}
        sessionCommands={[]}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId('dispatch-to-service-button'));
    });
    expect(screen.getByTestId('dispatch-evidence-summary')).toHaveTextContent('1 DTC');
  });

  // FG3: a threshold-raised code (the staging P0217 overheat) is not read from
  // an ECU, so a scan need not return it. The recorded ACTIVE list must still reach service.
  const recordedP0217 = [{
    code: 'P0217',
    description: 'Engine coolant critically overheated — stop driving, let engine cool',
    source: 'flink-maintenance-processor',
  }];
  // The marker must be true whether or not a scan exists, and whichever
  // session the latest scan belongs to (review FG3 cycle 1, W1).
  const MARKER = "From the vehicle's fault record, not from a diagnostic scan.";
  // Only a claim about the record row's source: the simulator can also put a
  // catalog code on an ECU, so nothing may say a scan cannot show it (cycle 2, W1).
  const THRESHOLD_NOTE = 'Raised in the cloud from telemetry.';

  function renderDispatchable(rows: SessionCommandEntry[], extra: Record<string, unknown> = {}) {
    return render(
      <VehicleDiagnosticsPanel
        vehicleId={VEHICLE_ID}
        connectionStatus="connected"
        vin="MRDN0000000000001"
        callerGroups={['fleet-operator']}
        catalog={[]}
        priorSessionCommands={rows}
        sessionCommands={[]}
        recordedActiveDtcs={recordedP0217}
        {...extra}
      />,
    );
  }

  it('an earlier session\'s scan: counts scan and record codes separately, and marks only the record code', async () => {
    const scan = clone(LIVE_SOVD_ROWS.scan) as { response: { components: Record<string, { dtcs: unknown[] }> } };
    scan.response.components.ECU_ENGINE.dtcs = [{ code: 'P0300', status: 'active' }];
    renderDispatchable([scan as unknown as SessionCommandEntry]);
    await act(async () => {
      fireEvent.click(screen.getByTestId('dispatch-to-service-button'));
    });
    expect(screen.getByTestId('dispatch-evidence-summary')).toHaveTextContent(
      '1 DTC from scan, 1 from the fault record',
    );
    const marker = screen.getByTestId('dispatch-dtc-recorded-P0217');
    expect(marker.textContent).toBe(`${MARKER} ${THRESHOLD_NOTE}`);
    expect(screen.queryByTestId('dispatch-dtc-recorded-P0300')).toBeNull();
  });

  it('no scan on record: the record code is counted as such and leads the complaint prefill', async () => {
    renderDispatchable([live(LIVE_SOVD_ROWS.sessionRoutine)]);
    await act(async () => {
      fireEvent.click(screen.getByTestId('dispatch-to-service-button'));
    });
    expect(screen.getByTestId('dispatch-evidence-summary')).toHaveTextContent('1 DTC from the fault record');
    expect(screen.getByTestId('dispatch-evidence-summary')).not.toHaveTextContent(/from scan/);
    expect(screen.getByTestId('dispatch-dtc-recorded-P0217').textContent).toBe(`${MARKER} ${THRESHOLD_NOTE}`);
    const textarea = screen.getByTestId('dispatch-complaint-textarea').querySelector('textarea');
    expect(textarea?.value).toContain('Fleet-detected P0217');
  });

  it('does not use the fault record to fetch suggested routines', async () => {
    await act(async () => {
      renderDispatchable([live(LIVE_SOVD_ROWS.sessionRoutine)]);
    });
    const urls = vi.mocked(authFetchModule.authFetch).mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes('?dtc='))).toBe(false);
  });
});

describe('W2 — the scan poll reads its own command, not the newest row', () => {
  it('ignores a newer self-test row and renders the scan\'s real ECUs', async () => {
    const scanRow = live(LIVE_SOVD_ROWS.scan);
    const routineRow = { ...live(LIVE_SOVD_ROWS.sessionRoutine), commandId: 'newer-routine' };
    // Record every interval callback. jsdom registers its own animation-frame
    // interval once an earlier test has animated, so "the last setInterval" is
    // not reliably the panel's scan poll; take the panel's one made after the click.
    const intervals: Array<() => Promise<void> | void> = [];
    const intervalSpy = vi.spyOn(globalThis, 'setInterval').mockImplementation(((fn: () => void) => {
      intervals.push(fn);
      return intervals.length as unknown as ReturnType<typeof setInterval>;
    }) as unknown as typeof setInterval);
    vi.mocked(authFetchModule.authFetch).mockImplementation(((url: string, opts?: RequestInit) => {
      if (opts?.method === 'POST') return okJson({ success: true, commandId: scanRow.commandId, status: 'SENT' });
      if (url.includes('limit=1') && !url.includes('limit=100')) return okJson({ commands: [routineRow], count: 1 });
      if (url.includes('limit=100')) return okJson({ commands: [routineRow, scanRow], count: 2 });
      return undefined;
    }) as unknown as typeof authFetchModule.authFetch);
    try {
      render(<VehicleDiagnosticsPanel vehicleId={VEHICLE_ID} connectionStatus="connected" catalog={[]} />);
      const before = intervals.length;
      await act(async () => {
        fireEvent.click(screen.getByTestId('health-strip-run-scan-button'));
      });
      await act(async () => { await Promise.resolve(); });
      const scanPoll = intervals
        .slice(before)
        .filter((fn) => !String(fn).includes('runAnimationFrameCallbacks'))
        .pop();
      expect(scanPoll).toBeDefined();
      await act(async () => { await (scanPoll as () => Promise<void>)(); });
      await waitFor(() => expect(screen.getByText(/Scan completed — no fault codes detected in \d+ ECUs/)).toBeDefined());
      // The routine envelope's keys must never appear as ECU names.
      expect(screen.queryByText('correlation_id')).toBeNull();
    } finally {
      intervalSpy.mockRestore();
    }
  });
});

describe('S8 — a rate-limited attempt is explained, not flagged as a silent failure', () => {
  it('says the vehicle was busy and shows no "without an explanation" warning', async () => {
    // The live unsessioned row is RATE_LIMITED with retry_after_ms and no reason.
    renderWithHistory([live(LIVE_SOVD_ROWS.unsessionedRoutine)]);
    await act(async () => {
      fireEvent.click(screen.getByTestId('session-view-button-__unsessioned__'));
    });
    const id = LIVE_SOVD_ROWS.unsessionedRoutine.commandId as string;
    const entry = screen.getByTestId(`routine-result-entry-${id}`);
    expect(entry).toHaveTextContent('the vehicle was busy and asked to retry later');
    expect(screen.queryByTestId(`routine-result-silent-failure-${id}`)).toBeNull();
    // The time is shown as a local date, not a raw ISO string.
    expect(entry.textContent ?? '').not.toContain('+00:00');
  });
});


// ── Review FG2 cycle 2 guards ─────────────────────────────────────────────────

describe('W1 (cycle 2) — the panel loads the event catalog and shows a severity verdict', () => {
  it('turns a scanned P0300 into a P1 verdict with the catalog description', async () => {
    const scan = clone(LIVE_SOVD_ROWS.scan) as { response: { components: Record<string, { dtcs: unknown[] }> } };
    scan.response.components.ECU_ENGINE.dtcs = [{ code: 'P0300', status: 'active' }];
    vi.mocked(authFetchModule.authFetch).mockImplementation(((url: string) =>
      url.includes('/api/v1/event-catalog')
        ? okJson({ events: [{ event_id: 'dtc.p0300', dtc_code: 'P0300', severity_hint: 'P1', description: 'Engine misfire detected.' }], count: 1 })
        : undefined
    ) as unknown as typeof authFetchModule.authFetch);
    render(
      <VehicleDiagnosticsPanel
        vehicleId={VEHICLE_ID}
        connectionStatus="connected"
        priorSessionCommands={[scan as unknown as SessionCommandEntry]}
        sessionCommands={[]}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId('health-strip-recommended-action')).toHaveTextContent('P1 — 1 fault code detected.'),
    );
    expect(screen.getByTestId('health-strip-recommended-action')).toHaveTextContent('Engine misfire detected.');
    expect(screen.getByTestId('health-strip-faults')).toHaveTextContent('1 open fault (P1 highest)');
  });
});

/** Start a scan and run one scan-poll tick against the given list responses. */
async function runOneScanPoll(opts: { scanId: string; latest: unknown[]; all?: unknown[]; ticks?: number }) {
  const intervals: Array<() => Promise<void> | void> = [];
  const intervalSpy = vi.spyOn(globalThis, 'setInterval').mockImplementation(((fn: () => void) => {
    intervals.push(fn);
    return intervals.length as unknown as ReturnType<typeof setInterval>;
  }) as unknown as typeof setInterval);
  vi.mocked(authFetchModule.authFetch).mockImplementation(((url: string, o?: RequestInit) => {
    if (o?.method === 'POST') return okJson({ success: true, commandId: opts.scanId, status: 'SENT' });
    if (url.includes('limit=1') && !url.includes('limit=100')) return okJson({ commands: opts.latest });
    if (url.includes('limit=100')) return okJson({ commands: opts.all ?? opts.latest });
    return undefined;
  }) as unknown as typeof authFetchModule.authFetch);
  try {
    const before = intervals.length;
    await act(async () => {
      fireEvent.click(screen.getByTestId('health-strip-run-scan-button'));
    });
    await act(async () => { await Promise.resolve(); });
    const scanPoll = intervals
      .slice(before)
      .filter((fn) => !String(fn).includes('runAnimationFrameCallbacks'))
      .pop();
    expect(scanPoll).toBeDefined();
    for (let i = 0; i < (opts.ticks ?? 1); i++) {
      await act(async () => { await (scanPoll as () => Promise<void>)(); });
    }
  } finally {
    intervalSpy.mockRestore();
  }
}

describe('C2 (cycle 2) — the scan poll never shows "no fault codes" for a missing or partial result', () => {
  it('a finished scan with no stored results says so after one re-read, never a green no-faults line', async () => {
    const scan = live(LIVE_SOVD_ROWS.scan) as unknown as Record<string, unknown>;
    delete scan.response;
    render(<VehicleDiagnosticsPanel vehicleId={VEHICLE_ID} connectionStatus="connected" catalog={[]} />);
    // First tick: status written, response not yet (two separate updates), so
    // the panel reads once more instead of concluding.
    await runOneScanPoll({ scanId: scan.commandId as string, latest: [scan], ticks: 1 });
    expect(screen.queryByTestId('scan-results-unavailable')).toBeNull();
    expect(screen.getByTestId('scan-spinner')).toBeDefined();
    expect(screen.queryByText(/no fault codes detected/i)).toBeNull();
  });

  it('concludes "not available" when the re-read still has no results', async () => {
    const scan = live(LIVE_SOVD_ROWS.scan) as unknown as Record<string, unknown>;
    delete scan.response;
    render(<VehicleDiagnosticsPanel vehicleId={VEHICLE_ID} connectionStatus="connected" catalog={[]} />);
    await runOneScanPoll({ scanId: scan.commandId as string, latest: [scan], ticks: 2 });
    await waitFor(() => expect(screen.getByTestId('scan-results-unavailable')).toBeDefined());
    expect(screen.queryByText(/no fault codes detected/i)).toBeNull();
  });

  it('an offloaded result is "too large to show here", not "run a new scan"', () => {
    const scan = clone(LIVE_SOVD_ROWS.scan) as Record<string, unknown>;
    delete scan.response;
    scan.s3Key = 'sovd-responses/example.json';
    renderWithHistory([scan as SessionCommandEntry]);
    const action = screen.getByTestId('health-strip-recommended-action');
    expect(action).toHaveTextContent('too large to show here');
    expect(action).not.toHaveTextContent(/Run a new scan|No issues found/i);
  });

  it('a PARTIAL scan is marked incomplete, and shows no green no-faults line', async () => {
    const scan = live(LIVE_SOVD_ROWS.scan) as unknown as Record<string, unknown>;
    scan.status = 'PARTIAL';
    render(<VehicleDiagnosticsPanel vehicleId={VEHICLE_ID} connectionStatus="connected" catalog={[]} />);
    await runOneScanPoll({ scanId: scan.commandId as string, latest: [scan] });
    await waitFor(() => expect(screen.getByTestId('scan-results-partial')).toBeDefined());
    expect(screen.queryByText(/no fault codes detected/i)).toBeNull();
  });

  it('ECU entries without a dtcs list are not counted as answered', async () => {
    const scan = live(LIVE_SOVD_ROWS.scan) as unknown as { response: { components: Record<string, Record<string, unknown>> }; commandId: string };
    delete scan.response.components.ECU_BRAKE.dtcs;
    render(<VehicleDiagnosticsPanel vehicleId={VEHICLE_ID} connectionStatus="connected" catalog={[]} />);
    await runOneScanPoll({ scanId: scan.commandId, latest: [scan] });
    await waitFor(() => expect(screen.getByTestId('scan-results-partial')).toBeDefined());
    expect(screen.queryByText('ECU_BRAKE')).toBeNull();
  });
});

describe('W3 (cycle 2) — an earlier session does not show this visit\'s scan cards', () => {
  it('omits the current scan\'s ECU cards from an earlier session', async () => {
    const scan = live(LIVE_SOVD_ROWS.scan) as unknown as { response: { components: Record<string, { dtcs: unknown[] }> }; commandId: string };
    scan.response.components.ECU_ENGINE.dtcs = [{ code: 'P0300', status: 'active' }];
    render(
      <VehicleDiagnosticsPanel
        vehicleId={VEHICLE_ID}
        connectionStatus="connected"
        catalog={[]}
        priorSessionCommands={[live(LIVE_SOVD_ROWS.sessionRoutine)]}
        sessionCommands={[]}
      />,
    );
    // The post-scan history refresh must still return the earlier session.
    await runOneScanPoll({ scanId: scan.commandId, latest: [scan], all: [scan, live(LIVE_SOVD_ROWS.sessionRoutine)] });
    await waitFor(() => expect(screen.getByTestId('ecu-cards')).toBeDefined());
    await act(async () => {
      fireEvent.click(screen.getByTestId('session-view-button-2cf1b8de-9df7-467d-a0d8-e2c867e3d935'));
    });
    const detail = screen.getByTestId('session-detail-view');
    expect(within(detail).queryByText('ECU_ENGINE')).toBeNull();
  });
});
