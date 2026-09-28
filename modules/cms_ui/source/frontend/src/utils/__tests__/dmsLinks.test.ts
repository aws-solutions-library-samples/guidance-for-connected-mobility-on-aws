// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * dmsLinks — deep links into the DMS console (FG5,
 * issues/2026-09-25-diagnostics-view-ro-link-404/).
 *
 * The property that matters: a link is either absolute on the configured https
 * DMS origin, or absent. Never relative, because a relative path resolves
 * against CMS and opens CMS's 404 page.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { dmsFleetRepairOrderUrl, getDmsUiOrigin } from '../dmsLinks';

const saved = (window as any).runtimeConfig;

function setOrigin(value: unknown) {
  (window as any).runtimeConfig = { awsRegion: 'us-west-2', apiEndpoint: 'https://api.example.com/', dmsUiOrigin: value };
}

beforeEach(() => {
  delete (window as any).runtimeConfig;
});

afterEach(() => {
  (window as any).runtimeConfig = saved;
});

describe('getDmsUiOrigin', () => {
  it('returns null when runtimeConfig has no dmsUiOrigin', () => {
    (window as any).runtimeConfig = { awsRegion: 'us-west-2', apiEndpoint: 'x' };
    expect(getDmsUiOrigin()).toBeNull();
  });

  it.each([
    [''], ['   '], ['not a url'], ['http://dms.example.com'], ['javascript:alert(1)'],
    ['ftp://dms.example.com'], ['data:text/html,x'], ['//dms.example.com'], [42], [null],
  ])(
    'returns null for %j',
    (value) => {
      setOrigin(value);
      expect(getDmsUiOrigin()).toBeNull();
    },
  );

  it('accepts a bare https origin, with or without a trailing slash or port', () => {
    for (const [value, expected] of [
      ['https://dms.example.com', 'https://dms.example.com'],
      ['https://dms.example.com/', 'https://dms.example.com'],
      ['https://dms.example.com:8443', 'https://dms.example.com:8443'],
      ['https://DMS.Example.com', 'https://dms.example.com'],
    ]) {
      setOrigin(value);
      expect(getDmsUiOrigin()).toBe(expected);
    }
  });

  it.each([
    ['https://good.example.com@evil.example.com'],  // userinfo: URL.origin would be evil.example.com
    ['https://@evil.example.com'],                  // empty userinfo: every parsed part is empty
    ['https://:@evil.example.com'],
    ['https://dms.example.com:443'],                // default port: the origin drops it, so the text differs
    ['https://user:pw@dms.example.com'],
    ['https://dms.example.com/some/path'],
    ['https://dms.example.com/?next=x'],
    ['https://dms.example.com/#frag'],
  ])('refuses %s instead of trimming it to an origin', (value) => {
    setOrigin(value);
    expect(getDmsUiOrigin()).toBeNull();
  });
});

describe('dmsFleetRepairOrderUrl', () => {
  it('builds the DMS fleet repair-order page URL on the configured origin', () => {
    setOrigin('https://dms.example.com');
    expect(dmsFleetRepairOrderUrl('47c2b8e7-0352-4b25-9b09-5ce6d41605ed')).toBe(
      'https://dms.example.com/fleet/repair-orders/47c2b8e7-0352-4b25-9b09-5ce6d41605ed',
    );
  });

  it('encodes the id as one path segment', () => {
    setOrigin('https://dms.example.com');
    expect(dmsFleetRepairOrderUrl('a/b?c#d')).toBe('https://dms.example.com/fleet/repair-orders/a%2Fb%3Fc%23d');
  });

  it('returns null, never a relative path, when no origin is configured', () => {
    expect(dmsFleetRepairOrderUrl('RO-1')).toBeNull();
  });

  it('returns null for a blank or non-string id', () => {
    setOrigin('https://dms.example.com');
    for (const id of ['  ', 42, null, undefined, { id: 'x' }]) {
      expect(dmsFleetRepairOrderUrl(id)).toBeNull();
    }
  });

  it('never returns anything but an absolute https URL', () => {
    const origins = ['https://dms.example.com', 'http://dms.example.com', 'javascript:alert(1)',
      'ftp://dms.example.com', 'data:text/html,x', '', undefined, 'not a url'];
    for (const origin of origins) {
      setOrigin(origin);
      const url = dmsFleetRepairOrderUrl('RO-1');
      if (url !== null) expect(url.startsWith('https://')).toBe(true);
    }
  });
});
