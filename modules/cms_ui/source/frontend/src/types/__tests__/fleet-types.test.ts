import { describe, expect, it } from 'vitest';
import {
  deriveVehicleSourceFromFleet,
  getFleetDataSource,
  getOEM1Status,
  getVehicleSource,
  isCloudOEM1Fleet,
  isCloudTelemetryFleet,
  isOEM1Vehicle,
} from '../fleet-types';
import type { FleetItem, VehicleItem } from '../fleet-types';

// ---------------------------------------------------------------------------
// getFleetDataSource
// ---------------------------------------------------------------------------
describe('getFleetDataSource', () => {
  it('returns vehicle-telemetry for legacy row with no data_source', () => {
    const fleet: FleetItem = {};
    expect(getFleetDataSource(fleet)).toBe('vehicle-telemetry');
  });

  it('returns cloud-telemetry for a new cloud-telemetry fleet', () => {
    const fleet: FleetItem = { data_source: 'cloud-telemetry' };
    expect(getFleetDataSource(fleet)).toBe('cloud-telemetry');
  });

  it('returns vehicle-telemetry for an unknown-string data_source (forward-compat fallback)', () => {
    const fleet: FleetItem = { data_source: 'future-source-x' };
    expect(getFleetDataSource(fleet)).toBe('vehicle-telemetry');
  });

  // Dual-read transition cases
  it('dual-read: row with cloud-oem1 → returns cloud-telemetry', () => {
    expect(getFleetDataSource({ data_source: 'cloud-oem1' })).toBe('cloud-telemetry');
  });

  it('dual-read: row with onboard-fwe → returns vehicle-telemetry', () => {
    expect(getFleetDataSource({ data_source: 'onboard-fwe' })).toBe('vehicle-telemetry');
  });

  it('dual-read: row with missing/undefined data_source → defaults to vehicle-telemetry', () => {
    expect(getFleetDataSource({ data_source: undefined })).toBe('vehicle-telemetry');
  });
});

// ---------------------------------------------------------------------------
// isCloudTelemetryFleet (new name) + isCloudOEM1Fleet (@deprecated alias)
// ---------------------------------------------------------------------------
describe('isCloudTelemetryFleet', () => {
  it('returns false for legacy row with no data_source', () => {
    expect(isCloudTelemetryFleet({})).toBe(false);
  });

  it('returns true for cloud-telemetry fleet', () => {
    expect(isCloudTelemetryFleet({ data_source: 'cloud-telemetry' })).toBe(true);
  });

  it('returns false for unknown-string data_source (forward-compat fallback)', () => {
    expect(isCloudTelemetryFleet({ data_source: 'future-source-x' })).toBe(false);
  });

  // Dual-read: old string must also return true
  it('dual-read: cloud-oem1 → returns true', () => {
    expect(isCloudTelemetryFleet({ data_source: 'cloud-oem1' })).toBe(true);
  });

  // onboard-fwe must return false
  it('dual-read: onboard-fwe → returns false', () => {
    expect(isCloudTelemetryFleet({ data_source: 'onboard-fwe' })).toBe(false);
  });

  it('dual-read: missing/undefined data_source → returns false', () => {
    expect(isCloudTelemetryFleet({ data_source: undefined })).toBe(false);
  });
});

