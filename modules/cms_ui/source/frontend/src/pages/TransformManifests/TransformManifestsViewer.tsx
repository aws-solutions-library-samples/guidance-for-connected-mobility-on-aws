// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from 'react';
import {
  Table, Box, SpaceBetween, Header, Button, Modal, StatusIndicator,
  Container, ExpandableSection, Badge,
} from '@cloudscape-design/components';
import { getDataProcessingApiEndpoint } from '@/config/api';
import { authFetch } from '@/utils/authFetch';
import signalCatalogData from '../../../../../../../services/data_processing/signal-catalog.json';

interface Manifest {
  name: string;
  source_type: string;
  last_modified: string;
  size: number;
}

export interface SignalMapping {
  cms_field: string;
  [key: string]: unknown;
}

export interface DeferredSignal {
  source_signal: string;
  reason: string;
}

export interface ManifestContent {
  signal_mappings?: SignalMapping[];
  metadata?: {
    deferred_signals?: DeferredSignal[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface CoverageResult {
  covered: number;
  total: number;
  gaps: string[];
  deferred: string[];
}

interface CatalogShape {
  signal_groups: Record<string, { signals: Record<string, unknown> }>;
}

/** Extract all signal names from a catalog object. */
export function extractCatalogSignals(catalog: CatalogShape): string[] {
  const signals: string[] = [];
  for (const group of Object.values(catalog.signal_groups)) {
    for (const name of Object.keys(group.signals)) {
      signals.push(name);
    }
  }
  return signals;
}

/** Compute signal coverage for any manifest against any catalog. OEM-agnostic. */
export function computeCoverage(manifest: ManifestContent, catalog: CatalogShape): CoverageResult {
  const catalogSignals = extractCatalogSignals(catalog);
  const total = catalogSignals.length;
  const covered = manifest.signal_mappings?.length ?? 0;

  const mappedFields = new Set((manifest.signal_mappings ?? []).map((m) => m.cms_field));
  const gaps = catalogSignals.filter((s) => !mappedFields.has(s));
  const deferred = (manifest.metadata?.deferred_signals ?? []).map((d) => d.source_signal);

  return { covered, total, gaps, deferred };
}

interface TransformManifestsViewerProps {
  onAddIntegration: () => void;
}

const TransformManifestsViewer: React.FC<TransformManifestsViewerProps> = ({ onAddIntegration }) => {
  const [manifests, setManifests] = useState<Manifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedManifest, setSelectedManifest] = useState<Manifest | null>(null);
  const [manifestContent, setManifestContent] = useState<ManifestContent | null>(null);
  const [showDetails, setShowDetails] = useState(false);

  const loadManifests = () => {
    setLoading(true);
    authFetch(`${getDataProcessingApiEndpoint()}manifests`)
      .then((res) => res.json())
      .then((data) => {
        setManifests(data.manifests || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    loadManifests();
  }, []);

  const viewDetails = (manifest: Manifest) => {
    setSelectedManifest(manifest);
    setManifestContent(null);
    setShowDetails(true);
    authFetch(`${getDataProcessingApiEndpoint()}manifests?name=${manifest.name}`)
      .then((res) => res.json())
      .then((data) => setManifestContent(data))
      .catch(() => setManifestContent(null));
  };

  const coverage = manifestContent
    ? computeCoverage(manifestContent, signalCatalogData as CatalogShape)
    : null;

  return (
    <>
      <Table
        loading={loading}
        header={
          <Header
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadManifests}>Refresh</Button>
                <Button variant="primary" onClick={onAddIntegration}>Add OEM Integration</Button>
              </SpaceBetween>
            }
          >
            Transform Manifests
          </Header>
        }
        columnDefinitions={[
          { id: 'name', header: 'Manifest Name', cell: (item: Manifest) => item.name },
          {
            id: 'source_type',
            header: 'Source Type',
            cell: () => <StatusIndicator type="success">cloud_to_cloud</StatusIndicator>,
          },
          {
            id: 'last_modified',
            header: 'Last Modified',
            cell: (item: Manifest) => new Date(item.last_modified).toLocaleString(),
          },
          {
            id: 'actions',
            header: 'Actions',
            cell: (item: Manifest) => (
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => viewDetails(item)}>View Details</Button>
              </SpaceBetween>
            ),
          },
        ]}
        items={manifests}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No transform manifests</b>
            <Box padding={{ bottom: 's' }} variant="p" color="inherit">
              Add an OEM integration to create your first transform manifest.
            </Box>
          </Box>
        }
      />

      <Modal
        visible={showDetails}
        onDismiss={() => setShowDetails(false)}
        header={selectedManifest?.name}
        size="large"
      >
        <SpaceBetween size="m">
          {manifestContent ? (
            <>
              {/* Signal Coverage Section */}
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      coverage && (
                        <Badge color={coverage.covered / coverage.total >= 0.5 ? 'green' : 'blue'}>
                          {coverage.covered} / {coverage.total} signals
                        </Badge>
                      )
                    }
                  >
                    Signal Coverage
                  </Header>
                }
              >
                {coverage ? (
                  <SpaceBetween size="s">
                    <Box data-testid="coverage-summary">
                      <strong>Catalog coverage:</strong>{' '}
                      {coverage.covered} of {coverage.total} catalog signals mapped
                      {' '}({Math.round((coverage.covered / coverage.total) * 100)}%)
                    </Box>

                    {coverage.gaps.length > 0 && (
                      <ExpandableSection
                        headerText={`Catalog gaps — ${coverage.gaps.length} catalog signal(s) not mapped`}
                        data-testid="gaps-section"
                      >
                        <Box variant="code" data-testid="gaps-list">
                          {coverage.gaps.join(', ')}
                        </Box>
                      </ExpandableSection>
                    )}

                    {coverage.deferred.length > 0 && (
                      <ExpandableSection
                        headerText={`Source signals deferred — ${coverage.deferred.length} source signal(s) with no catalog match`}
                        data-testid="deferred-section"
                      >
                        <Box variant="code" data-testid="deferred-list">
                          {coverage.deferred.join(', ')}
                        </Box>
                      </ExpandableSection>
                    )}
                  </SpaceBetween>
                ) : (
                  <StatusIndicator type="loading">Computing coverage...</StatusIndicator>
                )}
              </Container>

              {/* Raw manifest JSON */}
              <pre style={{ fontSize: '12px', overflow: 'auto', maxHeight: '400px' }}>
                {JSON.stringify(manifestContent, null, 2)}
              </pre>
            </>
          ) : (
            <StatusIndicator type="loading">Loading manifest...</StatusIndicator>
          )}
        </SpaceBetween>
      </Modal>
    </>
  );
};

export default TransformManifestsViewer;
