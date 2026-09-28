// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Shared TypeScript types used by the routine-renderer registry and each
 * individual pilot renderer.
 *
 * Wire shape (decisions.md 2026-09-13 T2.1):
 *   response.verdict  — top-level on the response object
 *   response.result   — top-level on the response object
 *
 * Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/ (Group 3)
 */

import React from 'react';
import type { StatusIndicatorProps } from '@cloudscape-design/components/status-indicator';

// ── Verdict type ──────────────────────────────────────────────────────────────

export type Verdict = 'in_spec' | 'marginal' | 'out_of_spec';

// ── Response shape navigated by every renderer ────────────────────────────────
// The renderer reads response.verdict and response.result directly (top-level).
// Legacy rows may have neither field; renderers must handle both gracefully.

export interface RoutineResponse {
  status?: string;
  verdict?: Verdict;
  result?: Record<string, unknown>;
  reason?: string;
}

// ── Props contract every renderer accepts ─────────────────────────────────────

export interface RoutineRendererProps {
  response: RoutineResponse;
  routineId: string;
  commandId?: string;
}

export type RoutineRenderer = React.FC<RoutineRendererProps>;

// ── Verdict display constants (from docs/tech.md § SOVD (c)) ─────────────────

/**
 * Human-readable verdict banner text.
 * Keep in sync with spec D5 / docs/tech.md § (c).
 */
export const VERDICT_LABEL: Record<Verdict, string> = {
  in_spec: 'Check passed',
  marginal: 'Reading within tolerance — monitor',
  out_of_spec: 'Reading outside tolerance — repair action required',
};

/**
 * Cloudscape StatusIndicator `type` per verdict.
 * Verbatim from docs/tech.md § SOVD (c).
 */
export const verdictType: Record<Verdict, StatusIndicatorProps['type']> = {
  out_of_spec: 'error',
  marginal: 'warning',
  in_spec: 'success',
};
