import { describe, it, expect } from 'vitest';
import { calculateVehicleStatus } from '../fleet-types';

// Spec: .kiro/specs/2026-08-19-cms-connection-status-single-source/
//
// Connectedness is server-derived. `main_api` applies the staleness window in
// connection_status.py, so this helper must consume `connectionStatus` and must
// not re-derive "connected" from timestamps.

const iso = (minutesAgo: number) =>
  new Date(Date.now() - minutesAgo * 60_000).toISOString();

const veh = (o: Record<string, unknown>) => o as any;

describe('calculateVehicleStatus', () => {
  it('trusts a server-reported connected vehicle even with an old timestamp', () => {
    // The server already applied the window; the client must not second-guess it.
    expect(
      calculateVehicleStatus(veh({ connectionStatus: 'connected', lastConnected: iso(120) })),
    ).toBe('connected');
  });

  it('accepts the uppercase form', () => {
    expect(calculateVehicleStatus(veh({ connectionStatus: 'CONNECTED' }))).toBe('connected');
  });

  it('does NOT report connected for a recently-seen but server-disconnected vehicle', () => {
    // This is the behaviour change. The removed 5-minute branch returned
    // 'connected' here, overriding the server's demotion.
    const status = calculateVehicleStatus(
      veh({ connectionStatus: 'disconnected', lastConnected: iso(1) }),
    );
    expect(status).not.toBe('connected');
    expect(status).toBe('active');
  });

  it('still reports active within the 30-day window', () => {
    expect(
      calculateVehicleStatus(veh({ connectionStatus: 'disconnected', lastConnected: iso(60 * 24 * 10) })),
    ).toBe('active');
  });

  it('reports inactive beyond the 30-day window', () => {
    expect(
      calculateVehicleStatus(veh({ connectionStatus: 'disconnected', lastConnected: iso(60 * 24 * 45) })),
    ).toBe('inactive');
  });

  it('falls back to the activityStatus field when no timestamp is present', () => {
    expect(
      calculateVehicleStatus(veh({ connectionStatus: 'disconnected', activityStatus: 'active' })),
    ).toBe('active');
  });

  it('reports inactive with nothing to go on', () => {
    expect(calculateVehicleStatus(veh({}))).toBe('inactive');
  });
});
