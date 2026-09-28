// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for `getCommandsApiBase()`.
 *
 * Regression guard for the 2026-08-03 fix (issue
 * `issues/2026-08-03-ui-pwa-manifest-and-commands-slash/`).
 *
 * Contract under test:
 *  - Strip a single trailing slash from `runtimeConfig.commandsApiEndpoint`.
 *  - Strip repeated trailing slashes (defensive against future misconfig).
 *  - Leave a URL without a trailing slash unchanged.
 *  - Return an empty string when `commandsApiEndpoint` is unset (matches the
 *    prior inline `... || ''` fallback that call sites relied on).
 *  - Do NOT strip slashes that are not trailing (protocol `://`, path parts).
 */
import { describe, expect, it, beforeEach, afterEach } from 'vitest';

import { getCommandsApiBase } from './api-config';

const setEndpoint = (value: string | undefined) => {
  (globalThis as any).window = (globalThis as any).window || {};
  (window as any).runtimeConfig = value === undefined ? undefined : { commandsApiEndpoint: value };
};

describe('getCommandsApiBase', () => {
  const originalConfig = (window as any).runtimeConfig;

  beforeEach(() => {
    (window as any).runtimeConfig = undefined;
  });

  afterEach(() => {
    (window as any).runtimeConfig = originalConfig;
  });

  it('strips a single trailing slash', () => {
    setEndpoint('https://xxxxx.execute-api.us-east-1.amazonaws.com/prod/');
    expect(getCommandsApiBase()).toBe('https://xxxxx.execute-api.us-east-1.amazonaws.com/prod');
  });

  it('strips repeated trailing slashes', () => {
    setEndpoint('https://xxxxx.execute-api.us-east-1.amazonaws.com/prod///');
    expect(getCommandsApiBase()).toBe('https://xxxxx.execute-api.us-east-1.amazonaws.com/prod');
  });

  it('leaves a URL without a trailing slash unchanged', () => {
    setEndpoint('https://xxxxx.execute-api.us-east-1.amazonaws.com/prod');
    expect(getCommandsApiBase()).toBe('https://xxxxx.execute-api.us-east-1.amazonaws.com/prod');
  });

  it('preserves internal slashes (protocol and path segments)', () => {
    setEndpoint('https://xxxxx.execute-api.us-east-1.amazonaws.com/prod/v2/');
    expect(getCommandsApiBase()).toBe('https://xxxxx.execute-api.us-east-1.amazonaws.com/prod/v2');
  });

  it('returns empty string when commandsApiEndpoint is unset', () => {
    setEndpoint(undefined);
    expect(getCommandsApiBase()).toBe('');
  });

  it('returns empty string when runtimeConfig is undefined', () => {
    (window as any).runtimeConfig = undefined;
    expect(getCommandsApiBase()).toBe('');
  });

  it('returns empty string when commandsApiEndpoint is an empty string', () => {
    setEndpoint('');
    expect(getCommandsApiBase()).toBe('');
  });

  it('produces a clean URL when concatenated with a leading-slash path', () => {
    // The whole point of the helper — no `//` in the resulting URL.
    setEndpoint('https://xxxxx.execute-api.us-east-1.amazonaws.com/prod/');
    const url = `${getCommandsApiBase()}/api/commands/VEH-123`;
    expect(url).toBe('https://xxxxx.execute-api.us-east-1.amazonaws.com/prod/api/commands/VEH-123');
    expect(url).not.toContain('//api');
  });
});
