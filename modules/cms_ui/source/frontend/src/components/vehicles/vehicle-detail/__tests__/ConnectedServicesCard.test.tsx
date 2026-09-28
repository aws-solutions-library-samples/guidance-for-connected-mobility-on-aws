// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Spec: `.kiro/specs/2026-09-10-cms-connected-services-consumer/` T2.4.
 *
 * Every fixture in this file is shaped from the LIVE producer capture in that
 * spec dir's `producer-live-shapes.json`, not from spec.md's prose. Three of
 * T1.4's happy-path mocks were wrong about the real contract, and every defect
 * this spec has found passed a green suite first — so a fixture that agrees
 * with the implementation rather than with the producer is the failure mode to
 * avoid here, and mutation testing cannot catch it because mutation and
 * assertion move together.
 *
 * Concretely pinned:
 *   - `timestamp` is epoch MILLISECONDS (1789245554121), not ISO, not seconds
 *   - enroll returns `added[] / already_present[] / scope_size`
 *   - unenroll returns `removed: bool / scope_size` — `false` is a no-op
 *   - the four 502 sub-classes are distinguished by `body.error`, and NONE of
 *     them may render as "no telemetry"
 */

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ConnectedServicesCard, {
  formatRecordTimestamp,
} from '../ConnectedServicesCard';
import {
  CS_FEED_PATH,
  ConnectedServicesError,
  deriveEnrollment,
  type ConnectedServicesFeed,
} from '@/api/connectedServices';

vi.mock('@/api/connectedServices', async () => {
  const actual = await vi.importActual<typeof import('@/api/connectedServices')>(
    '@/api/connectedServices',
  );
  return {
    ...actual,
    getSubscriptionFeed: vi.fn(),
    enrollVin: vi.fn(),
    unenrollVin: vi.fn(),
  };
});

// Imported after the mock so these are the mocked bindings.
import {
  enrollVin,
  getSubscriptionFeed,
  unenrollVin,
} from '@/api/connectedServices';

const VIN = 'MRDN0000000000005';
const OTHER_VIN = 'MRDN0000000000009';
/** Epoch ms, from the live capture. 13 digits — a 10-digit value is seconds. */
const LIVE_TS = 1789245554121;

function feed(overrides: Partial<ConnectedServicesFeed> = {}): ConnectedServicesFeed {
  return {
    subscription_id: 'sub-cms-telemetry',
    records: [],
    count: 0,
    vins_in_scope: [],
    unresolved_vins: [],
    quota: { records_per_call: 100 },
    ...overrides,
  };
}

function record(vin: string, timestamp: number) {
  return {
    vin,
    vehicleId: `VEH-${vin.slice(-4)}`,
    signals: { odometer_km: 41234.5, state_of_charge_pct: 62 },
    dataSourceRoute: 'cs-meridian',
    timestamp,
  };
}

beforeEach(() => {
  vi.mocked(getSubscriptionFeed).mockReset();
  vi.mocked(enrollVin).mockReset();
  vi.mocked(unenrollVin).mockReset();
});

describe('ConnectedServicesCard — the two D5 states', () => {
  it('renders not-enrolled with an enroll action when the VIN is absent from scope', async () => {
    vi.mocked(getSubscriptionFeed).mockResolvedValue(feed());

    render(<ConnectedServicesCard vin={VIN} />);

    expect(await screen.findByTestId('cs-not-enrolled')).toBeInTheDocument();
    expect(screen.getByTestId('cs-enroll')).toBeInTheDocument();
    expect(screen.queryByTestId('cs-unenroll')).not.toBeInTheDocument();
    expect(screen.getByText(/Not enrolled in Connected Services feed/)).toBeInTheDocument();
  });

  it('renders enrolled with last-record timestamp, expandable payload and unenroll', async () => {
    vi.mocked(getSubscriptionFeed).mockResolvedValue(
      feed({
        records: [record(VIN, LIVE_TS)],
        count: 1,
        vins_in_scope: [VIN],
      }),
    );

    render(<ConnectedServicesCard vin={VIN} />);

    expect(await screen.findByTestId('cs-enrolled')).toBeInTheDocument();
    expect(screen.getByTestId('cs-unenroll')).toBeInTheDocument();
    expect(screen.queryByTestId('cs-enroll')).not.toBeInTheDocument();

    // The rendered timestamp must be the ms value read as ms. A seconds
    // misreading of 1789245554121 lands ~54,000 years out, so asserting the
    // year is enough to catch it and does not pin the test to a locale format.
    const expectedYear = String(new Date(LIVE_TS).getFullYear());
    expect(screen.getByTestId('cs-last-record').textContent).toContain(expectedYear);
    expect(screen.getByTestId('cs-last-record').textContent).toContain('cs-meridian');

    // Payload is the raw record, JSON — an operator has to be able to see what
    // arrived, not a CMS-rewritten summary of it.
    await userEvent.click(screen.getByText('View latest record'));
    const payload = await screen.findByTestId('cs-payload');
    expect(payload.textContent).toContain('"odometer_km"');
    expect(payload.textContent).toContain(String(LIVE_TS));
  });

  it('shows enrolled-with-no-records as its own state, never as an error', async () => {
    vi.mocked(getSubscriptionFeed).mockResolvedValue(feed({ vins_in_scope: [VIN] }));

    render(<ConnectedServicesCard vin={VIN} />);

    expect(await screen.findByTestId('cs-enrolled')).toBeInTheDocument();
    expect(screen.getByTestId('cs-enrolled-no-records')).toBeInTheDocument();
    expect(screen.queryByTestId('cs-payload')).not.toBeInTheDocument();
  });

  it('does not treat another VIN\u2019s records as this VIN\u2019s', async () => {
    vi.mocked(getSubscriptionFeed).mockResolvedValue(
      feed({
        records: [record(OTHER_VIN, LIVE_TS)],
        count: 1,
        vins_in_scope: [OTHER_VIN],
      }),
    );

    render(<ConnectedServicesCard vin={VIN} />);

    expect(await screen.findByTestId('cs-not-enrolled')).toBeInTheDocument();
  });
});

