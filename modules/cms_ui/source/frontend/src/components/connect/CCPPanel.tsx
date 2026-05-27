// SPDX-License-Identifier: Apache-2.0

/**
 * CCPPanel — floating Amazon Connect soft-phone widget.
 *
 * Two visual states:
 *
 *   1. Collapsed (default): 60x60 pill icon in the bottom-right corner,
 *      staggered 16px to the left of the VFO ChatAgent blue button. Shows
 *      a small status dot (green when available, gray when offline/idle).
 *      Pulses when an incoming contact rings.
 *
 *   2. Expanded: 340x560 panel with the embedded CCP iframe. Opens when the
 *      agent clicks the pill, or automatically when a contact rings (for
 *      demo punchiness). A minimize button in the header collapses back
 *      to the pill without ending the agent session.
 *
 * The iframe is always mounted (the Streams SDK needs a live DOM element
 * to keep its WebSocket authenticated). When collapsed we move the iframe
 * off-screen via position:fixed;left:-9999px so the SDK keeps running but
 * the agent doesn't see it. Display:none doesn't play well with some SDK
 * internals (offsetParent checks, ringtone playback), hence the off-screen
 * approach.
 *
 * Integration contract
 * --------------------
 *  - Mounted once per authenticated session in App.tsx, gated on
 *    `platform-admin` Cognito group membership.
 *  - Loads the CCP iframe from the Connect instance at
 *    https://cms-vsa-demo-use1.my.connect.aws/ccp-v2/.
 *  - The iframe serves login UI; first-time agents enter Connect creds once,
 *    session cookies persist ~12h.
 *  - On contact.CONNECTED for a CHAT contact, extracts contact attributes
 *    (vehicleId, severity, summary, driverId, triageSessionId) and calls
 *    onEscalationAccepted() — the App-level handler navigates the router
 *    to /vehicle-management/{vehicleId}.
 *
 * Not in scope for this component:
 *  - The EscalationContextPanel (side panel with triage summary) — that's
 *    a separate component that reads from ConnectContext.
 *  - Auth: we don't re-authenticate the Connect session; the embedded
 *    CCP handles that itself via its own iframe login flow.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useConnect, ActiveConnectContact } from "./ConnectContext";

// amazon-connect-streams loads `window.connect` — we access it via the
// window global rather than importing it at module scope, because the
// package ships UMD and relies on being invoked after initCCP.
declare global {
  interface Window {
    connect: any;
  }
}

// eslint-disable-next-line @typescript-eslint/no-require-imports, @typescript-eslint/no-var-requires
import "amazon-connect-streams";

/** URL of the Connect instance's CCP v2 iframe. Pinned for Monday demo;
 *  make this a tenant-config lookup once we're multi-tenant. */
const CCP_URL = "https://cms-vsa-demo-use1.my.connect.aws/ccp-v2/";

/** Width/height of the expanded widget. Matches standard Connect agent
 *  app sizing — wide enough for the dialpad + chat pane, tall enough
 *  for transcripts without scrolling. */
const PANEL_WIDTH = 340;
const PANEL_HEIGHT = 560;

/** Collapsed pill diameter. Matches the existing VFO ChatAgent floating
 *  button (60x60 #0073bb) for visual consistency. */
const PILL_SIZE = 60;

/** Stagger: the VFO button is at right:24px. Our pill sits 16px to its
 *  left (right = 24 + 60 + 16). */
const PILL_RIGHT_OFFSET = 100;
const PILL_BOTTOM_OFFSET = 24;

/** CCP pill color. Orange-ish to visually distinguish from the blue
 *  VFO button. Matches the "Fleet Support (Agent)" naming — this is
 *  the customer-facing support channel, not the internal VFO chat. */
const PILL_COLOR = "#ec7211";
const PILL_COLOR_AVAILABLE = "#1d8102"; // green when agent is available
const PILL_COLOR_INCOMING = "#d91515"; // red when a contact rings

interface CCPPanelProps {
  /** Called when the agent accepts a VSA-escalated chat contact — lets
   *  the App-level router navigate to the vehicle detail page. */
  onEscalationAccepted?: (contact: ActiveConnectContact) => void;

