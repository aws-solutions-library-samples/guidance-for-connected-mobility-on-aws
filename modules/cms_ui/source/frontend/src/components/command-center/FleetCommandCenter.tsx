// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useState } from "react";
import {
  Box,
  Button,
  Container,
  Grid,
  Header,
  Link,
  Popover,
  SpaceBetween,
  Spinner,
  StatusIndicator,
} from "@cloudscape-design/components";
import { authFetch } from "../../utils/authFetch";
import { getApiEndpoint } from "../../config/api";

const SUB_SCORE_KEYS = [
  { key: "utilization", label: "Utilization" },
  { key: "cost_health", label: "Cost Health" },
  { key: "safety_compliance", label: "Safety" },
  { key: "maintenance_health", label: "Maintenance" },
];

const scoreType = (v: number): "success" | "warning" | "error" => v >= 80 ? "success" : v >= 60 ? "warning" : "error";
const scoreColor = (v: number) => v >= 80 ? "#00802f" : v >= 60 ? "#d4a017" : "#d13313";

/**
 * Compact "Info" link that reveals section-level help in a popover.
 * Mirrors the AWS console pattern — the trigger sits next to the section
 * title, the bubble shows a short paragraph explaining what the section
 * surfaces and when an operator would care.
 *
 * Using Cloudscape's <Popover> rather than <HelpPanel> intentionally:
 * HelpPanel owns the AppLayout right sidebar, which our
 * HelpPanelProvider already claims for the Tools panel. A popover is
 * inline and doesn't fight for that slot.
 */
const InfoPopover: React.FC<{ title: string; children: React.ReactNode }> = ({
  title,
  children,
}) => (
  <Popover
    dismissButton={false}
    position="top"
    size="medium"
    triggerType="custom"
    header={title}
    content={<Box variant="span">{children}</Box>}
  >
    <Link variant="info">Info</Link>
  </Popover>
);

