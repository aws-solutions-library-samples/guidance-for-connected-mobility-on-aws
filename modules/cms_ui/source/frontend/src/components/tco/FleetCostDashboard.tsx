// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useState } from "react";
import {
  Box,
  Button,
  ColumnLayout,
  Container,
  Header,
  Link,
  SpaceBetween,
  StatusIndicator,
  Table,
} from "@cloudscape-design/components";
import MixedLineBarChart from "@cloudscape-design/components/mixed-line-bar-chart";
import PieChart from "@cloudscape-design/components/pie-chart";
import BarChart from "@cloudscape-design/components/bar-chart";
import { getApiEndpoint } from "../../config/api";
import { authFetch } from "../../utils/authFetch";

const chartI18n = {
  filterLabel: "Filter",
  filterPlaceholder: "Filter data",
  filterSelectedAriaLabel: "selected",
  legendAriaLabel: "Legend",
  chartAriaRoleDescription: "chart",
  xAxisAriaRoleDescription: "x axis",
  yAxisAriaRoleDescription: "y axis",
};

const emptyBox = (
  <Box textAlign="center" color="inherit">
    <b>No data available</b>
  </Box>
);

const fmtMoney = (n: number | undefined) => {
  if (n === undefined || n === null) return "—";
  if (n >= 1000) return `$${(n / 1000).toFixed(1)}K`;
  return `$${n.toFixed(2)}`;
};

