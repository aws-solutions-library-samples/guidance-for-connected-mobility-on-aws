// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DealerPersonaBanner — dismissible banner shown to dealer-only users.
 *
 * Renders ONLY when the signed-in user's Cognito groups are ALL contained in
 * the 7-group DMS dealer set AND the user holds none of the CMS-side roles
 * (fleet-operator, fleet-viewer, fleet-guest, platform-admin, product-engineer,
 * dispatcher, connect-agent). This prevents CMS fleet personas who coincidentally
 * have a dealer group from seeing the banner.
 *
 * Per spec T4.2 (2026-08-31-dms-standalone-ui) and Decision D6.
 */

import React, { useState } from 'react';
import Flashbar from '@cloudscape-design/components/flashbar';

import { useAuth } from './useAuth';
import { getDmsUiUrl } from '../config/api';

/**
 * The 7 Cognito groups that belong exclusively to the DMS dealer surface.
 * Mirrors `DMS_ROUTE_GROUPS` values (guidance-for-dealer-management-system-on-aws)
 * and `ui_stack.py:706-785`.
 */
const DEALER_GROUPS = new Set([
  'dealer-admin',
  'service-advisor',
  'f-and-i-manager',
  'district-manager',
  'parts-manager',
  'bdc-rep',
  'dms-viewer',
]);

/**
 * CMS-native groups that, when present, suppress the banner. A user who is
 * also a fleet-operator or platform-admin is a CMS user first and should not
 * be directed away.
 */
const CMS_GROUPS = new Set([
  'platform-admin',
  'fleet-operator',
  'fleet-viewer',
  'fleet-guest',
  'product-engineer',
  'dispatcher',
  'connect-agent',
  'agent',
]);

/** Returns true iff the user holds at least one dealer group and NO CMS groups. */
function isDealerOnlyUser(groups: string[]): boolean {
  if (!groups.length) return false;
  const hasDealerGroup = groups.some((g) => DEALER_GROUPS.has(g));
  const hasCmsGroup = groups.some((g) => CMS_GROUPS.has(g));
  return hasDealerGroup && !hasCmsGroup;
}

/**
 * Dismissible banner pointing dealer-persona users to the standalone DMS app.
 * Returns null when:
 *   - the user is not authenticated
 *   - the user holds any CMS-native group (fleet/admin/engineer/dispatcher)
 *   - the banner has been dismissed for this session
 *
 * The DMS URL comes from `runtimeConfig.dmsUiUrl` per stage. When it is absent
 * the banner still renders — the user still needs to know they are in the wrong
 * app — but WITHOUT a link, rather than linking somewhere that does not resolve.
 * An earlier revision hardcoded a hardcoded internal-domain host, a
 * domain that does not exist; it also tripped `ui-quick-deploy`'s pre-sync
 * secret scan, since the internal corp domain suffix is a forbidden string that must not be
 * baked into a publishable bundle.
 */
export const DealerPersonaBanner: React.FC = () => {
  const { user, isAuthenticated } = useAuth();
  const [dismissed, setDismissed] = useState(false);

  if (!isAuthenticated || !user || dismissed) return null;
  if (!isDealerOnlyUser(user.groups)) return null;

  const dmsUrl = getDmsUiUrl();

  return (
    <Flashbar
      items={[
        {
          type: 'info',
          dismissible: true,
          onDismiss: () => setDismissed(true),
          header: 'Dealer Management System',
          content: dmsUrl ? (
            <>
              {"You're signed in as a dealer persona. "}
              <a href={dmsUrl} target="_blank" rel="noopener noreferrer">
                Open the dealer console
              </a>
            </>
          ) : (
            <>
              {"You're signed in as a dealer persona. The dealer console is a "}
              {'separate application; ask your administrator for its address.'}
            </>
          ),
          id: 'dealer-persona-banner',
        },
      ]}
    />
  );
};

export default DealerPersonaBanner;
