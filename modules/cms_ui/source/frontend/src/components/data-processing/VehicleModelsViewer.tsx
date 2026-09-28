// Vehicle Models — FleetWise Model Manifest viewer in Data Processing.
// Each card represents a vehicle model: a curated subset of the signal catalog
// that names which signals a given platform emits, paired with a decoder
// manifest version that decodes them.
//
// Backed by the cms-prod-model-manifest DynamoDB table via the
// /api/v1/model-manifests endpoint on cms-prod-data-processing-api.

import React, { useEffect, useState } from 'react';
import {
  Badge,
  Box,
  Cards,
  Header,
  Link,
  SpaceBetween,
  StatusIndicator,
  Spinner,
} from '@cloudscape-design/components';
import { useNavigate } from 'react-router-dom';
import { getDataProcessingApiEndpoint } from '../../config/api';
import { authFetch } from '../../utils/authFetch';

interface ECUEntry {
  ecu: string;
  displayName: string;
  baselineVersion: string;
  signalCount: number;
}

interface ModelManifest {
  modelManifestName: string;
  modelManifestVersion: string;
  displayName?: string;
  modelLine?: string;
  platform?: string;
  status?: 'ACTIVE' | 'DRAFT' | 'DEPRECATED' | string;
  productionPhase?: 'production' | 'validation' | string;
  description?: string;
  decoderManifestRef?: string;
  signalCatalogArn?: string;
  ecuConfigId?: string;
  ecus?: ECUEntry[];
  signalCount?: number;
  vehicleCount?: number;
  fleetIds?: string[];
  isDefault?: boolean;
  createTimestamp?: string;
  updateTimestamp?: string;
}

const API = () => getDataProcessingApiEndpoint().replace(/\/$/, '');