describe('ConnectedServicesCard — enroll / unenroll', () => {
  it('enroll then refresh flips the card to enrolled', async () => {
    vi.mocked(getSubscriptionFeed)
      .mockResolvedValueOnce(feed())
      .mockResolvedValueOnce(feed({ vins_in_scope: [VIN] }));
    vi.mocked(enrollVin).mockResolvedValue({
      added: [VIN],
      already_present: [],
      scope_size: 4,
    });

    render(<ConnectedServicesCard vin={VIN} />);
    await userEvent.click(await screen.findByTestId('cs-enroll'));

    expect(vi.mocked(enrollVin)).toHaveBeenCalledWith(VIN);
    expect(await screen.findByTestId('cs-enrolled')).toBeInTheDocument();
    expect(screen.getByTestId('cs-action-note').textContent).toContain('Enrolled');
  });

  it('stays enrolled after an enroll whose refresh cannot yet see it (scoped operator)', async () => {
    // `_cs_filter_feed` narrows `vins_in_scope` to the VINs of records that
    // survive the fleet filter, so a fleet-scoped operator's post-enroll GET
    // shows an EMPTY scope until the first record arrives. Without the
    // `justEnrolled` latch the card would answer "not enrolled" immediately
    // after confirming the enroll — indistinguishable from the enroll failing.
    vi.mocked(getSubscriptionFeed).mockResolvedValue(feed());
    vi.mocked(enrollVin).mockResolvedValue({
      added: [VIN],
      already_present: [],
      scope_size: 4,
    });

    render(<ConnectedServicesCard vin={VIN} />);
    await userEvent.click(await screen.findByTestId('cs-enroll'));

    expect(await screen.findByTestId('cs-enrolled')).toBeInTheDocument();
    expect(screen.getByTestId('cs-enrolled-no-records')).toBeInTheDocument();
  });

  it('reports already_present as a no-op, not as a fresh enrollment', async () => {
    vi.mocked(getSubscriptionFeed).mockResolvedValue(feed());
    vi.mocked(enrollVin).mockResolvedValue({
      added: [],
      already_present: [VIN],
      scope_size: 4,
    });

    render(<ConnectedServicesCard vin={VIN} />);
    await userEvent.click(await screen.findByTestId('cs-enroll'));

    const note = await screen.findByTestId('cs-action-note');
    expect(note.textContent).toContain('Already enrolled');
    expect(note.textContent).not.toMatch(/^Enrolled/);
  });

  it('unenroll then refresh reverts the card to not-enrolled', async () => {
    vi.mocked(getSubscriptionFeed)
      .mockResolvedValueOnce(feed({ records: [record(VIN, LIVE_TS)], count: 1, vins_in_scope: [VIN] }))
      .mockResolvedValueOnce(feed());
    vi.mocked(unenrollVin).mockResolvedValue({ removed: true, scope_size: 3 });

    render(<ConnectedServicesCard vin={VIN} />);
    await userEvent.click(await screen.findByTestId('cs-unenroll'));

    expect(vi.mocked(unenrollVin)).toHaveBeenCalledWith(VIN);
    expect(await screen.findByTestId('cs-not-enrolled')).toBeInTheDocument();
    expect(screen.getByTestId('cs-action-note').textContent).toContain('Unenrolled');
  });

  it('reports removed:false as already-absent, not as a removal it did not do', async () => {
    vi.mocked(getSubscriptionFeed)
      .mockResolvedValueOnce(feed({ vins_in_scope: [VIN] }))
      .mockResolvedValueOnce(feed());
    vi.mocked(unenrollVin).mockResolvedValue({ removed: false, scope_size: 3 });

    render(<ConnectedServicesCard vin={VIN} />);
    await userEvent.click(await screen.findByTestId('cs-unenroll'));

    const note = await screen.findByTestId('cs-action-note');
    expect(note.textContent).toContain('Already not enrolled');
  });

  it('surfaces a 403 on enroll as a scope story, and does not claim enrollment', async () => {
    vi.mocked(getSubscriptionFeed).mockResolvedValue(feed());
    vi.mocked(enrollVin).mockRejectedValue(
      new ConnectedServicesError('Access denied', 'forbidden', 403),
    );

    render(<ConnectedServicesCard vin={VIN} />);
    await userEvent.click(await screen.findByTestId('cs-enroll'));

    expect(await screen.findByTestId('cs-failure-forbidden')).toBeInTheDocument();
    expect(screen.queryByTestId('cs-enrolled')).not.toBeInTheDocument();
  });
});

