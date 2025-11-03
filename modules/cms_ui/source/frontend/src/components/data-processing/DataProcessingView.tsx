import React, { useState } from 'react';
import { Container, Tabs } from '@cloudscape-design/components';
import SignalCatalogViewer from './SignalCatalogViewer';
import EventCatalogViewer from './EventCatalogViewer';
import TransformManifestsViewer from './TransformManifestsViewer';
import OEMIntegrationWizard from './OEMIntegrationWizard';

const DataProcessingView: React.FC = () => {
  const [activeTabId, setActiveTabId] = useState('signal-catalog');
  const [showWizard, setShowWizard] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const handleWizardComplete = () => {
    setShowWizard(false);
    setRefreshKey(prev => prev + 1); // Trigger refresh of manifests
    setActiveTabId('transform-manifests'); // Switch to manifests tab
  };

  const tabs = [
    {
      id: 'signal-catalog',
      label: 'Signal Catalog',
      content: <SignalCatalogViewer />
    },
    {
      id: 'event-catalog',
      label: 'Event Catalog',
      content: <EventCatalogViewer />
    },
    {
      id: 'transform-manifests',
      label: 'Transform Manifests',
      content: <TransformManifestsViewer key={refreshKey} onAddIntegration={() => setShowWizard(true)} />
    }
  ];

  return (
    <>
      <Container>
        <Tabs
          activeTabId={activeTabId}
          onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
          tabs={tabs}
        />
      </Container>
      
      <OEMIntegrationWizard
        visible={showWizard}
        onDismiss={() => setShowWizard(false)}
        onComplete={handleWizardComplete}
      />
    </>
  );
};

export default DataProcessingView;
