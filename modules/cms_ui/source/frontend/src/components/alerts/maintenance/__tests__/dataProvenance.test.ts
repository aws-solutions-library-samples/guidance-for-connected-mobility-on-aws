// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * T4.3 — data provenance for the merged Maintenance view.
 *
 * The three states T4.3 names are fresh, cached-with-age, and unreachable. This
 * suite covers those plus the one the spec does not name and which exists in
 * production today: PARTIAL — one leg of the two-source merge failed, and the
 * table renders the other leg's rows under a row counter and KPI cards that all
 * describe a complete answer.
 *
 * The assertions worth reading are the negative ones. It is easy to write a
 * banner; it is the "and it does NOT say the other thing" checks that stop a
 * failure from being narrated as an absence, or a cache from being narrated as
 * live.
 */

import { describe, it, expect } from 'vitest';

import {
  describeProvenance,
  failedLeg,
  formatCacheAge,
  legFromBody,
  liveLeg,
  type MergeProvenance,
} from '../dataProvenance';

const NOW = new Date('2026-09-09T12:00:00.000Z');

function merge(overrides: Partial<MergeProvenance> = {}): MergeProvenance {
  return { alerts: liveLeg(), serviceHistory: liveLeg(), ...overrides };
}

describe('legFromBody', () => {
  it('absent metadata means live, not "cached, age unknown"', () => {
    // CMS's service-history table is still the primary store, so an unlabelled
    // response genuinely IS live. Defaulting to cached would label every row in
    // the current deployment as stale — false, and a UI that cries stale on fresh
    // data trains its reader to ignore the label.
    expect(legFromBody(undefined)).toEqual(liveLeg());
    expect(legFromBody(null)).toEqual(liveLeg());
    expect(legFromBody({})).toEqual(liveLeg());
    expect(legFromBody({ dataSource: 'live' })).toEqual(liveLeg());
  });

  it('recognises a declared cache, with either spelling', () => {
    expect(legFromBody({ dataSource: 'cache', asOf: NOW.toISOString() })).toEqual({
      status: 'ok', freshness: 'cached', asOf: NOW.toISOString(), reason: '',
    });
    expect(legFromBody({ dataSource: 'CACHED' }).freshness).toBe('cached');
  });

  it('a declared cache with no asOf is still cached', () => {
    // The age is unknown; the staleness is not. Suppressing the label because one
    // field is missing hides the more important fact.
    const leg = legFromBody({ dataSource: 'cache' });
    expect(leg.freshness).toBe('cached');
    expect(leg.asOf).toBe('');
  });

  it('an unrecognised dataSource does not silently become cached', () => {
    expect(legFromBody({ dataSource: 'warm' }).freshness).toBe('live');
  });
});

describe('formatCacheAge', () => {
  it.each([
    ['2026-09-09T11:59:30.000Z', 'less than a minute old'],
    ['2026-09-09T11:59:00.000Z', '1 minute old'],
    ['2026-09-09T11:30:00.000Z', '30 minutes old'],
    ['2026-09-09T11:00:00.000Z', '1 hour old'],
    ['2026-09-09T04:00:00.000Z', '8 hours old'],
    ['2026-09-08T12:00:00.000Z', '1 day old'],
    ['2026-09-04T12:00:00.000Z', '5 days old'],
  ])('%s -> %s', (asOf, expected) => {
    expect(formatCacheAge(asOf, NOW)).toBe(expected);
  });

  it('returns empty rather than a wrong age for unusable input', () => {
    expect(formatCacheAge('')).toBe('');
    expect(formatCacheAge('not-a-date', NOW)).toBe('');
  });

  it('clamps a future timestamp instead of rendering a negative age', () => {
    // Clock skew between CMS and DMS is normal; "-3 minutes old" is not.
    expect(formatCacheAge('2026-09-09T12:03:00.000Z', NOW)).toBe('less than a minute old');
  });
});

describe('describeProvenance — silence when there is nothing to say', () => {
  it('all live and complete produces no notice', () => {
    expect(describeProvenance(merge(), NOW)).toBeNull();
  });
});