const FleetCostDashboard: React.FC = () => {
  const [summary, setSummary] = useState<any>(null);
  const [breakdown, setBreakdown] = useState<any>(null);
  const [trend, setTrend] = useState<any[]>([]);
  const [outliers, setOutliers] = useState<any[]>([]);

  useEffect(() => {
    const base = getApiEndpoint().replace(/\/$/, '');
    authFetch(`${base}/api/v1/tco/summary`).then(r => r.json()).then(setSummary).catch(() => {});
    authFetch(`${base}/api/v1/tco/breakdown`).then(r => r.json()).then(setBreakdown).catch(() => {});
    authFetch(`${base}/api/v1/tco/trend`).then(r => r.json()).then(d => setTrend(d.trend || [])).catch(() => {});
    authFetch(`${base}/api/v1/tco/outliers`).then(r => r.json()).then(d => setOutliers(d.outliers || [])).catch(() => {});
  }, []);

  const breakdownData = breakdown?.breakdown
    ? Object.entries(breakdown.breakdown)
        .filter(([, v]) => (v as number) > 0)
        .map(([title, value]) => ({ title: title.charAt(0).toUpperCase() + title.slice(1), value: value as number }))
    : [];

  return (
    <SpaceBetween size="l">
      {/* KPI Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '20px' }}>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Total Fleet Cost MTD</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{summary?.totalCostMTD != null ? fmtMoney(summary.totalCostMTD) : '—'}</span>
            <Box color="text-body-secondary" fontSize="body-s">{summary?.yearMonth || 'No data available'}</Box>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Avg Cost/Mile</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{summary?.avgCostPerMile ? `$${summary.avgCostPerMile.toFixed(3)}` : '—'}</span>
            <Box color="text-body-secondary" fontSize="body-s">{summary?.vehicleCount ? `${summary.vehicleCount} vehicles` : 'No data available'}</Box>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Avg Cost/Vehicle</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{summary?.avgCostPerVehicle ? fmtMoney(summary.avgCostPerVehicle) : '—'}</span>
            <Box color="text-body-secondary" fontSize="body-s">{summary?.yearMonth ? 'Month-to-date' : 'No data available'}</Box>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Maintenance Ratio</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{summary?.maintenanceRatio ? `${summary.maintenanceRatio}%` : '—'}</span>
            <Box color="text-body-secondary" fontSize="body-s">{summary?.yearMonth ? 'Of total MTD' : 'No data available'}</Box>
          </SpaceBetween>
        </Container>
      </div>

      {/* === DEPLOY CHECK: If you see this, the new version is live === */}

      {/* Row 3: Cost Trend + Breakdown */}
      <ColumnLayout columns={2}>
        <Container
          header={
            <Header variant="h2">Cost Trend (6 Months)</Header>
          }
        >
          <MixedLineBarChart
            height={280}
            statusType="finished"
            xDomain={trend.map(t => t.yearMonth)}
            xScaleType="categorical"
            xTitle="Month"
            yTitle="Total Cost ($)"
            series={trend.length === 0 ? [] : [
              {
                title: "Monthly Cost",
                type: "bar",
                data: trend.map(t => ({ x: t.yearMonth, y: t.totalCost })),
              },
            ]}
            hideFilter
            ariaLabel="Cost Trend"
            i18nStrings={chartI18n}
            detailPopoverSeriesContent={({ series, y }) => ({
              key: series.title,
              value: `$${(y / 1000).toFixed(1)}K`,
            })}
            empty={emptyBox}
            noMatch={emptyBox}
          />
        </Container>

        <Container
          header={
            <Header variant="h2">Cost Breakdown MTD</Header>
          }
        >
          <PieChart
            variant="donut"
            size="medium"
            statusType="finished"
            hideFilter
            fitHeight
            data={breakdownData}
            innerMetricValue={breakdown?.total ? fmtMoney(breakdown.total) : "—"}
            innerMetricDescription="Total MTD"
            detailPopoverContent={(datum, sum) => [
              { key: "Amount", value: `$${datum.value.toLocaleString()}` },
              {
                key: "Percentage",
                value: `${((datum.value / sum) * 100).toFixed(0)}%`,
              },
            ]}
            segmentDescription={(datum, sum) =>
              `$${datum.value.toLocaleString()}, ${((datum.value / sum) * 100).toFixed(0)}%`
            }
            ariaLabel="Cost Breakdown"
            i18nStrings={{
              ...chartI18n,
              detailsValue: "Value",
              detailsPercentage: "Percentage",
              chartAriaRoleDescription: "pie chart",
            }}
            empty={emptyBox}
            noMatch={emptyBox}
          />
        </Container>
      </ColumnLayout>

      {/* Row 4: Outliers + Agent Activity */}
      <ColumnLayout columns={2}>
        <Container
          header={
            <Header
              variant="h2"
              counter={`(${outliers.length})`}
              actions={<Button variant="normal">View All Outliers</Button>}
            >
              Cost Outliers
            </Header>
          }
        >
          <Table
            items={outliers}
            empty={<Box textAlign="center" padding="l" color="text-body-secondary">No cost outliers</Box>}
            columnDefinitions={[
              {
                id: "vehicle",
                header: "Vehicle",
                cell: (item: any) => <Link href={`/vehicles/management/${item.vehicleId}`}>{item.vehicleId}</Link>,
              },
              { id: "cpm", header: "Cost/Mile", cell: (item: any) => `$${item.costPerMile.toFixed(3)}` },
              { id: "dev", header: "Deviation", cell: (item: any) => (
                <StatusIndicator type={item.deviationPct > 50 ? "warning" : "info"}>+{item.deviationPct}%</StatusIndicator>
              )},
              { id: "fleet", header: "Fleet", cell: (item: any) => item.fleetId },
              { id: "total", header: "Total Cost", cell: (item: any) => fmtMoney(item.totalCost) },
            ]}
            variant="embedded"
          />
        </Container>

        <Container
          header={
            <Header
              variant="h2"
              description="Real-time cost optimization pipeline"
              actions={
                <Link href="/fleet-costs/actions">
                  View all actions
                </Link>
              }
            >
              Agent Activity
            </Header>
          }
        >
          <Box textAlign="center" padding="l" color="text-body-secondary">No recent agent activity</Box>
        </Container>
      </ColumnLayout>

      {/* Row 5: Fleet Comparison */}
      <Container
        header={<Header variant="h2">Fleet Comparison</Header>}
      >
        <BarChart
          height={250}
          statusType="finished"
          xDomain={["Total Cost ($K)", "Cost/Mile (¢)", "Vehicles", "Maintenance %"]}
          yDomain={[0, 300]}
          xScaleType="categorical"
          xTitle="Metric"
          yTitle="Value"
          horizontalBars
          series={[]}
          hideFilter
          ariaLabel="Fleet Comparison"
          i18nStrings={chartI18n}
          detailPopoverSeriesContent={({ series, x, y }) => ({
            key: series.title,
            value:
              String(x).includes("Cost ($K)")
                ? `$${y}K`
                : String(x).includes("¢")
                  ? `$0.${y}`
                  : String(x).includes("%")
                    ? `${y}%`
                    : String(y),
          })}
          empty={emptyBox}
          noMatch={emptyBox}
        />
      </Container>
    </SpaceBetween>
  );
};

export default FleetCostDashboard;