describe('ConnectedServicesCard — failure states are not "no telemetry"', () => {
  // The substitution this whole card exists to avoid. index.py's
  // `_cs_filter_feed` makes the same argument backend-side: a body with no
  // `records` key read as an empty list turns "we could not read the feed" into
  // "this vehicle has no telemetry".
  const cases: Array<[string, string]> = [
    ['notConfigured', 'not configured'],
    ['producerUnavailable', 'unavailable'],
    ['producerUnauthorized', 'credential'],
    ['shapeDrift', 'could not be read'],
    ['network', 'Could not reach CMS'],
  ];

  it.each(cases)('%s renders its own message', async (kind, fragment) => {
    vi.mocked(getSubscriptionFeed).mockRejectedValue(
      new ConnectedServicesError('failed', kind as never, 502),
    );

    render(<ConnectedServicesCard vin={VIN} />);

    const alert = await screen.findByTestId(`cs-failure-${kind}`);
    // Asserted on the alert's own text, not via a document-wide `getByText`:
    // the fragment legitimately appears in both the header and the body, and a
    // document-wide query fails on "multiple elements" — a test that breaks
    // because the copy is clear is a badly-scoped test, not a defect.
    expect(alert.textContent).toMatch(new RegExp(fragment, 'i'));
    // The three things a failure must never do: claim non-enrollment, claim
    // enrollment, or offer an action whose outcome CMS cannot predict.
    expect(screen.queryByTestId('cs-not-enrolled')).not.toBeInTheDocument();
    expect(screen.queryByTestId('cs-enrolled')).not.toBeInTheDocument();
    expect(screen.queryByTestId('cs-enroll')).not.toBeInTheDocument();
  });

  it('every failure kind has copy \u2014 no kind falls through to a bare 502', async () => {
    // Guards the mapping itself rather than the five cases above: adding a new
    // `ConnectedServicesFailureKind` without copy would otherwise show the
    // `unknown` fallback and nothing would fail.
    const kinds = [
      'notConfigured',
      'producerUnavailable',
      'producerUnauthorized',
      'shapeDrift',
      'forbidden',
      'invalidVin',
      'network',
      'unknown',
    ];
    for (const kind of kinds) {
      vi.mocked(getSubscriptionFeed).mockRejectedValue(
        new ConnectedServicesError('failed', kind as never, 502),
      );
      const { unmount } = render(<ConnectedServicesCard vin={VIN} />);
      expect(await screen.findByTestId(`cs-failure-${kind}`)).toBeInTheDocument();
      unmount();
    }
  });

  it('renders unresolved-in-scope as a state, not an error', async () => {
    vi.mocked(getSubscriptionFeed).mockResolvedValue(
      feed({ vins_in_scope: [VIN], unresolved_vins: [VIN] }),
    );

    render(<ConnectedServicesCard vin={VIN} />);

    expect(await screen.findByTestId('cs-unresolved')).toBeInTheDocument();
    expect(screen.getByTestId('cs-enrolled')).toBeInTheDocument();
  });

  it('says so when the feed came from cache, and stays silent when it did not', async () => {
    // T2.6. The card must not present a cached answer as live.
    vi.mocked(getSubscriptionFeed).mockResolvedValueOnce(
      feed({ vins_in_scope: [VIN], cached_at: '2026-09-13T10:00:00Z' }),
    );
    const { unmount } = render(<ConnectedServicesCard vin={VIN} />);
    const banner = await screen.findByTestId('cs-cached-at');
    expect(banner.textContent).toContain('2026-09-13T10:00:00Z');
    unmount();

    vi.mocked(getSubscriptionFeed).mockResolvedValueOnce(feed({ vins_in_scope: [VIN] }));
    render(<ConnectedServicesCard vin={VIN} />);
    await screen.findByTestId('cs-enrolled');
    expect(screen.queryByTestId('cs-cached-at')).not.toBeInTheDocument();
  });

  it('a vehicle with no VIN says so instead of calling the route', async () => {
    render(<ConnectedServicesCard vin={undefined} />);

    expect(await screen.findByTestId('cs-no-vin')).toBeInTheDocument();
    expect(vi.mocked(getSubscriptionFeed)).not.toHaveBeenCalled();
  });

  it('never renders the error message \u2014 only the fixed per-kind copy', async () => {
    // Security-review Cycle 1 Suggestion 1. `ConnectedServicesError.message`
    // carries `body.detail || body.message || body.error` from the proxy, which
    // is producer-influenced: internal error text, an upstream hostname, a stack
    // fragment. React escapes it so there is no XSS vector, but any
    // authenticated CMS user would read it.
    //
    // The card renders only `FAILURE_COPY[kind]` today. This pins that, because
    // adding a "technical details" line reads like a UX improvement and IS a
    // disclosure change \u2014 the kind of decision that should fail a test rather
    // than pass unnoticed.
    const secret = 'UPSTREAM-DETAIL-producer-internal-7f3a.example.invalid';
    vi.mocked(getSubscriptionFeed).mockRejectedValue(
      new ConnectedServicesError(secret, 'producerUnavailable', 502),
    );

    const { container } = render(<ConnectedServicesCard vin={VIN} />);
    await screen.findByTestId('cs-failure-producerUnavailable');

    expect(container.textContent).not.toContain(secret);
    expect(container.textContent).not.toContain('example.invalid');
  });
});

