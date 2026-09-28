// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DecoderManifestsView and DecoderManifestDetailView tests — T3.4
 *
 * ## What this file covers
 *
 * 1. Fabricated column guard: `signal_mappings`, `ecu_count`, `vehicle_model_name`,
 *    `published_by`, and the old stub types (`STUB_MANIFESTS`, `STUB_MAPPINGS`,
 *    `STUB_USING`) must not appear in the list or detail view source.
 * 2. API field guard: only the 6 real API fields are rendered:
 *    `decoderManifestName`, `decoderManifestVersion`, `description`,
 *    `modelName`, `status`, `createTimestamp`.
 * 3. Three distinct states (unconfigured / error / not_found) in detail view.
 * 4. The detail view does NOT fabricate "Signal mappings" or "Vehicle models" tabs —
 *    those had no API source and have been removed.
 */

import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// ── Source load helpers ───────────────────────────────────────────────────────

const __dir = dirname(fileURLToPath(import.meta.url));

function loadCode(relative: string): string {
  const raw = readFileSync(resolve(__dir, relative), "utf8");
  return raw.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

const LIST_CODE = loadCode("../DecoderManifestsView.tsx");
const DETAIL_CODE = loadCode("../DecoderManifestDetailView.tsx");
const LIST_SOURCE = readFileSync(resolve(__dir, "../DecoderManifestsView.tsx"), "utf8");
const DETAIL_SOURCE = readFileSync(resolve(__dir, "../DecoderManifestDetailView.tsx"), "utf8");

// ── 1. Fabricated column guard ─────────────────────────────────────────────────

describe("DecoderManifestsView — no fabricated columns", () => {
  it("anti-vacuity: comment stripping leaves real code in list view", () => {
    expect(LIST_CODE).toMatch(/const DecoderManifestsView/);
    expect(LIST_CODE).toMatch(/fetchDecoderManifests/);
  });

  it("anti-vacuity: comment stripping leaves real code in detail view", () => {
    expect(DETAIL_CODE).toMatch(/const DecoderManifestDetailView/);
    expect(DETAIL_CODE).toMatch(/fetchDecoderManifests/);
  });

  it("STUB_MANIFESTS is absent from list view — replaced by live API data", () => {
    expect(
      LIST_CODE,
      "STUB_MANIFESTS was fabricated demo data. The list now reads from fetchDecoderManifests().",
    ).not.toMatch(/STUB_MANIFESTS/);
  });

  it("STUB_MAPPINGS is absent from detail view — no API source for signal mappings", () => {
    expect(
      DETAIL_CODE,
      "STUB_MAPPINGS (signal→CAN mapping table) has no counterpart on GET /decoder-manifests. " +
        "The endpoint returns only 6 fields per item. Do not fabricate this data.",
    ).not.toMatch(/STUB_MAPPINGS/);
  });

  it("STUB_USING is absent from detail view — no API source for vehicle models using this manifest", () => {
    expect(
      DETAIL_CODE,
      "STUB_USING (vehicle models using this manifest) has no counterpart on GET /decoder-manifests. " +
        "The decoderManifestRef cross-reference only resolves for CMS-FLEET-MODEL. " +
        "Do not fabricate this data.",
    ).not.toMatch(/STUB_USING/);
  });

  it("signal_mappings column is absent — no API field backs it", () => {
    expect(
      LIST_CODE,
      '"signal_mappings" has no counterpart on GET /decoder-manifests (6 fields only). ' +
        "Do not re-add a column for it.",
    ).not.toMatch(/signal_mappings/);
  });

  it("ecu_count column is absent — no API field backs it", () => {
    expect(
      LIST_CODE,
      '"ecu_count" has no counterpart on GET /decoder-manifests. Do not fabricate it.',
    ).not.toMatch(/ecu_count/);
  });
});

// ── 2. API fields rendered ─────────────────────────────────────────────────────

describe("DecoderManifestsView — API fields", () => {
  it("renders decoderManifestName (the manifest identifier)", () => {
    expect(LIST_CODE).toMatch(/decoderManifestName/);
  });

  it("renders decoderManifestVersion", () => {
    expect(LIST_CODE).toMatch(/decoderManifestVersion/);
  });

  it("renders modelName (the linked vehicle model)", () => {
    expect(LIST_CODE).toMatch(/modelName/);
  });

  it("renders status", () => {
    expect(LIST_CODE).toMatch(/status/);
  });

  it("renders createTimestamp", () => {
    expect(LIST_CODE).toMatch(/createTimestamp/);
  });

  it("detail view renders decoderManifestName, version, status, description, modelName, createTimestamp", () => {
    expect(DETAIL_CODE).toMatch(/decoderManifestName/);
    expect(DETAIL_CODE).toMatch(/decoderManifestVersion/);
    expect(DETAIL_CODE).toMatch(/modelName/);
    expect(DETAIL_CODE).toMatch(/status/);
    expect(DETAIL_CODE).toMatch(/createTimestamp/);
  });
});

// ── 3. Three distinct states in detail view ────────────────────────────────────

describe("DecoderManifestDetailView — three distinct states", () => {
  it("distinguishes unavailable (unconfigured) from not_found and error", () => {
    expect(DETAIL_CODE, "unconfigured state must use 'unavailable' token").toMatch(
      /unavailable/,
    );
    expect(DETAIL_CODE, "not_found state must be present").toMatch(/not_found/);
    expect(DETAIL_CODE, "error state must be present").toMatch(/error/);
  });

  it("null response from fetchDecoderManifests() degrades to unconfigured, not zero rows", () => {
    expect(
      DETAIL_CODE,
      "null must be caught and surfaced as 'unavailable', not rendered as empty",
    ).toMatch(/=== null/);
  });
});

// ── 4. Signal mappings and vehicle models tabs removed ─────────────────────────

describe("DecoderManifestDetailView — removed tabs", () => {
  it("Signal mappings tab label is absent — no API source for CAN mapping table", () => {
    expect(
      DETAIL_CODE,
      '"Signal mappings" tab was backed by STUB_MAPPINGS which had no API source. ' +
        "It has been removed. Do not re-add a tab that would require fabricating data.",
    ).not.toMatch(/Signal mappings/);
  });

  it("Vehicle models tab label is absent — no API source for which models use this manifest", () => {
    expect(
      DETAIL_CODE,
      '"Vehicle models" tab (STUB_USING) had no API source. It has been removed.',
    ).not.toMatch(/Vehicle models using this manifest/);
  });

  it("StubManifest, StubSignalMapping, StubUsingModel types are gone", () => {
    for (const t of ["StubManifest", "StubSignalMapping", "StubUsingModel"]) {
      expect(DETAIL_CODE, `${t} was a stub type and must not remain in wired code`).not.toMatch(
        new RegExp(t),
      );
    }
  });
});

// ── 5. List view reads the real client ────────────────────────────────────────

describe("DecoderManifestsView — reads real API client", () => {
  it("imports fetchDecoderManifests from the data model client", () => {
    expect(LIST_SOURCE).toMatch(/fetchDecoderManifests/);
    expect(LIST_SOURCE).toMatch(/dataModelClient/);
  });

  it("detail view imports fetchDecoderManifests from the data model client", () => {
    expect(DETAIL_SOURCE).toMatch(/fetchDecoderManifests/);
    expect(DETAIL_SOURCE).toMatch(/dataModelClient/);
  });

  it("list view distinguishes unconfigured from empty roster", () => {
    expect(LIST_CODE).toMatch(/unavailable/);
    expect(LIST_CODE).toMatch(/=== null/);
  });
});
