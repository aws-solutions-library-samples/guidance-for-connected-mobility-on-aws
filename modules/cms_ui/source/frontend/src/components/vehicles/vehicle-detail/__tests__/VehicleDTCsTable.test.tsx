// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// RED-PHASE SKELETONS — spec 2026-06-17-dtc-dedup-first-last-seen-schedule-service
// Task 1.4: 7 cases that FAIL until Group 2.5 ships the new columns + button.

import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

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

import VehicleDTCsTable from '../VehicleDTCsTable';

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
