// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useRef, useEffect, useState } from "react";
import {
  Box,
  Button,
  ColumnLayout,
  Container,
  Header,
  SpaceBetween,
  StatusIndicator,
} from "@cloudscape-design/components";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { withIdentityPoolId } from "@aws/amazon-location-utilities-auth-helper";

interface Location {
  id: string;
  vehicles: number;
  active: number;
  idle: number;
  util: number;
  status: string;
  surplus: number;
}

interface Props {
  locations: Location[];
}

const cityCoords: Record<string, [number, number]> = {
  "Denver": [-104.99, 39.74],
  "Dallas": [-96.80, 32.78],
  "Phoenix": [-112.07, 33.45],
  "Portland": [-122.68, 45.52],
  "Chicago": [-87.63, 41.88],
  "Atlanta": [-84.39, 33.75],
  "Seattle": [-122.33, 47.61],
  "Miami": [-80.19, 25.76],
  "Nashville": [-86.78, 36.16],
  "Las Vegas": [-115.14, 36.17],
  "Boston": [-71.06, 42.36],
  "Houston": [-95.37, 29.76],
};

// Amazon/Cloudscape-aligned colors
const getColor = (util: number): string => {
  if (util >= 90) return "#d91515"; // Cloudscape red — deficit
  if (util >= 80) return "#037f0c"; // Cloudscape green — healthy
  if (util >= 70) return "#8D6605"; // Cloudscape amber — watch
  return "#0972d3"; // Cloudscape blue — surplus
};

const getLabel = (surplus: number) => {
  if (surplus > 5) return "SURPLUS";
  if (surplus < -5) return "DEFICIT";
  if (surplus > 0) return "Slight surplus";
  if (surplus < 0) return "Slight deficit";
  return "Balanced";
};

