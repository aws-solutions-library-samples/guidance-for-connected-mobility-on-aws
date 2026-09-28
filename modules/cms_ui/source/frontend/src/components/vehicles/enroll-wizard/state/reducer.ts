// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { PreflightVehicleResult } from '@/api/oem1Preflight';

export interface VehicleRow {
  vin: string;
  driverId: string;
  driverName?: string;
  preflightResult?: PreflightVehicleResult;
}

export interface EnrollWizardState {
  /** Stable UUID generated once for idempotency dedup (Decision 014). */
  clientRequestId: string;
  step: number;
  fleetId: string;
  sku: string;
  rows: VehicleRow[];
  /** null = not loaded, undefined = loading, error string = failed */
  preflightStatus: 'idle' | 'loading' | 'done' | 'error';
  quotaRemaining: number | null;
  submitStatus: 'idle' | 'loading' | 'done' | 'error';
  submitResult: unknown;
  errorMessage?: string;
}

const SESSION_KEY = 'cms-enroll-wizard-state';

function makeInitialState(): EnrollWizardState {
  return {
    clientRequestId: crypto.randomUUID(),
    step: 0,
    fleetId: '',
    sku: '',
    rows: [],
    preflightStatus: 'idle',
    quotaRemaining: null,
    submitStatus: 'idle',
    submitResult: null,
  };
}

function loadFromSession(): EnrollWizardState | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as EnrollWizardState;
  } catch {
    return null;
  }
}

function saveToSession(state: EnrollWizardState): void {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(state));
  } catch {
    // storage unavailable — degrade gracefully
  }
}

export function clearSession(): void {
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* ignore */
  }
}

export function initState(): EnrollWizardState {
  return loadFromSession() ?? makeInitialState();
}

// ─── actions ─────────────────────────────────────────────────────────────────

export type WizardAction =
  | { type: 'SET_STEP'; step: number }
  | { type: 'SET_FLEET'; fleetId: string }
  | { type: 'SET_VINS'; vins: string[] }
  | { type: 'SET_SKU'; sku: string }
  | { type: 'SET_DRIVER'; vin: string; driverId: string; driverName?: string }
  | { type: 'SET_PREFLIGHT_STATUS'; status: EnrollWizardState['preflightStatus'] }
  | { type: 'SET_PREFLIGHT_RESULTS'; results: PreflightVehicleResult[] }
  | { type: 'SET_QUOTA'; remaining: number }
  | { type: 'SET_SUBMIT_STATUS'; status: EnrollWizardState['submitStatus'] }
  | { type: 'SET_SUBMIT_RESULT'; result: unknown }
  | { type: 'SET_ERROR'; message: string }
  | { type: 'RESET' };

export function wizardReducer(state: EnrollWizardState, action: WizardAction): EnrollWizardState {
  let next: EnrollWizardState;

  switch (action.type) {
    case 'SET_STEP':
      next = { ...state, step: action.step };
      break;

    case 'SET_FLEET':
      next = { ...state, fleetId: action.fleetId };
      break;

    case 'SET_VINS':
      // Preserve driver assignments for already-present VINs; add new rows blank.
      next = {
        ...state,
        rows: action.vins.map((vin) => {
          const existing = state.rows.find((r) => r.vin === vin);
          return existing ?? { vin, driverId: '' };
        }),
        preflightStatus: 'idle',
      };
      break;

    case 'SET_SKU':
      next = { ...state, sku: action.sku };
      break;

    case 'SET_DRIVER':
      next = {
        ...state,
        rows: state.rows.map((r) =>
          r.vin === action.vin
            ? { ...r, driverId: action.driverId, driverName: action.driverName }
            : r,
        ),
      };
      break;

    case 'SET_PREFLIGHT_STATUS':
      next = { ...state, preflightStatus: action.status };
      break;

    case 'SET_PREFLIGHT_RESULTS': {
      const map = new Map(action.results.map((r) => [r.vin, r]));
      next = {
        ...state,
        preflightStatus: 'done',
        rows: state.rows.map((r) => ({ ...r, preflightResult: map.get(r.vin) })),
      };
      break;
    }

    case 'SET_QUOTA':
      next = { ...state, quotaRemaining: action.remaining };
      break;

    case 'SET_SUBMIT_STATUS':
      next = { ...state, submitStatus: action.submitStatus };
      break;

    case 'SET_SUBMIT_RESULT':
      next = { ...state, submitResult: action.result, submitStatus: 'done' };
      break;

    case 'SET_ERROR':
      next = { ...state, errorMessage: action.message };
      break;

    case 'RESET':
      clearSession();
      return makeInitialState();

    default:
      return state;
  }

  saveToSession(next);
  return next;
}