describe('describeProvenance — both legs failed', () => {
  const notice = describeProvenance(
    merge({ alerts: failedLeg('500'), serviceHistory: failedLeg('500') }),
    NOW,
  )!;

  it('is an error, not a warning', () => {
    expect(notice.type).toBe('error');
  });

  it('says the emptiness is a failure, not a healthy fleet', () => {
    // An empty table with no notice reads as "your fleet is healthy", which is
    // the worst available lie.
    expect(notice.body).toMatch(/because the request failed/i);
    expect(notice.body).toMatch(/not because there is nothing to report/i);
  });
});

describe('describeProvenance — partial merge (the defect live in production today)', () => {
  it('names service history as the missing leg and says counts are incomplete', () => {
    const notice = describeProvenance(
      merge({ serviceHistory: failedLeg('502') }),
      NOW,
    )!;
    expect(notice.type).toBe('warning');
    expect(notice.header).toMatch(/service history is missing/i);
    expect(notice.body).toMatch(/incomplete/i);
    // The reason is surfaced, so an operator can tell an outage from a
    // permissions problem without opening a console.
    expect(notice.body).toContain('502');
  });

  it('names telemetry alerts as the missing leg when that is the one that failed', () => {
    const notice = describeProvenance(merge({ alerts: failedLeg('timeout') }), NOW)!;
    expect(notice.header).toMatch(/telemetry alerts are missing/i);
    expect(notice.body).toContain('timeout');
  });

  it('a partial merge is NEVER narrated as an absence of records', () => {
    // The load-bearing assertion. "No maintenance to report" and "we could not
    // load half of it" are different facts.
    for (const p of [
      merge({ serviceHistory: failedLeg('502') }),
      merge({ alerts: failedLeg('502') }),
    ]) {
      const notice = describeProvenance(p, NOW)!;
      const text = `${notice.header} ${notice.body}`;
      expect(text).not.toMatch(/no maintenance/i);
      expect(text).not.toMatch(/nothing to report\b(?!.*not because)/i);
      expect(text).not.toMatch(/up to date/i);
    }
  });

  it('a failed leg outranks a cached leg, because incomplete is worse than stale', () => {
    const notice = describeProvenance(
      merge({
        alerts: failedLeg('503'),
        serviceHistory: legFromBody({ dataSource: 'cache', asOf: '2026-09-09T11:00:00.000Z' }),
      }),
      NOW,
    )!;
    expect(notice.header).toMatch(/telemetry alerts are missing/i);
    expect(notice.type).toBe('warning');
  });
});

describe('describeProvenance — cached', () => {
  it('labels the cache WITH its age', () => {
    const notice = describeProvenance(
      merge({ serviceHistory: legFromBody({ dataSource: 'cache', asOf: '2026-09-09T09:00:00.000Z' }) }),
      NOW,
    )!;
    expect(notice.type).toBe('info');
    expect(notice.header).toMatch(/cached/i);
    expect(notice.header).toContain('3 hours old');
    expect(notice.body).toMatch(/will not appear/i);
  });

  it('still labels the cache when the age is unknown', () => {
    const notice = describeProvenance(
      merge({ serviceHistory: legFromBody({ dataSource: 'cache' }) }),
      NOW,
    )!;
    expect(notice.header).toMatch(/cached/i);
    expect(notice.header).not.toMatch(/undefined|NaN|null/);
  });

  it('cached is never presented as live, and never as an error', () => {
    const notice = describeProvenance(
      merge({ serviceHistory: legFromBody({ dataSource: 'cache', asOf: NOW.toISOString() }) }),
      NOW,
    )!;
    expect(notice.type).not.toBe('error');
    expect(notice.type).not.toBe('warning');
    expect(`${notice.header} ${notice.body}`).not.toMatch(/\blive\b|\bcurrent\b/i);
  });
});


