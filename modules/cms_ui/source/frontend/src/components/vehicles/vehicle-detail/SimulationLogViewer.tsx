// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useRef } from 'react';
import { getSimulationApiUrl } from '../../../utils/simulation-config';
import { Box, Container, Header, StatusIndicator, SpaceBetween } from '@cloudscape-design/components';

interface Props {
  simulationId: string | null;
}

const SimulationLogViewer: React.FC<Props> = ({ simulationId }) => {
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState('');
  const pollRef = useRef<any>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (!simulationId) return;

    setLogs(['Simulation started, waiting for logs...']);
    setStatus('running');

    const poll = async () => {
      try {
        const resp = await fetch(getSimulationApiUrl(`/status/${simulationId}`));
        if (resp.ok) {
          const data = await resp.json();
          const simLogs = (data.output || []).map((l: any) => `[SIM] ${l.message}`);
          const fweLogs = (data.fwe_logs || []).map((l: any) => `[FWE] ${l.message}`);
          const merged = [...simLogs, ...fweLogs];
          if (merged.length > 0) setLogs(merged.slice(-100));
          setStatus(data.status || 'running');
          if (data.status === 'completed' || data.status === 'failed') {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
        }
      } catch {}
    };

    poll();
    pollRef.current = setInterval(poll, 3000);

    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [simulationId]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  if (!simulationId) {
    return (
      <Container header={<Header variant="h3">Simulation Output</Header>}>
        <Box color="text-body-secondary" textAlign="center" padding="l">
          No active simulation. Use the Trip Simulator to start one.
        </Box>
      </Container>
    );
  }

  return (
    <Container header={
      <Header variant="h3" description={`ID: ${simulationId}`}>
        <SpaceBetween direction="horizontal" size="s">
          <span>Simulation Output</span>
          <StatusIndicator type={status === 'running' ? 'in-progress' : status === 'completed' ? 'success' : status === 'failed' ? 'error' : 'pending'}>
            {status || 'starting'}
          </StatusIndicator>
        </SpaceBetween>
      </Header>
    }>
      <div style={{
        backgroundColor: 'var(--color-background-code-editor-default, #0f1b2d)',
        color: '#d1d5db',
        fontFamily: 'monospace',
        fontSize: '12px',
        padding: '12px',
        borderRadius: '8px',
        maxHeight: '400px',
        overflowY: 'auto',
        whiteSpace: 'pre-wrap',
        lineHeight: '1.6',
      }}>
        {logs.join('\n')}
        <div ref={logEndRef} />
      </div>
    </Container>
  );
};

export default SimulationLogViewer;
