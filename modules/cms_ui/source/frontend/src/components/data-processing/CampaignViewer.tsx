import React, { useState, useEffect } from 'react';
import { Table, Box, Header, Button, SpaceBetween, StatusIndicator, Modal, ColumnLayout, KeyValuePairs } from '@cloudscape-design/components';

const API = 'https://sel11tei2c.execute-api.us-east-1.amazonaws.com/prod';

interface Campaign {
  campaignId: string;
  campaignVersion: string;
  status: string;
  decoderManifestName: string;
  targetArn: string;
  compression: string;
  description: string;
  creationTimestamp: number;
  lastUpdated: number;
  totalWaves: number;
  currentWave: number;
}

interface CollectionScheme {
  collectionScheme: any;
  signalsToCollect: { name: string; maxSampleCount: number; minimumSamplingIntervalMs: number }[];
  startTime: string;
  expiryTime: string;
}

const statusType = (s: string) => {
  if (s === 'RUNNING') return 'success';
  if (s === 'DRAFT') return 'info';
  if (s === 'SUSPENDED') return 'warning';
  return 'stopped';
};

const CampaignViewer: React.FC = () => {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Campaign | null>(null);
  const [scheme, setScheme] = useState<CollectionScheme | null>(null);
  const [showDetail, setShowDetail] = useState(false);

  const load = () => {
    setLoading(true);
    fetch(`${API}/campaigns`)
      .then(r => r.json())
      .then(data => { setCampaigns(data.campaigns || []); setLoading(false); })
      .catch(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const viewCampaign = (c: Campaign) => {
    setSelected(c);
    setShowDetail(true);
    setScheme(null);
    fetch(`${API}/campaigns/collection-scheme?name=${c.campaignId}&version=${c.campaignVersion}`)
      .then(r => r.json())
      .then(data => setScheme(data.collectionScheme || null))
      .catch(() => {});
  };

  const schemeLabel = (s: CollectionScheme | null) => {
    if (!s) return 'Loading...';
    const cs = s.collectionScheme;
    if (cs.timeBasedCollectionScheme) return `Time-based: every ${cs.timeBasedCollectionScheme.periodMs / 1000}s`;
    if (cs.conditionBasedCollectionScheme) return `Condition: ${cs.conditionBasedCollectionScheme.expression}`;
    return JSON.stringify(cs);
  };

  return (
    <>
      <Table loading={loading}
        header={
          <Header counter={`(${campaigns.length})`}
            actions={<Button iconName="refresh" onClick={load}>Refresh</Button>}
          >Campaigns</Header>
        }
        columnDefinitions={[
          { id: 'name', header: 'Campaign', cell: (c: Campaign) =>
            <Button variant="link" onClick={() => viewCampaign(c)}>{c.campaignId}</Button>
          },
          { id: 'status', header: 'Status', cell: (c: Campaign) =>
            <StatusIndicator type={statusType(c.status)}>{c.status}</StatusIndicator>
          },
          { id: 'decoder', header: 'Decoder Manifest', cell: (c: Campaign) => c.decoderManifestName },
          { id: 'target', header: 'Target', cell: (c: Campaign) => c.targetArn },
          { id: 'compression', header: 'Compression', cell: (c: Campaign) => c.compression },
          { id: 'waves', header: 'Waves', cell: (c: Campaign) => `${c.currentWave}/${c.totalWaves}` },
          { id: 'desc', header: 'Description', cell: (c: Campaign) => c.description },
        ]}
        items={campaigns}
        empty={<Box textAlign="center"><b>No campaigns</b></Box>}
      />

      <Modal visible={showDetail} onDismiss={() => setShowDetail(false)}
        header={selected?.campaignId} size="large">
        {selected && (
          <SpaceBetween size="l">
            <KeyValuePairs columns={3} items={[
              { label: 'Status', value: <StatusIndicator type={statusType(selected.status)}>{selected.status}</StatusIndicator> },
              { label: 'Decoder Manifest', value: selected.decoderManifestName },
              { label: 'Target', value: selected.targetArn },
              { label: 'Compression', value: selected.compression },
              { label: 'Collection Scheme', value: schemeLabel(scheme) },
              { label: 'Signals', value: scheme ? `${scheme.signalsToCollect.length} signals` : 'Loading...' },
            ]} />

            {scheme && (
              <Table
                header={<Header variant="h3" counter={`(${scheme.signalsToCollect.length})`}>Signals to Collect</Header>}
                columnDefinitions={[
                  { id: 'name', header: 'Signal Name', cell: (s: any) => s.name, sortingField: 'name' },
                  { id: 'maxSample', header: 'Max Samples', cell: (s: any) => s.maxSampleCount },
                  { id: 'minInterval', header: 'Min Interval (ms)', cell: (s: any) => s.minimumSamplingIntervalMs },
                ]}
                items={scheme.signalsToCollect}
                sortingDisabled={false}
                empty={<Box textAlign="center">No signals configured</Box>}
              />
            )}
          </SpaceBetween>
        )}
      </Modal>
    </>
  );
};

export default CampaignViewer;
