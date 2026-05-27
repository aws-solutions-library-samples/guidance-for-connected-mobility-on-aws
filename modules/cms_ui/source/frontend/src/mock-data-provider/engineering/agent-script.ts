// Scripted agent reasoning trace for the InvestigationWorkspace.
// In production this is the live Bedrock Agent trace; for the demo we stream
// these steps with realistic delays so the experience feels agentic.

export interface AgentStep {
  stepId: string;
  type:
    | 'thinking'
    | 'tool-invocation'
    | 'tool-result'
    | 'visualization'
    | 'finding'
    | 'summary'
    | 'design-options';
  toolName?: string;
  toolArgs?: Record<string, unknown>;
  textContent?: string;
  vizPayload?: { kind: string; data: Record<string, unknown> };
  resultDocIds?: string[];
  delayMs: number; // delay BEFORE this step appears, simulates agent thinking/tool latency
}

/**
 * Hero anomaly investigation script — the BE 6 → BE.07 thermal closed loop.
 * Total runtime: ~28 seconds for a punchy demo flow.
 */
export const HERO_AGENT_TRACE: AgentStep[] = [
  {
    stepId: 'step-01-thinking',
    type: 'thinking',
    textContent:
      'Investigating BE 6 battery degradation pattern in hot-climate operating regions. Starting with telemetry cohort analysis.',
    delayMs: 800,
  },
  {
    stepId: 'step-02-telemetry-tool',
    type: 'tool-invocation',
    toolName: 'query_telemetry',
    toolArgs: {
      metric: 'battery_soh_degradation_rate',
      cohort_filter: {
        fleetId: 'be6-prod-cohort-001',
        regionClimateClass: ['hot-arid', 'hot-humid'],
      },
      time_range: '90d',
      aggregation: 'monthly_rate',
    },
    delayMs: 1200,
  },
  {
    stepId: 'step-03-telemetry-result',
    type: 'tool-result',
    textContent:
      'Returned 130 vehicles in hot-climate cohort. Average SoH degradation: 1.01% per month vs baseline 0.90% per month. Statistically significant (p<0.001). Within hot-climate cohort, a sub-cohort of 40 vehicles shows the strongest signal.',
    delayMs: 1800,
  },
  {
    stepId: 'step-04-cohort-viz',
    type: 'visualization',
    vizPayload: {
      kind: 'cohort-heatmap',
      data: { highlight: 'affected-cohort' },
    },
    textContent: 'Plotting cohort distribution by region.',
    delayMs: 600,
  },
  {
    stepId: 'step-05-thinking-batch',
    type: 'thinking',
    textContent:
      'Hot-climate operation alone does not explain the sub-cohort signal — Karnataka and Tamil Nadu vehicles also operate hot but do not show the elevated degradation. Querying manufacturing context to identify what differentiates the 32 affected vehicles.',
    delayMs: 1400,
  },
  {
    stepId: 'step-06-kg-tool',
    type: 'tool-invocation',
    toolName: 'traverse_knowledge_graph',
    toolArgs: {
      start: { type: 'vehicle-cohort', filter: 'soh_degradation > 1.0% per month' },
      edges: ['vehicle→manufacturing_batch', 'manufacturing_batch→cell_supplier', 'manufacturing_batch→assembly_plant'],
      depth: 3,
    },
    delayMs: 1500,
  },
  {
    stepId: 'step-07-kg-result',
    type: 'tool-result',
    textContent:
      'All 40 vehicles in the affected sub-cohort trace to manufacturing batches BATCH-MH-Q3-2025-A12 and BATCH-MH-Q3-2025-B14, both produced at Chakan-MH plant in Q3 2025 with Voltrix NMC811 cells from cell-line CL-VTX-2025-Q3. Other manufacturing batches (both Voltrix earlier-quarter and CellPrime) operating in the same hot-climate regions do not show elevated degradation. The signal correlates strongly with batch + supplier, not just region.',
    delayMs: 2000,
  },
  {
    stepId: 'step-08-graph-viz',
    type: 'visualization',
    vizPayload: {
      kind: 'knowledge-graph',
      data: {
        focus: 'cohort-batch-supplier-trace',
      },
    },
    textContent: 'Rendering knowledge graph traversal: vehicle → batch → supplier.',
    delayMs: 600,
  },
  {
    stepId: 'step-09-thinking-spec',
    type: 'thinking',
    textContent:
      'Identified clustering by supplier and batch. Now retrieving BE 6 thermal management specification and Voltrix cell datasheet to understand the design constraint at the time of BE 6 release vs current cell datasheet revision.',
    delayMs: 1200,
  },
  {
    stepId: 'step-10-kb-tool-spec',
    type: 'tool-invocation',
    toolName: 'query_engineering_kb',
    toolArgs: { query: 'BE 6 battery thermal management spec; supplier thermal limit assumption' },
    delayMs: 1100,
  },
  {
    stepId: 'step-11-kb-result-spec',
    type: 'tool-result',
    resultDocIds: ['be6-thermal-management-spec'],
    textContent:
      'Retrieved BE 6 Thermal Management Spec rev 1.4. Operating envelope assumes cell thermal limit of 45°C (referenced as Voltrix datasheet rev 2.1, effective 2024-04-15). 3°C margin policy sized against this limit. PFMEA RPN 240 entry deferred — referenced.',
    delayMs: 1600,
  },
  {
    stepId: 'step-12-kb-tool-datasheet',
    type: 'tool-invocation',
    toolName: 'query_engineering_kb',
    toolArgs: { query: 'Voltrix NMC811 cell datasheet current revision' },
    delayMs: 1100,
  },
  {
    stepId: 'step-13-kb-result-datasheet',
    type: 'tool-result',
    resultDocIds: ['supplier-voltrix-datasheet-rev2-3'],
    textContent:
      'Retrieved Voltrix NMC811 datasheet rev 2.3 (effective 2025-07-01). Maximum continuous operating temperature LOWERED from 45°C (rev 2.1) to 42°C (rev 2.3) based on cycle-life characterization at 45°C ambient. Datasheet explicitly recommends customers revalidate thermal management designs sized against the prior limit.',
    delayMs: 2000,
  },
  {
    stepId: 'step-14-kb-tool-pfmea',
    type: 'tool-invocation',
    toolName: 'query_engineering_kb',
    toolArgs: { query: 'BE 6 PFMEA battery cooling RPN 240' },
    delayMs: 1000,
  },
  {
    stepId: 'step-15-kb-result-pfmea',
    type: 'tool-result',
    resultDocIds: ['be6-pfmea-battery-cooling'],
    textContent:
      'Retrieved BE 6 PFMEA. RPN 240 entry "Cell temperature exceeds supplier thermal limit in extreme ambient" was DEFERRED to BE.07 program with assumption of <8% hot-region market penetration. Current hot-region penetration is 65% — assumption invalid.',
    delayMs: 1700,
  },
  {
    stepId: 'step-16-finding',
    type: 'finding',
    textContent:
      'Root cause synthesis: BE 6 thermal spec was sized against Voltrix datasheet rev 2.1 (45°C cell limit). Voltrix issued rev 2.3 in Q3 2025 lowering the limit to 42°C. BE 6 spec was not updated. Q3 2025 production batches (BATCH-MH-Q3-2025-A12 and -B14) ship with Rev 2.3 cells but BE 6 thermal architecture still assumes 45°C. In Maharashtra/Gujarat peak ambient (44–46°C), pack cooling cannot maintain margin against the new 42°C limit. PFMEA RPN 240 was deferred under invalid assumption about hot-region market mix. BE.07 PRD draft v0.7 carries forward BE 6 thermal architecture without resolving this.',
    delayMs: 2400,
  },
  {
    stepId: 'step-17-thinking-options',
    type: 'thinking',
    textContent:
      'Generating design options for BE.07 thermal management. Spawning parallel exploration across cooling architecture, thermal mass, and supplier vectors. Each option will project SoH delta, cost, and qualification lead time against BE.07 launch window.',
    delayMs: 1500,
  },
  {
    stepId: 'step-18-design-options',
    type: 'design-options',
    textContent: 'Generated 3 design options ranked by impact / cost / lead-time tradeoff.',
    delayMs: 2200,
  },
  {
    stepId: 'step-19-summary',
    type: 'summary',
    textContent:
      'Recommended for engineer review: **Option B (PCM thermal mass insert)**. Best balance of projected SoH improvement (-5.4%), cost ($120/vehicle), and qualification time (8 weeks) — fits BE.07 launch window. Closes PFMEA RPN 240 (residual 96, acceptable). Requires BE.07 test fleet validation per BE07-TVP-001 protocol before production lock.',
    delayMs: 1500,
  },
];
