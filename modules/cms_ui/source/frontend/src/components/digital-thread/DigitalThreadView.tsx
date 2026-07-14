// Digital Thread destination — a visually distinct surface representing
// where data leaves CMS and arrives in engineering systems (PLM, sim params,
// requirements docs, design review tickets). Intentionally NOT styled as CMS:
// different header treatment, no CMS sidebar, "Acme Motors PLM | Digital Thread" branding,
// "← Return to CMS" link.

import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert,
  Badge,
  Box,
  Button,
  Container,
  Header,
  Icon,
  KeyValuePairs,
  ProgressBar,
  SpaceBetween,
  Tabs,
  StatusIndicator,
} from '@cloudscape-design/components';
import {
  DESIGN_OPTIONS,
  getDesignOption,
  getKbDocument,
} from '../../mock-data-provider/engineering';
import { UI_ROUTES } from '../../utils/constants';

const DigitalThreadView: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const optionId = searchParams.get('option') || DESIGN_OPTIONS.find((o) => o.recommended)?.optionId;
  const fromAnomalyId = searchParams.get('from');

  const option = useMemo(() => (optionId ? getDesignOption(optionId) : undefined), [optionId]);
  const [pushProgress, setPushProgress] = useState(0);
  const [pushComplete, setPushComplete] = useState(false);

  // Animated push progress simulating multi-system writeback
  useEffect(() => {
    if (!option) return;
    setPushProgress(0);
    setPushComplete(false);
    const totalSteps = 100;
    let current = 0;
    const interval = setInterval(() => {
      current += 4;
      if (current >= totalSteps) {
        current = 100;
        setPushProgress(100);
        setPushComplete(true);
        clearInterval(interval);
      } else {
        setPushProgress(current);
      }
    }, 90);
    return () => clearInterval(interval);
  }, [option]);

  if (!option) {
    return <Alert type="error">No design option specified for digital thread push.</Alert>;
  }

  const requirementsAction = option.digitalThreadActions.find(
    (a) => a.type === 'requirements-update',
  );
  const simParamAction = option.digitalThreadActions.find((a) => a.type === 'sim-param-update');
  const pfmeaAction = option.digitalThreadActions.find((a) => a.type === 'pfmea-update');
  const ticketAction = option.digitalThreadActions.find(
    (a) => a.type === 'design-review-ticket',
  );

  const reqDoc = requirementsAction
    ? getKbDocument((requirementsAction.payload as { docId: string }).docId)
    : undefined;

  return (
    <Box>
      {/* Distinct chrome — this is the "leaving CMS" visual moment */}
      <div
        style={{
          background: 'linear-gradient(135deg, #1a2541 0%, #2d4373 100%)',
          color: 'white',
          padding: '24px 32px',
          marginBottom: '24px',
          borderRadius: '8px',
        }}
      >
        <SpaceBetween size="xs">
          <SpaceBetween direction="horizontal" size="s" alignItems="center">
            <Box color="inherit" fontSize="display-l" fontWeight="bold">
              Acme Motors PLM
            </Box>
            <Box color="inherit" fontSize="heading-l" fontWeight="light">
              | Digital Thread
            </Box>
          </SpaceBetween>
          <Box color="inherit" variant="p">
            Engineering system of record for product requirements, simulation parameters, PFMEA, and design
            review tickets. Updates received from Connected Mobility Solution arrive here for engineering
            review and approval.
          </Box>
          <SpaceBetween direction="horizontal" size="s">
            <Button
              variant="link"
              iconName="arrow-left"
              onClick={() => navigate(UI_ROUTES.ENGINEERING_INSIGHTS)}
            >
              Return to CMS
            </Button>
            {fromAnomalyId && (
              <Box color="inherit" variant="small">
                Source: CMS anomaly{' '}
                <Box display="inline" color="inherit" fontWeight="bold">
                  {fromAnomalyId}
                </Box>
              </Box>
            )}
          </SpaceBetween>
        </SpaceBetween>
      </div>

      {/* Push status */}
      <Container>
        <SpaceBetween size="m">
          <Header
            variant="h2"
            description={`Receiving design change from CMS Product Engineering Agent: ${option.shortLabel}`}
          >
            {pushComplete ? (
              <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                <StatusIndicator type="success">Received</StatusIndicator>
                <Box>Design change ingested into engineering systems</Box>
              </SpaceBetween>
            ) : (
              <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                <StatusIndicator type="in-progress">Receiving</StatusIndicator>
                <Box>Design change in transit from CMS</Box>
              </SpaceBetween>
            )}
          </Header>

          <ProgressBar
            value={pushProgress}
            description={
              pushComplete
                ? 'All artifacts written. Engineering review queue updated.'
                : 'Writing requirements diff, sim parameters, PFMEA closure, and design review ticket…'
            }
            label="Digital Thread ingestion"
          />

          <KeyValuePairs
            columns={4}
            items={[
              { label: 'Source system', value: 'Connected Mobility Solution (CMS)' },
              { label: 'Source agent', value: 'Product Engineering Agent' },
              { label: 'Source persona', value: 'Givens-A (engineer)' },
              { label: 'Linked test fleet', value: 'be07-test-fleet-001' },
            ]}
          />
        </SpaceBetween>
      </Container>

      {/* SHIP-TO-FLEET CTA — closes the loop from design-change to firmware-deploy.
          Renders only after the writeback completes. */}
      {pushComplete && (
        <Box margin={{ top: 'l' }}>
          <Container>
            <SpaceBetween size="m">
              <SpaceBetween direction="horizontal" size="m" alignItems="center">
                <Icon name="status-positive" variant="success" size="medium" />
                <Header
                  variant="h2"
                  description="Engineering systems updated. Next: deploy the firmware fix to the production cohort via the OTA pipeline."
                >
                  Ready to ship
                </Header>
              </SpaceBetween>

              <Box>
                <SpaceBetween direction="horizontal" size="s" alignItems="center">
                  <Box>
                    <Box variant="small" color="text-body-secondary" margin={{ bottom: 'xxs' }}>
                      Pipeline ready
                    </Box>
                    <Box>
                      <Box display="inline" fontWeight="bold">Build #4823</Box>{' '}
                      <Box display="inline" variant="small">
                        — BMS firmware{' '}
                        <Box display="inline" fontWeight="bold">
                          v3.2.1 → v3.3.0
                        </Box>
                        , validated on BE.07 fleet, 5% canary in flight on BE 6
                      </Box>
                    </Box>
                  </Box>
                  <Button
                    variant="primary"
                    iconName="upload"
                    onClick={() =>
                      navigate('/fleets/management/be6-prod-cohort-001?tab=ota-rollouts')
                    }
                  >
                    Open OTA Rollouts on BE 6 fleet
                  </Button>
                </SpaceBetween>
              </Box>
            </SpaceBetween>
          </Container>
        </Box>
      )}

      {/* Tabs showing each writeback artifact */}
      <Box margin={{ top: 'l' }}>
        <Tabs
          tabs={[
            // ========================================================
            // Requirements diff
            // ========================================================
            {
              id: 'requirements',
              label: (
                <SpaceBetween direction="horizontal" size="xs">
                  <span>Requirements diff</span>
                  {pushComplete && <Icon name="status-positive" variant="success" />}
                </SpaceBetween>
              ),
              content: requirementsAction && reqDoc ? (
                <Container
                  header={
                    <Header variant="h3" description={`PRD update — ${reqDoc.title}`}>
                      {reqDoc.docId}
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <Badge>Section: {(requirementsAction.payload as any).section}</Badge>
                    <Box
                      padding="m"
                      fontSize="body-s"
                    >
                      <Box variant="small" color="text-status-error" fontWeight="bold" margin={{ bottom: 'xxs' }}>
                        − Removed
                      </Box>
                      <pre
                        style={{
                          background: '#fff5f5',
                          borderLeft: '3px solid #d13212',
                          padding: '12px',
                          fontFamily: 'inherit',
                          fontSize: '13px',
                          whiteSpace: 'pre-wrap',
                          margin: 0,
                        }}
                      >
                        {(requirementsAction.payload as any).oldText}
                      </pre>
                      <Box variant="small" color="text-status-success" fontWeight="bold" margin={{ top: 's', bottom: 'xxs' }}>
                        + Added
                      </Box>
                      <pre
                        style={{
                          background: '#f0f9eb',
                          borderLeft: '3px solid #1d8102',
                          padding: '12px',
                          fontFamily: 'inherit',
                          fontSize: '13px',
                          whiteSpace: 'pre-wrap',
                          margin: 0,
                        }}
                      >
                        {(requirementsAction.payload as any).newText}
                      </pre>
                    </Box>
                  </SpaceBetween>
                </Container>
              ) : (
                <Box color="text-body-secondary">No requirements update for this option.</Box>
              ),
            },
            // ========================================================
            // Simulation parameter update
            // ========================================================
            {
              id: 'sim-params',
              label: (
                <SpaceBetween direction="horizontal" size="xs">
                  <span>Simulation parameters</span>
                  {pushComplete && <Icon name="status-positive" variant="success" />}
                </SpaceBetween>
              ),
              content: simParamAction ? (
                <Container
                  header={
                    <Header variant="h3" description={simParamAction.description}>
                      Sim model: {simParamAction.target}
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <Box variant="small" color="text-body-secondary">
                      Parameter changes pushed to thermal simulation pipeline. Triggered sim runs queued.
                    </Box>
                    <pre
                      style={{
                        background: '#0f1b2d',
                        color: '#a8dadc',
                        padding: '16px',
                        borderRadius: '6px',
                        fontFamily: 'Menlo, Monaco, monospace',
                        fontSize: '12px',
                        overflow: 'auto',
                      }}
                    >
                      {JSON.stringify(simParamAction.payload, null, 2)}
                    </pre>
                  </SpaceBetween>
                </Container>
              ) : (
                <Box color="text-body-secondary">No sim parameter update for this option.</Box>
              ),
            },
            // ========================================================
            // PFMEA closure
            // ========================================================
            {
              id: 'pfmea',
              label: (
                <SpaceBetween direction="horizontal" size="xs">
                  <span>PFMEA closure</span>
                  {pushComplete && pfmeaAction && <Icon name="status-positive" variant="success" />}
                </SpaceBetween>
              ),
              content: pfmeaAction ? (
                <Container
                  header={
                    <Header variant="h3" description={pfmeaAction.description}>
                      PFMEA update: {pfmeaAction.target}
                    </Header>
                  }
                >
                  <KeyValuePairs
                    columns={2}
                    items={[
                      { label: 'RPN ID', value: (pfmeaAction.payload as any).rpnId },
                      {
                        label: 'Status',
                        value: (
                          <StatusIndicator type="success">
                            {(pfmeaAction.payload as any).status}
                          </StatusIndicator>
                        ),
                      },
                      { label: 'Closure reference', value: (pfmeaAction.payload as any).closureReference },
                      { label: 'Residual RPN', value: (pfmeaAction.payload as any).residualRPN?.toString() },
                      ...((pfmeaAction.payload as any).residualRationale
                        ? [
                            {
                              label: 'Residual rationale',
                              value: (pfmeaAction.payload as any).residualRationale,
                            },
                          ]
                        : []),
                    ]}
                  />
                </Container>
              ) : (
                <Box color="text-body-secondary">No PFMEA update for this option.</Box>
              ),
            },
            // ========================================================
            // Design review ticket
            // ========================================================
            {
              id: 'ticket',
              label: (
                <SpaceBetween direction="horizontal" size="xs">
                  <span>Design review ticket</span>
                  {pushComplete && <Icon name="status-positive" variant="success" />}
                </SpaceBetween>
              ),
              content: ticketAction ? (
                <Container
                  header={
                    <Header
                      variant="h3"
                      description={`Filed in PLM design review queue · ${(ticketAction.payload as any).ticketId}`}
                    >
                      {(ticketAction.payload as any).title}
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <KeyValuePairs
                      columns={2}
                      items={[
                        {
                          label: 'Status',
                          value: (
                            <StatusIndicator type="pending">
                              {(ticketAction.payload as any).status || 'pending-engineering-review'}
                            </StatusIndicator>
                          ),
                        },
                        {
                          label: 'Approvers',
                          value: ((ticketAction.payload as any).approvers as string[])?.join(', '),
                        },
                        {
                          label: 'Linked test fleet',
                          value: (ticketAction.payload as any).linkedTestFleet || '—',
                        },
                        {
                          label: 'Validation protocol',
                          value: (ticketAction.payload as any).validationProtocol || '—',
                        },
                      ]}
                    />
                    {(ticketAction.payload as any).evidence && (
                      <Box>
                        <Box variant="small" fontWeight="bold" margin={{ bottom: 'xxs' }}>
                          Evidence linked from CMS
                        </Box>
                        <SpaceBetween size="xxs">
                          {(((ticketAction.payload as any).evidence) as string[]).map((e) => (
                            <Box key={e} variant="small" color="text-body-secondary">
                              · {e}
                            </Box>
                          ))}
                        </SpaceBetween>
                      </Box>
                    )}
                  </SpaceBetween>
                </Container>
              ) : (
                <Box color="text-body-secondary">No design review ticket for this option.</Box>
              ),
            },
          ]}
        />
      </Box>

      {/* Footer */}
      {pushComplete && (
        <Box margin={{ top: 'l' }}>
          <Alert
            type="success"
            header="Loop closed"
            action={
              <Button onClick={() => navigate(UI_ROUTES.ENGINEERING_INSIGHTS)}>
                Return to CMS
              </Button>
            }
          >
            Production telemetry signal → manufacturing context → supplier datasheet → engineering spec → BE.07
            design change → BE.07 test fleet validation queue. The product digital thread now reflects this
            change. The BE.07 validation fleet will instrument the proposed mitigation in the next test cycle.
          </Alert>
        </Box>
      )}
    </Box>
  );
};

export default DigitalThreadView;
