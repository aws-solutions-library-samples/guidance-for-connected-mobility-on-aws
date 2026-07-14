// The 3 design options the Product Engineering Agent generates after diagnosing
// the BE 6 thermal/SoH root cause. Each option carries a digital-thread writeback
// payload — the "push" actions visible when the engineer accepts an option.

export interface DigitalThreadAction {
  type: 'requirements-update' | 'sim-param-update' | 'pfmea-update' | 'design-review-ticket';
  target: string;
  description: string;
  // Payload shape varies by type — keep loose for the demo
  payload: Record<string, unknown>;
}

export interface DesignOption {
  optionId: string;
  title: string;
  shortLabel: string;
  description: string;
  rationale: string;
  projectedImpact: {
    soHDegradationDelta_pct: number; // negative = improvement
    thermalMarginGain_C: number;
    rangeImpact_pct: number;
  };
  costDelta_perVehicle_USD: number;
  qualificationLeadTime_weeks: number;
  evidenceDocIds: string[];
  digitalThreadActions: DigitalThreadAction[];
  recommended: boolean;
}

export const DESIGN_OPTIONS: DesignOption[] = [
  // ============================================================================
  // OPTION A — Active liquid cooling channel
  // ============================================================================
  {
    optionId: 'opt-a-active-liquid-cooling',
    title: 'Active liquid cooling channel addition',
    shortLabel: 'Option A — Active liquid cooling',
    description:
      'Add a dedicated active liquid cooling channel through the battery pack with a secondary pump and chiller circuit, sized for sustained operation at 46°C ambient.',
    rationale:
      'Highest projected impact on SoH degradation. Gains full thermal margin against Voltrix Rev 2.3 spec. Mitigates PFMEA RPN 240 directly. Highest BOM and qualification cost.',
    projectedImpact: {
      soHDegradationDelta_pct: -8.2,
      thermalMarginGain_C: 6.0,
      rangeImpact_pct: -0.4,
    },
    costDelta_perVehicle_USD: 340,
    qualificationLeadTime_weeks: 18,
    evidenceDocIds: [
      'be6-pfmea-battery-cooling',
      'supplier-voltrix-datasheet-rev2-3',
      'be6-thermal-management-spec',
    ],
    digitalThreadActions: [
      {
        type: 'requirements-update',
        target: 'be07-thermal-management-prd-draft',
        description: 'Update BE.07 PRD §3 OQ-1 to specify active cooling channel architecture.',
        payload: {
          docId: 'be07-thermal-management-prd-draft',
          section: '3. Open questions / OQ-1',
          oldText: 'Validate thermal margin against 2026 supplier cell datasheets.',
          newText:
            'RESOLVED — Active liquid cooling channel adopted. Thermal margin sized against Voltrix VTX-NMC811-79 Rev 2.3 (42°C cell limit). Target margin: 6°C against Maharashtra/Gujarat peak ambient (46°C).',
        },
      },
      {
        type: 'sim-param-update',
        target: 'be07-thermal-pack-v3',
        description: 'Push updated coolant flow, secondary loop topology to thermal sim model.',
        payload: {
          model: 'be07-thermal-pack-v3',
          changes: {
            'pack.cooling.primary_loop_flow_lpm': { from: 8.0, to: 8.0 },
            'pack.cooling.secondary_loop_active': { from: false, to: true },
            'pack.cooling.secondary_loop_flow_lpm': { from: null, to: 5.5 },
            'pack.cooling.chiller_kw': { from: 0, to: 1.4 },
          },
          triggerSimRuns: ['be07-hot-climate-peak', 'be07-fastcharge-thermal'],
        },
      },
      {
        type: 'pfmea-update',
        target: 'be6-pfmea-battery-cooling',
        description: 'Close PFMEA RPN 240 with mitigation reference.',
        payload: {
          rpnId: 'RPN-240',
          status: 'closed-mitigated',
          closureReference: 'DC-BE07-2026-014',
          residualRPN: 84,
        },
      },
      {
        type: 'design-review-ticket',
        target: 'PLM-DR',
        description: 'File design review ticket with Powertrain CTO and Manufacturing Engineering.',
        payload: {
          ticketId: 'PLM-DR-2026-013',
          title: 'BE.07 thermal management — adopt active liquid cooling channel',
          approvers: ['Powertrain CTO', 'Manufacturing Engineering Lead', 'Cost Engineering Lead'],
          linkedTestFleet: 'be07-test-fleet-001',
        },
      },
    ],
    recommended: false,
  },

  // ============================================================================
  // OPTION B — PCM thermal mass (RECOMMENDED for the demo)
  // ============================================================================
  {
    optionId: 'opt-b-pcm-thermal-mass',
    title: 'Phase-change material (PCM) thermal mass insert',
    shortLabel: 'Option B — PCM thermal mass',
    description:
      'Insert phase-change material modules between cell rows. PCM phase change at 32°C absorbs thermal load during peak ambient stress, recovers overnight.',
    rationale:
      'Best balance of impact, cost, and qualification time. No new active subsystems — simpler validation. Closes most of the thermal margin gap. Fastest path to BE.07 launch.',
    projectedImpact: {
      soHDegradationDelta_pct: -5.4,
      thermalMarginGain_C: 3.0,
      rangeImpact_pct: -0.2,
    },
    costDelta_perVehicle_USD: 120,
    qualificationLeadTime_weeks: 8,
    evidenceDocIds: [
      'be6-thermal-management-spec',
      'supplier-voltrix-datasheet-rev2-3',
      'be07-thermal-validation-test-plan',
    ],
    digitalThreadActions: [
      {
        type: 'requirements-update',
        target: 'be07-thermal-management-prd-draft',
        description: 'Update BE.07 PRD §3 OQ-1 with PCM-Gen2 thermal mass adoption.',
        payload: {
          docId: 'be07-thermal-management-prd-draft',
          section: '3. Open questions / OQ-1',
          oldText: 'Validate thermal margin against 2026 supplier cell datasheets.',
          newText:
            'RESOLVED 2026-05-19 — Battery pack thermal management updated to include phase-change material thermal mass (PCM-Gen2) targeting +3°C cell margin against Voltrix NMC811 Rev 2.3 thermal limit (42°C). Supports BE.07 operation in Indian hot-climate envelope (ambient peak 46°C). Reference: PFMEA-BE6-bat-cool RPN 240 closed via design change DC-BE07-2026-014.',
        },
      },
      {
        type: 'sim-param-update',
        target: 'be07-thermal-pack-v3',
        description: 'Push updated thermal mass and PCM phase-change parameters to sim model.',
        payload: {
          model: 'be07-thermal-pack-v3',
          version: '0.8.0',
          changes: {
            'battery_pack.thermal_mass_kJ_per_K': { from: 14.2, to: 17.8 },
            'battery_pack.cooling.pcm_active': { from: false, to: true },
            'battery_pack.cooling.pcm_phase_change_temp_C': { from: null, to: 32 },
            'battery_pack.cooling.pcm_latent_heat_kJ_per_kg': { from: null, to: 215 },
          },
          triggerSimRuns: [
            'be07-hot-climate-peak',
            'be07-fastcharge-thermal',
            'be07-cycle-life-65k-km',
          ],
        },
      },
      {
        type: 'pfmea-update',
        target: 'be6-pfmea-battery-cooling',
        description: 'Close PFMEA RPN 240 — residual RPN 96 (acceptable).',
        payload: {
          rpnId: 'RPN-240',
          status: 'closed-mitigated',
          closureReference: 'DC-BE07-2026-014',
          residualRPN: 96,
          residualRationale: 'PCM provides margin under sustained peak ambient up to 46°C; residual risk during charging stack-up at peak ambient remains marginal but acceptable.',
        },
      },
      {
        type: 'design-review-ticket',
        target: 'PLM-DR',
        description: 'File design review ticket; queue BE.07 test fleet validation.',
        payload: {
          ticketId: 'PLM-DR-2026-014',
          title: 'BE.07 thermal management — adopt PCM-Gen2 thermal mass',
          linkedFromCMS: 'anomaly:anom-be6-thermal-001',
          evidence: [
            'telemetry-query:bq-be6-soh-q1-2026',
            'kb-doc:be6-pfmea-battery-cooling',
            'kb-doc:supplier-voltrix-datasheet-rev2-3',
          ],
          proposedBy: 'Product Engineering Agent (engineer review pending)',
          status: 'pending-engineering-review',
          approvers: ['Powertrain CTO', 'Manufacturing Engineering Lead'],
          linkedTestFleet: 'be07-test-fleet-001',
          validationProtocol: 'be07-thermal-validation-test-plan',
        },
      },
    ],
    recommended: true,
  },

  // ============================================================================
  // OPTION C — Supplier diversification
  // ============================================================================
  {
    optionId: 'opt-c-supplier-diversification',
    title: 'Cell supplier diversification (CellPrime primary)',
    shortLabel: 'Option C — Supplier diversification',
    description:
      'Shift BE.07 primary cell supplier from Voltrix to CellPrime (48°C thermal limit). Retain Voltrix as secondary for supply resilience.',
    rationale:
      'Lowest BOM impact and largest absolute thermal margin gain. Highest qualification time due to supplier requalification + dual-source manufacturing. Strategic dependency change.',
    projectedImpact: {
      soHDegradationDelta_pct: -6.1,
      thermalMarginGain_C: 6.0,
      rangeImpact_pct: 0.0,
    },
    costDelta_perVehicle_USD: 45,
    qualificationLeadTime_weeks: 26,
    evidenceDocIds: [
      'supplier-cellprime-datasheet-rev1-4',
      'supplier-voltrix-datasheet-rev2-3',
      'be6-thermal-management-spec',
    ],
    digitalThreadActions: [
      {
        type: 'requirements-update',
        target: 'be07-thermal-management-prd-draft',
        description: 'Update BE.07 PRD: primary cell supplier change to CellPrime.',
        payload: {
          docId: 'be07-thermal-management-prd-draft',
          section: '2. Carry-forward from BE 6',
          oldText: 'Cell supplier: Voltrix VTX-NMC811-79 (primary)',
          newText:
            'Cell supplier: CellPrime CPR-NMC811-79 (primary, 48°C thermal limit). Voltrix VTX-NMC811-79 retained as qualified secondary for supply resilience.',
        },
      },
      {
        type: 'sim-param-update',
        target: 'be07-thermal-pack-v3',
        description: 'Update cell thermal model to CellPrime envelope.',
        payload: {
          model: 'be07-thermal-pack-v3',
          changes: {
            'cell.thermal_limit_C': { from: 42, to: 48 },
            'cell.optimal_temp_C': { from: 25, to: 28 },
            'cell.thermal_derating_onset_C': { from: 36, to: 42 },
          },
          triggerSimRuns: ['be07-hot-climate-peak'],
        },
      },
      {
        type: 'design-review-ticket',
        target: 'PLM-DR',
        description: 'File supplier change review with sourcing + manufacturing.',
        payload: {
          ticketId: 'PLM-DR-2026-015',
          title: 'BE.07 cell supplier change — primary to CellPrime',
          approvers: [
            'Powertrain CTO',
            'Sourcing Lead',
            'Manufacturing Engineering Lead',
            'Supply Resilience Lead',
          ],
          flags: ['supplier-strategy-change', 'long-lead-qualification'],
        },
      },
    ],
    recommended: false,
  },
];

export const getDesignOption = (optionId: string) =>
  DESIGN_OPTIONS.find((o) => o.optionId === optionId);
