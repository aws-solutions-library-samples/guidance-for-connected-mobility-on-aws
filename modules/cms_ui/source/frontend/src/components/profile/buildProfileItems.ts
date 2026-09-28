// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * buildProfileItems — pure function for the account-dropdown items array.
 *
 * ## 2026-09-02: persona-switch dropdown group DELETED
 *
 * The persona-switch group (Fix Group E, spec 2026-08-05-cms-demo-identity-model)
 * was removed 2026-09-02 as the final part of retiring the demo-password auth
 * surface. Reasons:
 *
 *  1. Federate-only auth model: Amazon employees Federate-sign-in as
 *     `platform-admin` and use a role-emulation switcher (or the DMS
 *     PersonaSwitcher) for demo. Password-based persona switching in the
 *     account dropdown is inconsistent with that model.
 *  2. Post-edge-gate-drop the CMS staging URL is publicly reachable. The
 *     persona-switch mechanism resolved a password from
 *     `runtimeConfig.demoPasswords` and called `login(email, password)` —
 *     effectively a "hop between demo admin accounts" affordance no
 *     external visitor should see.
 *  3. The parallel auto-sign-in useEffect in SimpleAuthProvider (Group E
 *     E1) that consumed the same `demoPasswords` config was deleted in
 *     commit `1349ef78`. This function is now the sole remaining consumer
 *     of the same config and should be retired for symmetry.
 *
 * Signature reduced accordingly: the `demoPasswords`, `showDemoButtons`,
 * and `currentEmail` parameters are gone. See
 * `issues/2026-09-02-cms-persona-dropdown-removed/` for context.
 */

import type { ButtonDropdownProps } from '@cloudscape-design/components';

/**
 * Builds the ButtonDropdownProps.Items array for the account dropdown.
 *
 * @param opts  URL constants needed for the Support sub-group items.
 */
export function buildProfileItems(
  opts: {
    igRoot: string;
    feedbackUrl: string;
    supportUrl: string;
  },
): ButtonDropdownProps.Items {
  return [
    { id: 'preferences', text: 'Preferences' },
    { id: 'switchTheme', text: 'Switch Theme' },
    {
      id: 'support-group',
      text: 'Support',
      items: [
        {
          id: 'documentation',
          text: 'Documentation',
          href: opts.igRoot,
          external: true,
          externalIconAriaLabel: ' (opens in new tab)',
        },
        {
          id: 'feedback',
          text: 'Feedback',
          href: opts.feedbackUrl,
          external: true,
          externalIconAriaLabel: ' (opens in new tab)',
        },
        {
          id: 'support',
          text: 'Customer support',
          href: opts.supportUrl,
          external: true,
          externalIconAriaLabel: ' (opens in new tab)',
        },
      ],
    },
    {
      id: 'signout',
      text: 'Sign out',
      ariaLabel: 'Sign out',
      iconName: 'lock-private',
    },
  ];
}
