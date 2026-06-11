// SPDX-License-Identifier: Apache-2.0

/**
 * EscalationContextPanel — sidebar showing VSA escalation context.
 *
 * Rendered whenever the agent has an active Connect contact (as tracked by
 * ConnectContext.activeContact). Slides in from the right side of the screen.
 *
 * The panel gives the agent everything they need without leaving their seat:
 *  - Severity (P0 / P1) with a color-coded badge
 *  - Driver name + vehicle ID + VIN
 *  - Nova's conversation summary (what Nova already handled before escalating)
 *  - The driver's stated reason
 *  - If P0: a banner confirming roadside assistance has been dispatched
 *  - A dismiss button (closes the panel but keeps the Connect contact alive)
 *
 * The goal is that an agent accepting a VSA contact already has context the
 * moment the screen-pop happens — no clicks, no reading through Connect's
 * "contact attributes" tab, no asking the driver to repeat themselves.
 *
 * Rendering strategy
 * ------------------
 * Position: fixed right side, 380px wide, top 72px (below app header).
 * z-index: 9998 (one below CCPPanel's 9999 so the CCP always wins if they overlap).
 * Visibility: only renders when activeContact != null. When the contact ends,
 *             this unmounts automatically.
 */

import React, { useState } from "react";
import type { CSSProperties } from "react";
import { useConnect } from "./ConnectContext";

const PANEL_WIDTH = 380;

/** Color palette keyed on severity. Red for P0 (stop driving / roadside),
 *  amber for P1 (service now), neutral for P2/P3 if they ever show up. */
const SEVERITY_STYLES: Record<string, { bg: string; fg: string; label: string }> = {
  P0: { bg: "#c62828", fg: "#ffffff", label: "P0 · STOP DRIVING" },
  P1: { bg: "#e65100", fg: "#ffffff", label: "P1 · SERVICE NOW" },
  P2: { bg: "#f9a825", fg: "#000000", label: "P2 · SERVICE SOON" },
  P3: { bg: "#546e7a", fg: "#ffffff", label: "P3 · MONITOR" },
};

const DEFAULT_SEVERITY_STYLE = { bg: "#546e7a", fg: "#ffffff", label: "UNKNOWN" };

