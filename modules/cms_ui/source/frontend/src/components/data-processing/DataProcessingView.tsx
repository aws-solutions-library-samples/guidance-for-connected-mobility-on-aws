import React, { useState } from 'react';
import { Container, Tabs } from '@cloudscape-design/components';
import SignalCatalogViewer from './SignalCatalogViewer';
import EventCatalogViewer from './EventCatalogViewer';
import DecoderManifestViewer from './DecoderManifestViewer';
import CampaignViewer from './CampaignViewer';

const DataProcessingView: React.FC = () => {
  const [activeTabId, setActiveTabId] = useState('signal-catalog');

  const tabs = [
    { id: 'signal-catalog', label: 'Signal Catalog', content: <SignalCatalogViewer /> },
    { id: 'event-catalog', label: 'Event Catalog', content: <EventCatalogViewer /> },
    { id: 'decoder-manifests', label: 'Decoder Manifests', content: <DecoderManifestViewer /> },
    { id: 'campaigns', label: 'Campaigns', content: <CampaignViewer /> },
  ];

  return (
    <Container>
      <Tabs activeTabId={activeTabId}
        onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
        tabs={tabs} />
    </Container>
  );
};

export default DataProcessingView;
