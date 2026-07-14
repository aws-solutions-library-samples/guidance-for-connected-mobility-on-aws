import React, { useState, useEffect } from 'react';
import {
  Table, Box, Header, Button, SpaceBetween, StatusIndicator, Badge, Modal,
  KeyValuePairs, Select, FormField, Multiselect, Alert, ButtonDropdown
} from '@cloudscape-design/components';
import { getDataProcessingApiEndpoint } from '../../../config/api';
import { authFetch } from '../../../utils/authFetch';
import CreateCampaignWizard from '../../commons/CreateCampaignWizard';

interface Campaign {
  campaignId: string;
  campaignName: string;
  targetArn: string;
  status: string;
  sourceFleetId?: string;
  sourceFleetCampaignId?: string;
  decoderManifestId?: string;
  collectionScheme?: {
    type: string;
    conditionExpression?: string;
    periodMs?: number;
    minimumIntervalMs?: number;
    triggerMode?: string;
  };
  signalsToCollect?: number[];
  eventRef?: string;
}

interface Props {
  vehicleId?: string;
  onCountChange?: (count: number) => void;
}

// FleetWise campaign statuses: CREATING → WAITING_FOR_APPROVAL → RUNNING → SUSPENDED
const statusType = (s: string): 'success' | 'warning' | 'info' | 'stopped' | 'loading' => {
  switch (s) {
    case 'RUNNING': return 'success';
    case 'SUSPENDED': return 'stopped';
    case 'WAITING_FOR_APPROVAL': return 'warning';
    case 'CREATING': return 'loading';
    default: return 'info';
  }
};

const schemeLabel = (cs?: Campaign['collectionScheme']) => {
  if (!cs) return 'N/A';
  if (cs.type === 'TIME_BASED') return `Every ${(cs.periodMs || 0) / 1000}s`;
  if (cs.type === 'CONDITION_BASED') return cs.conditionExpression || 'Condition';
  return cs.type;
};

