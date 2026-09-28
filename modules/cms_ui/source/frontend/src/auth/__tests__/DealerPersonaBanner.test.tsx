// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for DealerPersonaBanner (T4.2).
 *
 * Spec: 2026-08-31-dms-standalone-ui T4.2 — "renders only when the user's
 * groups are ALL in the 7-group dealer set and none are fleet/engineering/
 * platform-admin."
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { DealerPersonaBanner } from '../DealerPersonaBanner';

// Mock useAuth so we control the groups returned
// `dmsUiUrl` comes from runtimeConfig per stage. Mocked so both states are
// covered: configured (renders a link) and absent (renders link-free text).
// The previous revision asserted on a hardcoded that host
// — a host that does not exist, and an internal hostname the pre-sync secret
// scan refuses to ship. Asserting on it locked the defect in place.
let mockDmsUiUrl: string | undefined = 'https://dms.example.test';
vi.mock('../../config/api', () => ({
  getDmsUiUrl: () => mockDmsUiUrl,
}));

vi.mock('../useAuth', () => ({
  useAuth: vi.fn(),
}));
import { useAuth } from '../useAuth';

const mockUseAuth = useAuth as unknown as ReturnType<typeof vi.fn>;

function makeUser(groups: string[]) {
  return {
    username: 'test@example.com',
    email: 'test@example.com',
    name: 'Test',
    groups,
    roles: groups,
  };
}

describe('DealerPersonaBanner', () => {
  it('shows banner when user has ONLY dealer groups (service-advisor)', () => {
    mockUseAuth.mockReturnValue({
      user: makeUser(['service-advisor']),
      isAuthenticated: true,
    });
    render(<DealerPersonaBanner />);
    expect(screen.getByText(/dealer management system/i)).toBeTruthy();
    expect(screen.getByRole('link')).toHaveAttribute('href', 'https://dms.example.test');
  });

  it('shows banner for multi-dealer-group user with no CMS groups', () => {
    mockUseAuth.mockReturnValue({
      user: makeUser(['dealer-admin', 'district-manager', 'dms-viewer']),
      isAuthenticated: true,
    });
    render(<DealerPersonaBanner />);
    expect(screen.getByText(/dealer management system/i)).toBeTruthy();
  });

  it('hides banner when user also has fleet-operator group', () => {
    mockUseAuth.mockReturnValue({
      user: makeUser(['service-advisor', 'fleet-operator']),
      isAuthenticated: true,
    });
    const { container } = render(<DealerPersonaBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('hides banner when user also has platform-admin group', () => {
    mockUseAuth.mockReturnValue({
      user: makeUser(['dealer-admin', 'platform-admin']),
      isAuthenticated: true,
    });
    const { container } = render(<DealerPersonaBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('hides banner when user has only CMS groups (fleet-viewer)', () => {
    mockUseAuth.mockReturnValue({
      user: makeUser(['fleet-viewer']),
      isAuthenticated: true,
    });
    const { container } = render(<DealerPersonaBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('hides banner when user has no groups', () => {
    mockUseAuth.mockReturnValue({
      user: makeUser([]),
      isAuthenticated: true,
    });
    const { container } = render(<DealerPersonaBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('hides banner when not authenticated', () => {
    mockUseAuth.mockReturnValue({
      user: null,
      isAuthenticated: false,
    });
    const { container } = render(<DealerPersonaBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('is dismissible — clicking dismiss hides the banner', () => {
    mockUseAuth.mockReturnValue({
      user: makeUser(['service-advisor']),
      isAuthenticated: true,
    });
    render(<DealerPersonaBanner />);
    // Banner is visible
    expect(screen.getByText(/dealer management system/i)).toBeTruthy();
    // Cloudscape Flashbar renders a button for dismissing - find any button in the component
    const buttons = screen.getAllByRole('button');
    expect(buttons.length).toBeGreaterThan(0);
    fireEvent.click(buttons[0]);
    expect(screen.queryByText(/dealer management system/i)).toBeNull();
  });
});

describe('DealerPersonaBanner — dmsUiUrl absent', () => {
  it('still warns the user but renders no link when the URL is unconfigured', () => {
    mockDmsUiUrl = undefined;
    render(<DealerPersonaBanner />);
    // The user must still learn they are in the wrong app...
    expect(screen.getByText(/dealer persona/i)).toBeTruthy();
    // ...but must NOT be given a link that cannot resolve.
    expect(screen.queryByRole('link')).toBeNull();
    mockDmsUiUrl = 'https://dms.example.test';
  });
});
