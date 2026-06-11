import React, { useState, useEffect } from 'react';
import { Table, Box, Header, Button, SpaceBetween, StatusIndicator, Modal, KeyValuePairs, Pagination, TextFilter, Select, Tabs } from '@cloudscape-design/components';
import { getDataProcessingApiEndpoint, getRuntimeConfig } from '../../config/api';
import { authFetch } from '../../utils/authFetch';
import CreateCampaignWizard from '../commons/CreateCampaignWizard';

const api = () => getDataProcessingApiEndpoint().replace(/\/$/, '');
const mainApi = () => getRuntimeConfig().apiEndpoint;

interface Campaign {
  campaignId: string;
  campaignName: string;
  status: string;
  targetArn: string;
  decoderManifestId?: string;
  description?: string;
  collectionScheme?: any;
  signalsToCollect?: number[];
  eventRef?: string;
  createdAt?: string;
}

interface TemplateRow extends Campaign {
  vehicleCount: number;
}

const statusType = (s: string): 'success' | 'warning' | 'info' | 'stopped' | 'loading' => {
  switch (s) {
    case 'RUNNING': return 'success';
    case 'SUSPENDED': return 'stopped';
    case 'WAITING_FOR_APPROVAL': return 'warning';
    case 'CREATING': return 'loading';
    case 'ACTIVE': return 'info';
    default: return 'info';
  }
};

const schemeLabel = (cs: any, sigMap?: Record<number, any>) => {
  if (!cs) return '—';
  if (cs.type === 'TIME_BASED') return `Every ${(cs.periodMs || 0) / 1000}s`;
  if (cs.type === 'CONDITION_BASED') {
    const expr = cs.conditionExpression || '';
    const match = expr.match(/signal\((\d+)\)/);
    if (match && sigMap) {
      const sig = sigMap[Number(match[1])];
      if (sig) return expr.replace(match[0], `${sig.signal_name} (${match[1]})`);
    }
    return expr || 'Condition';
  }
  return cs.type || '—';
};

