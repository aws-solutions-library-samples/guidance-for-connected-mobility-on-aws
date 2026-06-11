// Engineer Insights — landing page for the Product Engineer persona.
// Shows the anomaly feed surfaced from cohort analytics across BE 6 prod and BE.07 test fleets.
// Click an anomaly → Investigation Workspace.

import React from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Badge,
  Box,
  Cards,
  ColumnLayout,
  Container,
  Header,
  KeyValuePairs,
  Link,
  SpaceBetween,
  StatusIndicator,
} from '@cloudscape-design/components';
import {
  ENGINEERING_ANOMALIES,
  type Anomaly,
} from '../../mock-data-provider/engineering';
import { COHORT_STATS } from '../../mock-data-provider/engineering/vehicles';
import { UI_ROUTES } from '../../utils/constants';

const severityToStatus: Record<Anomaly['severity'], 'success' | 'info' | 'warning' | 'error'> = {
  low: 'info',
  medium: 'warning',
  high: 'error',
  critical: 'error',
};

const formatDelta = (deltaPct: number): string => {
  const sign = deltaPct >= 0 ? '+' : '';
  return `${sign}${deltaPct.toFixed(1)}%`;
};

const EngineerInsightsView: React.FC = () => {
  const navigate = useNavigate();

  return (
    <SpaceBetween size="l">
      {/* ============================================================ */}
      {/* TOP STRIP — Engineer fleet-of-fleets KPI overview            */}
      {/* ============================================================ */}
      <Container header={<Header variant="h2">Engineering coverage</Header>}>
        <ColumnLayout columns={4} variant="text-grid">
          <KeyValuePairs
            columns={1}
            items={[
              {
                label: 'BE 6 production cohort',
                value: `${COHORT_STATS.be6Total} vehicles`,
              },
              {
                label: 'In affected sub-cohort',
                value: (
                  <Box color="text-status-error" fontWeight="bold">
                    {COHORT_STATS.be6Affected} vehicles
                  </Box>
                ),
              },
            ]}
          />
          <KeyValuePairs
            columns={1}
            items={[
              { label: 'BE.07 validation fleet', value: `${COHORT_STATS.be07Total} vehicles` },
              { label: 'Telemetry tier', value: 'Instrumented (full CAN)' },
            ]}
          />
          <KeyValuePairs
            columns={1}
            items={[
              { label: 'Active anomalies', value: ENGINEERING_ANOMALIES.length.toString() },
              {
                label: 'High severity',
                value: (
                  <Box color="text-status-error" fontWeight="bold">
                    {ENGINEERING_ANOMALIES.filter((a) => a.severity === 'high' || a.severity === 'critical').length}
                  </Box>
                ),
              },
            ]}
          />
          <KeyValuePairs
            columns={1}
            items={[
              { label: 'Operating regions', value: 'India (5 climate zones)' },
              { label: 'Manufacturing plants', value: '1 (Chakan-MH)' },
            ]}
          />
        </ColumnLayout>
      </Container>

      {/* ============================================================ */}
      {/* ANOMALY FEED                                                  */}
      {/* ============================================================ */}
      <Cards
        cardDefinition={{
          header: (item: Anomaly) => (
            <Link
              fontSize="heading-m"
              onFollow={(e) => {
                e.preventDefault();
                navigate(`${UI_ROUTES.ENGINEERING_INVESTIGATE}/${item.anomalyId}`);
              }}
              href={`${UI_ROUTES.ENGINEERING_INVESTIGATE}/${item.anomalyId}`}
            >
              {item.title}
            </Link>
          ),
          sections: [
            {
              id: 'severity',
              header: 'Classification',
              content: (item: Anomaly) => (
                <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                  <StatusIndicator type={severityToStatus[item.severity]}>
                    {item.severity.toUpperCase()}
                  </StatusIndicator>
                  {item.productionPhase && (
                    <Badge color={item.productionPhase === 'pre-prod' ? 'green' : 'red'}>
                      {item.productionPhase === 'pre-prod' ? 'Pre-prod · BE.07' : 'Post-prod · BE 6'}
                    </Badge>
                  )}
                  <Badge color="blue">{item.domain.toUpperCase()}</Badge>
                  {item.implicatedECUs && item.implicatedECUs.map((ecu) => (
                    <Badge key={ecu} color="grey">{ecu}</Badge>
                  ))}
                </SpaceBetween>
              ),
            },
            {
              id: 'summary',
              header: 'Summary',
              content: (item: Anomaly) => <Box variant="p">{item.summary}</Box>,
            },
            {
              id: 'cohort',
              header: 'Affected cohort',
              content: (item: Anomaly) => (
                <SpaceBetween size="xs">
                  <Box>
                    <Box display="inline" fontWeight="bold">
                      {item.affectedVehicleCount}
                    </Box>{' '}
                    vehicles · {item.modelLine}
                  </Box>
                  <Box variant="small" color="text-body-secondary">
                    {item.cohortDescription}
                  </Box>
                </SpaceBetween>
              ),
            },
            {
              id: 'metric',
              header: 'Metric',
              content: (item: Anomaly) => (
                <SpaceBetween size="xs">
                  <Box>{item.metricName}</Box>
                  <Box>
                    Baseline:{' '}
                    <Box display="inline" fontWeight="bold">
                      {item.metricBaseline} {item.metricUnit}
                    </Box>{' '}
                    · Observed:{' '}
                    <Box
                      display="inline"
                      fontWeight="bold"
                      color={
                        Math.abs(item.metricDeltaPercent) >= 10
                          ? 'text-status-error'
                          : 'text-status-warning'
                      }
                    >
                      {item.metricObserved} {item.metricUnit} ({formatDelta(item.metricDeltaPercent)})
                    </Box>
                  </Box>
                </SpaceBetween>
              ),
            },
            {
              id: 'detected',
              header: 'Detected',
              content: (item: Anomaly) => (
                <Box variant="small" color="text-body-secondary">
                  {new Date(item.detectedAt).toLocaleString()}
                </Box>
              ),
            },
          ],
        }}
        cardsPerRow={[{ cards: 1 }, { minWidth: 900, cards: 2 }]}
        items={ENGINEERING_ANOMALIES}
        loadingText="Loading anomalies"
        empty={<Box>No anomalies detected.</Box>}
        header={
          <Header
            variant="h2"
            description="Cohort-level signals surfaced from production telemetry. Click any anomaly to open the Investigation Workspace and engage the Product Engineering Agent."
            counter={`(${ENGINEERING_ANOMALIES.length})`}
          >
            Anomaly feed
          </Header>
        }
      />
    </SpaceBetween>
  );
};

export default EngineerInsightsView;
