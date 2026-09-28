// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * RawDrawer — expandable raw-JSON response drawer.
 *
 * Extracted verbatim from `VehicleDiagnosticsPanel.tsx` (lines ~723-770).
 * Preserves byte-identical UI output including:
 *   - 4 096-byte truncation boundary (RESPONSE_DISPLAY_THRESHOLD)
 *   - data: URI download link (avoids Blob lifecycle management)
 *   - `data-testid` values driven by `commandId` prop
 *
 * Used as the fallback renderer for routine IDs that have no registered
 * custom renderer, and for legacy rows (pre-schema) that fail schema validation.
 *
 * Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/ Group 3 T3.1
 * R4: must produce byte-identical UI to the inline version.
 */

import React from 'react';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Box from '@cloudscape-design/components/box';
import type { RoutineRendererProps } from './types';

// Truncation boundary — must remain 4 096 to match the inline version.
const RESPONSE_DISPLAY_THRESHOLD = 4096;

/**
 * RawDrawer — fallback renderer; renders the raw response payload as
 * pretty-printed JSON inside an ExpandableSection.
 *
 * When the serialised string exceeds 4 KB: content is truncated and a
 * data: URI download affordance is provided instead of dumping the full payload.
 */
export const RawDrawer: React.FC<RoutineRendererProps> = ({
  response,
  commandId,
}) => {
  // When status is not SUCCEEDED or response is empty, render nothing
  // (matches the inline guard: "status === 'SUCCEEDED' && cmd.response !== undefined")
  if (response.status !== 'SUCCEEDED') {
    return null;
  }

  const id = commandId ?? 'raw';
  const jsonString = JSON.stringify(response, null, 2);
  const exceedsThreshold = jsonString.length > RESPONSE_DISPLAY_THRESHOLD;
  const displayContent = exceedsThreshold
    ? jsonString.slice(0, RESPONSE_DISPLAY_THRESHOLD)
    : jsonString;

  // data: URI for download affordance — avoids Blob lifecycle management.
  // Encoded as UTF-8 JSON; filename includes the command ID for traceability.
  const downloadHref = exceedsThreshold
    ? `data:application/json;charset=utf-8,${encodeURIComponent(jsonString)}`
    : undefined;

  return (
    <ExpandableSection
      headerText="raw response payload"
      data-testid={`session-log-response-drawer-${id}`}
    >
      <pre
        style={{ overflowX: 'auto', fontSize: '0.85em', margin: 0 }}
        data-testid={`session-log-response-pre-${id}`}
      >
        {displayContent}
      </pre>
      {exceedsThreshold && (
        <Box margin={{ top: 'xs' }} data-testid={`session-log-response-truncated-${id}`}>
          <Box variant="p" color="text-status-inactive">
            Payload exceeds 4 KB — showing first {RESPONSE_DISPLAY_THRESHOLD} characters.{' '}
            <a
              href={downloadHref}
              download={`response-${id}.json`}
              data-testid={`session-log-response-download-${id}`}
            >
              Download full response
            </a>
          </Box>
        </Box>
      )}
    </ExpandableSection>
  );
};

export default RawDrawer;
