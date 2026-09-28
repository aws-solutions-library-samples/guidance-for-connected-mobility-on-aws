// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ProvenanceField — provenance-enforced value display.
 *
 * ## Visual display removed 2026-09-05 (amends spec D5)
 *
 * D5 originally read: "'simulated' renders a visible label". That was implemented as a
 * blue `simulated` Badge beside every value — and with 227 call sites across 26 screens,
 * the result was a portal too noisy to read or demo. User verdict on first sight of the
 * built UI: "it's hard to use the ui with all that crazy data."
 *
 * The badge is gone. What remains is the part that was load-bearing:
 *
 *   - `assertProvenance()` still throws at render time on a missing or unrecognised
 *     marker. The contract is unchanged and the runtime guard is unchanged.
 *   - `provenance: "absent"` still renders an explicit em-dash rather than nothing, so
 *     "no data" stays distinguishable from zero or empty-string.
 *   - The `data-testid` suffix still encodes the marker (`<testId>-simulated`,
 *     `-live`, `-absent`). Provenance therefore remains machine-checkable in the DOM
 *     and in tests without being visible to a reader.
 *
 * That last point is why this is a presentation change rather than a retreat from D5's
 * intent. The honest-data property was never carried by the badge — it is carried by the
 * fixture-shape guard, the render-path guard, and the runtime throw, all of which still
 * hold. The badge only *displayed* a property that was already enforced elsewhere.
 *
 * The "all data here is simulated" statement now appears once, on the sign-in screen,
 * plus a compact indicator in the top bar so a screenshot of any single screen cannot be
 * mistaken for live data.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/ — see decisions.md,
 * entry "2026-09-05 — Provenance display removed from every value".
 *
 * Zero-import constraint (T2.5):
 * Zero imports from *.fixture.ts, any API client, fetch/XHR, or ../screens/.
 */

import Box from "@cloudscape-design/components/box";
import React from "react";
import { type ProvenanceValue, assertProvenance } from "../../types";

interface ProvenanceFieldProps<T> {
  /** The provenance-wrapped value to display. */
  field: ProvenanceValue<T> | undefined | null;
  /** Human-readable label for this field (used in error messages and testids). */
  label: string;
  /** Render the value as a string. Defaults to String(). */
  render?: (value: T) => string;
  /** CSS class for the value text. */
  className?: string;
  /** data-testid for the root element. */
  testId?: string;
}

/**
 * Render a provenance-wrapped field, enforcing the D5 contract at render time.
 *
 * Throws via assertProvenance() if the marker is absent or invalid — a component is
 * never allowed to render a value whose origin it did not receive.
 */
function ProvenanceField<T>({
  field,
  label,
  render = (v: T) => String(v),
  testId,
}: ProvenanceFieldProps<T>): React.ReactElement {
  // D5 enforcement: throws if marker is missing or invalid. Unchanged.
  assertProvenance(field, label);

  if (field.provenance === "absent") {
    const resolvedTestId = testId ? `${testId}-absent` : `field-${label}-absent`;
    return (
      <Box data-testid={resolvedTestId} color="text-status-inactive">
        <em>—</em>
      </Box>
    );
  }

  const displayValue = field.value != null ? render(field.value) : "—";

  // `simulated` and `live` render identically to the reader. The marker survives in the
  // testid suffix, which is what guards and tests assert on.
  const suffix = field.provenance === "simulated" ? "simulated" : "live";
  const resolvedTestId = testId
    ? `${testId}-${suffix}`
    : `field-${label}-${suffix}`;

  return (
    <Box data-testid={resolvedTestId}>
      <span>{displayValue}</span>
    </Box>
  );
}

export default ProvenanceField;
