// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * MarketPostureView — per-market data-sovereignty and homologation posture.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.4
 *
 * ## Screen layout
 *
 * Three jurisdiction cards (US, Germany, India), each displaying:
 *   - Data-sovereignty posture
 *   - Permanent-roaming status
 *   - Homologation approved count (links to /manufacturing/homologation)
 *   - Fleet-average emissions (gCO₂/km)
 *   - TCU-tier distribution (Tier 1 / 2 / 3 counts)
 *
 * ## Cross-link to Homologation
 *
 * The homologation count links to /manufacturing/homologation. No import of
 * homologation.fixture.ts — only the registry path is used.
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value is passed through ProvenanceField (spec D4 /
 * T2.2 provenanceRender guard). No {x.value} in JSX.
 *
 * ## No location fields
 *
 * No trip / GPS / driver-identity / vehicle-location field is rendered here.
 * Market attribution (US, Germany, India) is the only geographic reference.
 */

import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import SpaceBetween from "@cloudscape-design/components/space-between";
import React from "react";
import { useNavigate } from "react-router-dom";

import ProvenanceField from "../../commons/ProvenanceField";

import { MARKET_POSTURE_CARDS } from "./marketPosture.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-market-posture-jurisdiction-matrix";

/** Registry path for the homologation registry (T6.1). */
const HOMOLOGATION_PATH = "/manufacturing/homologation";

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * MarketPostureView renders three jurisdiction cards.
 * Each card links its homologation count to the Homologation registry screen.
 */
const MarketPostureView: React.FC = () => {
  const navigate = useNavigate();

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="market-posture-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      <Header variant="h1">Market Posture</Header>

      <ColumnLayout columns={3} data-testid="market-posture-cards">
        {MARKET_POSTURE_CARDS.map((card) => (
          <Container
            key={card.id}
            header={
              <Header variant="h2">
                <ProvenanceField field={card.market} label="mp_market" />
              </Header>
            }
            data-testid={`market-posture-card-${card.id}`}
          >
            <SpaceBetween size="s">
              <KeyValuePairs
                columns={1}
                items={[
                  {
                    label: "Data Sovereignty Posture",
                    value: (
                      <ProvenanceField
                        field={card.dataSovereigntyPosture}
                        label="mp_data_sovereignty"
                        testId={`mp-sovereignty-${card.id}`}
                      />
                    ),
                  },
                  {
                    label: "Permanent Roaming",
                    value: (
                      <ProvenanceField
                        field={card.permanentRoamingStatus}
                        label="mp_roaming_status"
                        testId={`mp-roaming-${card.id}`}
                      />
                    ),
                  },
                  {
                    label: "Homologation Approvals",
                    value: (
                      <SpaceBetween size="xs" direction="horizontal">
                        <ProvenanceField
                          field={card.homologationApprovedCount}
                          label="mp_homologation_count"
                          testId={`mp-homologation-count-${card.id}`}
                          render={(v) => String(v)}
                        />
                        <Button
                          variant="inline-link"
                          onClick={() => void navigate(HOMOLOGATION_PATH)}
                          ariaLabel={`View homologation registry`}
                          data-testid={`mp-homologation-link-${card.id}`}
                        >
                          View registry
                        </Button>
                      </SpaceBetween>
                    ),
                  },
                  {
                    label: "Fleet Avg Emissions (gCO₂/km)",
                    value: (
                      <ProvenanceField
                        field={card.fleetAvgEmissionsGCo2PerKm}
                        label="mp_emissions"
                        testId={`mp-emissions-${card.id}`}
                        render={(v) => String(v)}
                      />
                    ),
                  },
                ]}
              />

              <Box variant="h4">TCU-Tier Distribution</Box>
              <KeyValuePairs
                columns={3}
                items={[
                  {
                    label: "Tier 1",
                    value: (
                      <ProvenanceField
                        field={card.tcuTierDistribution.tier1}
                        label="mp_tcu_tier1"
                        testId={`mp-tcu-tier1-${card.id}`}
                        render={(v) => String(v)}
                      />
                    ),
                  },
                  {
                    label: "Tier 2",
                    value: (
                      <ProvenanceField
                        field={card.tcuTierDistribution.tier2}
                        label="mp_tcu_tier2"
                        testId={`mp-tcu-tier2-${card.id}`}
                        render={(v) => String(v)}
                      />
                    ),
                  },
                  {
                    label: "Tier 3",
                    value: (
                      <ProvenanceField
                        field={card.tcuTierDistribution.tier3}
                        label="mp_tcu_tier3"
                        testId={`mp-tcu-tier3-${card.id}`}
                        render={(v) => String(v)}
                      />
                    ),
                  },
                ]}
              />
            </SpaceBetween>
          </Container>
        ))}
      </ColumnLayout>
    </SpaceBetween>
  );
};

export default MarketPostureView;