const UtilizationHeatmap: React.FC<Props> = ({ locations }) => {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const sorted = [...locations].sort((a, b) => a.surplus - b.surplus);

  useEffect(() => {
    if (!mapContainer.current || map.current) return;

    const initMap = async () => {
      try {
        const runtimeConfig = (window as any).runtimeConfig;
        const region = runtimeConfig?.awsRegion || "us-east-1";
        const mapName = runtimeConfig?.locationServices?.mapName || "cms-vehicle-map";
        const identityPoolId = runtimeConfig?.awsCredentials?.identityPoolId;

        if (!identityPoolId) {
          setMapError("No identity pool configured");
          return;
        }

        const authHelper = await withIdentityPoolId(identityPoolId);

        // Wait for credentials to be ready
        await new Promise(resolve => setTimeout(resolve, 1000));

        map.current = new maplibregl.Map({
          container: mapContainer.current!,
          style: `https://maps.geo.${region}.amazonaws.com/maps/v0/maps/${mapName}/style-descriptor`,
          center: [-96.5, 38.0], // Center of continental US
          zoom: 4.2,
          minZoom: 3,
          maxZoom: 12,
          maxBounds: [[-130, 22], [-65, 52]], // Lock to continental US
          ...authHelper.getMapAuthenticationOptions(),
        });

        map.current.addControl(new maplibregl.NavigationControl(), "top-right");

        map.current.on("load", () => {
          // Add markers for each location
          locations.forEach((loc) => {
            const coords = cityCoords[loc.id];
            if (!coords) return;

            const color = getColor(loc.util);
            const size = 30 + (loc.vehicles / 5);

            // Create marker element
            const el = document.createElement("div");
            el.style.width = `${size}px`;
            el.style.height = `${size}px`;
            el.style.borderRadius = "50%";
            el.style.backgroundColor = color;
            el.style.border = `3px solid ${color}`;
            el.style.opacity = "0.85";
            el.style.display = "flex";
            el.style.alignItems = "center";
            el.style.justifyContent = "center";
            el.style.color = "#FFFFFF";
            el.style.fontSize = "11px";
            el.style.fontWeight = "700";
            el.style.cursor = "pointer";
            el.style.boxShadow = `0 0 ${Math.abs(loc.surplus) > 5 ? '12' : '4'}px ${color}`;
            el.textContent = `${loc.util}%`;

            const popup = new maplibregl.Popup({ offset: 15, closeButton: false }).setHTML(`
              <div style="padding:8px;font-family:Amazon Ember,Helvetica,Arial,sans-serif;">
                <div style="font-weight:700;font-size:14px;margin-bottom:4px;">${loc.id}</div>
                <div style="font-size:13px;color:${color};font-weight:700;">${loc.util}% utilization</div>
                <div style="font-size:12px;margin-top:4px;">${loc.active} active / ${loc.idle} idle of ${loc.vehicles}</div>
                <div style="font-size:12px;color:${color};margin-top:2px;">${getLabel(loc.surplus)}${loc.surplus !== 0 ? ` (${Math.abs(loc.surplus)} vehicles)` : ''}</div>
              </div>
            `);

            new maplibregl.Marker({ element: el })
              .setLngLat(coords)
              .setPopup(popup)
              .addTo(map.current!);
          });
        });
      } catch (err: any) {
        console.error("Map init error:", err);
        setMapError(err.message || "Failed to initialize map");
      }
    };

    initMap();

    return () => {
      if (map.current) {
        map.current.remove();
        map.current = null;
      }
    };
  }, [locations]);

  return (
    <SpaceBetween size="l">
      <Container header={
        <Header variant="h2" description="Vehicle locations color-coded by utilization. Click markers for details.">
          Fleet Utilization Map
        </Header>
      }>
        <div ref={mapContainer} style={{ height: '420px', borderRadius: '8px', overflow: 'hidden' }}>
          {mapError && (
            <Box textAlign="center" padding="xl" color="text-status-error">
              Map unavailable: {mapError}
            </Box>
          )}
        </div>
      </Container>

      {/* Location cards below map */}
      <ColumnLayout columns={4}>
        {sorted.map((loc) => (
          <Container key={loc.id}>
            <SpaceBetween size="xxs">
              <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>{loc.id}</span>
              <span style={{ fontSize: '28px', fontWeight: 700, display: 'block', lineHeight: 1.2, color: getColor(loc.util) }}>{loc.util}%</span>
              <Box color="text-body-secondary" fontSize="body-s">
                {loc.active} active / {loc.idle} idle of {loc.vehicles}
              </Box>
              <StatusIndicator type={
                loc.surplus > 5 ? "info" : loc.surplus < -5 ? "error" : loc.surplus > 0 ? "info" : loc.surplus < 0 ? "warning" : "success"
              }>
                {getLabel(loc.surplus)}{loc.surplus !== 0 ? ` (${Math.abs(loc.surplus)} vehicles)` : ""}
              </StatusIndicator>
              <Box margin={{ top: "xs" }}>
                {loc.surplus > 3 ? (
                  <Button variant="inline-link" iconName="upload">Move vehicles out</Button>
                ) : loc.surplus < -3 ? (
                  <Button variant="inline-link" iconName="download">Request vehicles</Button>
                ) : (
                  <Button variant="inline-link" iconName="status-info">View details</Button>
                )}
              </Box>
            </SpaceBetween>
          </Container>
        ))}
      </ColumnLayout>

      {/* Legend */}
      <Container>
        <SpaceBetween direction="horizontal" size="xl">
          <SpaceBetween direction="horizontal" size="xs">
            <div style={{ width: 16, height: 16, borderRadius: '50%', backgroundColor: '#0972d3', display: 'inline-block' }} />
            <Box fontSize="body-m"><b>Surplus</b> — below 70% utilization (move vehicles out)</Box>
          </SpaceBetween>
          <SpaceBetween direction="horizontal" size="xs">
            <div style={{ width: 16, height: 16, borderRadius: '50%', backgroundColor: '#8D6605', display: 'inline-block' }} />
            <Box fontSize="body-m"><b>Watch</b> — 70–80% utilization</Box>
          </SpaceBetween>
          <SpaceBetween direction="horizontal" size="xs">
            <div style={{ width: 16, height: 16, borderRadius: '50%', backgroundColor: '#037f0c', display: 'inline-block' }} />
            <Box fontSize="body-m"><b>Healthy</b> — 80–90% utilization</Box>
          </SpaceBetween>
          <SpaceBetween direction="horizontal" size="xs">
            <div style={{ width: 16, height: 16, borderRadius: '50%', backgroundColor: '#d91515', display: 'inline-block' }} />
            <Box fontSize="body-m"><b>Deficit</b> — above 90% utilization (need vehicles)</Box>
          </SpaceBetween>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
};

export default UtilizationHeatmap;