describe('deriveEnrollment', () => {
  it('surfaces cached_at so a cached feed is distinguishable from a live one', () => {
    // T2.6. A cached answer presented as live is a correctness bug — the
    // operator's next decision depends on how old the data is.
    const withCache = deriveEnrollment(
      feed({ vins_in_scope: [VIN], cached_at: '2026-09-13T10:00:00Z' }),
      VIN,
    );
    expect(withCache.cachedAt).toBe('2026-09-13T10:00:00Z');

    const live = deriveEnrollment(feed({ vins_in_scope: [VIN] }), VIN);
    expect(live.cachedAt).toBeUndefined();
  });

  it('picks the NEWEST record, not the first in the array', () => {
    const older = record(VIN, LIVE_TS - 60_000);
    const newer = record(VIN, LIVE_TS);
    const view = deriveEnrollment(
      feed({ records: [newer, older], count: 2, vins_in_scope: [VIN] }),
      VIN,
    );
    expect(view.latestRecord?.timestamp).toBe(LIVE_TS);
    expect(view.recordCount).toBe(2);
  });

  it('treats a record-bearing VIN as enrolled even when vins_in_scope was filtered away', () => {
    const view = deriveEnrollment(
      feed({ records: [record(VIN, LIVE_TS)], count: 1, vins_in_scope: [] }),
      VIN,
    );
    expect(view.enrolled).toBe(true);
  });

  it('tolerates a feed missing optional arrays without throwing', () => {
    const partial = { subscription_id: 's', count: 0 } as unknown as ConnectedServicesFeed;
    const view = deriveEnrollment(partial, VIN);
    expect(view.enrolled).toBe(false);
    expect(view.latestRecord).toBeNull();
  });
});

describe('formatRecordTimestamp', () => {
  it('reads the value as epoch milliseconds', () => {
    expect(formatRecordTimestamp(LIVE_TS)).toContain(
      String(new Date(LIVE_TS).getFullYear()),
    );
  });

  it('does not invent a date for a non-finite value', () => {
    expect(formatRecordTimestamp(Number.NaN)).toBe('Unknown');
  });
});

describe('route surface', () => {
  it('the client targets CMS\u2019s own path and nothing else', () => {
    // Pinned here as well as in the bundle test: this is the one string that
    // decides whether the browser talks to CMS or to the producer.
    expect(CS_FEED_PATH).toBe('api/v1/connected-services/subscription-feed');
  });
});
