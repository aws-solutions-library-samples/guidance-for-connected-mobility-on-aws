// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from "react";
import {
  Container,
  Header,
  Link,
  SpaceBetween,
  Spinner,
  StatusIndicator,
  Table,
  Tabs,
} from "@cloudscape-design/components";
import WarrantyDashboard from "./WarrantyDashboard";
import { authFetch } from "../../utils/authFetch";

const WarrantyPage: React.FC = () => {
  const [summary, setSummary] = useState<any>(null);
  const [recallClaims, setRecallClaims] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const apiEndpoint = (window as any).runtimeConfig?.apiEndpoint || "";
    authFetch(`${apiEndpoint}/api/v1/warranty-claims`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d) {
          setSummary(d.summary);
          // Claims with a recallId are recall-related
          setRecallClaims((d.claims || []).filter((c: any) => c.recallId));
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner size="large" />;

  const s = summary || { total: 0, paid: 0, submitted: 0, approved: 0, denied: 0, totalRecovered: 0, totalPending: 0 };
  const eligible = s.submitted + s.approved;
  const expiring = 0; // TODO: compute from daysRemaining < 60

  return (
    <SpaceBetween size="l">
      {/* KPI Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px' }}>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Total Claims</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{s.total}</span>
            <StatusIndicator type="info">{s.paid} paid · {s.denied} denied</StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Recovered YTD</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2, color: '#037f0c' }}>${s.totalRecovered.toLocaleString()}</span>
            <StatusIndicator type="success">From paid claims</StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Open Claims</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{eligible}</span>
            <StatusIndicator type="in-progress">{s.submitted} submitted · {s.approved} approved</StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Pending Amount</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2, color: '#8D6605' }}>${s.totalPending.toLocaleString()}</span>
            <StatusIndicator type="warning">Awaiting OEM response</StatusIndicator>
          </SpaceBetween>
        </Container>
      </div>

      {/* Tabs */}
      <Tabs
        tabs={[
          {
            label: "Warranty Claims",
            id: "claims",
            content: <WarrantyDashboard />,
          },
          {
            label: `Recall-Related Claims (${recallClaims.length})`,
            id: "recall-claims",
            content: (
              <Container header={
                <Header variant="h2" counter={`(${recallClaims.length})`}
                  description="Warranty claims filed for repairs performed under NHTSA recalls">
                  Recall-Related Warranty Claims
                </Header>
              }>
                <Table
                  columnDefinitions={[
                    { id: "id", header: "Claim #", cell: (item: any) => <Link>{item.claimId}</Link>, width: 130 },
                    { id: "vehicle", header: "Vehicle", cell: (item: any) => <Link href={`/vehicles/${item.vehicleId}`}>{item.vin}</Link>, width: 180 },
                    { id: "recall", header: "Recall #", cell: (item: any) => (
                      <Link href={`https://www.nhtsa.gov/recalls?nhtsaId=${item.recallId}`} external>{item.recallId}</Link>
                    ), width: 120 },
                    { id: "component", header: "Component", cell: (item: any) => item.component },
                    { id: "oem", header: "OEM", cell: (item: any) => item.make, width: 90 },
                    { id: "amount", header: "Amount", cell: (item: any) => <span style={{ fontWeight: 700 }}>${item.paidAmount || item.claimAmount}</span>, width: 90 },
                    { id: "status", header: "Status", cell: (item: any) => (
                      <StatusIndicator type={
                        item.status === "PAID" ? "success" : item.status === "APPROVED" ? "success" : item.status === "SUBMITTED" ? "in-progress" : "error"
                      }>{item.status}</StatusIndicator>
                    ), width: 100 },
                  ]}
                  items={recallClaims}
                  variant="embedded"
                />
              </Container>
            ),
          },
        ]}
      />
    </SpaceBetween>
  );
};

export default WarrantyPage;