const CampaignViewer: React.FC = () => {
  const [templates, setTemplates] = useState<TemplateRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<TemplateRow | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [schemeDetail, setSchemeDetail] = useState<any>(null);
  const [showWizard, setShowWizard] = useState(false);
  const [showAssign, setShowAssign] = useState(false);
  const [assignMode, setAssignMode] = useState('vehicles');
  const [vehicles, setVehicles] = useState<any[]>([]);
  const [fleets, setFleets] = useState<any[]>([]);
  const [selectedVehicles, setSelectedVehicles] = useState<any[]>([]);
  const [selectedFleet, setSelectedFleet] = useState<any>(null);
  const [assigning, setAssigning] = useState(false);
  const [signalPage, setSignalPage] = useState(1);
  const [signalFilter, setSignalFilter] = useState('');
  const [signalMap, setSignalMap] = useState<Record<number, any>>({});
  const PAGE_SIZE = 20;

  const load = () => {
    setLoading(true);
    authFetch(`${api()}/campaigns`)
      .then(r => r.json())
      .then(data => {
        const all: Campaign[] = data.campaigns || [];
        const tpls = all.filter(c => c.targetArn === 'template');
        const assigns = all.filter(c => c.targetArn !== 'template');
        // Count assignments per template name
        const counts: Record<string, number> = {};
        assigns.forEach(a => { counts[a.campaignName] = (counts[a.campaignName] || 0) + 1; });
        setTemplates(tpls.map(t => ({ ...t, vehicleCount: counts[t.campaignName] || 0 })));
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    load();
    authFetch(`${mainApi()}api/v1/signal-catalog`).then(r => r.json())
      .then(d => {
        const m: Record<number, any> = {};
        (d.signals || []).forEach((s: any) => { m[s.signal_id] = s; });
        setSignalMap(m);
      }).catch(() => {});
  }, []);

  const viewCampaign = (c: TemplateRow) => {
    setSelected(c);
    setShowDetail(true);
    setSchemeDetail(null);
    setSignalPage(1);
    setSignalFilter('');
    authFetch(`${api()}/campaigns/collection-scheme?name=${c.campaignId}`)
      .then(r => r.json())
      .then(data => setSchemeDetail(data.collectionScheme || null))
      .catch(() => {});
  };

  const openAssign = () => {
    setShowAssign(true);
    setSelectedVehicles([]);
    setSelectedFleet(null);
    setAssignMode('vehicles');
    if (vehicles.length === 0) {
      authFetch(`${mainApi()}api/v1/vehicles?limit=200`).then(r => r.json())
        .then(d => setVehicles(d.vehicles || [])).catch(() => {});
    }
    if (fleets.length === 0) {
      authFetch(`${mainApi()}api/v1/fleets`).then(r => r.json())
        .then(d => setFleets(d.fleets || [])).catch(() => {});
    }
  };

  const handleAssign = async () => {
    if (!selected) return;
    setAssigning(true);
    try {
      const body: any = { campaignName: selected.campaignName };
      if (assignMode === 'fleet' && selectedFleet) {
        body.fleetId = selectedFleet.value;
      } else {
        body.vehicles = selectedVehicles.map((v: any) => v.vin || v.vehicleId);
      }
      const res = await authFetch(`${api()}/campaigns/assign`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) throw new Error((await res.json()).error || 'Assignment failed');
      const data = await res.json();
      setShowAssign(false);
      setShowDetail(false);
      load();
      alert(`Assigned to ${data.assigned?.length || 0} vehicle(s)`);
    } catch (e: any) { alert(e.message); }
    setAssigning(false);
  };

  return (
    <>
      <Table loading={loading} resizableColumns
        header={
          <Header counter={`(${templates.length})`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="primary" onClick={() => setShowWizard(true)}>Create campaign</Button>
                <Button iconName="refresh" onClick={load} />
              </SpaceBetween>
            }>
            Campaigns
          </Header>
        }
        columnDefinitions={[
          { id: 'name', header: 'Campaign Name', cell: (c: TemplateRow) =>
            <Button variant="link" onClick={() => viewCampaign(c)}>{c.campaignName}</Button>, minWidth: 180, width: 260 },
          { id: 'type', header: 'Type', cell: (c: TemplateRow) =>
              c.collectionScheme?.type === 'CONDITION_BASED' ? 'Condition' : 'Time-based', minWidth: 100, width: 110 },
          { id: 'scheme', header: 'Collection Scheme', cell: (c: TemplateRow) => schemeLabel(c.collectionScheme, signalMap), minWidth: 150, width: 280 },
          { id: 'status', header: 'Status', cell: (c: TemplateRow) =>
            <StatusIndicator type={statusType(c.status)}>{c.status}</StatusIndicator>, minWidth: 100, width: 130 },
          { id: 'vehicles', header: 'Vehicles', cell: (c: TemplateRow) => c.vehicleCount, minWidth: 80, width: 90 },
          { id: 'signals', header: 'Signals', cell: (c: TemplateRow) => c.signalsToCollect?.length || 0, minWidth: 70, width: 80 },
          { id: 'eventRef', header: 'Event Ref', cell: (c: TemplateRow) => c.eventRef || '—', minWidth: 120, width: 180 },
          { id: 'desc', header: 'Description', cell: (c: TemplateRow) => c.description || '—', minWidth: 150 },
        ]}
        items={templates}
        empty={<Box textAlign="center"><b>No campaigns</b><br/>Create a campaign to get started.</Box>}
      />

      {/* Detail Modal */}
      <Modal visible={showDetail} onDismiss={() => setShowDetail(false)}
        header={selected?.campaignName} size="large"
        footer={<Box float="right"><Button variant="primary" onClick={openAssign}>Assign vehicles</Button></Box>}>
        {selected && (
          <SpaceBetween size="l">
            <KeyValuePairs columns={3} items={[
              { label: 'Campaign ID', value: selected.campaignId },
              { label: 'Status', value: <StatusIndicator type={statusType(selected.status)}>{selected.status}</StatusIndicator> },
              { label: 'Vehicles assigned', value: String(selected.vehicleCount) },
              { label: 'Decoder Manifest', value: selected.decoderManifestId || '—' },
              { label: 'Type', value: selected.collectionScheme?.type || '—' },
              { label: 'Collection Scheme', value: schemeLabel(selected.collectionScheme, signalMap) },
              { label: 'Event Reference', value: selected.eventRef || '—' },
              { label: 'Signals', value: String(selected.signalsToCollect?.length || 0) },
              { label: 'Created', value: selected.createdAt || '—' },
            ]} />
            {schemeDetail?.signalsToCollect && (() => {
              const allSignals = (schemeDetail.signalsToCollect as any[]).map((s: any) => {
                const id = s.signalId ?? s;
                const cat = signalMap[id];
                return { id, name: cat?.signal_name || `signal_${id}`, group: cat?.signal_group || '—', unit: cat?.unit || '—' };
              });
              const filtered = signalFilter
                ? allSignals.filter(s => `${s.name} ${s.group}`.toLowerCase().includes(signalFilter.toLowerCase()))
                : allSignals;
              const pageItems = filtered.slice((signalPage - 1) * PAGE_SIZE, signalPage * PAGE_SIZE);
              return (
                <Table
                  header={<Header variant="h3" counter={`(${filtered.length})`}>Signals</Header>}
                  items={pageItems}
                  columnDefinitions={[
                    { id: 'name', header: 'Signal Name', cell: s => s.name },
                    { id: 'group', header: 'Group', cell: s => s.group },
                    { id: 'unit', header: 'Unit', cell: s => s.unit },
                  ]}
                  filter={
                    <TextFilter filteringText={signalFilter}
                      onChange={({ detail }) => { setSignalFilter(detail.filteringText); setSignalPage(1); }}
                      filteringPlaceholder="Find signal" />
                  }
                  pagination={
                    <Pagination currentPageIndex={signalPage}
                      pagesCount={Math.ceil(filtered.length / PAGE_SIZE) || 1}
                      onChange={({ detail }) => setSignalPage(detail.currentPageIndex)} />
                  }
                  empty={<Box textAlign="center">No signals</Box>}
                />
              );
            })()}
          </SpaceBetween>
        )}
      </Modal>

      {/* Assign Vehicles/Fleet Modal */}
      <Modal visible={showAssign} onDismiss={() => setShowAssign(false)}
        header={`Assign: ${selected?.campaignName}`} size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setShowAssign(false)}>Cancel</Button>
              <Button variant="primary" loading={assigning} onClick={handleAssign}
                disabled={assignMode === 'vehicles' ? selectedVehicles.length === 0 : !selectedFleet}>
                {assignMode === 'fleet' ? `Assign to fleet` : `Assign to ${selectedVehicles.length} vehicle(s)`}
              </Button>
            </SpaceBetween>
          </Box>
        }>
        <Tabs tabs={[
          {
            id: 'vehicles', label: 'By vehicle',
            content: (
              <Table selectionType="multi" selectedItems={selectedVehicles}
                onSelectionChange={({ detail }) => { setSelectedVehicles(detail.selectedItems); setAssignMode('vehicles'); }}
                items={vehicles}
                columnDefinitions={[
                  { id: 'vin', header: 'VIN', cell: (v: any) => v.vin || v.vehicleId },
                  { id: 'make', header: 'Make/Model', cell: (v: any) => `${v.make || ''} ${v.model || ''}`.trim() || '—' },
                  { id: 'fleet', header: 'Fleet', cell: (v: any) => v.fleet_name || v.fleetName || '—' },
                ]}
                empty={<Box textAlign="center">No vehicles found</Box>}
              />
            ),
          },
          {
            id: 'fleet', label: 'By fleet',
            content: (
              <SpaceBetween size="s">
                <Select selectedOption={selectedFleet}
                  onChange={({ detail }) => { setSelectedFleet(detail.selectedOption); setAssignMode('fleet'); }}
                  options={fleets.map((f: any) => ({ value: f.fleetId, label: f.name || f.fleetId, description: `${f.fleetType || ''} · ${f.operationalCity || ''}` }))}
                  placeholder="Select fleet" filteringType="auto" />
                {selectedFleet && <Box>All vehicles in this fleet will be assigned to the campaign.</Box>}
              </SpaceBetween>
            ),
          },
        ]} />
      </Modal>

      {/* Create Campaign Wizard */}
      {showWizard && (
        <Modal visible={true} onDismiss={() => setShowWizard(false)} header="Create Campaign" size="large">
          <CreateCampaignWizard
            visible={true}
            onDismiss={() => setShowWizard(false)}
            onCreated={() => { setShowWizard(false); load(); }}
          />
        </Modal>
      )}
    </>
  );
};

export default CampaignViewer;
