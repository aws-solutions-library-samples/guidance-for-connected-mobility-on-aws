import React, { useState, useEffect, useMemo } from 'react';
import { Table, Box, Header, Button, SpaceBetween, StatusIndicator, Modal, ExpandableSection, Badge } from '@cloudscape-design/components';

import { getDataProcessingApiEndpoint } from '../../config/api';
import { authFetch } from '../../utils/authFetch';
import CreateDecoderManifestWizard from './CreateDecoderManifestWizard';

const API = () => getDataProcessingApiEndpoint().replace(/\/$/, '');

interface DecoderManifest {
  decoderManifestName: string;
  decoderManifestVersion: string;
  status: string;
  modelName: string;
  description: string;
  createTimestamp: string;
}

interface SignalDecoder {
  fullyQualifiedName: string;
  signalDecoderType: string;
  interfaceId: string;
  signalDecoderPayloadType: string;
  hasPayload: boolean;
  signalDecoderPayload?: any;
}

interface NetworkInterface {
  interfaceId: string;
  networkInterfaceType: string;
  networkInterfacePayload: string;
}

interface SignalTreeNode {
  name: string;
  path: string;
  children: Map<string, SignalTreeNode>;
  signals: SignalDecoder[];
}

function buildSignalTree(signals: SignalDecoder[]): SignalTreeNode {
  const root: SignalTreeNode = { name: 'Vehicle', path: '', children: new Map(), signals: [] };
  for (const sig of signals) {
    const parts = sig.fullyQualifiedName.split('.');
    // Skip "Vehicle" root, group by level 2 (e.g., ADAS, Body, Cabin)
    let node = root;
    for (let i = 1; i < parts.length - 1; i++) {
      const key = parts[i];
      if (!node.children.has(key)) {
        node.children.set(key, { name: key, path: parts.slice(0, i + 1).join('.'), children: new Map(), signals: [] });
      }
      node = node.children.get(key)!;
    }
    node.signals.push(sig);
  }
  return root;
}

function countSignals(node: SignalTreeNode): number {
  let count = node.signals.length;
  for (const child of node.children.values()) count += countSignals(child);
  return count;
}

const signalColumns = [
  { id: 'name', header: 'Signal Name', cell: (item: SignalDecoder) => item.fullyQualifiedName.split('.').pop() },
  { id: 'type', header: 'Decoder Type', cell: (item: SignalDecoder) => item.signalDecoderType },
  { id: 'iface', header: 'Interface', cell: (item: SignalDecoder) => item.interfaceId },
  { id: 'msgId', header: 'Message ID', cell: (item: SignalDecoder) => item.signalDecoderPayload?.messageId != null ? `0x${item.signalDecoderPayload.messageId.toString(16).toUpperCase()}` : '-' },
  { id: 'startBit', header: 'Start Bit', cell: (item: SignalDecoder) => item.signalDecoderPayload?.startBit ?? '-' },
  { id: 'length', header: 'Length', cell: (item: SignalDecoder) => item.signalDecoderPayload?.length ?? '-' },
  { id: 'factor', header: 'Factor', cell: (item: SignalDecoder) => item.signalDecoderPayload?.factor ?? '-' },
  { id: 'offset', header: 'Offset', cell: (item: SignalDecoder) => item.signalDecoderPayload?.offset ?? '-' },
  { id: 'endian', header: 'Big Endian', cell: (item: SignalDecoder) => item.signalDecoderPayload ? (item.signalDecoderPayload.isBigEndian ? 'Yes' : 'No') : '-' },
];

const SignalBranch: React.FC<{ node: SignalTreeNode; depth?: number }> = ({ node, depth = 0 }) => {
  const sortedChildren = Array.from(node.children.values()).sort((a, b) => a.name.localeCompare(b.name));
  return (
    <div style={{ paddingLeft: depth > 0 ? 24 : 0 }}>
      <SpaceBetween size="xs">
        {sortedChildren.map(child => {
          const total = countSignals(child);
          return (
            <ExpandableSection
              key={child.path}
              headerText={`${child.name} (${total})`}
              variant={depth === 0 ? 'default' : 'footer'}
            >
              {child.signals.length > 0 && (
                <Table
                  columnDefinitions={signalColumns}
                  items={child.signals.sort((a, b) => a.fullyQualifiedName.localeCompare(b.fullyQualifiedName))}
                  variant="embedded"
                  empty={<Box textAlign="center">No signals</Box>}
                />
              )}
              {child.children.size > 0 && <SignalBranch node={child} depth={depth + 1} />}
            </ExpandableSection>
          );
        })}
        {node.signals.length > 0 && node.children.size > 0 && (
          <Table
            columnDefinitions={signalColumns}
            items={node.signals.sort((a, b) => a.fullyQualifiedName.localeCompare(b.fullyQualifiedName))}
            variant="embedded"
          />
        )}
      </SpaceBetween>
    </div>
  );
};

