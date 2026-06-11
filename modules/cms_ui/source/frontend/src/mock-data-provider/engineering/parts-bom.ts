// Engineering parts BOM — read-only mirror of Acme Motors PLM (Teamcenter).
// Used by the Parts tab on the engineering vehicle detail page.
//
// Structure: hierarchical BOM tree rooted at the vehicle. Each node has a part
// number, supplier, requirement IDs, design revision, and (where applicable) a
// link to the ECU that runs on it. Per-vehicle enrichment (cell batch, supplier
// for this specific VIN's pack) is computed from the DynamoDB vehicle record.
//
// In production: queried from PLM via OAuth/SAML federated read-only API.
// For the demo: inline structure, joined client-side by ecuConfigId + vehicle metadata.

import { ECUId } from './ecus';

// ============================================================================
// PART NODE TYPES
// ============================================================================

export interface RequirementRef {
  /** PLM requirement ID (Teamcenter format). */
  reqId: string;
  /** Human-readable title of the requirement. */
  title: string;
  /** Most-recent revision known. */
  rev: string;
}

export interface TestReportRef {
  reportId: string;
  title: string;
  status: 'passed' | 'failed' | 'conditional-pass' | 'open';
  date: string;
}

export interface PartNode {
  /** PLM part number. */
  partNumber: string;
  name: string;
  /** Supplier ID — joins to BATTERY_SUPPLIERS or other supplier registries. */
  supplierId?: string;
  /** Per-part design revision (e.g., "Rev D"). */
  designRev: string;
  /** Linked PLM requirements that this part fulfils or is constrained by. */
  requirements: RequirementRef[];
  /** Linked PLM test reports. */
  testReports: TestReportRef[];
  /** ECU that runs on this hardware part, if applicable. */
  linkedECU?: ECUId;
  /**
   * Indicates this part's batch/lot is per-vehicle (e.g., HV battery cells —
   * different vehicles get different cell lots). UI should pull
   * vehicle.manufacturingBatchId / batteryCellLot for these.
   */
  perVehicleBatch?: boolean;
  /** Children parts (sub-assemblies). */
  children?: PartNode[];
}

// ============================================================================
// BE 6 BOM TREE (canonical config — applies to ECU-CONFIG-BE6-V12-PROD)
// ============================================================================
//
// ~38 parts, depth 3-4. Tree mirrors a typical EV BOM but trimmed for demo
// legibility. The HV battery branch is the demo's "smoking gun" — its cell
// batch metadata is per-vehicle and reveals the affected cohort's root cause.