const VehicleModelsViewer: React.FC = () => {
  const navigate = useNavigate();
  const [models, setModels] = useState<ModelManifest[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await authFetch(`${API()}/model-manifests`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        // Default model first, then by name; ACTIVE before DRAFT within those.
        const sorted = (data.modelManifests ?? []).slice().sort((a: ModelManifest, b: ModelManifest) => {
          if (a.isDefault !== b.isDefault) return a.isDefault ? -1 : 1;
          if (a.status !== b.status) return (a.status === 'ACTIVE') ? -1 : 1;
          return (a.modelManifestName || '').localeCompare(b.modelManifestName || '');
        });
        setModels(sorted);
      } catch (e: any) {
        if (!cancelled) setError(e.message ?? 'Error loading vehicle models');
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  if (models === null && !error) {
    return <Box padding="l"><Spinner /> Loading vehicle models…</Box>;
  }
  if (error) {
    return (
      <Box padding="l">
        <StatusIndicator type="error">Couldn't load vehicle models: {error}</StatusIndicator>
      </Box>
    );
  }

  const goSignals = (modelName: string, ecu?: string) => {
    const ecuParam = ecu ? `&ecu=${ecu}` : '';
    navigate(`/data-processing?tab=signal-catalog&model=${modelName}${ecuParam}`);
  };

  const goEvents = (modelName: string) => {
    navigate(`/data-processing?tab=event-catalog&model=${modelName}`);
  };

  return (
    <Cards
      header={
        <Header
          variant="h2"
          counter={`(${(models ?? []).length})`}
          description="A vehicle model defines which signals a given vehicle platform emits, paired with a decoder manifest that translates CAN/Ethernet frames into those signals. Vehicles in the fleet reference one of these models. Click an ECU badge to see the signals that ECU produces for this model."
        >
          Vehicle Models
        </Header>
      }
      items={models ?? []}
      cardsPerRow={[{ cards: 1 }, { minWidth: 1100, cards: 2 }]}
      cardDefinition={{
        header: (m: ModelManifest) => (
          <SpaceBetween direction="horizontal" size="xs" alignItems="center">
            <span style={{ fontFamily: 'monospace', fontSize: 16, fontWeight: 700 }}>
              {m.modelManifestName}
            </span>
            <Badge color={m.status === 'ACTIVE' ? 'green' : m.status === 'DRAFT' ? 'blue' : 'grey'}>
              {m.status ?? '—'}
            </Badge>
            {m.isDefault && <Badge color="blue">Default</Badge>}
            {!m.isDefault && (
              <Badge color={m.productionPhase === 'production' ? 'red' : 'green'}>
                {m.productionPhase === 'production' ? 'Production' : 'Validation'}
              </Badge>
            )}
          </SpaceBetween>
        ),
        sections: [
          {
            id: 'summary',
            content: (m: ModelManifest) => (
              <SpaceBetween size="s">
                <Box>
                  <Box variant="h3">{m.displayName ?? m.modelManifestName}</Box>
                  <Box variant="small" color="text-body-secondary">
                    {m.modelLine ?? '—'} · {m.platform ?? '—'} · v{m.modelManifestVersion} · paired with{' '}
                    <span style={{ fontFamily: 'monospace' }}>{m.decoderManifestRef ?? '—'}</span>
                  </Box>
                </Box>
                <Box variant="p">{m.description ?? '—'}</Box>
              </SpaceBetween>
            ),
          },
          {
            id: 'scope',
            content: (m: ModelManifest) => (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
                <ScopeStat label="Signals in model" value={String(m.signalCount ?? 0)} />
                <ScopeStat label="Domain controllers" value={String(m.ecus?.length ?? 0)} />
                <ScopeStat
                  label="Vehicles"
                  value={String(m.vehicleCount ?? 0)}
                  sub={(m.fleetIds ?? []).length > 0 ? `across ${(m.fleetIds ?? []).length} fleet${(m.fleetIds ?? []).length === 1 ? '' : 's'}` : undefined}
                />
              </div>
            ),
          },
          {
            id: 'ecus',
            header: 'Domain controllers — click to filter signal catalog',
            content: (m: ModelManifest) => (
              <SpaceBetween size="xxs">
                {(m.ecus ?? []).map((entry) => (
                  <div
                    key={entry.ecu}
                    onClick={() => goSignals(m.modelManifestName, entry.ecu)}
                    style={{
                      cursor: 'pointer',
                      padding: '6px 8px',
                      borderRadius: 4,
                      transition: 'background 0.1s',
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = '#f4f6fa'; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
                  >
                    <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                      <Badge color="blue">{entry.ecu}</Badge>
                      <Box variant="small" fontWeight="bold">{entry.displayName}</Box>
                      <Box variant="small" color="text-body-secondary">
                        v{entry.baselineVersion}
                      </Box>
                      <Box variant="small" color="text-body-secondary">
                        · {entry.signalCount} signals →
                      </Box>
                    </SpaceBetween>
                  </div>
                ))}
              </SpaceBetween>
            ),
          },
          {
            id: 'actions',
            content: (m: ModelManifest) => (
              <SpaceBetween direction="horizontal" size="m" alignItems="center">
                <Link
                  onFollow={(e) => { e.preventDefault(); goSignals(m.modelManifestName); }}
                  href="#"
                >
                  View all {m.signalCount ?? 0} signals →
                </Link>
                <Link
                  onFollow={(e) => { e.preventDefault(); goEvents(m.modelManifestName); }}
                  href="#"
                >
                  View applicable events →
                </Link>
                <Box variant="small" color="text-body-secondary">
                  {m.updateTimestamp ? `Updated ${new Date(m.updateTimestamp).toLocaleDateString()}` : ''}
                </Box>
                <StatusIndicator type={m.status === 'ACTIVE' ? 'success' : 'in-progress'}>
                  {m.isDefault
                    ? 'Default for new vehicles'
                    : m.status === 'ACTIVE'
                      ? 'Deployed to fleet'
                      : 'Engineering / draft'}
                </StatusIndicator>
              </SpaceBetween>
            ),
          },
        ],
      }}
    />
  );
};

// Inline KPI stat for the Scope section.
function ScopeStat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <Box variant="awsui-key-label">{label}</Box>
      <Box fontSize="display-l" fontWeight="bold">{value}</Box>
      {sub && <Box variant="small" color="text-body-secondary">{sub}</Box>}
    </div>
  );
}

export default VehicleModelsViewer;