  /** Auto-expand the panel when a contact starts ringing. Defaults to
   *  true — mirrors the demo narrative ("the CCP auto-pops when a
   *  call comes in"). Turn off for production if agents prefer to
   *  explicitly accept visibility. */
  autoExpandOnIncoming?: boolean;

  /** Start collapsed (default true). The agent can always expand from
   *  the pill. Defaulting to collapsed so the big 340x560 panel doesn't
   *  dominate the screen when nothing's happening. */
  defaultCollapsed?: boolean;
}

/**
 * Inject the keyframe animation once. React doesn't have a built-in way to
 * add @keyframes via inline styles, so we just append a <style> tag on first
 * mount. Named `ccp-pulse` to avoid conflicts with other pulse animations
 * in the app.
 */
const PULSE_STYLE_ID = "ccp-pulse-keyframes";
function ensurePulseKeyframes() {
  if (typeof document === "undefined") return;
  if (document.getElementById(PULSE_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = PULSE_STYLE_ID;
  style.textContent = `
    @keyframes ccp-pulse {
      0%   { box-shadow: 0 0 0 0 rgba(217, 21, 21, 0.7); }
      70%  { box-shadow: 0 0 0 18px rgba(217, 21, 21, 0); }
      100% { box-shadow: 0 0 0 0 rgba(217, 21, 21, 0); }
    }
  `;
  document.head.appendChild(style);
}

export const CCPPanel: React.FC<CCPPanelProps> = ({
  onEscalationAccepted,
  autoExpandOnIncoming = true,
  defaultCollapsed = true,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const initializedRef = useRef(false);
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const [error, setError] = useState<string | null>(null);
  const {
    signedIn,
    incomingContact,
    setInitialized,
    setSignedIn,
    setActiveContact,
    setIncomingContact,
  } = useConnect();

  // Inject pulse keyframes on first mount.
  useEffect(() => {
    ensurePulseKeyframes();
  }, []);

  // Auto-expand on incoming contact (demo-friendly default).
  // Deliberately only triggers on the *transition* — once expanded,
  // the agent can still collapse it manually and the auto-expand
  // won't fire again for the same contact.
  const prevIncomingIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!autoExpandOnIncoming) return;
    const currentId = incomingContact?.contactId ?? null;
    if (currentId && currentId !== prevIncomingIdRef.current) {
      setCollapsed(false);
    }
    prevIncomingIdRef.current = currentId;
  }, [incomingContact, autoExpandOnIncoming]);

  /** Extract VSA contact attributes from a Streams SDK contact object.
   *
   *  Contact attributes are set by our /escalate Lambda when it calls
   *  StartChatContact. They come through as
   *  `{ <name>: { name, value } }` pairs from contact.getAttributes() —
   *  we flatten the `.value` strings out for easier consumption.
   */
  const extractContactAttributes = useCallback((contact: any): ActiveConnectContact => {
    const raw = contact.getAttributes?.() ?? {};
    const attr = (key: string): string | undefined => raw[key]?.value;
    return {
      contactId: contact.getContactId?.() ?? "unknown",
      customerDisplayName:
        contact.getConnections?.()?.find((c: any) => c.getType?.() === "inbound")?.getEndpoint?.()?.name
        ?? attr("driverId")
        ?? "Customer",
      vin: attr("vin"),
      vehicleId: attr("vehicleId"),
      severity: attr("severity"),
      reason: attr("reason"),
      summary: attr("summary"),
      tenantId: attr("tenantId"),
      driverId: attr("driverId"),
      triageSessionId: attr("triageSessionId"),
      acceptedAt: new Date().toISOString(),
    };
  }, []);

  /** Subscribe to agent + contact lifecycle events once the CCP is initialised.
   *  The Streams SDK's callback model is async; we keep our refs-of-subscribers
   *  minimal and let the SDK own the event bus. */
  const wireSubscribers = useCallback(() => {
    const connect = window.connect;
    if (!connect) {
      console.warn("[CCPPanel] window.connect not available — initCCP probably failed");
      return;
    }

    connect.agent((agent: any) => {
      console.info("[CCPPanel] Agent available:", agent.getName?.());
      setSignedIn(true);

      agent.onStateChange((stateChange: any) => {
        // Useful during demo rehearsal for debugging "why isn't Kevin
        // getting contacts" — available/offline/onCall transitions show
        // up here. Not surfaced to UI currently.
        console.debug("[CCPPanel] Agent state:", stateChange.newState);
      });
    });

    connect.contact((contact: any) => {
      const contactId = contact.getContactId?.();
      const type = contact.getType?.();
      console.info(`[CCPPanel] New contact: ${contactId} type=${type}`);

      // Only chat contacts flow from VSA escalation. Voice contacts
      // (if we add them in v2) would take a different code path.
      if (type !== connect.ContactType.CHAT) {
        return;
      }

      // Incoming / ringing — populate the preview state so the UI can
      // render a "ringing" indicator outside the CCP iframe if it wants.
      contact.onIncoming(() => {
        const info = extractContactAttributes(contact);
        console.info(`[CCPPanel] Contact ringing: ${info.contactId} vehicleId=${info.vehicleId}`);
        setIncomingContact(info);
      });

      // Connected = agent clicked Accept. This is when we navigate.
      contact.onConnected(() => {
        const info = extractContactAttributes(contact);
        console.info(
          `[CCPPanel] Contact connected: ${info.contactId} ` +
          `severity=${info.severity} vehicleId=${info.vehicleId}`,
        );
        setIncomingContact(null);
        setActiveContact(info);
        onEscalationAccepted?.(info);
      });

      contact.onEnded(() => {
        console.info(`[CCPPanel] Contact ended: ${contactId}`);
        setIncomingContact(null);
        setActiveContact(null);
      });

      contact.onMissed(() => {
        console.warn(`[CCPPanel] Contact missed: ${contactId}`);
        setIncomingContact(null);
      });
    });
  }, [
    extractContactAttributes,
    onEscalationAccepted,
    setActiveContact,
    setIncomingContact,
    setSignedIn,
  ]);

  useEffect(() => {
    // StrictMode / hot-reload guard — initCCP is not idempotent. Calling
    // it twice leaves the DOM with two overlapping iframes, both trying
    // to claim the same WebSocket, and neither works.
    if (initializedRef.current) {
      return;
    }
    if (!containerRef.current) {
      return;
    }
    if (typeof window === "undefined" || !window.connect) {
      setError(
        "amazon-connect-streams failed to load. Check the network tab and " +
          "approved-origins on the Connect instance.",
      );
      return;
    }

    try {
      window.connect.core.initCCP(containerRef.current, {
        ccpUrl: CCP_URL,
        loginPopup: true,
        loginPopupAutoClose: true,
        loginOptions: {
          autoClose: true,
          height: 600,
          width: 400,
        },
        softphone: {
          allowFramedSoftphone: false,
          disableRingtone: true,
        },
        pageOptions: {
          enableAudioDeviceSettings: false,
          enablePhoneTypeSettings: false,
        },
      });

      initializedRef.current = true;
      setInitialized(true);
      wireSubscribers();
    } catch (e) {
      console.error("[CCPPanel] initCCP threw:", e);
      setError(String(e));
    }
  }, [setInitialized, wireSubscribers]);

  // --- Styling -----------------------------------------------------------

  // Pill state + color logic. Priority order: incoming (red, pulses) >
  // available (green) > signed-in (orange) > offline (gray).
  const hasIncoming = incomingContact !== null;
  const pillBackground = hasIncoming
    ? PILL_COLOR_INCOMING
    : signedIn
    ? PILL_COLOR_AVAILABLE
    : PILL_COLOR;

  const pillStyle: CSSProperties = {
    position: "fixed",
    bottom: PILL_BOTTOM_OFFSET,
    right: PILL_RIGHT_OFFSET,
    width: PILL_SIZE,
    height: PILL_SIZE,
    borderRadius: "50%",
    backgroundColor: pillBackground,
    color: "#ffffff",
    display: collapsed ? "flex" : "none",
    alignItems: "center",
    justifyContent: "center",
    cursor: "pointer",
    boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
    zIndex: 1000,
    border: "none",
    padding: 0,
    // Pulse animation when there's an incoming contact. The keyframes
    // live in the injected <style> tag (ensurePulseKeyframes).
    animation: hasIncoming ? "ccp-pulse 1.5s infinite" : undefined,
    transition: "background-color 0.2s ease-out",
  };

  // Expanded panel — shown when !collapsed. Iframe container is always
  // mounted; when collapsed we move it off-screen so the SDK keeps its
  // WebSocket alive without the agent seeing it.
  const frameStyle: CSSProperties = collapsed
    ? {
        // Off-screen when collapsed. Can't use display:none because the
        // Streams SDK checks offsetParent in some internals. Can't use
        // visibility:hidden either because it pauses the iframe's media.
        // left:-9999px keeps it rendered but invisible.
        position: "fixed",
        left: -9999,
        top: 0,
        width: PANEL_WIDTH,
        height: PANEL_HEIGHT,
        pointerEvents: "none",
      }
    : {
        position: "fixed",
        bottom: 16,
        right: 16,
        width: PANEL_WIDTH,
        height: PANEL_HEIGHT,
        boxShadow: "0 8px 32px rgba(0, 0, 0, 0.25)",
        borderRadius: 8,
        border: "1px solid rgba(0, 0, 0, 0.15)",
        background: "#ffffff",
        zIndex: 9999,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      };

  const headerStyle: CSSProperties = {
    height: 40,
    padding: "0 12px",
    background: "#232f3e",
    color: "#ffffff",
    fontSize: 13,
    fontWeight: 600,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    flexShrink: 0,
    userSelect: "none",
  };

  const minimizeButtonStyle: CSSProperties = {
    background: "transparent",
    color: "#ffffff",
    border: "none",
    cursor: "pointer",
    fontSize: 18,
    lineHeight: 1,
    padding: "4px 8px",
  };

  const bodyStyle: CSSProperties = {
    flex: 1,
    position: "relative",
  };

  const errorBannerStyle: CSSProperties = {
    padding: 12,
    background: "#ffebee",
    color: "#b71c1c",
    fontSize: 12,
  };

  // Collapsed pill: shows a headset icon. When there's an incoming
  // contact, the "caller" name overlays the icon as a small badge.
  return (
    <>
      {/* Collapsed pill — always rendered, display toggled in pillStyle. */}
      <button
        type="button"
        style={pillStyle}
        onClick={() => setCollapsed(false)}
        aria-label={
          hasIncoming
            ? "Incoming Fleet Support chat — click to accept"
            : "Open Fleet Support agent panel"
        }
        title={
          hasIncoming
            ? `Incoming: ${incomingContact?.customerDisplayName ?? "driver"}`
            : signedIn
            ? "Fleet Support — Available"
            : "Fleet Support"
        }
      >
        {/* Simple headset glyph. Using unicode instead of an icon
         *   component to avoid pulling in a new icon library. */}
        <svg
          width="28"
          height="28"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M3 18v-6a9 9 0 0 1 18 0v6" />
          <path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z" />
        </svg>
      </button>

      {/* Expanded panel — iframe container is always mounted. When
       *   collapsed, the frame style moves it off-screen so the SDK
       *   keeps the WebSocket alive. */}
      <div style={frameStyle} role="region" aria-label="Amazon Connect agent panel">
        {!collapsed && (
          <div style={headerStyle}>
            <span>Fleet Support (Agent)</span>
            <button
              type="button"
              style={minimizeButtonStyle}
              onClick={() => setCollapsed(true)}
              aria-label="Minimize Fleet Support panel"
              title="Minimize"
            >
              −
            </button>
          </div>
        )}
        {!collapsed && error && <div style={errorBannerStyle}>{error}</div>}
        <div ref={containerRef} style={bodyStyle} />
      </div>
    </>
  );
};
