// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * T3.2 role-gate tests for EnrollWizard.
 *
 * (a) hidden for fleet-viewer
 * (b) visible for fleet-operator
 * (c) visible for platform-admin
 * (d) FleetPicker scoped (fleet-scope=locked) for fleet-operator
 * (e) FleetPicker unscoped (fleet-scope=open) for platform-admin
 *
 * Defense-in-depth only — server gate (T2.1/T2.4) is authoritative.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mock heavy dependencies before importing the component ───────────────────

vi.mock('@/auth/useUserRole', () => ({
  useUserRole: vi.fn(),
}));

vi.mock('@/api/oem1BulkEnroll', () => ({
  oem1BulkEnroll: vi.fn(),
}));

// Mock Cloudscape Wizard to avoid complex DOM setup — renders a simple div
// with a data-testid so the role-gate wrapper is still visible.
vi.mock('@cloudscape-design/components', () => ({
  Wizard: ({ children }: { children?: React.ReactNode }) =>
    React.createElement('div', { 'data-testid': 'cloudscape-wizard' }, children),
}));

// Mock all step sub-components (they import their own deps)
vi.mock('../steps/Source', () => ({ default: () => null }));
vi.mock('../steps/Preflight', () => ({ default: () => null }));
vi.mock('../steps/SkuPick', () => ({ default: () => null }));
vi.mock('../steps/DriverAssign', () => ({ default: () => null }));
vi.mock('../steps/QuotaCheck', () => ({ default: () => null }));
vi.mock('../steps/Confirm', () => ({ default: () => null }));
vi.mock('../steps/Result', () => ({ default: () => null }));

// Import after mocks are registered
import { useUserRole } from '@/auth/useUserRole';
import EnrollWizard from '../EnrollWizard';

// ── helpers ──────────────────────────────────────────────────────────────────

const mockUseUserRole = vi.mocked(useUserRole);

function makeRole(overrides: Partial<ReturnType<typeof useUserRole>> = {}): ReturnType<typeof useUserRole> {
  return {
    isAdmin: false,
    isOperator: false,
    isViewer: false,
    isConnectAgent: false,
    isEngineer: false,
    canWrite: false,
    fleetIds: [],
    ...overrides,
  };
}

function renderWizard(fleetId = 'fleet-001') {
  return render(<EnrollWizard fleetId={fleetId} onClose={vi.fn()} />);
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ── (a) fleet-viewer: wizard hidden ─────────────────────────────────────────

describe('(a) fleet-viewer — wizard hidden', () => {
  it('renders nothing for fleet-viewer', () => {
    mockUseUserRole.mockReturnValue(makeRole({ isViewer: true }));
    const { container } = renderWizard();
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing for unauthenticated user (no groups)', () => {
    mockUseUserRole.mockReturnValue(makeRole());
    const { container } = renderWizard();
    expect(container.firstChild).toBeNull();
  });
});

// ── (b) fleet-operator: wizard visible ──────────────────────────────────────

describe('(b) fleet-operator — wizard visible', () => {
  it('renders the wizard for fleet-operator', () => {
    mockUseUserRole.mockReturnValue(makeRole({
      isOperator: true,
      canWrite: true,
      fleetIds: ['fleet-op-001'],
    }));
    renderWizard();
    expect(screen.getByTestId('enroll-wizard')).toBeInTheDocument();
  });
});

// ── (c) platform-admin: wizard visible ──────────────────────────────────────

describe('(c) platform-admin — wizard visible', () => {
  it('renders the wizard for platform-admin', () => {
    mockUseUserRole.mockReturnValue(makeRole({
      isAdmin: true,
      canWrite: true,
      fleetIds: [],
    }));
    renderWizard('fleet-admin-001');
    expect(screen.getByTestId('enroll-wizard')).toBeInTheDocument();
  });
});

// ── (d) fleet-operator: FleetPicker scoped (locked to user.fleetIds[0]) ─────

describe('(d) fleet-operator — FleetPicker scoped', () => {
  it('sets fleet-scope=locked for fleet-operator', () => {
    mockUseUserRole.mockReturnValue(makeRole({
      isOperator: true,
      canWrite: true,
      fleetIds: ['fleet-op-001', 'fleet-op-002'],
    }));
    renderWizard('ignored-prop-fleet');
    const wrapper = screen.getByTestId('enroll-wizard');
    expect(wrapper).toHaveAttribute('data-fleet-scope', 'locked');
  });

  it('pre-selects user.fleetIds[0] as the effective fleetId for fleet-operator', () => {
    mockUseUserRole.mockReturnValue(makeRole({
      isOperator: true,
      canWrite: true,
      fleetIds: ['fleet-op-primary'],
    }));
    renderWizard('some-other-fleet');
    const wrapper = screen.getByTestId('enroll-wizard');
    expect(wrapper).toHaveAttribute('data-fleet-id', 'fleet-op-primary');
  });
});

// ── (e) platform-admin: FleetPicker unscoped (open) ─────────────────────────

describe('(e) platform-admin — FleetPicker unscoped', () => {
  it('sets fleet-scope=open for platform-admin', () => {
    mockUseUserRole.mockReturnValue(makeRole({
      isAdmin: true,
      canWrite: true,
      fleetIds: [],
    }));
    renderWizard('cross-fleet-001');
    const wrapper = screen.getByTestId('enroll-wizard');
    expect(wrapper).toHaveAttribute('data-fleet-scope', 'open');
  });

  it('uses the caller-supplied fleetId for platform-admin', () => {
    mockUseUserRole.mockReturnValue(makeRole({
      isAdmin: true,
      canWrite: true,
      fleetIds: [],
    }));
    renderWizard('admin-chosen-fleet');
    const wrapper = screen.getByTestId('enroll-wizard');
    expect(wrapper).toHaveAttribute('data-fleet-id', 'admin-chosen-fleet');
  });
});