const VehicleCampaignsTable: React.FC<Props> = ({ vehicleId, onCountChange }) => {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [selectedItems, setSelectedItems] = useState<Campaign[]>([]);
  const [detailVisible, setDetailVisible] = useState(false);
  const [detailCampaign, setDetailCampaign] = useState<Campaign | null>(null);
  const [schemeDetail, setSchemeDetail] = useState<any>(null);

  // Assign modal state
  const [assignVisible, setAssignVisible] = useState(false);
  const [createWizardVisible, setCreateWizardVisible] = useState(false);
  const [templates, setTemplates] = useState<Campaign[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<any>(null);
  const [assignError, setAssignError] = useState('');

  const apiBase = getDataProcessingApiEndpoint();

  const load = async () => {
    if (!vehicleId || !apiBase) { setLoading(false); return; }
    setLoading(true);
    try {
      const res = await authFetch(`${apiBase}campaigns?vehicle=${vehicleId}`);
      const data = await res.json();
      setCampaigns((data.campaigns || []).filter((c: Campaign) => c.targetArn !== 'template'));
      onCountChange?.((data.campaigns || []).filter((c: Campaign) => c.targetArn !== 'template').length);
    } catch (e) {
      console.error('Failed to load campaigns:', e);
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, [vehicleId, apiBase]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadTemplates = async () => {
    try {
      const res = await authFetch(`${apiBase}campaigns`);
      const data = await res.json();
      setTemplates((data.campaigns || []).filter((c: Campaign) => c.targetArn === 'template'));
    } catch { /* ignore */ }
  };

  const updateStatus = async (action: 'SUSPEND' | 'RESUME') => {
    if (!selectedItems.length) return;
    setActionLoading(true);
    const newStatus = action === 'SUSPEND' ? 'SUSPENDED' : 'RUNNING';
    try {
      for (const c of selectedItems) {
        await authFetch(`${apiBase}campaigns`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ campaignId: c.campaignId, status: newStatus })
        });
      }
      setSelectedItems([]);
      await load();
    } catch (e) {
      console.error('Failed to update campaign:', e);
    }
    setActionLoading(false);
  };

  const deleteCampaigns = async () => {
    if (!selectedItems.length) return;
    setActionLoading(true);
    try {
      for (const c of selectedItems) {
        await authFetch(`${apiBase}campaigns?campaignId=${encodeURIComponent(c.campaignId)}`, {
          method: 'DELETE'
        });
      }
      setSelectedItems([]);
      await load();
    } catch (e) {
      console.error('Failed to delete campaign:', e);
    }
    setActionLoading(false);
  };

  const assignCampaign = async () => {
    if (!selectedTemplate || !vehicleId) return;
    setAssignError('');
    setActionLoading(true);
    try {
      const res = await authFetch(`${apiBase}campaigns/assign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaignName: selectedTemplate.value, vehicles: [vehicleId] })
      });
      if (!res.ok) {
        const err = await res.json();
        setAssignError(err.error || 'Assignment failed');
      } else {
        setAssignVisible(false);
        setSelectedTemplate(null);
        await load();
      }
    } catch (e) {
      setAssignError('Network error');
    }
    setActionLoading(false);
  };

  const viewDetail = async (c: Campaign) => {
    setDetailCampaign(c);
    setDetailVisible(true);
    setSchemeDetail(null);
    try {
      const res = await authFetch(`${apiBase}campaigns/collection-scheme?name=${c.campaignId}`);
      const data = await res.json();
      setSchemeDetail(data.collectionScheme || null);
    } catch { /* ignore */ }
  };

  const openAssign = () => {
    loadTemplates();
    setAssignVisible(true);
    setAssignError('');
    setSelectedTemplate(null);
  };

  // Determine available actions based on selection
  const hasRunning = selectedItems.some(c => c.status === 'RUNNING');
  const hasSuspended = selectedItems.some(c => c.status === 'SUSPENDED');

  return (
    <>
      <Table
        loading={loading}
        loadingText="Loading campaigns..."
        selectionType="multi"
        selectedItems={selectedItems}
        onSelectionChange={({ detail }) => setSelectedItems(detail.selectedItems)}
        header={
          <Header counter={`(${campaigns.length})`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setCreateWizardVisible(true)} variant="primary">Create campaign</Button>
                <Button onClick={openAssign}>Assign existing</Button>
                <ButtonDropdown
                  items={[
                    { id: 'suspend', text: 'Suspend', disabled: !hasRunning || actionLoading || selectedItems.some((c: Campaign) => c.sourceFleetId) },
                    { id: 'resume', text: 'Resume', disabled: !hasSuspended || actionLoading || selectedItems.some((c: Campaign) => c.sourceFleetId) },
                    { id: 'delete', text: 'Remove', disabled: !selectedItems.length || actionLoading || selectedItems.some((c: Campaign) => c.sourceFleetId) },
                  ]}
                  disabled={!selectedItems.length}
                  onItemClick={({ detail }) => {
                    if (detail.id === 'suspend') updateStatus('SUSPEND');
                    else if (detail.id === 'resume') updateStatus('RESUME');
                    else if (detail.id === 'delete') deleteCampaigns();
                  }}
                >
                  Actions
                </ButtonDropdown>
                <Button iconName="refresh" onClick={load} />
              </SpaceBetween>
            }>
            Active Campaigns
          </Header>
        }
        items={campaigns}
        columnDefinitions={[
          {
            id: 'name', header: 'Campaign', width: 220,
            cell: (c) => <Button variant="link" onClick={() => viewDetail(c)}>{c.campaignName}</Button>
          },
          {
            id: 'type', header: 'Type', width: 130,
            cell: (c) => c.collectionScheme?.type === 'CONDITION_BASED' ? 'Condition' : 'Time-based'
          },
          {
            id: 'scheme', header: 'Collection Scheme', width: 250,
            cell: (c) => schemeLabel(c.collectionScheme)
          },
          {
            id: 'status', header: 'Status', width: 140,
            cell: (c) => <StatusIndicator type={statusType(c.status)}>{c.status}</StatusIndicator>
          },
          {
            id: 'source', header: 'Source', width: 100,
            cell: (c) => c.sourceFleetId
              ? <Badge color="blue">Fleet</Badge>
              : <Badge color="grey">Direct</Badge>
          },
          {
            id: 'sync', header: 'Sync Health', width: 150,
            cell: (c) => c.syncStatus === 'HEALTHY'
              ? <StatusIndicator type="success">{c.lastSyncedAt ? `Synced ${new Date(c.lastSyncedAt).toLocaleDateString()}` : 'Healthy'}</StatusIndicator>
              : <StatusIndicator type="pending">Pending sync</StatusIndicator>
          },
          {
            id: 'signals', header: 'Signals', width: 80,
            cell: (c) => c.signalsToCollect?.length || 0
          },
          {
            id: 'eventRef', header: 'Event Ref', width: 180,
            cell: (c) => c.eventRef || '—'
          }
        ]}
        empty={<Box textAlign="center" padding="l"><b>No campaigns assigned to this vehicle</b></Box>}
      />

      {/* Assign Campaign Modal */}
      <Modal visible={assignVisible} onDismiss={() => setAssignVisible(false)}
        header="Assign Campaign" size="medium"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAssignVisible(false)}>Cancel</Button>
              <Button variant="primary" loading={actionLoading}
                disabled={!selectedTemplate} onClick={assignCampaign}>
                Assign
              </Button>
            </SpaceBetween>
          </Box>
        }>
        <SpaceBetween size="m">
          {assignError && <Alert type="error">{assignError}</Alert>}
          <FormField label="Campaign template" description="Select a campaign template to assign to this vehicle">
            <Select
              selectedOption={selectedTemplate}
              onChange={({ detail }) => setSelectedTemplate(detail.selectedOption)}
              options={templates
                .filter(t => !campaigns.some(c => c.campaignName === t.campaignName))
                .map(t => ({
                  label: t.campaignName,
                  value: t.campaignName,
                  description: `${t.collectionScheme?.type === 'CONDITION_BASED' ? 'Condition' : 'Time-based'} — ${schemeLabel(t.collectionScheme)}`
                }))}
              placeholder="Choose a campaign template"
              filteringType="auto"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Campaign Detail Modal */}
      <Modal visible={detailVisible} onDismiss={() => setDetailVisible(false)}
        header={detailCampaign?.campaignName} size="large">
        {detailCampaign && (
          <SpaceBetween size="l">
            <KeyValuePairs columns={3} items={[
              { label: 'Campaign ID', value: detailCampaign.campaignId },
              { label: 'Status', value: <StatusIndicator type={statusType(detailCampaign.status)}>{detailCampaign.status}</StatusIndicator> },
              { label: 'Decoder Manifest', value: detailCampaign.decoderManifestId || 'N/A' },
              { label: 'Type', value: detailCampaign.collectionScheme?.type || 'N/A' },
              { label: 'Collection Scheme', value: schemeLabel(detailCampaign.collectionScheme) },
              { label: 'Event Reference', value: detailCampaign.eventRef || 'N/A' },
              { label: 'Trigger Mode', value: detailCampaign.collectionScheme?.triggerMode || 'N/A' },
              { label: 'Min Interval', value: detailCampaign.collectionScheme?.minimumIntervalMs ? `${detailCampaign.collectionScheme.minimumIntervalMs}ms` : 'N/A' },
              { label: 'Signals Count', value: String(detailCampaign.signalsToCollect?.length || 0) },
            ]} />
            {schemeDetail?.signalsToCollect && (
              <Table
                header={<Header variant="h3" counter={`(${schemeDetail.signalsToCollect.length})`}>Signals</Header>}
                items={schemeDetail.signalsToCollect}
                columnDefinitions={[
                  { id: 'id', header: 'Signal ID', cell: (s: any) => s.signalId ?? s },
                  { id: 'name', header: 'Signal Name', cell: (s: any) => s.signalName || s.name || '—' },
                ]}
                empty={<Box textAlign="center">No signals</Box>}
              />
            )}
          </SpaceBetween>
        )}
      </Modal>

      {/* Create Campaign Wizard */}
      {createWizardVisible && (
        <Modal visible={true} onDismiss={() => setCreateWizardVisible(false)}
          header="Create Campaign" size="max">
          <CreateCampaignWizard
            visible={true}
            onDismiss={() => setCreateWizardVisible(false)}
            onCreated={() => { setCreateWizardVisible(false); load(); }}
            lockedVehicle={vehicleId}
          />
        </Modal>
      )}
    </>
  );
};

export default VehicleCampaignsTable;