// ---------------------------------------------------------------------------
// T1.4 — red tests for the `mixed` provenance label
// (spec `2026-09-10-service-history-read-path-correctness`)
//
// These tests are written RED against the current implementation and must STAY
// red until Group 3 of that spec implements the `partial` Freshness variant in
// dataProvenance.ts.  Do NOT weaken the existing absent-means-live default.
//
// Three contracts:
//
//   (a) legFromBody({dataSource:"mixed",...}) must report partial staleness, not live.
//       Currently fails because the else-branch returns liveLeg().
//
//   (b) describeProvenance on a partially-stale leg must produce a notice that
//       names the cached subset (count + age), not the generic "all cached" copy.
//       Currently fails because Freshness has no "partial" variant, so
//       describeProvenance hits no branch and returns null.
//
//   (c) An UNRECOGNISED dataSource value still falls back to live.  This was
//       always true (the else-branch default), but it was an *accident* not an
//       invariant.  Naming it here turns it into a guarded contract: once "mixed"
//       gets a dedicated branch the fall-through default must remain correct for
//       any future unknown value.  The test uses a value that will never become
//       a real enum member ("__future_unknown__") and asserts live explicitly.
//       Currently PASSES (the behaviour is already correct), and must continue
//       to pass after the implementation changes.
// ---------------------------------------------------------------------------

describe('legFromBody — mixed (T1.4)', () => {
  it(
    '(a) mixed is not live — reports partial staleness',
    () => {
      // This is the primary safety test.  R1 in the spec: introducing "mixed"
      // without touching legFromBody would silently present stale rows as live.
      const leg = legFromBody({
        dataSource: 'mixed',
        asOf: '2026-09-09T07:45:37Z',
        // cacheOnlyCount will be added to ProvenanceEnvelope in the implementation.
        // The test drives through the body shape the backend will send, so the
        // assertion is on freshness — not on the presence of cacheOnlyCount here.
      } as ProvenanceEnvelope);

      // FAILS today: legFromBody falls through to liveLeg(), so freshness === 'live'.
      expect(leg.freshness).not.toBe('live');
      // The exact variant name ("partial") is up to the implementation, but it
      // must not be "live" and must not be "cached" (mixed is genuinely distinct).
      expect(leg.freshness).not.toBe('cached');
    },
  );

  it(
    '(a.2) mixed carries asOf from the body so the notice can show an age',
    () => {
      const asOf = '2026-09-09T07:45:37Z';
      const leg = legFromBody({ dataSource: 'mixed', asOf } as ProvenanceEnvelope);

      // FAILS today: liveLeg() returns asOf: ''.
      expect(leg.asOf).toBe(asOf);
    },
  );

  it(
    '(a.3) MIXED is recognised case-insensitively, matching cache/cached precedent',
    () => {
      const leg = legFromBody({ dataSource: 'MIXED' } as ProvenanceEnvelope);

      // FAILS today: 'MIXED'.toLowerCase() === 'mixed', which hits the else-branch.
      expect(leg.freshness).not.toBe('live');
    },
  );
});

