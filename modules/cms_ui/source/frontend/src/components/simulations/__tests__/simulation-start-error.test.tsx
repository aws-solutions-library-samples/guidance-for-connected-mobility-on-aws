// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Simulation-start error banner — campaign-missing 400 display test.
 *
 * Spec: ``2026-09-01-cms-campaign-follows-enrollment`` § D4 + § "Test surface > Frontend > SSE1"
 *
 * NOTE (Group 4 decision): TripSimulatorModal.tsx is NOT modified by this spec.
 * It already surfaces `data.error` verbatim via:
 *   `setError(data.error || 'Failed to start simulation')`
 * rendered as `{error && <Alert type="error">{error}</Alert>}` (lines ~147, ~221).
 * This test is therefore a **regression guard** — it asserts the existing
 * pass-through behavior is preserved. See decisions.md.
 */

import React from 'react';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock the simulation-config module — must match the import path in TripSimulatorModal.tsx.
vi.mock('../../../utils/simulation-config', () => ({
  getSimulationApiUrl: (path: string) => `http://localhost:5001/api/simulation${path}`,
  getSimulationApiBase: () => 'http://localhost:5001',
  getSimulationMode: () => 'local',
  isCloudSimAvailable: () => false,
}));

// Mock authFetch (event-catalog call inside the component).
vi.mock('../../../utils/authFetch', () => ({
  authFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ events: [] }),
  }),
}));

// ── Campaign-missing error message (spec § D2 verbatim prefix) ────────────

const CAMPAIGN_MISSING_ERROR =
  "Cannot start simulation: this vehicle has no active data collection campaign, so the trip would produce no telemetry. Attach a campaign from the vehicle's Campaigns tab, then retry.";

// ── Tests ─────────────────────────────────────────────────────────────────

describe('TripSimulatorModal — simulation-start 400 error display (SSE1)', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.clearAllMocks();
  });

  /**
   * SSE1: Regression guard.
   *
   * TripSimulatorModal already surfaces `data.error` verbatim — this test
   * confirms the campaign-missing 400 message reaches the user unchanged.
   * No component change is made by this spec.
   */
  it(
    'SSE1: simulation-start 400 with campaign-missing message → error banner shows message verbatim',
    async () => {
      // Mock global.fetch: simulation-start returns 400 with the campaign-missing error.
      // The event-catalog call is handled by authFetch (already mocked above).
      global.fetch = vi.fn().mockImplementation((url: string) => {
        if (String(url).includes('/api/simulation/start')) {
          return Promise.resolve({
            ok: false,
            status: 400,
            json: async () => ({
              success: false,
              error: CAMPAIGN_MISSING_ERROR,
            }),
          } as unknown as Response);
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({}),
        } as unknown as Response);
      });

      // Import the component after mocks are set up.
      const TripSimulatorModal = (
        await import('../../vehicles/vehicle-detail/TripSimulatorModal')
      ).default;

      const onDismiss = vi.fn();
      const onStarted = vi.fn();

      render(
        <TripSimulatorModal
          visible={true}
          vehicleId="TEST-VIN-001"
          vin="TEST-VIN-001"
          onDismiss={onDismiss}
          onStarted={onStarted}
        />,
      );

      // Wait for the "Trip Simulator" header to confirm modal rendered.
      await waitFor(
        () => expect(screen.getByText('Trip Simulator')).toBeInTheDocument(),
        { timeout: 3000 },
      );

      // Find the Start Trip button and click it.
      const startBtn = screen.getByRole('button', { name: /Start Trip/i });
      expect(startBtn).toBeInTheDocument();

      await act(async () => {
        fireEvent.click(startBtn);
      });

      // Assert: error banner shows the campaign-missing message verbatim.
      // The spec requires the message to NOT be rewritten — it contains
      // operator-recovery instructions that must reach the user.
      await waitFor(
        () => {
          const errorEl = screen.getByText((content) =>
            content.includes('Cannot start simulation: this vehicle has no active data collection campaign'),
          );
          expect(errorEl).toBeInTheDocument();
        },
        { timeout: 3000 },
      );

      // The modal must remain visible (not dismissed on error).
      expect(onDismiss).not.toHaveBeenCalled();
      expect(onStarted).not.toHaveBeenCalled();
    },
    10000, // generous timeout for this integration-level test
  );
});
