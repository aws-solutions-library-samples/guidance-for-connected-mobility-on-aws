// Investigation Workspace — the hero engineer demo view.
// Three-panel layout: Agent reasoning trace (left) | Live visualization (center) | Evidence panel (right)
// The agent script streams through the anomaly investigation; visualizations render
// as the agent invokes its tools; evidence panel populates with KB documents the agent retrieves.

import React, { useEffect, useMemo, useState, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Badge,
  Box,
  Button,
  Container,
  ExpandableSection,
  Grid,
  Header,
  Link,
  SpaceBetween,
  Spinner,
  StatusIndicator,
} from '@cloudscape-design/components';
import {
  HERO_AGENT_TRACE,
  HERO_ANOMALY_ID,
  getAnomaly,
  getKbDocument,
  type AgentStep,
  type KbDocument,
  AFFECTED_COHORT_FILTER,
  BE6_PROD_COHORT_VEHICLES,
  getAffectedCohort,
  OPERATING_REGIONS,
  MANUFACTURING_BATCHES,
  BATTERY_SUPPLIERS,
} from '../../mock-data-provider/engineering';
import { UI_ROUTES } from '../../utils/constants';

// ============================================================================
// HELPERS
// ============================================================================

const stepIcon = (step: AgentStep): string => {
  switch (step.type) {
    case 'thinking':
      return '🤔';
    case 'tool-invocation':
      return '🔧';
    case 'tool-result':
      return '📊';
    case 'visualization':
      return '📈';
    case 'finding':
      return '💡';
    case 'design-options':
      return '🎯';
    case 'summary':
      return '✅';
    default:
      return '·';
  }
};

const stepLabel = (step: AgentStep): string => {
  switch (step.type) {
    case 'thinking':
      return 'Reasoning';
    case 'tool-invocation':
      return `Tool: ${step.toolName}`;
    case 'tool-result':
      return 'Tool result';
    case 'visualization':
      return 'Visualization';
    case 'finding':
      return 'Root cause';
    case 'design-options':
      return 'Design options generated';
    case 'summary':
      return 'Recommendation';
    default:
      return step.type;
  }
};

// ============================================================================
// COMPONENTS
// ============================================================================

