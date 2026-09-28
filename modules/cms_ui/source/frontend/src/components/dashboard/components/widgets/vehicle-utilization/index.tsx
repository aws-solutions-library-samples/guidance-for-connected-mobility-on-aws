// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useContext } from "react";
import { Header } from "@cloudscape-design/components";
import { commonChartProps } from "../chart-commons";
import { WidgetConfig } from "../interfaces";
import PieChart from "@cloudscape-design/components/pie-chart";
import Box from "@cloudscape-design/components/box";
import { FleetItem } from "@/api/fleet-management-client";
import {
  fetchVehicleUtilization,
  UtilizationData as VehicleUtilizationData,
} from "@/data-provider/dashboard-widgets";

enum UtilizationType {
  UTILIZED = "UTILIZED",
  NOT_UTILIZED = "NOT_UTILIZED",
  NOT_REPORTED = "NOT_REPORTED",
}
import { UserContext } from "@/components/commons/UserContext";
export const vehicleUtilization: WidgetConfig = {
  definition: { defaultRowSpan: 4, defaultColumnSpan: 2, minRowSpan: 3 },
  data: {
    icon: "pieChart",
    title: "Vehicle Utilization",
    description: "Current utilization of all fleet vehicles",
    header: WidgetHeader,
    content: WidgetContent,
    staticMinHeight: 560,
  },
};

function WidgetHeader() {
  return (
    <Header variant="h2" description="Current utilization of all fleet vehicles">
      Vehicle Utilization Status
    </Header>
  );
}

export default function WidgetContent() {
  const [data, setData] = useState<VehicleUtilizationData[]>([]);
  const [utilizationAvg, setUtilizationAvg] = useState<string>("0%");
  const [dataStatus, setDataStatus] = useState<"loading" | "finished" | "error">("loading");

  useEffect(() => {
    fetchVehicleUtilization().then(d => {
      setData(d);
      const total = d.reduce((s, x) => s + x.value, 0);
      const active = d.find(x => x.title === 'Active')?.value || 0;
      setUtilizationAvg(total > 0 ? `${Math.round(100 * active / total)}%` : '0%');
      setDataStatus(d.length > 0 ? "finished" : "error");
    }).catch(() => setDataStatus("error"));
  }, []);

  return (
    <PieChart
      {...commonChartProps}
      variant="donut"
      data={data}
      statusType={dataStatus}
      size="medium"
      loadingText="Fetching vehicle utilization"
      detailPopoverContent={(datum, sum) => [
        { key: "Vehicle count", value: datum.value },
        {
          key: "Percentage",
          value: `${((datum.value / sum) * 100).toFixed(0)}%`,
        },
      ]}
      segmentDescription={(datum, sum) =>
        `${datum.value} vehicles, ${((datum.value / sum) * 100).toFixed(0)}%`
      }
      innerMetricDescription="Utilized"
      innerMetricValue={utilizationAvg}
      hideFilter={true}
      hideLegend={true}
      fitHeight={true}
      ariaLabel="Fleet Vehicle Utilization"
      empty={
        <Box textAlign="center" color="inherit">
          <b>No data available</b>
          <Box variant="p" color="inherit">
            There is no data available
          </Box>
        </Box>
      }
      noMatch={
        <Box textAlign="center" color="inherit">
          <b>No Data</b>
          <Box variant="p" color="inherit">
            There is no vehicle utilization data available
          </Box>
        </Box>
      }
    />
  );
}
