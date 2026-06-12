// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useState } from "react";
import { Box, Button, Header, Select, SpaceBetween, Spinner, StatusIndicator, Table } from "@cloudscape-design/components";
import { authFetch } from "../../utils/authFetch";
import { getApiEndpoint } from "../../config/api";
import { DocumentViewer } from "./DocumentViewer";

interface DocItem {
  key: string;
  size: number;
  lastModified: string;
}

const PREFIX_OPTIONS = [
  { label: "Service Invoices", value: "service-invoices/" },
  { label: "Warranty Claims", value: "warranty-claims/" },
  { label: "Fleet Context", value: "fleet-context/" },
  { label: "Fleet Operations", value: "fleet-operations/" },
];

export const DocumentBrowser: React.FC = () => {
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedPrefix, setSelectedPrefix] = useState(PREFIX_OPTIONS[0]);
  const [viewerKey, setViewerKey] = useState("");
  const [viewerVisible, setViewerVisible] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError("");
    authFetch(`${getApiEndpoint()}/api/v1/documents?prefix=${encodeURIComponent(selectedPrefix.value)}`)
      .then((res) => res.json())
      .then((data) => {
        if (data.error) throw new Error(data.error);
        setDocs(data.documents || []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [selectedPrefix]);

  return (
    <SpaceBetween size="m">
      <Table
        header={
          <Header
            counter={`(${docs.length})`}
            actions={
              <Select
                selectedOption={selectedPrefix}
                onChange={({ detail }) => setSelectedPrefix(detail.selectedOption as typeof selectedPrefix)}
                options={PREFIX_OPTIONS}
              />
            }
          >
            Knowledge Base Documents
          </Header>
        }
        loading={loading}
        loadingText="Loading documents..."
        empty={error ? <StatusIndicator type="error">{error}</StatusIndicator> : <Box textAlign="center">No documents found</Box>}
        items={docs}
        columnDefinitions={[
          {
            id: "name",
            header: "Document Name",
            cell: (item) => item.key.split("/").pop(),
            sortingField: "key",
          },
          {
            id: "type",
            header: "Type",
            cell: (item) => item.key.split("/")[0] || "-",
          },
          {
            id: "size",
            header: "Size",
            cell: (item) => `${(item.size / 1024).toFixed(1)} KB`,
          },
          {
            id: "lastModified",
            header: "Last Modified",
            cell: (item) => new Date(item.lastModified).toLocaleDateString(),
          },
          {
            id: "actions",
            header: "Actions",
            cell: (item) => (
              <Button variant="inline-link" onClick={() => { setViewerKey(item.key); setViewerVisible(true); }}>
                View
              </Button>
            ),
          },
        ]}
      />
      <DocumentViewer documentKey={viewerKey} visible={viewerVisible} onDismiss={() => setViewerVisible(false)} />
    </SpaceBetween>
  );
};
