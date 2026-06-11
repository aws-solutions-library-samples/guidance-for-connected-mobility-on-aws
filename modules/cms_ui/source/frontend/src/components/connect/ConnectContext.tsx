// SPDX-License-Identifier: Apache-2.0

/**
 * ConnectContext — React context for tracking the agent's current Connect session state.
 *
 * Consumers:
 *  - CCPPanel.tsx (initialises the CCP, writes to this context)
 *  - EscalationContextPanel.tsx (reads the active contact to show per-vehicle context)
 *  - App.tsx (reads activeContact to navigate to /vehicle-management/{vehicleId}
 *             when a P0/P1 VSA escalation is accepted)
 *
 * We deliberately keep this context tiny — just what the rest of the app needs
 * to react to agent-side handoff events. The CCP manages its own streams
 * state internally (contacts, agent status); we surface only what our UI
 * actually uses.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

/** A VSA-escalated contact as seen by the CMS UI agent view. */
export interface ActiveConnectContact {
  /** Connect contact id — stable across the lifetime of the chat. */
  contactId: string;
  /** Agent-visible display name of the customer (from StartChatContact). */
  customerDisplayName: string;
  /** VIN passed as contact attribute by the /escalate Lambda. */
  vin?: string;
  /** CMS vehicleId passed as contact attribute. Drives the screen-pop target. */
  vehicleId?: string;
  /** P0 or P1 — P0 means roadside is dispatched in parallel. */
  severity?: string;
  /** Free-form human reason from the driver / Nova. */
  reason?: string;
  /** Nova conversation summary, populated for context-on-accept. */
  summary?: string;
  /** Tenant id ('acme' for the Monday demo). */
  tenantId?: string;
  /** Driver CMS id, if resolved. */
  driverId?: string;
  /** VSA triage session id, for cross-reference into triage-decisions DDB. */
  triageSessionId?: string;
  /** When the contact was accepted locally. Used for session timers. */
  acceptedAt: string;
}

interface ConnectContextValue {
  /** True once connect.core.initCCP has finished and the iframe is loaded. */
  initialized: boolean;
  /** True when this agent has signed into the CCP (Connect user, not Cognito). */
  signedIn: boolean;
  /** The single contact currently being handled. Null when the CCP is idle. */
  activeContact: ActiveConnectContact | null;
  /** True while a contact is ringing but not yet accepted. */
  incomingContact: ActiveConnectContact | null;

  // Setters — only CCPPanel.tsx should call these, but consumers may need
  // to clear state (e.g., when the user manually closes a context panel).
  setInitialized: (value: boolean) => void;
  setSignedIn: (value: boolean) => void;
  setActiveContact: (contact: ActiveConnectContact | null) => void;
  setIncomingContact: (contact: ActiveConnectContact | null) => void;
}

const ConnectContext = createContext<ConnectContextValue | undefined>(undefined);

export const ConnectProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [initialized, setInitialized] = useState(false);
  const [signedIn, setSignedIn] = useState(false);
  const [activeContact, setActiveContactRaw] = useState<ActiveConnectContact | null>(null);
  const [incomingContact, setIncomingContactRaw] = useState<ActiveConnectContact | null>(null);

  // Stable setters — memoized so consumers that depend on them in effects
  // don't re-render on every state change.
  const setActiveContact = useCallback(
    (c: ActiveConnectContact | null) => setActiveContactRaw(c),
    [],
  );
  const setIncomingContact = useCallback(
    (c: ActiveConnectContact | null) => setIncomingContactRaw(c),
    [],
  );

  const value = useMemo<ConnectContextValue>(
    () => ({
      initialized,
      signedIn,
      activeContact,
      incomingContact,
      setInitialized,
      setSignedIn,
      setActiveContact,
      setIncomingContact,
    }),
    [
      initialized,
      signedIn,
      activeContact,
      incomingContact,
      setActiveContact,
      setIncomingContact,
    ],
  );

  return <ConnectContext.Provider value={value}>{children}</ConnectContext.Provider>;
};

/** Consumer hook. Throws if called outside a ConnectProvider — prevents silent
 * failures where a component thinks it has contact context but actually has
 * undefined. */
export function useConnect(): ConnectContextValue {
  const ctx = useContext(ConnectContext);
  if (ctx === undefined) {
    throw new Error("useConnect must be called inside a <ConnectProvider>");
  }
  return ctx;
}