export const EscalationContextPanel: React.FC = () => {
  const { activeContact } = useConnect();
  const [dismissed, setDismissed] = useState(false);

  // Reset dismissed state when a new contact arrives.
  // We use the contactId as the dependency so a NEW contact after a
  // dismissal will still show the panel — the previous `dismissed=true`
  // only applies to that one session.
  React.useEffect(() => {
    setDismissed(false);
  }, [activeContact?.contactId]);

  if (!activeContact || dismissed) {
    return null;
  }

  const severity = activeContact.severity ?? "UNKNOWN";
  const severityStyle = SEVERITY_STYLES[severity] ?? DEFAULT_SEVERITY_STYLE;
  const isP0 = severity === "P0";

  // --- Styling -----------------------------------------------------------
  // Inline styles mirror CCPPanel — avoids pulling Cloudscape just for
  // one sidebar. Cloudscape's HelpPanel would be "correct" but introduces
  // layout coupling with AppLayout which we don't want to fight with.
  const panelStyle: CSSProperties = {
    position: "fixed",
    top: 72,
    right: 16,
    width: PANEL_WIDTH,
    maxHeight: "calc(100vh - 88px)",
    overflowY: "auto",
    background: "#ffffff",
    borderRadius: 8,
    boxShadow: "0 8px 32px rgba(0, 0, 0, 0.2)",
    border: "1px solid rgba(0, 0, 0, 0.1)",
    zIndex: 9998,
    display: "flex",
    flexDirection: "column",
    fontSize: 13,
    color: "#232f3e",
  };

  const severityBannerStyle: CSSProperties = {
    background: severityStyle.bg,
    color: severityStyle.fg,
    padding: "12px 16px",
    fontSize: 13,
    fontWeight: 700,
    letterSpacing: 0.5,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    borderTopLeftRadius: 8,
    borderTopRightRadius: 8,
  };

  const dismissButtonStyle: CSSProperties = {
    background: "transparent",
    color: severityStyle.fg,
    border: "none",
    cursor: "pointer",
    fontSize: 18,
    lineHeight: 1,
    padding: 4,
  };

  const bodyStyle: CSSProperties = {
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 14,
  };

  const fieldStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 2,
  };

  const labelStyle: CSSProperties = {
    fontSize: 11,
    fontWeight: 600,
    color: "#5f6b7a",
    textTransform: "uppercase",
    letterSpacing: 0.3,
  };

  const valueStyle: CSSProperties = {
    fontSize: 13,
    color: "#232f3e",
    wordBreak: "break-word",
  };

  const monoValueStyle: CSSProperties = {
    ...valueStyle,
    fontFamily:
      "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Courier New', monospace",
    fontSize: 12,
  };

  const rsaBannerStyle: CSSProperties = {
    background: "#fff3e0",
    border: "1px solid #ffb74d",
    color: "#bf360c",
    padding: "10px 12px",
    borderRadius: 6,
    fontSize: 12,
    fontWeight: 600,
    display: "flex",
    alignItems: "center",
    gap: 8,
  };

  const summaryBlockStyle: CSSProperties = {
    background: "#f4f6f8",
    border: "1px solid #e6eaee",
    padding: "10px 12px",
    borderRadius: 6,
    fontSize: 12,
    lineHeight: 1.5,
    color: "#3c4858",
    whiteSpace: "pre-wrap",
  };

  return (
    <aside style={panelStyle} role="complementary" aria-label="VSA escalation context">
      <div style={severityBannerStyle}>
        <span>{severityStyle.label}</span>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          style={dismissButtonStyle}
          aria-label="Dismiss escalation context panel"
          title="Dismiss (contact stays active)"
        >
          ×
        </button>
      </div>

      <div style={bodyStyle}>
        {isP0 && (
          <div style={rsaBannerStyle}>
            <span aria-hidden="true">🚨</span>
            <span>
              Roadside assistance has been dispatched. Driver has been
              instructed to pull over.
            </span>
          </div>
        )}

        <div style={fieldStyle}>
          <span style={labelStyle}>Driver</span>
          <span style={valueStyle}>
            {activeContact.customerDisplayName}
            {activeContact.driverId && (
              <>
                {" "}
                <span style={{ color: "#5f6b7a", fontSize: 11 }}>
                  ({activeContact.driverId})
                </span>
              </>
            )}
          </span>
        </div>

        {activeContact.vehicleId && (
          <div style={fieldStyle}>
            <span style={labelStyle}>Vehicle</span>
            <span style={monoValueStyle}>{activeContact.vehicleId}</span>
          </div>
        )}

        {activeContact.vin && (
          <div style={fieldStyle}>
            <span style={labelStyle}>VIN</span>
            <span style={monoValueStyle}>{activeContact.vin}</span>
          </div>
        )}

        {activeContact.reason && (
          <div style={fieldStyle}>
            <span style={labelStyle}>Reason</span>
            <span style={valueStyle}>{activeContact.reason}</span>
          </div>
        )}

        {activeContact.summary && (
          <div style={fieldStyle}>
            <span style={labelStyle}>Nova conversation summary</span>
            <div style={summaryBlockStyle}>{activeContact.summary}</div>
          </div>
        )}

        {activeContact.triageSessionId && (
          <div style={fieldStyle}>
            <span style={labelStyle}>Triage session</span>
            <span style={monoValueStyle}>{activeContact.triageSessionId}</span>
          </div>
        )}

        <div style={{ ...fieldStyle, marginTop: 8 }}>
          <span style={labelStyle}>Accepted</span>
          <span style={valueStyle}>
            {new Date(activeContact.acceptedAt).toLocaleTimeString()}
          </span>
        </div>
      </div>
    </aside>
  );
};