export const BE6_BOM: PartNode = {
  partNumber: 'MFG-VEH-BE6-ASSY',
  name: 'BE 6 Vehicle Assembly',
  designRev: 'Rev G',
  requirements: [
    { reqId: 'REQ-VEH-001', title: 'Vehicle-level functional safety (ASIL-D rated subsystems)', rev: 'C' },
    { reqId: 'REQ-VEH-014', title: 'Type-approval homologation, India (AIS-156, AIS-038)', rev: 'B' },
  ],
  testReports: [
    { reportId: 'TR-VEH-2025-09-001', title: 'Type approval — AIS-156 EV safety', status: 'passed',  date: '2024-11-12' },
    { reportId: 'TR-VEH-2025-10-003', title: 'Production-launch sign-off', status: 'passed',  date: '2025-04-02' },
  ],
  children: [
    // ---------- POWERTRAIN ----------
    {
      partNumber: 'MFG-PT-BE6-ASSY',
      name: 'Powertrain Assembly',
      designRev: 'Rev E',
      requirements: [
        { reqId: 'REQ-PT-001', title: 'Powertrain peak power 210 kW', rev: 'D' },
        { reqId: 'REQ-PT-008', title: 'Regen brake torque blending', rev: 'C' },
      ],
      testReports: [
        { reportId: 'TR-PT-2025-001', title: 'Powertrain dyno characterization', status: 'passed', date: '2024-09-15' },
      ],
      children: [
        {
          partNumber: 'MFG-HVB-BE6-79KWH',
          name: 'HV Battery Pack — 79 kWh',
          designRev: 'Rev D',
          requirements: [
            { reqId: 'REQ-BATT-001', title: 'Pack thermal envelope: cells operating range -10°C to 45°C', rev: 'B' },
            { reqId: 'REQ-BATT-007', title: 'IP67 enclosure rating', rev: 'A' },
            { reqId: 'REQ-BATT-014', title: 'Cell vendor qualification — datasheet alignment to pack thermal spec', rev: 'A' },
          ],
          testReports: [
            { reportId: 'TR-BATT-2025-Q1-002', title: 'Pack thermal chamber, -20°C to +50°C',  status: 'conditional-pass',  date: '2025-02-08' },
            { reportId: 'TR-BATT-2025-Q3-007', title: 'Pack thermal chamber re-test (Voltrix Q3 batch)', status: 'failed',  date: '2025-08-30' },
          ],
          children: [
            {
              partNumber: 'MFG-HVB-MOD-PRISMATIC-V2',
              name: 'Battery Module (×8)',
              designRev: 'Rev D',
              requirements: [
                { reqId: 'REQ-BATT-MOD-001', title: 'Module cell-balance accuracy ±2 mV', rev: 'C' },
              ],
              testReports: [
                { reportId: 'TR-BATT-MOD-2025-Q1', title: 'Module BoL cycle life', status: 'passed', date: '2024-11-22' },
              ],
              children: [
                {
                  partNumber: 'CELL-NMC811-VTX-A',
                  name: 'HV Battery Cell — NMC811 (per-vehicle batch)',
                  supplierId: 'SUP-VOLTRIX', // overridden per-vehicle in UI for non-Voltrix vehicles
                  designRev: 'Datasheet rev 2.3',
                  perVehicleBatch: true,
                  requirements: [
                    { reqId: 'REQ-CELL-001', title: 'Cell thermal limit ≥ 45°C (BE 6 vehicle spec)', rev: 'A' },
                    { reqId: 'REQ-CELL-002', title: 'Energy density ≥ 280 Wh/kg', rev: 'B' },
                  ],
                  testReports: [
                    { reportId: 'TR-CELL-DS-RE-V23', title: 'Datasheet rev 2.3 review (thermal limit lowered to 42°C)', status: 'open', date: '2025-07-12' },
                  ],
                },
                {
                  partNumber: 'MFG-BMS-SLAVE-V2',
                  name: 'BMS Slave Board (per module)',
                  supplierId: 'SUP-CELLPRIME',
                  designRev: 'Rev C',
                  requirements: [
                    { reqId: 'REQ-BMS-SLAVE-001', title: 'Cell voltage measurement ±1 mV', rev: 'B' },
                  ],
                  testReports: [
                    { reportId: 'TR-BMS-SLAVE-2024', title: 'Slave board EMC + accuracy', status: 'passed', date: '2024-08-04' },
                  ],
                },
              ],
            },
            {
              partNumber: 'MFG-BMS-MASTER-V2',
              name: 'BMS Master Controller',
              supplierId: 'SUP-CELLPRIME',
              designRev: 'Rev C',
              linkedECU: 'BMS',
              requirements: [
                { reqId: 'REQ-BMS-001', title: 'SoC accuracy ±3% (steady-state)', rev: 'B' },
                { reqId: 'REQ-BMS-014', title: 'Thermal compensation algorithm references vendor datasheet', rev: 'A' },
              ],
              testReports: [
                { reportId: 'TR-BMS-2025-04', title: 'BMS firmware v3.2.1 release validation', status: 'passed', date: '2025-04-22' },
                { reportId: 'TR-BMS-2026-05', title: 'BMS firmware v3.3.0-rc2 validation (Pune R&D test fleet)', status: 'passed', date: '2026-05-15' },
              ],
            },
            {
              partNumber: 'MFG-COOL-LOOP-V1',
              name: 'Battery Coolant Loop',
              supplierId: 'SUP-VALEO',
              designRev: 'Rev B',
              requirements: [
                { reqId: 'REQ-COOL-001', title: 'Coolant flow rate 6 L/min @ 35°C inlet', rev: 'A' },
              ],
              testReports: [],
            },
            {
              partNumber: 'MFG-HVJB-V1',
              name: 'HV Junction Box',
              supplierId: 'SUP-CONTINENTAL',
              designRev: 'Rev B',
              requirements: [],
              testReports: [],
            },
          ],
        },
        {
          partNumber: 'MFG-DU-BE6-RWD',
          name: 'Drive Unit (RWD)',
          supplierId: 'SUP-VALEO',
          designRev: 'Rev D',
          requirements: [
            { reqId: 'REQ-DU-001', title: 'Peak motor torque 380 Nm', rev: 'C' },
          ],
          testReports: [],
          children: [
            { partNumber: 'MFG-MOTOR-PMSM-180KW', name: 'eMotor PMSM 180 kW', supplierId: 'SUP-VALEO',  designRev: 'Rev C', requirements: [], testReports: [] },
            { partNumber: 'MFG-INV-SiC-V1',       name: 'Inverter (SiC, 800V)',  supplierId: 'SUP-VALEO',  designRev: 'Rev D', requirements: [], testReports: [] },
            { partNumber: 'MFG-GBX-FIXED',        name: 'Fixed-Ratio Gearbox',   supplierId: 'SUP-ACME-IH', designRev: 'Rev B', requirements: [], testReports: [] },
          ],
        },
        {
          partNumber: 'MFG-VCU-V1',
          name: 'Vehicle Control Unit',
          supplierId: 'SUP-BOSCH',
          designRev: 'Rev D',
          linkedECU: 'VCU',
          requirements: [],
          testReports: [],
        },
      ],
    },

    // ---------- CHARGING ----------
    {
      partNumber: 'MFG-CHARGE-BE6-ASSY',
      name: 'Charging Subsystem',
      designRev: 'Rev C',
      requirements: [
        { reqId: 'REQ-CHARGE-001', title: 'DC fast charge 175 kW peak', rev: 'B' },
        { reqId: 'REQ-CHARGE-008', title: 'ISO 15118 plug-and-charge', rev: 'A' },
      ],
      testReports: [],
      children: [
        { partNumber: 'MFG-OBC-LGI-11KW', name: 'On-Board Charger (11 kW AC)', supplierId: 'SUP-LG-INNOTEK', designRev: 'Rev B', linkedECU: 'CCU', requirements: [], testReports: [] },
        { partNumber: 'MFG-CCS2-PORT',    name: 'CCS-2 Charge Port',           supplierId: 'SUP-PHOENIX-CT', designRev: 'Rev A', requirements: [], testReports: [] },
        { partNumber: 'MFG-DCDC-3KW',     name: 'DC-DC Converter 3 kW (12V aux)', supplierId: 'SUP-LG-INNOTEK', designRev: 'Rev B', requirements: [], testReports: [] },
      ],
    },

    // ---------- BODY ----------
    {
      partNumber: 'MFG-BODY-BE6-ASSY',
      name: 'Body & Comfort',
      designRev: 'Rev F',
      requirements: [],
      testReports: [],
      children: [
        { partNumber: 'MFG-BCM-V1',  name: 'Body Control Module',       supplierId: 'SUP-CONTINENTAL', designRev: 'Rev D', linkedECU: 'BCM', requirements: [], testReports: [] },
        { partNumber: 'MFG-HVAC-V2', name: 'HVAC System (heat pump)',   supplierId: 'SUP-VALEO',       designRev: 'Rev C', requirements: [], testReports: [] },
      ],
    },

    // ---------- ADAS ----------
    {
      partNumber: 'MFG-ADAS-BE6-ASSY',
      name: 'ADAS Subsystem',
      designRev: 'Rev C',
      requirements: [
        { reqId: 'REQ-ADAS-001', title: 'L2+ feature set: ACC, lane-keep, AEB', rev: 'B' },
      ],
      testReports: [],
      children: [
        { partNumber: 'MFG-ADAS-ECU-ORIN', name: 'ADAS Domain Controller (Orin)', supplierId: 'SUP-NVIDIA',     designRev: 'Rev B', linkedECU: 'ADAS', requirements: [], testReports: [] },
        { partNumber: 'MFG-CAM-FRONT-8MP',  name: 'Front Camera (8 MP)',           supplierId: 'SUP-CONTINENTAL', designRev: 'Rev A', requirements: [], testReports: [] },
        { partNumber: 'MFG-RADAR-FRONT-77', name: 'Front Radar (77 GHz)',          supplierId: 'SUP-BOSCH',       designRev: 'Rev A', requirements: [], testReports: [] },
      ],
    },

    // ---------- CONNECTIVITY & COCKPIT ----------
    {
      partNumber: 'MFG-CONN-BE6-ASSY',
      name: 'Connectivity & Cockpit',
      designRev: 'Rev D',
      requirements: [],
      testReports: [],
      children: [
        { partNumber: 'MFG-TCU-G2',     name: 'Telematics Control Unit (5G)', supplierId: 'SUP-ACME-IH', designRev: 'Rev B', linkedECU: 'TCU', requirements: [], testReports: [] },
        { partNumber: 'MFG-IVI-COCKPIT', name: 'Cockpit Compute Module',       supplierId: 'SUP-VISTEON',     designRev: 'Rev C', linkedECU: 'IVI', requirements: [], testReports: [] },
        { partNumber: 'MFG-GW-V1',       name: 'Central Gateway',              supplierId: 'SUP-CONTINENTAL', designRev: 'Rev B', linkedECU: 'GW',  requirements: [], testReports: [] },
      ],
    },
  ],
};

// BE.07 BOM is the same shape — just different cells (Voltrix Q4 / Q1 2026)
// and dev firmware. Reuse BE6_BOM with per-vehicle batch override at render
// time. Future: distinct BOM if components actually diverge.
export const BE07_BOM: PartNode = BE6_BOM;

export const BOM_BY_CONFIG: Record<string, PartNode> = {
  'ECU-CONFIG-BE6-V12-PROD':  BE6_BOM,
  'ECU-CONFIG-BE07-V13-DEV':  BE07_BOM,
};

export function getBOMForConfig(ecuConfigId: string): PartNode | undefined {
  return BOM_BY_CONFIG[ecuConfigId];
}

/** Recursive count of leaf parts in a tree, for UI "X parts" caption. */
export function countParts(node: PartNode): number {
  if (!node.children || node.children.length === 0) return 1;
  return node.children.reduce((sum, c) => sum + countParts(c), 0);
}

/** Find a part by part number anywhere in the tree. */
export function findPart(node: PartNode, partNumber: string): PartNode | undefined {
  if (node.partNumber === partNumber) return node;
  for (const child of node.children ?? []) {
    const hit = findPart(child, partNumber);
    if (hit) return hit;
  }
  return undefined;
}
