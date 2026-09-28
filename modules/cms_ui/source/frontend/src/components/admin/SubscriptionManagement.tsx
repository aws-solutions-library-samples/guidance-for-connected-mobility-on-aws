import React, { useState, useEffect, useCallback } from 'react';
import {
  Table, Button, SpaceBetween, Header, Modal, FormField, Select, Box,
  StatusIndicator, Badge, Alert, SelectProps
} from '@cloudscape-design/components';
import { useAuth } from '../../auth/useAuth';

interface Subscription {
  vehicleId: string;
  fleetId: string;
  tier: string;
  tierName: string;
  status: string;
  subscribedAt: string;
  subscribedBy: string;
}

const TIER_OPTIONS: SelectProps.Option[] = [
  { label: 'Basic — Location, speed, odometer, ignition', value: 'basic' },
  { label: 'Standard — Basic + safety & vehicle health', value: 'standard' },
  { label: 'Premium — All available signals', value: 'premium' },
];

export default function SubscriptionManagement() {
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [vehicles, setVehicles] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showEnroll, setShowEnroll] = useState(false);
  const [alert, setAlert] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);
  const [form, setForm] = useState({ vehicleId: '', fleetId: '', tier: 'standard' });

  const apiBase = (window as any).runtimeConfig?.apiEndpoint || '';
  const { getIdToken } = useAuth();
  const hdrs = () => {
    const token = getIdToken();
    return { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  };

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [subResp, vehResp] = await Promise.all([
        fetch(`${apiBase}api/v1/subscriptions`, { headers: hdrs() }),
        fetch(`${apiBase}api/v1/vehicles`, { headers: hdrs() }),
      ]);
      const subData = await subResp.json();
      const vehData = await vehResp.json();
      setSubscriptions(subData.subscriptions || []);
      setVehicles(vehData.vehicles || vehData.items || []);
    } catch (e) { console.error(e); }
    setLoading(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const enrollVehicle = async () => {
    try {
      const resp = await fetch(`${apiBase}api/v1/subscriptions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      const data = await resp.json();
      if (resp.ok) {
        setAlert({ type: 'success', msg: `${form.vehicleId} enrolled in ${form.tier} plan` });
        setShowEnroll(false);
        fetchData();
      } else { setAlert({ type: 'error', msg: data.error }); }
    } catch (e: any) { setAlert({ type: 'error', msg: e.message }); }
  };

  const cancelSubscription = async (vehicleId: string) => {
    if (!confirm(`Cancel subscription for ${vehicleId}?`)) return;
    try {
      await fetch(`${apiBase}api/v1/subscriptions/${encodeURIComponent(vehicleId)}`, { method: 'DELETE', headers: hdrs() });
      setAlert({ type: 'success', msg: `Subscription cancelled for ${vehicleId}` });
      fetchData();
    } catch (e: any) { setAlert({ type: 'error', msg: e.message }); }
  };

  const changeTier = async (vehicleId: string, fleetId: string, newTier: string) => {
    try {
      await fetch(`${apiBase}api/v1/subscriptions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vehicleId, fleetId, tier: newTier }),
      });
      setAlert({ type: 'success', msg: `${vehicleId} changed to ${newTier}` });
      fetchData();
    } catch (e: any) { setAlert({ type: 'error', msg: e.message }); }
  };

  // Vehicles not yet subscribed
  const subscribedIds = new Set(subscriptions.filter(s => s.status === 'active').map(s => s.vehicleId));
  const unsubscribedVehicles = vehicles.filter(v => !subscribedIds.has(v.vehicleId));
  const vehicleOptions = unsubscribedVehicles.map(v => ({
    label: `${v.vehicleId} — ${v.make || ''} ${v.model || ''} ${v.vin ? `(${v.vin})` : ''}`.trim(),
    value: v.vehicleId,
    description: v.fleetId || 'No fleet',
  }));

  return (
    <SpaceBetween size="l">
      {alert && <Alert type={alert.type} dismissible onDismiss={() => setAlert(null)}>{alert.msg}</Alert>}

      <div style={{ overflow: 'visible' }}>
      <Table
        header={
          <Header variant="h2" counter={`(${subscriptions.filter(s => s.status === 'active').length} active)`}
            description="Manage vehicle telemetry subscriptions. Vehicles must be enrolled in a plan to receive telemetry data."
            actions={<Button variant="primary" onClick={() => setShowEnroll(true)} disabled={unsubscribedVehicles.length === 0}>Enroll Vehicle</Button>}>
            Telemetry Subscriptions
          </Header>
        }
        loading={loading}
        items={subscriptions}
        columnDefinitions={[
          { id: 'vehicleId', header: 'Vehicle', cell: (s) => s.vehicleId, sortingField: 'vehicleId' },
          { id: 'fleetId', header: 'Fleet', cell: (s) => s.fleetId },
          { id: 'tier', header: 'Plan', cell: (s) => (
            <Select
              selectedOption={TIER_OPTIONS.find(o => o.value === s.tier) || TIER_OPTIONS[1]}
              options={TIER_OPTIONS}
              onChange={({ detail }) => {
                if (detail.selectedOption.value !== s.tier) {
                  changeTier(s.vehicleId, s.fleetId, detail.selectedOption.value!);
                }
              }}
            />
          )},
          { id: 'status', header: 'Status', cell: (s) => (
            <StatusIndicator type={s.status === 'active' ? 'success' : 'stopped'}>
              {s.status}
            </StatusIndicator>
          )},
          { id: 'subscribedAt', header: 'Enrolled', cell: (s) => s.subscribedAt ? new Date(s.subscribedAt).toLocaleDateString() : '—' },
          { id: 'actions', header: 'Actions', cell: (s) => (
            s.status === 'active' ? (
              <Button variant="link" onClick={() => cancelSubscription(s.vehicleId)}>Cancel</Button>
            ) : (
              <Badge color="grey">Cancelled</Badge>
            )
          )},
        ]}
        empty={<Box textAlign="center">No subscriptions. Enroll a vehicle to start receiving telemetry.</Box>}
      />
      </div>

      {/* Enroll Vehicle Modal */}
      <Modal visible={showEnroll} onDismiss={() => setShowEnroll(false)} header="Enroll Vehicle in Telemetry Plan" size="medium"
        footer={<SpaceBetween direction="horizontal" size="xs">
          <Button onClick={() => setShowEnroll(false)}>Cancel</Button>
          <Button variant="primary" onClick={enrollVehicle} disabled={!form.vehicleId}>Enroll</Button>
        </SpaceBetween>}>
        <SpaceBetween size="m">
          <FormField label="Vehicle" description="Only vehicles not already subscribed are shown">
            <Select
              selectedOption={vehicleOptions.find(o => o.value === form.vehicleId) || null}
              options={vehicleOptions}
              placeholder="Select a vehicle"
              onChange={({ detail }) => {
                const v = vehicles.find(v => v.vehicleId === detail.selectedOption.value);
                setForm({ ...form, vehicleId: detail.selectedOption.value || '', fleetId: v?.fleetId || '' });
              }}
              filteringType="auto"
            />
          </FormField>
          <FormField label="Telemetry Plan">
            <Select
              selectedOption={TIER_OPTIONS.find(o => o.value === form.tier) || TIER_OPTIONS[1]}
              options={TIER_OPTIONS}
              onChange={({ detail }) => setForm({ ...form, tier: detail.selectedOption.value || 'standard' })}
            />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
}
