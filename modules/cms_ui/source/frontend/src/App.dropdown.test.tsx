// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * App.dropdown.test.tsx — regression guards for the account-dropdown items.
 *
 * ## 2026-09-02: rewritten — persona-switch tests DELETED, guards KEPT
 *
 * The full persona-switch test suite (buildProfileItems Fix Cycle 2 wiring +
 * profileActions persona items sections (a)–(f)) exercised code that was
 * deleted 2026-09-02 as the final part of retiring the demo-password auth
 * surface — see issues/2026-09-02-cms-persona-dropdown-removed/. Those tests
 * are gone.
 *
 * This file is now bounded to three regression guards:
 *   1. `buildProfileItems` returns the expected static items in the expected
 *      order (preferences → switchTheme → support-group → signout).
 *   2. `buildProfileItems` produces NO `persona:*` id anywhere in the tree.
 *   3. `buildProfileItems` produces NO `persona-group` container.
 *
 * If any future edit reintroduces persona-switch dropdown items, (2) or (3)
 * fails and the reviewer gets a loud signal that the dead-code invariant is
 * being violated.
 */

import { describe, expect, it } from 'vitest';
import { buildProfileItems } from './components/profile/buildProfileItems';

const TEST_URLS = {
  igRoot: 'https://example.com/ig/',
  feedbackUrl: 'https://example.com/feedback',
  supportUrl: 'https://example.com/support',
};

describe('buildProfileItems — Federate-only account dropdown (2026-09-02 regression guards)', () => {
  it('returns items in the documented order: preferences, switchTheme, support-group, signout', () => {
    const items = buildProfileItems(TEST_URLS);
    const ids = items.map((i: any) => i.id);
    expect(ids).toEqual(['preferences', 'switchTheme', 'support-group', 'signout']);
  });

  it('does not emit any persona-group container (persona-switch dropdown deleted 2026-09-02)', () => {
    const items = buildProfileItems(TEST_URLS);
    const ids = items.map((i: any) => i.id);
    expect(ids).not.toContain('persona-group');
  });

  it('does not emit any persona:<email> item id anywhere in the tree', () => {
    const items = buildProfileItems(TEST_URLS);
    const allIds: string[] = [];
    const collect = (node: any) => {
      if (node?.id) allIds.push(node.id);
      if (Array.isArray(node?.items)) node.items.forEach(collect);
    };
    items.forEach(collect);
    for (const id of allIds) {
      expect(id.startsWith('persona:')).toBe(false);
    }
  });

  it('support-group contains documentation + feedback + customer-support items with the passed URLs', () => {
    const items = buildProfileItems(TEST_URLS);
    const supportGroup: any = items.find((i: any) => i.id === 'support-group');
    expect(supportGroup).toBeDefined();
    const subIds = supportGroup.items.map((i: any) => i.id);
    expect(subIds).toEqual(['documentation', 'feedback', 'support']);
    const supportUrls = supportGroup.items.map((i: any) => i.href);
    expect(supportUrls).toEqual([TEST_URLS.igRoot, TEST_URLS.feedbackUrl, TEST_URLS.supportUrl]);
  });

  it('signout item is present and marked with the lock-private icon', () => {
    const items = buildProfileItems(TEST_URLS);
    const signout: any = items.find((i: any) => i.id === 'signout');
    expect(signout).toBeDefined();
    expect(signout.text).toBe('Sign out');
    expect(signout.iconName).toBe('lock-private');
  });
});
