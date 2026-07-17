import React, { useState, useEffect } from 'react';
import {
  Wizard, FormField, Input, Select, Textarea, SpaceBetween, Header,
  Toggle, Table, Box, Alert, Multiselect, ColumnLayout, Badge
} from '@cloudscape-design/components';
import { getDataProcessingApiEndpoint, getRuntimeConfig } from '../../config/api';
import { authFetch } from '../../utils/authFetch';
import { severityLabel } from '../../utils/severity';

interface Signal {
  signal_id: number;
  signal_name: string;
  signal_group: string;
  unit: string;
  vss_path: string;
}

interface Props {
  visible: boolean;
  onDismiss: () => void;
  onCreated: () => void;
  lockedVehicle?: string;
}

const dpApi = () => getDataProcessingApiEndpoint();
const mainApi = () => getRuntimeConfig().apiEndpoint;

const CreateCampaignWizard: React.FC<Props> = ({ visible, onDismiss, onCreated, lockedVehicle }) => {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [schemeType, setSchemeType] = useState<any>({ value: 'CONDITION_BASED', label: 'Condition-based' });
  const [conditionSignal, setConditionSignal] = useState<any>(null);
  const [conditionOperator, setConditionOperator] = useState<any>({ value: '>', label: '>' });
  const [conditionValue, setConditionValue] = useState('');
  const conditionExpression = conditionSignal
    ? `signal(${conditionSignal.value}) ${conditionOperator?.value || '>'} ${conditionValue || '0'}`
    : '';
  const conditionDisplay = conditionSignal
    ? `${conditionSignal.label} (${conditionSignal.value}) ${conditionOperator?.value || '>'} ${conditionValue || '0'}`
    : '';
  const [triggerMode, setTriggerMode] = useState<any>({ value: 'RISING_EDGE', label: 'On first trigger (Rising Edge)' });
  const [minIntervalMs, setMinIntervalMs] = useState('1000');
  const [periodMs, setPeriodMs] = useState('10000');
  const [decoderManifest, setDecoderManifest] = useState<any>(null);
  const [eventRef, setEventRef] = useState<any>(null);
  const [isSafetyCampaign, setIsSafetyCampaign] = useState(false);
  const [allSignals, setAllSignals] = useState<Signal[]>([]);
  const [selectedSignalOptions, setSelectedSignalOptions] = useState<any[]>([]);
  const [vehicles, setVehicles] = useState<any[]>([]);
  const [selectedVehicles, setSelectedVehicles] = useState<any[]>([]);
  const [decoderManifests, setDecoderManifests] = useState<any[]>([]);
  const [events, setEvents] = useState<any[]>([]);
  const [activeStep, setActiveStep] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible) return;
    authFetch(`${mainApi()}api/v1/signal-catalog`).then(r => r.json())
      .then(d => setAllSignals(d.signals || [])).catch(() => {});
    authFetch(`${dpApi()}decoder-manifests`).then(r => r.json())
      .then(d => {
        const m = d.decoderManifests || d.manifests || [];
        setDecoderManifests(m);
        if (m.length === 1) setDecoderManifest({ value: m[0].decoderManifestName, label: m[0].decoderManifestName });
      }).catch(() => {});
    authFetch(`${mainApi()}api/v1/event-catalog`).then(r => r.json())
      .then(d => setEvents(d.events || [])).catch(() => {});
    if (!lockedVehicle) {
      authFetch(`${mainApi()}api/v1/vehicles?limit=200`).then(r => r.json())
        .then(d => setVehicles(d.vehicles || [])).catch(() => {});
    }
  }, [visible]);

  const reset = () => {
    setName(''); setDescription(''); setConditionSignal(null); setConditionValue(''); setError('');
    setSelectedSignalOptions([]); setSelectedVehicles([]); setActiveStep(0);
    setIsSafetyCampaign(false); setEventRef(null); setSubmitting(false);
  };

  const handleDismiss = () => { reset(); onDismiss(); };

  const signalOptions = allSignals.map(s => ({
    value: String(s.signal_id),
    label: `${s.signal_name} (${s.signal_group})`,
    description: `ID: ${s.signal_id} | ${s.vss_path} | ${s.unit}`,
    tags: [s.signal_group]
  }));

  const handleSubmit = async () => {
    setSubmitting(true); setError('');
    try {
      const collectionScheme: any = { type: schemeType.value };
      if (schemeType.value === 'CONDITION_BASED') {
        collectionScheme.conditionExpression = conditionExpression;
        collectionScheme.triggerMode = triggerMode.value;
        collectionScheme.minimumIntervalMs = Number(minIntervalMs);
      } else {
        collectionScheme.periodMs = Number(periodMs);
      }
      const body: any = {
        campaignName: name, description, collectionScheme,
        signalsToCollect: selectedSignalOptions.map(s => Number(s.value)),
        decoderManifestId: decoderManifest?.value || 'cms-fleet-v1',
      };
      if (isSafetyCampaign && eventRef) body.eventRef = eventRef.value;

      const res = await authFetch(`${dpApi()}campaigns`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) throw new Error((await res.json()).error || 'Failed to create campaign');

      const vins = lockedVehicle
        ? [lockedVehicle]
        : selectedVehicles.map((v: any) => v.vin || v.vehicleId);
      if (vins.length > 0) {
        const assignRes = await authFetch(`${dpApi()}campaigns/assign`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ campaignName: name, vehicles: vins })
        });
        if (!assignRes.ok) throw new Error((await assignRes.json()).error || 'Template created but assignment failed');
      }
      reset(); onCreated();
    } catch (e: any) { setError(e.message); }
    setSubmitting(false);
  };

  if (!visible) return null;

  const submitLabel = lockedVehicle
    ? 'Create & assign to vehicle'
    : selectedVehicles.length > 0
      ? `Create & assign to ${selectedVehicles.length} vehicle(s)`
      : 'Create campaign';

  const summaryItems = (
    <ColumnLayout columns={3} variant="text-grid">
      <div><Box variant="awsui-key-label">Name</Box><div>{name || '—'}</div></div>
      <div><Box variant="awsui-key-label">Type</Box><div>{schemeType.label}</div></div>
      <div><Box variant="awsui-key-label">Scheme</Box><div>{schemeType.value === 'CONDITION_BASED' ? conditionExpression : `Every ${Number(periodMs) / 1000}s`}</div></div>
      <div><Box variant="awsui-key-label">Signals</Box><div>{selectedSignalOptions.length}</div></div>
      <div><Box variant="awsui-key-label">Decoder Manifest</Box><div>{decoderManifest?.label || '—'}</div></div>
      <div><Box variant="awsui-key-label">Event Ref</Box><div>{eventRef?.label || '—'}</div></div>
      {lockedVehicle && <div><Box variant="awsui-key-label">Target Vehicle</Box><div>{lockedVehicle}</div></div>}
    </ColumnLayout>
  );

  const steps = [
    {
      title: 'Configure campaign',
      content: (
        <SpaceBetween size="s">
          {error && <Alert type="error">{error}</Alert>}
          <FormField label="Campaign name" constraintText="Lowercase letters, numbers, hyphens only">
            <Input value={name} onChange={({ detail }) => setName(detail.value.replace(/[^a-z0-9-]/g, ''))} placeholder="cms-safety-my-campaign" />
          </FormField>
          <FormField label="Description">
            <Textarea value={description} onChange={({ detail }) => setDescription(detail.value)} rows={2} />
          </FormField>
          <FormField label="Decoder manifest">
            <Select selectedOption={decoderManifest}
              onChange={({ detail }) => setDecoderManifest(detail.selectedOption)}
              options={decoderManifests.map(m => ({ value: m.decoderManifestName, label: m.decoderManifestName }))}
              placeholder="Select decoder manifest" />
          </FormField>
          <FormField label="Scheme type">
            <Select selectedOption={schemeType}
              onChange={({ detail }) => setSchemeType(detail.selectedOption)}
              options={[
                { value: 'CONDITION_BASED', label: 'Condition-based', description: 'Collect when a condition is met' },
                { value: 'TIME_BASED', label: 'Time-based', description: 'Collect at regular intervals' },
              ]} />
          </FormField>
          {schemeType.value === 'CONDITION_BASED' ? (
            <>
              <FormField label="Condition source">
                <Select selectedOption={isSafetyCampaign ? { value: 'event', label: 'From safety event' } : { value: 'manual', label: 'Manual expression' }}
                  onChange={({ detail }) => {
                    setIsSafetyCampaign(detail.selectedOption.value === 'event');
                    if (detail.selectedOption.value === 'manual') { setEventRef(null); }
                  }}
                  options={[
                    { value: 'manual', label: 'Manual expression', description: 'Pick signal, operator, and threshold' },
                    { value: 'event', label: 'From safety event', description: 'Auto-populate from event catalog' },
                  ]} />
              </FormField>
              {isSafetyCampaign && (
                <FormField label="Safety event">
                  <Select selectedOption={eventRef}
                    onChange={({ detail }) => {
                      setEventRef(detail.selectedOption);
                      const evt = events.find((e: any) => e.event_id === detail.selectedOption.value);
                      if (evt?.signal_id != null) {
                        const sid = Number(evt.signal_id);
                        const sig = allSignals.find(s => Number(s.signal_id) === sid);
                        if (sig) setConditionSignal({ value: String(sig.signal_id), label: sig.signal_name });
                      }
                      if (evt?.threshold_operator) setConditionOperator({ value: evt.threshold_operator, label: evt.threshold_operator });
                      if (evt?.threshold_value != null) setConditionValue(String(evt.threshold_value));
                    }}
                    options={events.filter(e => e.category === 'safety').map(e => ({
                      value: e.event_id, label: e.event_id,
                      description: `${e.description || ''} · severity ${severityLabel(e.severity ?? e.severity_hint)}`,
                    }))}
                    placeholder="Select safety event" filteringType="auto" />
                </FormField>
              )}
              <FormField label="Condition" description={conditionDisplay ? `Expression: ${conditionDisplay}` : ''}>
                <ColumnLayout columns={3}>
                  <Select selectedOption={conditionSignal}
                    onChange={({ detail }) => setConditionSignal(detail.selectedOption)}
                    options={allSignals.map(s => ({ value: String(s.signal_id), label: s.signal_name, description: `ID: ${s.signal_id} · ${s.unit}` }))}
                    filteringType="auto" placeholder="Select signal" disabled={isSafetyCampaign} />
                  <Select selectedOption={conditionOperator}
                    onChange={({ detail }) => setConditionOperator(detail.selectedOption)}
                    options={['>', '>=', '<', '<=', '==', '!='].map(op => ({ value: op, label: op }))}
                    disabled={isSafetyCampaign} />
                  <Input type="number" value={conditionValue} onChange={({ detail }) => setConditionValue(detail.value)} placeholder="Threshold" disabled={isSafetyCampaign} />
                </ColumnLayout>
              </FormField>
              <ColumnLayout columns={2}>
                <FormField label="Trigger mode">
                  <Select selectedOption={triggerMode}
                    onChange={({ detail }) => setTriggerMode(detail.selectedOption)}
                    options={[
                      { value: 'RISING_EDGE', label: 'Rising Edge' },
                      { value: 'ALWAYS', label: 'Always' },
                    ]} />
                </FormField>
                <FormField label="Min trigger interval (ms)">
                  <Input type="number" value={minIntervalMs} onChange={({ detail }) => setMinIntervalMs(detail.value)} />
                </FormField>
              </ColumnLayout>
            </>
          ) : (
            <FormField label="Collection period (ms)" constraintText="10,000 – 60,000 ms">
              <Input type="number" value={periodMs} onChange={({ detail }) => setPeriodMs(detail.value)} />
            </FormField>
          )}
        </SpaceBetween>
      ),
    },
    {
      title: 'Select signals',
      content: (
        <SpaceBetween size="s">
          <FormField label="Signals to collect" description={`${selectedSignalOptions.length} of ${signalOptions.length} selected`}>
            <SpaceBetween size="xs">
              <SpaceBetween direction="horizontal" size="xs">
                <Badge color={selectedSignalOptions.length === signalOptions.length ? 'green' : 'grey'}>
                  {selectedSignalOptions.length} / {signalOptions.length}
                </Badge>
                <button
                  type="button"
                  onClick={() => setSelectedSignalOptions([...signalOptions])}
                  style={{ background: 'none', border: 'none', color: '#0972d3', cursor: 'pointer', textDecoration: 'underline', padding: 0, fontSize: 'inherit' }}
                >Select all</button>
                {selectedSignalOptions.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setSelectedSignalOptions([])}
                    style={{ background: 'none', border: 'none', color: '#0972d3', cursor: 'pointer', textDecoration: 'underline', padding: 0, fontSize: 'inherit' }}
                  >Clear all</button>
                )}
                {allSignals.length > 0 && (
                  <Select
                    selectedOption={null}
                    onChange={({ detail }) => {
                      const group = detail.selectedOption.value;
                      if (!group) return;
                      const groupSignalIds = new Set(allSignals.filter(s => s.signal_group === group).map(s => String(s.signal_id)));
                      const currentIds = new Set(selectedSignalOptions.map(o => o.value));
                      const alreadyAllSelected = [...groupSignalIds].every(id => currentIds.has(id));
                      if (alreadyAllSelected) {
                        setSelectedSignalOptions(selectedSignalOptions.filter(o => !groupSignalIds.has(o.value)));
                      } else {
                        const newOptions = signalOptions.filter(o => groupSignalIds.has(o.value) && !currentIds.has(o.value));
                        setSelectedSignalOptions([...selectedSignalOptions, ...newOptions]);
                      }
                    }}
                    options={[...new Set(allSignals.map(s => s.signal_group))].sort().map(g => ({ value: g, label: `Toggle: ${g}` }))}
                    placeholder="Toggle group..."
                    filteringType="auto"
                  />
                )}
              </SpaceBetween>
              <Multiselect selectedOptions={selectedSignalOptions}
                onChange={({ detail }) => setSelectedSignalOptions([...detail.selectedOptions])}
                options={signalOptions} filteringType="auto"
                placeholder="Search and select signals" tokenLimit={10} />
            </SpaceBetween>
          </FormField>
          {selectedSignalOptions.length > 0 && (
            <Box>
              <Box variant="awsui-key-label" margin={{ bottom: 'xs' }}>Selected by group</Box>
              <SpaceBetween direction="horizontal" size="xs">
                {Object.entries(
                  selectedSignalOptions.reduce((acc: Record<string, number>, opt) => {
                    const sig = allSignals.find(s => String(s.signal_id) === opt.value);
                    const group = sig?.signal_group || 'other';
                    acc[group] = (acc[group] || 0) + 1;
                    return acc;
                  }, {})
                ).sort(([a], [b]) => a.localeCompare(b)).map(([group, count]) => (
                  <Badge key={group} color="blue">{group}: {count}</Badge>
                ))}
              </SpaceBetween>
            </Box>
          )}
        </SpaceBetween>
      ),
    },
  ];

  if (lockedVehicle) {
    steps.push({
      title: 'Review & create',
      content: (
        <SpaceBetween size="s">
          {error && <Alert type="error">{error}</Alert>}
          {summaryItems}
        </SpaceBetween>
      ),
    });
  } else {
    steps.push({
      title: 'Assign targets',
      isOptional: true,
      content: (
        <SpaceBetween size="s">
          {error && <Alert type="error">{error}</Alert>}
          {summaryItems}
          <Table
            header={<Header counter={`(${selectedVehicles.length} selected)`}
              description="Leave empty to create without vehicle assignment">Vehicles</Header>}
            selectionType="multi"
            selectedItems={selectedVehicles}
            onSelectionChange={({ detail }) => setSelectedVehicles(detail.selectedItems)}
            items={vehicles}
            columnDefinitions={[
              { id: 'vin', header: 'VIN', cell: (v: any) => v.vin || v.vehicleId },
              { id: 'make', header: 'Make/Model', cell: (v: any) => `${v.make || ''} ${v.model || ''}`.trim() || '—' },
              { id: 'fleet', header: 'Fleet', cell: (v: any) => v.fleet_name || v.fleetName || '—' },
            ]}
            empty={<Box textAlign="center">No vehicles found</Box>}
          />
        </SpaceBetween>
      ),
    });
  }

  return (
    <Wizard
      i18nStrings={{
        stepNumberLabel: n => `Step ${n}`,
        collapsedStepsLabel: (step, total) => `Step ${step} of ${total}`,
        submitButton: submitLabel,
        cancelButton: 'Cancel', previousButton: 'Previous', nextButton: 'Next',
      }}
      onCancel={handleDismiss}
      onSubmit={handleSubmit}
      activeStepIndex={activeStep}
      onNavigate={({ detail }) => setActiveStep(detail.requestedStepIndex)}
      isLoadingNextStep={submitting}
      steps={steps}
    />
  );
};

export default CreateCampaignWizard;
