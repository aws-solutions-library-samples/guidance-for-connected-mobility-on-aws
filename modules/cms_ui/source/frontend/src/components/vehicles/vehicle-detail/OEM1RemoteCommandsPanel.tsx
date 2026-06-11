// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { authFetch } from '@/utils/authFetch';

interface Props {
  vin: string;
}

type CommandState = 'idle' | 'sending' | 'success' | 'error';

interface CommandResult {
  command: string;
  message: string;
  type: 'success' | 'error';
}

const COMMANDS = [
  { id: 'LOCK',   label: 'Lock',          icon: 'lock-private' as const,  variant: 'primary' as const },
  { id: 'UNLOCK', label: 'Unlock',        icon: 'unlocked'     as const,  variant: 'normal'  as const },
  { id: 'START',  label: 'Remote Start',  icon: 'caret-right-filled' as const, variant: 'normal' as const },
  { id: 'STOP',   label: 'Remote Stop',   icon: 'close'        as const,  variant: 'normal'  as const },
] as const;

export default function OEM1RemoteCommandsPanel({ vin }: Props) {
  const [sending, setSending] = useState<string | null>(null);
  const [result, setResult] = useState<CommandResult | null>(null);

  const sendCommand = async (command: string) => {
    setSending(command);
    setResult(null);
    try {
      const res = await authFetch('/admin/oem1/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vin, command }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        const vinResult = Array.isArray(data.results) ? data.results[0] : null;
        if (vinResult?.error) {
          setResult({ command, type: 'error', message: `Command failed: ${vinResult.error.message || JSON.stringify(vinResult.error)} (code ${vinResult.error.code})` });
        } else {
          setResult({ command, type: 'success', message: `${command} command sent successfully` });
        }
      } else {
        setResult({ command, type: 'error', message: data.error || `Request failed (${res.status})` });
      }
    } catch {
      setResult({ command, type: 'error', message: 'Network error — could not reach command API' });
    } finally {
      setSending(null);
    }
  };

  return (
    <Container
      header={
        <Header
          variant="h2"
          description="Commands are sent via the OEM telematics command API. Vehicle must be awake — deep sleep mode prevents remote commands."
        >
          Remote Commands
        </Header>
      }
    >
      <SpaceBetween size="m">
        <Alert type="info" dismissible={false}>
          <strong>Deep sleep notice:</strong> Once a vehicle has entered deep sleep mode, remote
          features will not be available. Ensure vehicles are driven regularly to prevent deep sleep.
        </Alert>

        <SpaceBetween direction="horizontal" size="s">
          {COMMANDS.map(cmd => (
            <Button
              key={cmd.id}
              variant={cmd.variant}
              iconName={cmd.icon}
              loading={sending === cmd.id}
              disabled={sending !== null}
              onClick={() => sendCommand(cmd.id)}
            >
              {cmd.label}
            </Button>
          ))}
        </SpaceBetween>

        {result && (
          <Box>
            <StatusIndicator type={result.type === 'success' ? 'success' : 'error'}>
              {result.message}
            </StatusIndicator>
          </Box>
        )}
      </SpaceBetween>
    </Container>
  );
}