const FleetCommandCenter: React.FC = () => {
  const [health, setHealth] = useState<any>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  const [actions, setActions] = useState<any[]>([]);
  const [actionsLoading, setActionsLoading] = useState(true);
  const [decisions, setDecisions] = useState<any[]>([]);

  const [briefing, setBriefing] = useState<{ label: string; text: string; type: string; }[]>([]);
  const [briefingDate, setBriefingDate] = useState<string>('');
  const [briefingLoading, setBriefingLoading] = useState(true);

  useEffect(() => {
    authFetch(`${getApiEndpoint()}/api/v1/fleet-health`)
      .then(r => r.json())
      .then(d => setHealth(d))
      .catch(() => setHealth({ composite: 100, utilization: 100, cost_health: 100, safety_compliance: 100, maintenance_health: 100, details: {} }))
      .finally(() => setHealthLoading(false));

    authFetch(`${getApiEndpoint()}/api/v1/daily-briefing`)
      .then(r => r.json())
      .then(d => {
        setBriefing(d.items || []);
        setBriefingDate(d.date || '');
      })
      .catch(() => {})
      .finally(() => setBriefingLoading(false));

    authFetch(`${getApiEndpoint()}/api/v1/fleet-actions?status=ALL`)
      .then(r => r.json())
      .then(d => setActions(d.actions || []))
      .catch(() => {})
      .finally(() => setActionsLoading(false));

    authFetch(`${getApiEndpoint()}/api/v1/decision-journal`)
      .then(r => r.json())
      .then(d => setDecisions(d.decisions || []))
      .catch(() => {});
  }, []);

  const composite = health?.composite ?? 100;
  const details = health?.details ?? {};

  const handleAction = (actionId: string, type: 'approve' | 'reject') => {
    authFetch(`${getApiEndpoint()}/api/v1/fleet-actions/${actionId}/${type}`, { method: 'POST' })
      .then(() => setActions(prev => prev.map(a => a.actionId === actionId ? { ...a, status: type === 'approve' ? 'APPROVED' : 'REJECTED' } : a)))
      .catch(() => {});
  };

  const pendingActions = actions.filter(a => a.status === 'PENDING');

  return (
    <SpaceBetween size="l">
      {/* Row 1: Daily Briefing + Health Score */}
      <Grid gridDefinition={[{ colspan: 8 }, { colspan: 4 }]}>
        <Container
          header={
            <Header
              description={briefingDate ? `Auto-generated · ${briefingDate}` : undefined}
              info={
                <InfoPopover title="Daily Briefing">
                  Automatically generated at 6am ET by the Virtual Fleet Operator.
                  Highlights overnight events, at-risk vehicles, and cross-domain
                  trends your fleet should know about today. Refreshed daily.
                </InfoPopover>
              }
            >
              Daily Briefing
            </Header>
          }
        >
          <SpaceBetween size="s">
            {briefingLoading ? (
              <Box textAlign="center" padding="l"><Spinner /></Box>
            ) : briefing.length === 0 ? (
              <Box textAlign="center" padding="l" color="text-body-secondary">No briefing available</Box>
            ) : briefing.map((b, i) => (
              <div key={i}><StatusIndicator type={(b.type as "warning" | "error" | "info" | "success")}><strong>{b.label}:</strong> {b.text}</StatusIndicator></div>
            ))}
          </SpaceBetween>
        </Container>
        <Container
          header={
            <Header
              description={`${details.total_vehicles ?? '—'} vehicles · ${details.active_vehicles ?? '—'} active`}
              info={
                <InfoPopover title="Fleet Health">
                  Composite fleet score (0–100) rolled up from four sub-domains:
                  Utilization (are vehicles being driven?), Cost Health (are repair
                  costs trending high?), Safety (DTCs + driver behaviour), and
                  Maintenance (preventive and reactive). Below 60 is investigated;
                  below 40 is escalated.
                </InfoPopover>
              }
            >
              Fleet Health
            </Header>
          }
        >
          {healthLoading ? <Box textAlign="center" padding="l"><Spinner /></Box> : !health ? (
            <Box textAlign="center" padding="l" color="text-body-secondary">No fleet health data available</Box>
          ) : (
          <SpaceBetween size="m">
            <div style={{ display: "flex", justifyContent: "center" }}>
              <div style={{ width: 100, height: 100, borderRadius: "50%", backgroundColor: scoreColor(composite), color: "#fff", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", fontSize: 32, fontWeight: "bold" }}>
                {composite}<span style={{ fontSize: 12, fontWeight: "normal" }}>/100</span>
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "center" }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 28px", maxWidth: 260 }}>
                {SUB_SCORE_KEYS.map((s) => {
                  const val = health?.[s.key] ?? 100;
                  return (
                    <div key={s.key} style={{ textAlign: "center" }}>
                      <Box fontSize="body-s" color="text-body-secondary">{s.label}</Box>
                      <StatusIndicator type={scoreType(val)}>{val}%</StatusIndicator>
                    </div>
                  );
                })}
              </div>
            </div>
          </SpaceBetween>
          )}
        </Container>
      </Grid>

      {/* Row 3: Pending Actions + Agent Activity */}
      <Grid gridDefinition={[{ colspan: 7 }, { colspan: 5 }]}>
        <Container
          header={
            <Header
              counter={`${pendingActions.length} pending`}
              info={
                <InfoPopover title="Pending Actions">
                  Recommendations the Virtual Fleet Operator has queued for human
                  approval. Each action is generated by a specialist agent (Cost /
                  Recall-Warranty / Maintenance / Rebalancing) and includes
                  reasoning. Approve to execute; reject to dismiss.
                </InfoPopover>
              }
            >
              Pending Actions
            </Header>
          }
        >
          {actionsLoading ? <Box textAlign="center" padding="l"><Spinner /></Box> : pendingActions.length === 0 ? (
            <Box textAlign="center" padding="l" color="text-body-secondary">No pending actions</Box>
          ) : (
          <SpaceBetween size="xs">
            {pendingActions.slice(0, 8).map((a: any) => (
              <div key={a.actionId} style={{ padding: "8px 0", borderBottom: "1px solid var(--color-border-divider-default, #e9ebed)", fontSize: 13 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4, flexWrap: "wrap" }}>
                      {/* Severity — inherent to the underlying event.  CRITICAL
                          uses error (red), HIGH uses warning (orange-ish),
                          MEDIUM uses info (blue), LOW uses success (green).
                          Matches the canonical vocab from
                          docs/SEVERITY_VOCABULARY.md. */}
                      <StatusIndicator
                        type={
                          a.severity === 'CRITICAL' ? 'error' :
                          a.severity === 'HIGH' ? 'warning' :
                          a.severity === 'LOW' ? 'success' :
                          'info'  // MEDIUM or unknown
                        }
                      >
                        Severity: {a.severity || 'MEDIUM'}
                      </StatusIndicator>
                      {/* Priority — may differ from severity when a future VFO
                          policy layer downgrades/upgrades based on context
                          (e.g. already in shop, cross-country trip). Today
                          same as severity for most producers; showing both
                          so a future divergence is already visible. */}
                      {a.priority && a.priority !== a.severity && (
                        <StatusIndicator
                          type={
                            a.priority === 'CRITICAL' ? 'error' :
                            a.priority === 'HIGH' ? 'warning' :
                            a.priority === 'LOW' ? 'success' :
                            'info'
                          }
                        >
                          Priority: {a.priority}
                        </StatusIndicator>
                      )}
                      <Box fontWeight="bold" fontSize="body-s">{a.domain}</Box>
                      <Box fontSize="body-s" color="text-body-secondary">{a.createdAt?.slice(0, 16).replace('T', ' ')}</Box>
                    </div>
                    <Box fontSize="body-s">{(a.agentResponse || '').slice(0, 200)}...</Box>
                  </div>
                  <SpaceBetween direction="horizontal" size="xxs">
                    <Button variant="primary" onClick={() => handleAction(a.actionId, 'approve')}>Approve</Button>
                    <Button onClick={() => handleAction(a.actionId, 'reject')}>Reject</Button>
                  </SpaceBetween>
                </div>
              </div>
            ))}
          </SpaceBetween>
          )}
        </Container>
        <Container
          header={
            <Header
              description="Autonomous decisions by the Virtual Fleet Operator"
              counter={`(${decisions.length})`}
              info={
                <InfoPopover title="VFO Decision Journal">
                  The autonomous decisions the Virtual Fleet Operator made without
                  human input — within its policy guardrails. Each row includes
                  vehicle, category, reasoning, and estimated impact. Useful for
                  audit and for tuning which decisions should be autonomous vs.
                  queued for approval.
                </InfoPopover>
              }
            >
              VFO Decision Journal
            </Header>
          }
        >
          <SpaceBetween size="xxs">
            {decisions.length === 0 ? (
              <Box textAlign="center" padding="l" color="text-body-secondary">No decisions yet</Box>
            ) : decisions.slice(0, 10).map((d: any, i: number) => (
              <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start", padding: "6px 0", borderBottom: i < 9 ? "1px solid #e9ebed" : undefined, fontSize: 13 }}>
                <StatusIndicator type={d.decision === 'SCHEDULE_SERVICE' ? 'warning' : d.decision === 'REASSIGN_VEHICLE' ? 'info' : 'success'}>
                  {d.decision}
                </StatusIndicator>
                <div>
                  <div><strong>{d.vehicleId}</strong> · {d.category}</div>
                  <div style={{ color: '#656871', fontSize: 12 }}>{d.reasoning?.slice(0, 120)}{d.reasoning?.length > 120 ? '...' : ''}</div>
                  {d.estimated_cost > 0 && <div style={{ fontSize: 12, color: '#037f0c' }}>Est. cost: ${d.estimated_cost}</div>}
                </div>
              </div>
            ))}
          </SpaceBetween>
        </Container>
      </Grid>
    </SpaceBetween>
  );
};

export default FleetCommandCenter;
