// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useState } from "react";
import { Box, Modal, Spinner, StatusIndicator } from "@cloudscape-design/components";
import { authFetch } from "../../utils/authFetch";
import { getApiEndpoint } from "../../config/api";

interface DocumentViewerProps {
  documentKey: string;
  visible: boolean;
  onDismiss: () => void;
}

export const DocumentViewer: React.FC<DocumentViewerProps> = ({ documentKey, visible, onDismiss }) => {
  const [content, setContent] = useState("");
  const [pdfUrl, setPdfUrl] = useState("");
  const [docType, setDocType] = useState<"pdf" | "text">("text");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!visible || !documentKey) return;
    setLoading(true);
    setError("");
    setContent("");
    setPdfUrl("");
    authFetch(`${getApiEndpoint()}/api/v1/documents?key=${encodeURIComponent(documentKey)}`)
      .then((res) => res.json())
      .then((data) => {
        if (data.error) throw new Error(data.error);
        if (data.type === "pdf" && data.url) {
          setDocType("pdf");
          setPdfUrl(data.url);
        } else {
          setDocType("text");
          setContent(data.content || "");
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [visible, documentKey]);

  const filename = documentKey.split("/").pop() || documentKey;

  return (
    <Modal visible={visible} onDismiss={onDismiss} size="max" header={filename}>
      {loading ? (
        <Box textAlign="center" padding="xl"><Spinner size="large" /></Box>
      ) : error ? (
        <StatusIndicator type="error">{error}</StatusIndicator>
      ) : docType === "pdf" ? (
        <iframe src={pdfUrl} style={{ width: "100%", height: "80vh", border: "none" }} title={filename} />
      ) : (
        <Box variant="code">
          <pre style={{ whiteSpace: "pre-wrap", fontFamily: "monospace", margin: 0 }}>{content}</pre>
        </Box>
      )}
    </Modal>
  );
};
