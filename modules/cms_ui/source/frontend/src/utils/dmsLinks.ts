// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Deep links from CMS into the standalone DMS console.
 *
 * DMS is a separate app on its own origin. Its address comes from
 * `runtimeConfig.dmsUiOrigin`, which `make regenerate-runtime-config` writes from
 * the stage's `DMS_UI_CALLBACK_ORIGIN` (`deployment/config/<stage>.env`). It is
 * never hardcoded: the host is stage-specific and an internal hostname, which the
 * pre-sync secret scan correctly refuses to ship.
 *
 * Every helper returns `null` when the origin is not configured or is not an
 * https origin. Callers render the plain repair-order id then, never a link: a
 * relative path would resolve against CMS's own origin and open CMS's 404 page
 * (issues/2026-09-25-diagnostics-view-ro-link-404/).
 */

import { getRuntimeConfig } from '../config/api';

/**
 * The DMS console origin (scheme + host, optional port), or null when unset, not
 * https, or not a bare origin. A value carrying userinfo, a path, a query or a
 * fragment is refused rather than trimmed: `new URL('https://good@evil').origin`
 * is `https://evil`, so trimming would hide exactly the malformed values that
 * should fail loudly.
 */
export function getDmsUiOrigin(): string | null {
  const raw = getRuntimeConfig().dmsUiOrigin;
  if (typeof raw !== 'string' || raw.trim() === '') return null;
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  if (url.protocol !== 'https:') return null;
  // The configured text must BE the origin (a trailing slash aside, and host
  // case aside, since URL lowercases it). Comparing text rather than parsed parts
  // also refuses empty userinfo (`https://@evil`), whose parts are all empty.
  if (raw.trim().replace(/\/$/, '').toLowerCase() !== url.origin) return null;
  return url.origin;
}

/**
 * DMS's read-only fleet repair-order page for `roId`
 * (`/fleet/repair-orders/:roId`, DMS spec `2026-09-25-dms-fleet-ro-page`), or
 * null when the DMS origin is not configured or the id is blank or not a string.
 * Takes `unknown` because DispatchModal passes the id straight from the dispatch
 * response; a non-string must yield no link rather than throw after DMS has
 * already created the repair order.
 */
export function dmsFleetRepairOrderUrl(roId: unknown): string | null {
  if (typeof roId !== 'string') return null;
  const id = roId.trim();
  const origin = getDmsUiOrigin();
  if (!origin || !id) return null;
  return `${origin}/fleet/repair-orders/${encodeURIComponent(id)}`;
}
