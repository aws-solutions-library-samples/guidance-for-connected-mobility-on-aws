// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// CatalogListPage — the fleet operator's registered data products catalog.
//
// CMS is a third-party fleet platform. It has no notion of an upstream CS
// portal — the fleet operator defines every data product themselves here:
// producer identity, connection endpoint, authentication, tier offering,
// and the credentials pointer. Subscriptions consume this catalog.
//
// UI is a stub with mock + session-added catalog entries via the Add
// Data Product wizard. No backend.

import React, { useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Box,
  Button,
  Container,
  Header,
  SpaceBetween,
  StatusIndicator,
  Table,
} from '@cloudscape-design/components';
import { useNavigate } from 'react-router-dom';
import { getAllCatalog, getSupportedTiers, getTotalSignalCount, DataProduct, CatalogStatus } from './mockData';
import AddDataProductWizard from './AddDataProductWizard';

function statusIndicator(status: CatalogStatus) {
  switch (status) {
    case 'Active':
      return <StatusIndicator type="success">Active</StatusIndicator>;
    case 'Draft':
      return <StatusIndicator type="pending">Draft</StatusIndicator>;
    case 'Deprecated':
      return <StatusIndicator type="stopped">Deprecated</StatusIndicator>;
  }
}

const CatalogListPage: React.FC = () => {
  const navigate = useNavigate();
  const [wizardVisible, setWizardVisible] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const [justAddedName, setJustAddedName] = useState<string | null>(null);

  const catalog = useMemo(
    () => getAllCatalog(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [refreshTick],
  );

  const kpis = useMemo(() => {
    const active = catalog.filter((p) => p.status === 'Active');
    const producers = new Set(catalog.map((p) => p.producer));
    const totalSignals = catalog.reduce((s, p) => s + getTotalSignalCount(p), 0);
    return {
      total: catalog.length,
      active: active.length,
      producers: producers.size,
      totalSignals,
    };
  }, [catalog]);

  return (
    <SpaceBetween size="l">
      {justAddedName && (
        <Alert
          type="success"
          dismissible
          onDismiss={() => setJustAddedName(null)}
          header="Data product added"
        >
          "{justAddedName}" is now in the catalog. Subscribe to it from the Subscriptions page.
        </Alert>
      )}

      {/* KPI Cards — same shape as SubscriptionsListPage */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px' }}>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>
              Catalog entries
            </span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>
              {kpis.total}
            </span>
            <Box color="text-body-secondary" fontSize="body-s">
              {kpis.active} active
            </Box>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>
              Producers
            </span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>
              {kpis.producers}
            </span>
            <Box color="text-body-secondary" fontSize="body-s">
              unique OEMs
            </Box>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>
              Signals available
            </span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>
              {kpis.totalSignals}
            </span>
            <Box color="text-body-secondary" fontSize="body-s">
              across all products
            </Box>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>
              Ready to consume
            </span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>
              {kpis.active}
            </span>
            <StatusIndicator type="success">
              subscribable
            </StatusIndicator>
          </SpaceBetween>
        </Container>
      </div>

      <Table
        items={catalog}
        header={
          <Header
            counter={`(${catalog.length})`}
            description="Data products this fleet operator has defined. Subscribe to any Active product from the Subscriptions page."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={() => setRefreshTick((n) => n + 1)}>
                  Refresh
                </Button>
                <Button variant="primary" onClick={() => setWizardVisible(true)}>
                  Add data product
                </Button>
              </SpaceBetween>
            }
          >
            Catalog
          </Header>
        }
        columnDefinitions={[
          {
            id: 'productName',
            header: 'Product',
            isRowHeader: true,
            cell: (p: DataProduct) => (
              <Button
                variant="inline-link"
                onClick={() => navigate(`/data-products/catalog/${p.productId}`)}
              >
                {p.productName}
              </Button>
            ),
            sortingField: 'productName',
          },
          {
            id: 'producer',
            header: 'Producer',
            cell: (p: DataProduct) => p.producer,
            sortingField: 'producer',
          },
          {
            id: 'connectionType',
            header: 'Connection',
            cell: (p: DataProduct) => <Badge color="green">{p.connectionType}</Badge>,
          },
          {
            id: 'authType',
            header: 'Auth',
            cell: (p: DataProduct) => <Badge color="grey">{p.authType}</Badge>,
          },
          {
            id: 'tiers',
            header: 'Tiers',
            cell: (p: DataProduct) => (
              <SpaceBetween direction="horizontal" size="xxs">
                {getSupportedTiers(p).map((t) => (
                  <Badge key={t} color={t === 'Premium' ? 'blue' : t === 'Standard' ? 'green' : 'grey'}>
                    {t}
                  </Badge>
                ))}
              </SpaceBetween>
            ),
          },
          {
            id: 'signals',
            header: 'Signals',
            cell: (p: DataProduct) => (
              <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}>
                {getTotalSignalCount(p)}
              </span>
            ),
          },
          {
            id: 'status',
            header: 'Status',
            cell: (p: DataProduct) => statusIndicator(p.status),
          },
          {
            id: 'actions',
            header: 'Actions',
            cell: (p: DataProduct) => (
              <Button
                variant="inline-link"
                onClick={() => navigate(`/data-products/catalog/${p.productId}`)}
              >
                View details
              </Button>
            ),
          },
        ]}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No data products defined</b>
            <Box padding={{ bottom: 's' }} variant="p" color="inherit">
              Add a data product to define which OEMs this fleet can consume telemetry from.
            </Box>
            <Button variant="primary" onClick={() => setWizardVisible(true)}>
              Add your first data product
            </Button>
          </Box>
        }
      />

      <AddDataProductWizard
        visible={wizardVisible}
        onDismiss={() => setWizardVisible(false)}
        onAdded={(name: string) => {
          setWizardVisible(false);
          setRefreshTick((n) => n + 1);
          setJustAddedName(name);
        }}
      />
    </SpaceBetween>
  );
};

export default CatalogListPage;
