// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from "react";
import {
  Box,
  Button,
  Container,
  Header,
  Link,
  ProgressBar,
  SpaceBetween,
  StatusIndicator,
  Spinner,
  Table,
} from "@cloudscape-design/components";
import { authFetch } from "../../utils/authFetch";

interface Claim {
  claimId: string;
  vehicleId: string;
  vin: string;
  component: string;
  failureCode: string;
  claimAmount: number;
  paidAmount: number;
  status: string;
  filedDate: string;
  resolvedDate?: string;
  oem: string;
  make: string;
  mileageAtFailure: number;
  warrantyLimit: string;
  daysRemaining: number;
  confidence: number;
  evidenceSummary?: string;
}

interface Summary {
  total: number;
  paid: number;
  submitted: number;
  approved: number;
  denied: number;
  totalRecovered: number;
  totalPending: number;
}

const WarrantyDashboard: React.FC = () => {
  const [claims, setClaims] = useState<Claim[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const apiEndpoint = (window as any).runtimeConfig?.apiEndpoint || "";
    authFetch(`${apiEndpoint}/api/v1/warranty-claims`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d) {
          setClaims(d.claims || []);
          setSummary(d.summary || null);
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner size="large" />;

  const eligible = claims.filter(c => c.daysRemaining > 0 && !['PAID', 'DENIED'].includes(c.status?.toUpperCase()));
  const filed = claims.filter(c => ['SUBMITTED', 'APPROVED', 'PAID', 'DENIED'].includes(c.status?.toUpperCase()));

  return (
    <SpaceBetween size="l">
      {/* Warranty-Eligible Failures */}
      <Container header={
        <Header variant="h2" counter={`(${eligible.length})`}
          description="Agent-detected failures matching warranty coverage rules. Review and approve to file claims."
          actions={<Button variant="primary">File All Drafted Claims</Button>}>
          Warranty-Eligible Failures
        </Header>
      }>
        <Table
          columnDefinitions={[
            { id: "vin", header: "VIN", cell: (item) => <Link href={`/vehicles/${item.vehicleId}`}>{item.vin}</Link>, width: 180 },
            { id: "component", header: "Component", cell: (item) => item.component },
            { id: "failure", header: "Failure", cell: (item) => <code>{item.failureCode}</code>, width: 120 },
            { id: "mileage", header: "Mileage", cell: (item) => item.mileageAtFailure?.toLocaleString(), width: 90 },
            { id: "limit", header: "Warranty Limit", cell: (item) => item.warrantyLimit, width: 110 },
            { id: "expiring", header: "Coverage Left", cell: (item) => (
              <span style={{ color: item.daysRemaining < 60 ? '#d91515' : item.daysRemaining < 120 ? '#8D6605' : undefined }}>
                {item.daysRemaining} days
              </span>
            ), width: 100 },
            { id: "amount", header: "Est. Claim", cell: (item) => <span style={{ fontWeight: 700 }}>${item.claimAmount?.toLocaleString()}</span>, width: 90 },
            { id: "confidence", header: "Confidence", cell: (item) => (
              <ProgressBar value={item.confidence || 0} additionalInfo={`${item.confidence || 0}%`} variant="key-value" />
            ), width: 120 },
            { id: "status", header: "Status", cell: (item) => (
              <StatusIndicator type={item.status === "SUBMITTED" ? "in-progress" : "pending"}>{item.status}</StatusIndicator>
            ), width: 100 },
          ]}
          items={eligible}
          variant="embedded"
          stickyHeader
          empty={<Box textAlign="center" color="inherit">No eligible failures found</Box>}
        />
      </Container>

      {/* Claim Tracking */}
      <Container header={<Header variant="h2" counter={`(${filed.length})`}>Claim Tracking</Header>}>
        <Table
          columnDefinitions={[
            { id: "id", header: "Claim #", cell: (item) => <Link>{item.claimId}</Link> },
            { id: "vin", header: "VIN", cell: (item) => <Link href={`/vehicles/${item.vehicleId}`}>{item.vin}</Link>, width: 180 },
            { id: "component", header: "Component", cell: (item) => item.component },
            { id: "oem", header: "OEM", cell: (item) => item.make || item.oem },
            { id: "amount", header: "Amount", cell: (item) => `$${(item.paidAmount || item.claimAmount)?.toLocaleString()}` },
            { id: "filed", header: "Filed", cell: (item) => item.filedDate },
            { id: "status", header: "Status", cell: (item) => (
              <StatusIndicator type={
                item.status === "PAID" ? "success" : item.status === "APPROVED" ? "success" : item.status === "SUBMITTED" ? "in-progress" : "error"
              }>{item.status}</StatusIndicator>
            )},
            { id: "actions", header: "", cell: (item) => item.status === "DENIED" ? (
              <Button iconName="redo">Escalate</Button>
            ) : null },
          ]}
          items={filed}
          variant="embedded"
          empty={<Box textAlign="center" color="inherit">No claims filed</Box>}
        />
      </Container>
    </SpaceBetween>
  );
};

export default WarrantyDashboard;
