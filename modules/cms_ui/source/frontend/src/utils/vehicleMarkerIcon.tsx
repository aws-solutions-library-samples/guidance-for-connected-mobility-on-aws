// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';

export type VehicleType = 'car' | 'suv' | 'van' | 'truck';
export type MarkerStyle = 'A' | 'B' | 'C';

const TRUCK_MAKES = ['freightliner', 'kenworth', 'peterbilt', 'mack', 'volvo trucks', 'international', 'western star', 'fleet vehicle'];
const VAN_MODELS = ['transit', 'sprinter', 'promaster', 'nv', 'nv200', 'express', 'savana', 'metris', 'van', 'cargo'];
const SUV_MODELS = ['explorer', 'expedition', 'tahoe', 'suburban', 'traverse', 'pilot', 'highlander', 'pathfinder', 'equinox', 'escape', 'cr-v', 'rav4', 'suv', 'crossover', 'bronco', 'blazer', 'durango', 'wrangler', '4runner', 'rogue', 'cx-5', 'edge', 'murano'];

export function getVehicleType(make: string, model: string): VehicleType {
  const m = (make || '').toLowerCase();
  const mo = (model || '').toLowerCase();
  if (TRUCK_MAKES.some(t => m.includes(t))) return 'truck';
  if (VAN_MODELS.some(v => mo.includes(v))) return 'van';
  if (SUV_MODELS.some(s => mo.includes(s))) return 'suv';
  return 'car';
}

// ─── Clean monoline vehicle glyphs on 16×16 viewBox ───────────────────────
// Designed to be readable at small sizes with strokeWidth=1.2, no fill.
const GLYPHS: Record<VehicleType, React.ReactElement> = {
  // Sedan: low roofline, sloped front/rear
  car: (
    <g strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 10 L3.2 7 Q3.8 5.5 5.5 5.5 L10.5 5.5 Q12.2 5.5 12.8 7 L14 10 L14 12 Q14 12.5 13.5 12.5 L2.5 12.5 Q2 12.5 2 12 Z" />
      <circle cx="4.5" cy="12.5" r="1.3" />
      <circle cx="11.5" cy="12.5" r="1.3" />
      <path d="M5 5.5 L6 3.5 Q6.3 3 7 3 L9 3 Q9.7 3 10 3.5 L11 5.5" />
    </g>
  ),
  // SUV: taller, boxier roofline
  suv: (
    <g strokeLinecap="round" strokeLinejoin="round">
      <path d="M1.5 10.5 L2.5 7 Q3 5 5 5 L11 5 Q13 5 13.5 7 L14.5 10.5 L14.5 12.5 Q14.5 13 14 13 L2 13 Q1.5 13 1.5 12.5 Z" />
      <circle cx="4" cy="13" r="1.3" />
      <circle cx="12" cy="13" r="1.3" />
      <path d="M4.5 5 L4.5 3.2 Q4.5 2.5 5.2 2.5 L10.8 2.5 Q11.5 2.5 11.5 3.2 L11.5 5" />
    </g>
  ),
  // Van: tall upright cab, flat roof extends forward
  van: (
    <g strokeLinecap="round" strokeLinejoin="round">
      <path d="M1.5 11 L1.5 5.5 Q1.5 4.5 2.5 4.5 L10 4.5 Q11.5 4.5 12.5 5.8 L14.5 9 L14.5 12 Q14.5 12.5 14 12.5 L2 12.5 Q1.5 12.5 1.5 12 Z" />
      <circle cx="4" cy="12.5" r="1.2" />
      <circle cx="11.5" cy="12.5" r="1.2" />
      <line x1="9.5" y1="4.5" x2="9.5" y2="12.5" />
      <path d="M9.5 7 L13.5 7" />
    </g>
  ),
  // Truck: cab + trailer / flatbed distinction
  truck: (
    <g strokeLinecap="round" strokeLinejoin="round">
      {/* Cab */}
      <path d="M1.5 11 L1.5 6 Q1.5 5 2.5 5 L7.5 5 L7.5 12.5 L2 12.5 Q1.5 12.5 1.5 12 Z" />
      <path d="M4 5 L4 3.5 Q4 3 4.8 3 L6.5 3 Q7.5 3 7.5 4 L7.5 5" />
      {/* Trailer bed */}
      <path d="M7.5 7 L14 7 L14 12.5 L7.5 12.5 Z" />
      <circle cx="3.5" cy="12.5" r="1.2" />
      <circle cx="10.5" cy="12.5" r="1.2" />
      <circle cx="13" cy="12.5" r="1.2" />
    </g>
  ),
};

// ─── Style A: Teardrop pin ──────────────────────────────────────────────────
function StyleA({ type, color }: { type: VehicleType; color: string }) {
  // 28×36 pin — pointy bottom
  return (
    <svg width="28" height="36" viewBox="0 0 28 36" style={{ display: 'block', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,0.35))' }}>
      {/* Pin shape */}
      <path
        d="M14 1 C7.4 1 2 6.4 2 13 C2 22 14 35 14 35 C14 35 26 22 26 13 C26 6.4 20.6 1 14 1 Z"
        fill={color}
        stroke="rgba(255,255,255,0.3)"
        strokeWidth="1"
      />
      {/* White glyph area */}
      <circle cx="14" cy="13" r="9" fill="rgba(255,255,255,0.15)" />
      {/* Vehicle glyph scaled to fit the 16×16 glyph into ~14×14 area centered at 14,13 */}
      <g transform="translate(7, 5) scale(0.875)" stroke="white" strokeWidth="1.2" fill="none">
        {GLYPHS[type]}
      </g>
    </svg>
  );
}

// ─── Style B: Rounded badge chip ───────────────────────────────────────────
function StyleB({ type, color }: { type: VehicleType; color: string }) {
  return (
    <svg width="30" height="22" viewBox="0 0 30 22" style={{ display: 'block', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,0.3))' }}>
      <rect x="1" y="1" width="28" height="20" rx="5" ry="5" fill={color} stroke="rgba(255,255,255,0.25)" strokeWidth="1" />
      {/* Glyph centered in badge */}
      <g transform="translate(7, 3) scale(1)" stroke="white" strokeWidth="1.2" fill="none">
        {GLYPHS[type]}
      </g>
    </svg>
  );
}

// ─── Style C: Circle with glyph ────────────────────────────────────────────
function StyleC({ type, color }: { type: VehicleType; color: string }) {
  return (
    <svg width="26" height="26" viewBox="0 0 26 26" style={{ display: 'block', filter: 'drop-shadow(0 1px 3px rgba(0,0,0,0.35))' }}>
      <circle cx="13" cy="13" r="12" fill={color} stroke="rgba(255,255,255,0.4)" strokeWidth="1.5" />
      <g transform="translate(5, 5) scale(1)" stroke="white" strokeWidth="1.2" fill="none">
        {GLYPHS[type]}
      </g>
    </svg>
  );
}

// ─── Public component ───────────────────────────────────────────────────────
interface VehicleMarkerIconProps {
  make: string;
  model: string;
  connected: boolean;
  style?: MarkerStyle;
}

export const VehicleMarkerIcon: React.FC<VehicleMarkerIconProps> = ({
  make, model, connected, style = 'A',
}) => {
  const type = getVehicleType(make, model);
  const color = connected ? '#16A34A' : '#6B7280';

  if (style === 'B') return <StyleB type={type} color={color} />;
  if (style === 'C') return <StyleC type={type} color={color} />;
  return <StyleA type={type} color={color} />;
};