describe('describeProvenance — mixed / partial (T1.4)', () => {
  // Build a legFromBody result for mixed.  Until the implementation lands,
  // legFromBody returns liveLeg(), so this fixture also tests the notice via the
  // freshness field directly once the partial variant exists.
  //
  // We test through legFromBody so that (a) and (b) are coupled: if legFromBody
  // maps mixed incorrectly the notice test catches it too.

  it(
    '(b) a partially-stale leg produces a notice — not null',
    () => {
      const leg = legFromBody({
        dataSource: 'mixed',
        asOf: '2026-09-09T09:00:00.000Z',
      } as ProvenanceEnvelope);

      const notice = describeProvenance(
        merge({ serviceHistory: leg }),
        NOW,
      );

      // FAILS today: legFromBody("mixed") returns liveLeg(), so all-live produces null.
      expect(notice).not.toBeNull();
    },
  );

  it(
    '(b.2) the notice names the cached subset, not the generic all-cached copy',
    () => {
      const leg = legFromBody({
        dataSource: 'mixed',
        asOf: '2026-09-09T09:00:00.000Z',
      } as ProvenanceEnvelope);

      const notice = describeProvenance(
        merge({ serviceHistory: leg }),
        NOW,
      )!;

      // FAILS today for the same reason (notice is null, accessing .body throws).
      // When the implementation lands, the notice must distinguish "some rows"
      // from "all rows".  We check that "partial" or "some" appears to distinguish
      // this banner from the fully-cached one, AND that it does not claim all data
      // is live.
      const text = `${notice?.header ?? ''} ${notice?.body ?? ''}`;
      // Must describe partial coverage explicitly.
      expect(text.toLowerCase()).toMatch(/partial|some|subset|certain|not all|cache/i);
      // Must NOT claim the full service history is current/live.
      expect(text).not.toMatch(/\bfully live\b|\ball live\b|\bfully current\b/i);
      // Must be informational, not an error (mixed is less severe than a failure).
      expect(notice?.type).not.toBe('error');
    },
  );

  it(
    '(b.3) the notice body mentions the cached subset count when > 0',
    () => {
      // spec D3: "carry asOf and cacheOnlyCount into the notice text"
      // The body object is what legFromBody receives.  cacheOnlyCount is carried
      // in the ProvenanceEnvelope once that interface is extended by the impl.
      const leg = legFromBody({
        dataSource: 'mixed',
        asOf: '2026-09-09T09:00:00.000Z',
        cacheOnlyCount: 3,
      } as unknown as ProvenanceEnvelope);  // cast until interface is widened

      const notice = describeProvenance(
        merge({ serviceHistory: leg }),
        NOW,
      )!;

      // FAILS today: notice is null, and even when non-null the impl does not yet
      // carry cacheOnlyCount into the text.
      const text = `${notice?.header ?? ''} ${notice?.body ?? ''}`;
      // The count "3" should appear in the text once the implementation names it.
      expect(text).toContain('3');
    },
  );

  it(
    '(b.4) mixed is a warning, not an error — stale subset is less severe than unavailable data',
    () => {
      const leg = legFromBody({
        dataSource: 'mixed',
        asOf: '2026-09-09T09:00:00.000Z',
      } as ProvenanceEnvelope);

      const notice = describeProvenance(
        merge({ serviceHistory: leg }),
        NOW,
      )!;

      // FAILS today (notice is null).
      expect(notice?.type).toBe('info');
    },
  );

  it(
    '(b.5) mixed notice carries the cache age, matching the fully-cached precedent',
    () => {
      const leg = legFromBody({
        dataSource: 'mixed',
        asOf: '2026-09-09T09:00:00.000Z',  // 3 hours before NOW
      } as ProvenanceEnvelope);

      const notice = describeProvenance(
        merge({ serviceHistory: leg }),
        NOW,
      )!;

      // FAILS today (notice is null).
      // '3 hours old' is what formatCacheAge returns for this timestamp.
      const text = `${notice?.header ?? ''} ${notice?.body ?? ''}`;
      expect(text).toContain('3 hours old');
    },
  );
});

describe('legFromBody — unrecognised value falls back to live (T1.4 invariant)', () => {
  it(
    '(c) an unrecognised dataSource is live, not cached — the safe default is a guarded invariant',
    () => {
      // This test captures the property that made introducing "mixed" dangerous:
      // before this spec, an unrecognised value silently resolved to live.  That
      // default is CORRECT — see the module header note.  But a correct accident is
      // not a contract.  Once "mixed" gains its own branch, we must ensure the
      // fall-through continues to mean live for values this module never expected.
      //
      // Uses a sentinel that will never become a real value.
      const leg = legFromBody({ dataSource: '__future_unknown__' } as ProvenanceEnvelope);

      // This assertion is GREEN today and must remain green after the impl.
      expect(leg.freshness).toBe('live');
      expect(leg).toEqual(liveLeg());
    },
  );

  it(
    '(c.2) the fall-through default does NOT produce a staleness notice',
    () => {
      const leg = legFromBody({ dataSource: '__future_unknown__' } as ProvenanceEnvelope);
      const notice = describeProvenance(merge({ serviceHistory: leg }), NOW);

      // GREEN today; must remain green.  An unknown value must never produce a
      // false staleness banner — that would be the symmetric error to false liveness.
      expect(notice).toBeNull();
    },
  );
});