const CohortHeatmap: React.FC = () => {
  // Simple region × supplier grid colored by affected count
  const cells = useMemo(() => {
    const regions = OPERATING_REGIONS.filter((r) => r.country === 'IN');
    const suppliers = BATTERY_SUPPLIERS;
    const grid: { region: string; regionId: string; supplier: string; affected: number; total: number }[] = [];
    for (const r of regions) {
      for (const s of suppliers) {
        const total = BE6_PROD_COHORT_VEHICLES.filter((v) => {
          if (v.operatingRegionId !== r.regionId) return false;
          const batch = MANUFACTURING_BATCHES.find((b) => b.batchId === v.manufacturingBatchId);
          return batch?.batterySupplierId === s.supplierId;
        }).length;
        const affected = BE6_PROD_COHORT_VEHICLES.filter((v) => {
          if (v.operatingRegionId !== r.regionId) return false;
          const batch = MANUFACTURING_BATCHES.find((b) => b.batchId === v.manufacturingBatchId);
          return batch?.batterySupplierId === s.supplierId && v.isAffectedCohort;
        }).length;
        grid.push({
          region: r.regionName,
          regionId: r.regionId,
          supplier: s.supplierName,
          affected,
          total,
        });
      }
    }
    return grid;
  }, []);

  return (
    <Container
      header={<Header variant="h3">Cohort distribution — region × supplier</Header>}
    >
      <Box>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '8px', borderBottom: '1px solid #ccc' }}>
                Region
              </th>
              {BATTERY_SUPPLIERS.map((s) => (
                <th
                  key={s.supplierId}
                  style={{
                    padding: '8px',
                    borderBottom: '1px solid #ccc',
                    textAlign: 'center',
                  }}
                >
                  {s.supplierName}
                  <Box variant="small" color="text-body-secondary">
                    {s.thermalLimit_C}°C limit
                  </Box>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {OPERATING_REGIONS.filter((r) => r.country === 'IN').map((r) => (
              <tr key={r.regionId}>
                <td style={{ padding: '8px', fontWeight: 'bold' }}>
                  {r.regionName}
                  <Box variant="small" color="text-body-secondary">
                    Peak {r.avgSummerPeak_C}°C · {r.climateClass}
                  </Box>
                </td>
                {BATTERY_SUPPLIERS.map((s) => {
                  const cell = cells.find((c) => c.regionId === r.regionId && c.supplier === s.supplierName);
                  if (!cell || cell.total === 0) {
                    return (
                      <td
                        key={s.supplierId}
                        style={{
                          padding: '8px',
                          textAlign: 'center',
                          color: '#999',
                        }}
                      >
                        —
                      </td>
                    );
                  }
                  const affectedRatio = cell.affected / cell.total;
                  const bgColor =
                    affectedRatio > 0.5
                      ? '#d13212' // red — heavily affected
                      : affectedRatio > 0
                      ? '#ec7211' // orange — partial
                      : '#1d8102'; // green — clean
                  return (
                    <td
                      key={s.supplierId}
                      style={{
                        padding: '8px',
                        textAlign: 'center',
                        backgroundColor: bgColor,
                        color: 'white',
                        fontWeight: 'bold',
                      }}
                    >
                      {cell.affected} / {cell.total}
                      <Box variant="small" color="inherit">
                        {affectedRatio > 0 ? `${Math.round(affectedRatio * 100)}% affected` : 'baseline'}
                      </Box>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <Box margin={{ top: 's' }} variant="small" color="text-body-secondary">
          Cells show affected-vehicles / total-vehicles by operating region × battery cell supplier. The signal
          isolates to <strong>Maharashtra-Hot + Gujarat-Hot</strong> intersected with <strong>Voltrix</strong> (specifically Q3 2025 batches).
        </Box>
      </Box>
    </Container>
  );
};

const KnowledgeGraphCanvas: React.FC = () => {
  // SVG-based small knowledge graph visualization
  const affected = getAffectedCohort();
  return (
    <Container
      header={<Header variant="h3">Knowledge graph traversal</Header>}
    >
      <Box>
        <svg viewBox="0 0 800 320" style={{ width: '100%', height: '320px' }}>
          {/* Nodes */}
          <g>
            <rect x="20" y="120" width="140" height="80" rx="8" fill="#1f9bdf" />
            <text x="90" y="155" textAnchor="middle" fill="white" fontWeight="bold">
              {affected.length} vehicles
            </text>
            <text x="90" y="175" textAnchor="middle" fill="white" fontSize="11">
              Affected sub-cohort
            </text>

            <rect x="220" y="60" width="140" height="60" rx="8" fill="#ec7211" />
            <text x="290" y="85" textAnchor="middle" fill="white" fontWeight="bold">
              BATCH-MH-Q3-2025-A12
            </text>
            <text x="290" y="103" textAnchor="middle" fill="white" fontSize="10">
              22 vehicles · Chakan-MH
            </text>

            <rect x="220" y="200" width="140" height="60" rx="8" fill="#ec7211" />
            <text x="290" y="225" textAnchor="middle" fill="white" fontWeight="bold">
              BATCH-MH-Q3-2025-B14
            </text>
            <text x="290" y="243" textAnchor="middle" fill="white" fontSize="10">
              18 vehicles · Chakan-MH
            </text>

            <rect x="420" y="130" width="160" height="80" rx="8" fill="#d13212" />
            <text x="500" y="160" textAnchor="middle" fill="white" fontWeight="bold">
              SUP-VOLTRIX
            </text>
            <text x="500" y="180" textAnchor="middle" fill="white" fontSize="11">
              Voltrix Energy Systems
            </text>
            <text x="500" y="195" textAnchor="middle" fill="white" fontSize="10">
              CL-VTX-2025-Q3 cells
            </text>

            <rect x="640" y="100" width="140" height="60" rx="8" fill="#7d2105" />
            <text x="710" y="125" textAnchor="middle" fill="white" fontWeight="bold">
              Datasheet rev 2.3
            </text>
            <text x="710" y="143" textAnchor="middle" fill="white" fontSize="10">
              42°C limit (was 45°C)
            </text>

            <rect x="640" y="180" width="140" height="60" rx="8" fill="#7d2105" />
            <text x="710" y="205" textAnchor="middle" fill="white" fontWeight="bold">
              PFMEA RPN 240
            </text>
            <text x="710" y="223" textAnchor="middle" fill="white" fontSize="10">
              Deferred (invalid)
            </text>
          </g>

          {/* Edges */}
          <g stroke="#5f6b7a" strokeWidth="2" fill="none">
            <path d="M 160 145 L 220 90" markerEnd="url(#arrow)" />
            <path d="M 160 175 L 220 230" markerEnd="url(#arrow)" />
            <path d="M 360 90 L 420 150" markerEnd="url(#arrow)" />
            <path d="M 360 230 L 420 180" markerEnd="url(#arrow)" />
            <path d="M 580 155 L 640 130" markerEnd="url(#arrow)" />
            <path d="M 580 175 L 640 200" markerEnd="url(#arrow)" />
          </g>

          <defs>
            <marker
              id="arrow"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#5f6b7a" />
            </marker>
          </defs>

          {/* Edge labels */}
          <g fill="#5f6b7a" fontSize="10">
            <text x="190" y="115" textAnchor="middle">vehicle→batch</text>
            <text x="390" y="120" textAnchor="middle">batch→supplier</text>
            <text x="610" y="135" textAnchor="middle">datasheet</text>
            <text x="610" y="195" textAnchor="middle">PFMEA</text>
          </g>
        </svg>
        <Box variant="small" color="text-body-secondary">
          78% of affected vehicles trace through 2 manufacturing batches → 1 supplier → 1 datasheet revision
          → 1 deferred PFMEA mitigation. The graph isolates the root cause.
        </Box>
      </Box>
    </Container>
  );
};

const TelemetryChart: React.FC = () => {
  // Inline SVG line-chart of SoH degradation: affected cohort vs baseline
  return (
    <Container header={<Header variant="h3">Battery SoH degradation — last 90 days</Header>}>
      <Box>
        <svg viewBox="0 0 800 280" style={{ width: '100%', height: '280px' }}>
          {/* axes */}
          <line x1="60" y1="240" x2="780" y2="240" stroke="#5f6b7a" strokeWidth="1" />
          <line x1="60" y1="20" x2="60" y2="240" stroke="#5f6b7a" strokeWidth="1" />
          {/* y axis labels */}
          <text x="55" y="40" textAnchor="end" fontSize="10" fill="#5f6b7a">100%</text>
          <text x="55" y="100" textAnchor="end" fontSize="10" fill="#5f6b7a">98%</text>
          <text x="55" y="160" textAnchor="end" fontSize="10" fill="#5f6b7a">96%</text>
          <text x="55" y="220" textAnchor="end" fontSize="10" fill="#5f6b7a">94%</text>
          <text x="40" y="130" textAnchor="middle" fontSize="11" fill="#5f6b7a" transform="rotate(-90,40,130)">SoH %</text>

          {/* Baseline line (gentle slope) */}
          <polyline
            points="60,40 200,55 340,72 480,90 620,108 760,127"
            fill="none"
            stroke="#1d8102"
            strokeWidth="2.5"
          />
          {/* Affected cohort line (steeper) */}
          <polyline
            points="60,40 200,62 340,90 480,120 620,152 760,186"
            fill="none"
            stroke="#d13212"
            strokeWidth="2.5"
          />
          {/* x labels */}
          <text x="60" y="260" textAnchor="middle" fontSize="10" fill="#5f6b7a">90d ago</text>
          <text x="410" y="260" textAnchor="middle" fontSize="10" fill="#5f6b7a">45d ago</text>
          <text x="760" y="260" textAnchor="middle" fontSize="10" fill="#5f6b7a">today</text>

          {/* Legend */}
          <g>
            <rect x="600" y="30" width="14" height="3" fill="#1d8102" />
            <text x="620" y="34" fontSize="11" fill="#5f6b7a">Baseline (n=160)</text>
            <rect x="600" y="48" width="14" height="3" fill="#d13212" />
            <text x="620" y="52" fontSize="11" fill="#5f6b7a">Affected sub-cohort (n=40)</text>
          </g>

          {/* annotation */}
          <line x1="760" y1="186" x2="760" y2="127" stroke="#d13212" strokeDasharray="3,3" />
          <text x="700" y="160" textAnchor="middle" fontSize="11" fontWeight="bold" fill="#d13212">
            +12.2% gap
          </text>
        </svg>
        <Box variant="small" color="text-body-secondary">
          Affected sub-cohort degrades at <strong>1.01% per month</strong> vs baseline <strong>0.90% per month</strong> — a <strong>+12.2%</strong> delta over the 90-day window. Statistically significant (p &lt; 0.001).
        </Box>
      </Box>
    </Container>
  );
};

// ============================================================================
// MAIN VIEW
// ============================================================================

const InvestigationWorkspace: React.FC = () => {
  const navigate = useNavigate();
  const { caseId } = useParams<{ caseId?: string }>();
  const anomalyId = caseId || HERO_ANOMALY_ID;
  const anomaly = getAnomaly(anomalyId);

  const [visibleSteps, setVisibleSteps] = useState<AgentStep[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [evidenceDocs, setEvidenceDocs] = useState<KbDocument[]>([]);
  const [showHeatmap, setShowHeatmap] = useState(false);
  const [showGraph, setShowGraph] = useState(false);
  const [showDesignOptions, setShowDesignOptions] = useState(false);

  const startInvestigation = useCallback(() => {
    setVisibleSteps([]);
    setEvidenceDocs([]);
    setShowHeatmap(false);
    setShowGraph(false);
    setShowDesignOptions(false);
    setStreaming(true);

    let cumulativeDelay = 0;
    HERO_AGENT_TRACE.forEach((step) => {
      cumulativeDelay += step.delayMs;
      setTimeout(() => {
        setVisibleSteps((prev) => [...prev, step]);
        if (step.type === 'visualization') {
          if (step.vizPayload?.kind === 'cohort-heatmap') setShowHeatmap(true);
          if (step.vizPayload?.kind === 'knowledge-graph') setShowGraph(true);
        }
        if (step.resultDocIds) {
          const docs = step.resultDocIds
            .map((id) => getKbDocument(id))
            .filter((d): d is KbDocument => Boolean(d));
          setEvidenceDocs((prev) => [...prev, ...docs]);
        }
        if (step.type === 'design-options') {
          setShowDesignOptions(true);
        }
        if (step.stepId === HERO_AGENT_TRACE[HERO_AGENT_TRACE.length - 1].stepId) {
          setStreaming(false);
        }
      }, cumulativeDelay);
    });
  }, []);

  // Auto-start on mount
  useEffect(() => {
    const timer = setTimeout(startInvestigation, 600);
    return () => clearTimeout(timer);
  }, [startInvestigation]);

  if (!anomaly) {
    return <Alert type="error">Anomaly {anomalyId} not found.</Alert>;
  }

  return (
    <Container>
      <SpaceBetween size="m">
        {/* Anomaly summary strip — title is rendered globally via PageHeader,
            so we just surface severity + summary text inline here. */}
        <Box>
          <SpaceBetween direction="horizontal" size="xs" alignItems="center">
            <Badge color={anomaly.severity === 'high' || anomaly.severity === 'critical' ? 'red' : 'blue'}>
              {anomaly.severity.toUpperCase()}
            </Badge>
            <Box variant="p" color="text-body-secondary">
              {anomaly.summary}
            </Box>
          </SpaceBetween>
        </Box>

        {/* 3-panel grid */}
      <Grid
        gridDefinition={[
          { colspan: { default: 12, m: 4 } },
          { colspan: { default: 12, m: 5 } },
          { colspan: { default: 12, m: 3 } },
        ]}
      >
        {/* ================ LEFT: Agent reasoning trace ================ */}
        <Container
          header={
            <Header
              variant="h3"
              actions={
                <Button
                  iconName="refresh"
                  variant="icon"
                  onClick={startInvestigation}
                  disabled={streaming}
                />
              }
            >
              Product Engineering Agent
            </Header>
          }
        >
          <Box>
            {visibleSteps.length === 0 && !streaming && (
              <Box color="text-body-secondary" textAlign="center" padding="l">
                <Spinner /> Starting investigation…
              </Box>
            )}
            <SpaceBetween size="s">
              {visibleSteps.map((step) => (
                <Box
                  key={step.stepId}
                  padding="xs"
                  variant="small"
                >
                  <SpaceBetween size="xxs">
                    <Box>
                      <span style={{ fontSize: '14px', marginRight: '6px' }}>{stepIcon(step)}</span>
                      <Box display="inline" fontWeight="bold" fontSize="body-s">
                        {stepLabel(step)}
                      </Box>
                    </Box>
                    {step.toolArgs && (
                      <Box
                        padding="xxs"
                        margin={{ left: 'l' }}
                        fontSize="body-s"
                      >
                        <code style={{ background: '#f5f5f5', padding: '2px 6px', borderRadius: '4px', fontSize: '11px', display: 'block', whiteSpace: 'pre-wrap' }}>
                          {JSON.stringify(step.toolArgs, null, 2)}
                        </code>
                      </Box>
                    )}
                    {step.textContent && (
                      <Box margin={{ left: 'l' }} fontSize="body-s">
                        {step.textContent}
                      </Box>
                    )}
                  </SpaceBetween>
                </Box>
              ))}
              {streaming && visibleSteps.length > 0 && (
                <Box padding="xs" color="text-body-secondary">
                  <Spinner /> <Box display="inline" margin={{ left: 'xs' }}>Thinking…</Box>
                </Box>
              )}
              {showDesignOptions && (
                <Box margin={{ top: 's' }}>
                  <Button
                    variant="primary"
                    iconName="external"
                    iconAlign="right"
                    onClick={() =>
                      navigate(`${UI_ROUTES.ENGINEERING_DESIGN_OPTIONS}/${anomalyId}`)
                    }
                  >
                    Review design options
                  </Button>
                </Box>
              )}
            </SpaceBetween>
          </Box>
        </Container>

        {/* ================ CENTER: Live visualization ================= */}
        <SpaceBetween size="s">
          <TelemetryChart />
          {showHeatmap && <CohortHeatmap />}
          {showGraph && <KnowledgeGraphCanvas />}
        </SpaceBetween>

        {/* ================ RIGHT: Evidence panel ===================== */}
        <Container
          header={
            <Header variant="h3" counter={`(${evidenceDocs.length})`}>
              Evidence
            </Header>
          }
        >
          <SpaceBetween size="s">
            {evidenceDocs.length === 0 ? (
              <Box color="text-body-secondary" variant="small">
                Knowledge base documents the agent retrieves will appear here.
              </Box>
            ) : (
              evidenceDocs.map((doc) => (
                <ExpandableSection
                  key={doc.docId}
                  headerText={doc.title}
                  variant="container"
                >
                  <Box>
                    <Box variant="small" color="text-body-secondary" margin={{ bottom: 'xs' }}>
                      {doc.category} · {doc.modelLine} · last modified {doc.lastModified}
                    </Box>
                    <Box
                      fontSize="body-s"
                      padding="xs"
                    >
                      <pre
                        style={{
                          whiteSpace: 'pre-wrap',
                          fontFamily: 'inherit',
                          fontSize: '12px',
                          background: '#fafbfc',
                          padding: '8px',
                          borderRadius: '4px',
                          maxHeight: '300px',
                          overflow: 'auto',
                        }}
                      >
                        {doc.content}
                      </pre>
                    </Box>
                  </Box>
                </ExpandableSection>
              ))
            )}
          </SpaceBetween>
        </Container>
      </Grid>
      </SpaceBetween>
    </Container>
  );
};

export default InvestigationWorkspace;
