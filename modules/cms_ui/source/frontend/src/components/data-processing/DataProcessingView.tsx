import React, { useEffect, useState } from 'react';
import { Container, Tabs, Modal } from '@cloudscape-design/components';
import { useSearchParams } from 'react-router-dom';
import SignalCatalogViewer from './SignalCatalogViewer';
import VehicleModelsViewer from './VehicleModelsViewer';
import EventCatalogViewer from './EventCatalogViewer';
import DecoderManifestViewer from './DecoderManifestViewer';
import CampaignViewer from './CampaignViewer';
import TransformManifestsViewer from './TransformManifestsViewer';
import OEMIntegrationWizard from './OEMIntegrationWizard';

const VALID_TABS = new Set([
  'signal-catalog',
  'vehicle-models',
  'event-catalog',
  'decoder-manifests',
  'transform-manifests',
  'campaigns',
]);

const DataProcessingView: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = searchParams.get('tab');
  const [activeTabId, setActiveTabId] = useState<string>(
    initialTab && VALID_TABS.has(initialTab) ? initialTab : 'signal-catalog'
  );
  const [showOEMWizard, setShowOEMWizard] = useState(false);

  // External URL changes (e.g., navigated from another component) → sync state.
  useEffect(() => {
    const fromUrl = searchParams.get('tab');
    if (fromUrl && VALID_TABS.has(fromUrl) && fromUrl !== activeTabId) {
      setActiveTabId(fromUrl);
    }
  }, [searchParams, activeTabId]);

  const onTabChange = (newTab: string) => {
    setActiveTabId(newTab);
    // Preserve other query params (?ecu, ?model) when changing tabs.
    const next = new URLSearchParams(searchParams);
    next.set('tab', newTab);
    setSearchParams(next, { replace: true });
  };

  const tabs = [
    { id: 'signal-catalog',      label: 'Signal Catalog',      content: <SignalCatalogViewer /> },
    { id: 'vehicle-models',      label: 'Vehicle Models',      content: <VehicleModelsViewer /> },
    { id: 'event-catalog',       label: 'Event Catalog',       content: <EventCatalogViewer /> },
    { id: 'decoder-manifests',   label: 'Decoder Manifests',   content: <DecoderManifestViewer /> },
    { id: 'transform-manifests', label: 'Transform Manifests', content: (
      <TransformManifestsViewer onAddIntegration={() => setShowOEMWizard(true)} />
    )},
    { id: 'campaigns',           label: 'Campaigns',           content: <CampaignViewer /> },
  ];

  return (
    <Container>
      <Tabs
        activeTabId={activeTabId}
        onChange={({ detail }) => onTabChange(detail.activeTabId)}
        tabs={tabs}
      />
      <Modal
        visible={showOEMWizard}
        onDismiss={() => setShowOEMWizard(false)}
        header="Add OEM Integration"
        size="large"
      >
        <OEMIntegrationWizard
          visible={showOEMWizard}
          onDismiss={() => setShowOEMWizard(false)}
          onComplete={() => {
            setShowOEMWizard(false);
            onTabChange('transform-manifests');
          }}
        />
      </Modal>
    </Container>
  );
};

export default DataProcessingView;
