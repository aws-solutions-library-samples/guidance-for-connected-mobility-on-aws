// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Unit tests for the shared status helper.
 *
 * See issues/2026-09-25-status-casing-icons/ for the reason this helper exists.
 * The mutation-check step at the end of that issue's summary references the
 * cases below.
 */

import { describe, it, expect } from 'vitest';
import { statusEquals, isStatusActive } from '../statusMatch';

describe('statusMatch — statusEquals', () => {
  it('matches identical casing', () => {
    expect(statusEquals('active', 'active')).toBe(true);
    expect(statusEquals('ACTIVE', 'ACTIVE')).toBe(true);
  });

  it('matches lowercase vs uppercase', () => {
    expect(statusEquals('active', 'ACTIVE')).toBe(true);
    expect(statusEquals('ACTIVE', 'active')).toBe(true);
  });

  it('matches title case vs lowercase', () => {
    expect(statusEquals('Active', 'active')).toBe(true);
    expect(statusEquals('Active', 'ACTIVE')).toBe(true);
  });

  it('does not match different statuses', () => {
    expect(statusEquals('active', 'terminated')).toBe(false);
    expect(statusEquals('ACTIVE', 'COMPLETED')).toBe(false);
    expect(statusEquals('active', 'inactive')).toBe(false);
  });

  it('returns false for non-string inputs', () => {
    expect(statusEquals(undefined, 'active')).toBe(false);
    expect(statusEquals(null, 'active')).toBe(false);
    expect(statusEquals('active', undefined)).toBe(false);
    expect(statusEquals('active', null)).toBe(false);
  });

  it('is case-insensitive on multi-word enum values', () => {
    expect(statusEquals('ON_LEAVE', 'on_leave')).toBe(true);
    expect(statusEquals('On_Leave', 'on_leave')).toBe(true);
  });
});

describe('statusMatch — isStatusActive', () => {
  it('returns true for every casing observed on staging', () => {
    expect(isStatusActive('active')).toBe(true);
    expect(isStatusActive('ACTIVE')).toBe(true);
    expect(isStatusActive('Active')).toBe(true);
  });

  it('returns false for non-active values', () => {
    expect(isStatusActive('on_leave')).toBe(false);
    expect(isStatusActive('terminated')).toBe(false);
    expect(isStatusActive('COMPLETED')).toBe(false);
    expect(isStatusActive('in_progress')).toBe(false);
    expect(isStatusActive('')).toBe(false);
  });

  it('returns false for non-string inputs', () => {
    expect(isStatusActive(undefined)).toBe(false);
    expect(isStatusActive(null)).toBe(false);
  });
});
