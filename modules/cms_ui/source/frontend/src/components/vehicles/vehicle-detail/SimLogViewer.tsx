import React, { useState, useEffect, useRef } from 'react';
import { getSimulationApiUrl } from '../../../utils/simulation-config';
import { Box, Button, SpaceBetween, StatusIndicator, Textarea, Select, Modal, Header } from '@cloudscape-design/components';

interface SimLogViewerProps {
  vehicleId: string;
  vin?: string;
  simReachable: boolean;
  simulationId?: string | null;
}

const SimLogViewer: React.FC<SimLogViewerProps> = ({ vehicleId, vin, simReachable, simulationId }) => {
  const [logs, setLogs] = useState<string[]>([]);
  const [simId, setSimId] = useState<any>(null);
  const [simOptions, setSimOptions] = useState<any[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const modalLogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
    if (modalLogRef.current) modalLogRef.current.scrollTop = modalLogRef.current.scrollHeight;
  }, [logs]);

  const fetchSims = async () => {
    try {
      const r = await fetch(getSimulationApiUrl('/list'));
      if (r.ok) {
        const d = await r.json();
        const filtered = (d.simulations || []).filter((s: any) => {
          const vehicles = s.config?.vehicles || [];
          const ids = [vehicleId, vin].filter(Boolean);
          return vehicles.some((v: any) => {
            const val = typeof v === 'string' ? v : (v.vehicleId || v.vin || '');
            return ids.includes(val);
          });
        });
        const opts = filtered.map((s: any) => ({
          value: s.id, label: `${s.id} (${s.status})`, description: s.start_time?.substring(11, 19)
        }));
        setSimOptions(opts);
        // Only auto-select if nothing is selected yet — prefer running sims
        if (!simId && opts.length > 0) {
          const running = opts.find((o: any) => o.label.includes('running'));
          setSimId(running || opts[0]);
        }
      }
    } catch {}
  };

  const fetchLogs = async () => {
    if (!simId?.value) return;
    try {
      const r = await fetch(getSimulationApiUrl(`/status/${simId.value}`));
      if (r.ok) {
        const d = await r.json();
        const simLines = (d.output || []).map((o: any) => `[${o.timestamp?.substring(11, 19) || ''}] ${o.message}`);
        const startup = (d.config?._startup_logs || []).map((l: string) => `[startup] ${l}`);
        const status = d.status || 'unknown';
        
        if (simLines.length > 0) {
          setLogs([...startup, ...simLines]);
        } else if (status === 'running') {
          setLogs([
            `Simulation ${simId.value} is ${status}`,
            `Task: ${d.task_arn?.split('/').pop() || 'pending'}`,
            `ECS status: ${d.ecs_status || 'starting'}`,
            '',
            'Waiting for simulator output...',
            '(FWE agent must pass health check before simulator starts — this can take 1-2 minutes)',
          ]);
        } else if (status === 'completed') {
          setLogs([...startup, ...simLines, '', `✅ Simulation ${status}`]);
        } else {
          setLogs([`Simulation ${simId.value}: ${status}`]);
        }
      }
    } catch {}
  };

  useEffect(() => {
    if (simReachable) {
      fetchSims();
      // Poll for simulations until one is found
      const pollId = setInterval(fetchSims, 3000);
      return () => clearInterval(pollId);
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [simReachable]);

  useEffect(() => { 
    if (simId) {
      fetchLogs();
      if (!intervalRef.current) {
        intervalRef.current = setInterval(fetchLogs, 2000);
        setStreaming(true);
      }
    }
  }, [simId]);

  // Auto-select and stream when simulationId prop changes
  useEffect(() => {
    if (simulationId) {
      const opt = { value: simulationId, label: `Simulation ${simulationId}` };
      setSimId(opt);
      setStreaming(true);
      fetchSims();
    }
  }, [simulationId]);

  const toggleStream = () => {
    if (streaming) {
      if (intervalRef.current) clearInterval(intervalRef.current);
      intervalRef.current = null;
      setStreaming(false);
    } else {
      fetchLogs();
      intervalRef.current = setInterval(() => { fetchLogs(); fetchSims(); }, 2000);
      setStreaming(true);
    }
  };

  if (!simReachable) return <StatusIndicator type="stopped">Simulator offline</StatusIndicator>;

  const logStyle: React.CSSProperties = { height: '320px', overflowY: 'auto' as const };

  return (
    <>
      <SpaceBetween size="s">
        <Box>
          <SpaceBetween direction="horizontal" size="xs">
            <Select selectedOption={simId} onChange={({ detail }) => setSimId(detail.selectedOption)}
              options={simOptions} placeholder="Select simulation" />
            <Button iconName={streaming ? 'close' : 'caret-right-filled'} onClick={toggleStream}>
              {streaming ? 'Stop' : 'Stream'}
            </Button>
            <Button iconName="refresh" onClick={() => { fetchSims(); fetchLogs(); }} variant="icon" />
            <Button iconName="expand" onClick={() => setExpanded(true)} variant="icon" />
          </SpaceBetween>
        </Box>
        <div ref={logRef} className="theme-log-viewer" style={{ height: '320px' }}>{logs.join('\n')}</div>
      </SpaceBetween>
      <Modal visible={expanded} onDismiss={() => setExpanded(false)} size="max"
        header={<Header>Simulation Logs</Header>}>
        <div ref={modalLogRef} className="theme-log-viewer" style={{ height: '70vh' }}>{logs.join('\n')}</div>
      </Modal>
    </>
  );
};

export default SimLogViewer;