describe('isCloudOEM1Fleet (@deprecated alias — same behavior as isCloudTelemetryFleet)', () => {
  it('returns false for legacy row with no data_source', () => {
    expect(isCloudOEM1Fleet({})).toBe(false);
  });

  it('returns true for cloud-oem1 fleet (old string)', () => {
    expect(isCloudOEM1Fleet({ data_source: 'cloud-oem1' })).toBe(true);
  });

  it('returns true for cloud-telemetry fleet (new string)', () => {
    expect(isCloudOEM1Fleet({ data_source: 'cloud-telemetry' })).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// deriveVehicleSourceFromFleet (new helper)
// ---------------------------------------------------------------------------
describe('deriveVehicleSourceFromFleet', () => {
  it('returns oem1 for cloud-telemetry fleet', () => {
    expect(deriveVehicleSourceFromFleet({ data_source: 'cloud-telemetry' })).toBe('oem1');
  });

  it('returns oem1 for cloud-oem1 fleet (dual-read)', () => {
    expect(deriveVehicleSourceFromFleet({ data_source: 'cloud-oem1' })).toBe('oem1');
  });

  it('returns cms for vehicle-telemetry fleet', () => {
    expect(deriveVehicleSourceFromFleet({ data_source: 'vehicle-telemetry' })).toBe('cms');
  });

  it('returns cms for legacy row with no data_source (default)', () => {
    expect(deriveVehicleSourceFromFleet({})).toBe('cms');
  });
});

// ---------------------------------------------------------------------------
// getOEM1Status
// ---------------------------------------------------------------------------
describe('getOEM1Status', () => {
  it('defaults enrollmentStatus to UNKNOWN for legacy row with no OEM1 fields', () => {
    const vehicle: VehicleItem = { vehicleId: 'LEGACY-VIN-001' };
    const snapshot = getOEM1Status(vehicle);
    expect(snapshot.enrollmentStatus).toBe('UNKNOWN');
    expect(snapshot.fcsCode).toBeUndefined();
    expect(snapshot.message).toBeUndefined();
    expect(snapshot.readiness).toBeUndefined();
    expect(snapshot.refreshedAt).toBeUndefined();
    expect(snapshot.activationDate).toBeUndefined();
  });

  it('maps known OEM1 fields for a COMPLETED vehicle', () => {
    const vehicle: VehicleItem = {
      vehicleId: 'VIN-COMPLETED',
      oem1_enrollment_status: 'COMPLETED',
      oem1_fcs_code: 3,
      oem1_status_message: 'Vehicle has been successfully enrolled',
      oem1_readiness_summary: 'READY',
      oem1_status_refreshed_at: '2026-06-05T18:00:00Z',
      subscription_service_activation_date: '2026-06-05T10:00:00Z',
    };
    const snapshot = getOEM1Status(vehicle);
    expect(snapshot.enrollmentStatus).toBe('COMPLETED');
    expect(snapshot.fcsCode).toBe(3);
    expect(snapshot.message).toBe('Vehicle has been successfully enrolled');
    expect(snapshot.readiness).toBe('READY');
    expect(snapshot.refreshedAt).toBe('2026-06-05T18:00:00Z');
    expect(snapshot.activationDate).toBe('2026-06-05T10:00:00Z');
  });

  it('passes through unknown enrollment_status string without throwing (forward-compat)', () => {
    const vehicle: VehicleItem = {
      vehicleId: 'VIN-FUTURE',
      oem1_enrollment_status: 'FUTURE_STATUS_VALUE',
    };
    const snapshot = getOEM1Status(vehicle);
    // Unknown string is cast; no throw; value preserved
    expect(snapshot.enrollmentStatus).toBe('FUTURE_STATUS_VALUE');
  });
});

// ---------------------------------------------------------------------------
// Phase 2 helpers preserved unchanged (regression guard)
// ---------------------------------------------------------------------------
describe('isOEM1Vehicle (Phase 2 — preserved)', () => {
  it('returns false for CMS-native vehicle', () => {
    expect(isOEM1Vehicle({ oem_source: 'cms' })).toBe(false);
  });

  it('returns true for OEM1 vehicle', () => {
    expect(isOEM1Vehicle({ oem_source: 'oem1' })).toBe(true);
  });

  it('returns false for legacy vehicle with no oem_source', () => {
    expect(isOEM1Vehicle({})).toBe(false);
  });
});

describe('getVehicleSource (Phase 2 — preserved)', () => {
  it('returns cms for CMS-native vehicle', () => {
    expect(getVehicleSource({ oem_source: 'cms' })).toBe('cms');
  });

  it('returns oem1 for OEM1 vehicle', () => {
    expect(getVehicleSource({ oem_source: 'oem1' })).toBe('oem1');
  });

  it('returns cms for legacy vehicle with no oem_source (default)', () => {
    expect(getVehicleSource({})).toBe('cms');
  });
});