const DecoderManifestViewer: React.FC = () => {
  const [manifests, setManifests] = useState<DecoderManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<DecoderManifest | null>(null);
  const [signals, setSignals] = useState<SignalDecoder[]>([]);
  const [interfaces, setInterfaces] = useState<NetworkInterface[]>([]);
  const [signalsLoading, setSignalsLoading] = useState(false);
  const [showDetail, setShowDetail] = useState(false);
  const [showCreate, setShowCreate] = useState(false);

  const signalTree = useMemo(() => buildSignalTree(signals), [signals]);

  const loadManifests = () => {
    setLoading(true);
    const endpoint = API();
    if (!endpoint) { console.error('Data processing API endpoint not configured'); setLoading(false); return; }
    authFetch(`${endpoint}/decoder-manifests`)
      .then(res => { if (!res.ok) throw new Error(`HTTP ${res.status}`); return res.json(); })
      .then(data => { console.log('Decoder manifests response:', data); setManifests(data.decoderManifests || data.manifests || []); setLoading(false); })
      .catch(err => { console.error('Failed to load decoder manifests:', err); setLoading(false); });
  };

  useEffect(() => { loadManifests(); }, []);

  const viewManifest = (manifest: DecoderManifest) => {
    setSelected(manifest);
    setShowDetail(true);
    setSignalsLoading(true);
    Promise.all([
      authFetch(`${API()}/decoder-manifests/signals?name=${manifest.decoderManifestName}&include_payload=true`).then(r => r.json()),
      authFetch(`${API()}/decoder-manifests/network-interfaces?name=${manifest.decoderManifestName}`).then(r => r.json()),
    ]).then(([sigData, ifData]) => {
      setSignals(sigData.signals || []);
      setInterfaces(ifData.networkInterfaces || []);
      setSignalsLoading(false);
    }).catch(() => setSignalsLoading(false));
  };

  return (
    <>
      <Table
        loading={loading}
        header={
          <Header counter={`(${manifests.length})`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadManifests}>Refresh</Button>
                <Button variant="primary" onClick={() => setShowCreate(true)}>Create decoder manifest</Button>
              </SpaceBetween>
            }
          >Decoder Manifests</Header>
        }
        columnDefinitions={[
          { id: 'name', header: 'Name', cell: (item: DecoderManifest) =>
            <Button variant="link" onClick={() => viewManifest(item)}>{item.decoderManifestName}</Button>
          },
          { id: 'version', header: 'Version', cell: (item: DecoderManifest) => item.decoderManifestVersion },
          { id: 'status', header: 'Status', cell: (item: DecoderManifest) =>
            <StatusIndicator type={item.status === 'ACTIVE' ? 'success' : 'info'}>{item.status}</StatusIndicator>
          },
          { id: 'model', header: 'Model', cell: (item: DecoderManifest) => item.modelName },
          { id: 'desc', header: 'Description', cell: (item: DecoderManifest) => item.description },
        ]}
        items={manifests}
        empty={<Box textAlign="center"><b>No decoder manifests</b></Box>}
      />

      <Modal visible={showDetail} onDismiss={() => setShowDetail(false)}
        header={selected?.decoderManifestName} size="large">
        <SpaceBetween size="l">
          <ExpandableSection headerText={`Network Interfaces (${interfaces.length})`} defaultExpanded>
            <Table loading={signalsLoading}
              columnDefinitions={[
                { id: 'id', header: 'Interface ID', cell: (item: NetworkInterface) => item.interfaceId },
                { id: 'type', header: 'Type', cell: (item: NetworkInterface) => item.networkInterfaceType },
                { id: 'payload', header: 'Config', cell: (item: NetworkInterface) => {
                  try { const p = JSON.parse(item.networkInterfacePayload); return `${p.canInterfaceName} (${p.protocolName} ${p.protocolVersion})`; }
                  catch { return item.networkInterfacePayload; }
                }},
              ]}
              items={interfaces}
              empty={<Box textAlign="center">No network interfaces</Box>}
            />
          </ExpandableSection>

          <ExpandableSection headerText={`Signals (${signals.length})`} defaultExpanded>
            {signalsLoading ? (
              <Box textAlign="center" padding="l"><StatusIndicator type="loading">Loading signals...</StatusIndicator></Box>
            ) : (
              <SignalBranch node={signalTree} />
            )}
          </ExpandableSection>
        </SpaceBetween>
      </Modal>

      <Modal visible={showCreate} onDismiss={() => setShowCreate(false)}
        header="Create decoder manifest" size="large">
        <CreateDecoderManifestWizard
          onDismiss={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); loadManifests(); }}
        />
      </Modal>
    </>
  );
};

export default DecoderManifestViewer;
