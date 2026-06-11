import React, { useState, useEffect, useRef } from 'react';
import { getSimulationApiBase } from '../../../utils/simulation-config';
import { Box, Button, Modal, Header, SpaceBetween, StatusIndicator, Textarea } from '@cloudscape-design/components';

interface FWELogViewerProps {
  vin: string;
  simReachable: boolean;
  agentRunning: boolean;
}

const FWELogViewer: React.FC<FWELogViewerProps> = ({ vin, simReachable, agentRunning }) => {
  const [logs, setLogs] = useState<string[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const modalLogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
    if (modalLogRef.current) modalLogRef.current.scrollTop = modalLogRef.current.scrollHeight;
  }, [logs]);

  const fetchLogs = async () => {
    try {
      const r = await fetch(`${getSimulationApiBase()}/api/simulation/agent/logs/${vin}?tail=300&since=10m`);
      if (r.ok) {
        const d = await r.json();
        setLogs(d.logs || d.lines || []);
      }
    } catch {}
  };

  useEffect(() => {
    if (simReachable && agentRunning) {
      fetchLogs();
      if (!intervalRef.current) {
        intervalRef.current = setInterval(fetchLogs, 2000);
        setStreaming(true);
      }
    } else {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      setStreaming(false);
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [vin, simReachable, agentRunning]);

  const toggleStream = () => {
    if (streaming) {
      if (intervalRef.current) clearInterval(intervalRef.current);
      intervalRef.current = null;
      setStreaming(false);
    } else {
      fetchLogs();
      intervalRef.current = setInterval(fetchLogs, 2000);
      setStreaming(true);
    }
  };

  if (!simReachable) return <StatusIndicator type="stopped">Simulator offline</StatusIndicator>;
  if (!agentRunning) return <StatusIndicator type="stopped">No FWE agent running for {vin}</StatusIndicator>;

  const logStyle: React.CSSProperties = { height: '320px', overflowY: 'auto' as const };

  return (
    <>
      <SpaceBetween size="s">
        <Box>
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName={streaming ? 'close' : 'caret-right-filled'} onClick={toggleStream}>
              {streaming ? 'Stop' : 'Stream'}
            </Button>
            <Button iconName="refresh" onClick={fetchLogs} variant="icon" />
            <Button iconName="expand" onClick={() => setExpanded(true)} variant="icon" />
          </SpaceBetween>
        </Box>
        <div ref={logRef} className="theme-log-viewer" style={{ height: '320px' }}>{logs.join('\n')}</div>
      </SpaceBetween>
      <Modal visible={expanded} onDismiss={() => setExpanded(false)} size="max"
        header={<Header>FWE Agent Logs — {vin}</Header>}>
        <div ref={modalLogRef} className="theme-log-viewer" style={{ height: '70vh' }}>{logs.join('\n')}</div>
      </Modal>
    </>
  );
};

export default FWELogViewer;
