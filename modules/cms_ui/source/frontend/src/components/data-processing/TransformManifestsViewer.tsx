import React, { useState, useEffect } from 'react';
import { Table, Box, SpaceBetween, Header, Button, Modal, StatusIndicator } from '@cloudscape-design/components';
import { getDataProcessingApiEndpoint } from '../../config/api';
import { authFetch } from '../../utils/authFetch';

interface Manifest {
  name: string;
  source_type: string;
  last_modified: string;
  size: number;
}

interface TransformManifestsViewerProps {
  onAddIntegration: () => void;
}

const TransformManifestsViewer: React.FC<TransformManifestsViewerProps> = ({ onAddIntegration }) => {
  const [manifests, setManifests] = useState<Manifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedManifest, setSelectedManifest] = useState<Manifest | null>(null);
  const [manifestContent, setManifestContent] = useState<any>(null);
  const [showDetails, setShowDetails] = useState(false);

  const loadManifests = () => {
    setLoading(true);
    authFetch(`${getDataProcessingApiEndpoint()}manifests`)
      .then(res => res.json())
      .then(data => {
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
    setShowDetails(true);
    // Fetch manifest content
    authFetch(`${getDataProcessingApiEndpoint()}manifests?name=${manifest.name}`)
      .then(res => res.json())
      .then(data => setManifestContent(data))
      .catch(() => setManifestContent(null));
  };

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
            cell: () => <StatusIndicator type="success">cloud_to_cloud</StatusIndicator>
          },
          { 
            id: 'last_modified', 
            header: 'Last Modified', 
            cell: (item: Manifest) => new Date(item.last_modified).toLocaleString() 
          },
          {
            id: 'actions',
            header: 'Actions',
            cell: (item: Manifest) => (
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => viewDetails(item)}>View Details</Button>
              </SpaceBetween>
            )
          }
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
        <Box>
          {manifestContent ? (
            <pre style={{ fontSize: '12px', overflow: 'auto', maxHeight: '500px' }}>
              {JSON.stringify(manifestContent, null, 2)}
            </pre>
          ) : (
            <StatusIndicator type="loading">Loading manifest...</StatusIndicator>
          )}
        </Box>
      </Modal>
    </>
  );
};

export default TransformManifestsViewer;
