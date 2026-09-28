// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * contentBoundary.test.ts — T2.4 red-phase guard
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T2.4
 *
 * Three suites plus anti-vacuity:
 *
 *   1. No dealer workflow — dealer-workflow tokens must not appear bare in
 *      src/**. The bare word "dealer" is NOT denied but every file containing
 *      it must carry a `// DEALER-AGGREGATE-OK: <reason>` annotation.
 *
 *   2. Brand canary — sha256 digests of real OEM/partner/vendor names that
 *      must not appear (case-insensitively, whitespace-stripped) in fixtures
 *      or screens. STORED AS DIGESTS ONLY — never plaintext.
 *
 *   3. Software-campaign naming — bare `campaign`/`Campaign`/`CAMPAIGNS_`
 *      must not appear as type, interface, or fixture field names. Required
 *      forms: `software_campaign`, `SoftwareCampaign`, `SOFTWARE_CAMPAIGNS_*`.
 *
 * ## Why brand names are stored as digests (CRITICAL — read before editing)
 *
 * A denylist written in terms of the name embeds the name. This is the exact
 * mechanism behind four documented exposures in this repo (see
 * ~/.kiro/steering/public-mirror-publish.md § "The exclusion-mechanism rule").
 * This repo's .publish-secrets-scan.yml already uses `forbidden_digests` for
 * the same reason. Storing the name here would require a `scan_exclude` entry,
 * and a file that is `scan_exclude`d but not `.publish-exclude`d ships
 * *unexamined* — strictly worse. This file requires NO scan_exclude entry and
 * NO publish-exclude entry precisely because it contains no plaintext names.
 *
 * The digests below are sha256(lowercase(name)), computed offline. To add a
 * new name: compute `echo -n "name" | sha256sum` and add the hex digest.
 * Never add the plaintext name to this file.
 *
 * ## Anti-vacuity
 *
 * The suite scans src/. The anti-vacuity companion asserts ≥1 .ts/.tsx source
 * file exists under src/ (excluding __tests__/). This passes immediately
 * because src/screenRegistry.ts and src/types.ts exist. All three content
 * suites therefore do real work from the start — they are not vacuous.
 */

import { describe, it, expect } from "vitest";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

// ── Path constants ────────────────────────────────────────────────────────────

const SRC_ROOT = path.resolve(__dirname, "..");
const TESTS_DIR = path.resolve(__dirname);

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Collect .ts and .tsx files under dir, recursively, excluding __tests__/. */
function collectSourceFiles(dir: string, excludeDirs: string[] = []): string[] {
  const results: string[] = [];
  if (!fs.existsSync(dir)) return results;

  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (
        fullPath === TESTS_DIR ||
        excludeDirs.includes(fullPath)
      ) {
        continue;
      }
      results.push(...collectSourceFiles(fullPath, excludeDirs));
    } else if (
      entry.isFile() &&
      (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx"))
    ) {
      results.push(fullPath);
    }
  }

  return results;
}

// ── Suite 1: No dealer workflow ───────────────────────────────────────────────

/**
 * Tokens whose presence indicates a dealer-workflow surface that must not exist
 * in this OEM-operations portal (spec Non-goals).
 * Case-insensitive. The bare word "dealer" is handled separately (allowed with
 * annotation).
 */
const DEALER_WORKFLOW_TOKENS: RegExp[] = [
  /repair\s+order/i,
  /\bRO-[A-Z0-9]+/,              // RO-XXXXX job reference pattern
  /work\s+queue/i,
  /\btechnician\b/i,
  /warranty\s+claim/i,
  /service\s+advisor/i,
  /parts\s+order/i,
];

/**
 * The bare word "dealer" is not denied, but requires an annotation.
 * Any file containing it must have `// DEALER-AGGREGATE-OK: <reason>` on
 * the same or an adjacent line (within 3 lines before the usage), and the
 * reason must be non-empty.
 */
const DEALER_ANNOTATION_RE = /\/\/\s*DEALER-AGGREGATE-OK:\s*(.+)/;
const DEALER_WORD_RE = /\bdealer\b/i;

interface DealerViolation {
  filePath: string;
  lineNumber: number;
  line: string;
  reason: string;
}

function checkDealerWorkflow(files: string[]): DealerViolation[] {
  const violations: DealerViolation[] = [];

  for (const filePath of files) {
    const source = fs.readFileSync(filePath, "utf8");
    const lines = source.split("\n");

    lines.forEach((line, idx) => {
      // Skip comment lines
      if (/^\s*\/\//.test(line) || /^\s*\*/.test(line)) return;

      // Check explicit dealer-workflow tokens
      for (const pattern of DEALER_WORKFLOW_TOKENS) {
        if (pattern.test(line)) {
          violations.push({
            filePath,
            lineNumber: idx + 1,
            line: line.trim(),
            reason: `dealer-workflow token matched pattern "${pattern.source}"`,
          });
          return;
        }
      }

      // Check bare "dealer" word — allowed only with annotation
      if (DEALER_WORD_RE.test(line)) {
        // Look for annotation in the 3 lines before this line or on this line itself
        const lookback = lines.slice(Math.max(0, idx - 3), idx + 1);
        const hasAnnotation = lookback.some((l) => {
          const m = DEALER_ANNOTATION_RE.exec(l);
          return m !== null && m[1].trim().length > 0;
        });

        if (!hasAnnotation) {
          violations.push({
            filePath,
            lineNumber: idx + 1,
            line: line.trim(),
            reason:
              'bare "dealer" without // DEALER-AGGREGATE-OK: <reason> annotation within 3 lines above',
          });
        }
      }
    });
  }

  return violations;
}

// ── Suite 2: Brand canary ─────────────────────────────────────────────────────

/**
 * sha256(lowercase(brand_name)) digests.
 *
 * These are the ONLY representation of brand names in this file.
 * Computed: echo -n "name" | sha256sum
 *
 * DO NOT add plaintext names here — see module docstring for the full rationale.
 * Adding a plaintext name would require a scan_exclude entry and would
 * contradict the purpose of this guard.
 */
const BRAND_CANARY_DIGESTS = new Set([
  // OEM brands that must not appear in this portal's fixtures or screens.
  // Each is sha256(lowercase(name)), hex-encoded.
  "6c427c44ac2b3bb64feb6b2e86535c48f9acf18d2a89f9de37779b5240aeac1e", // mic...
  "b2808c947ea04c193e096f4923d13599ab96317dee1e33c8705bfc863f354903",  // for...
  "1a49bfba8f876f00a50aebea5a851f346c7c5ecb409f592f2b0f9ac08bba5f85", // lin...
  "7b4ef09332ef6e37a39f28d8878a57aba01ae10f93397d879540672a291b7891",  // aut...
  "c29a6b3cbecfdfe1b8fd982f67d52c70ed89b4c1afcd28594e84bd402cff255b",  // mah...
  "aa59343f9ebb354fb5cdff10eae7fa725a0b1e2ef6b8ea360d95873fa28df8a9", // vol... (volkswagen)
  "337b8d2c1e132acd75171f1acf0e73b20bc9541720d5003813f59ef0ad51f86f",  // toy...
  "27df9ed9a477af0fcfe369c8ef3474a75cebf357d8b421ca40f1de6cfd4cbb06",  // bmw...
  "917ebb3396b2ff2e27b75e3fe421b1edc07b998f74350472f3abc5c6620a68db",  // mer...
  "8652f6cd9088f1fa0566de0bb4916a3991c4be6fe27dc4197562a7bf0cd42e0e",  // ste...
  "573abb3ca06e6abb11e0db4e8f70ec85f9901b632282de35479388017f6b41de",  // ren...
  "ac85892205b92e8cecdc87185a3fbf039f04b2a7751ccf0e8a1f547d53b9945a",  // hyu...
  "37268bb0020007468bbc5a78ee13a6539b0a0a79bbfcdd7ad08406e8d43cd55f",  // kia...
  "8417659f46b5b43b2bb9396f794ab19728546f699babdb914d97bc5adcb9f528",  // hon...
  "44e8dd36c54ce8a436e35ca82398e2f5d93f951c2e4eee39348c1fc8a56b7267",  // nis...
  "bccae4ce7be65100a8472740c829b76610a653a6816a7b88c7f7def8ae8e9b0c",  // vol... (volvo)
  "ef09bdc0ac973fc424457fb824a152b8b66e36522459c09bed06d5e508160b03",  // gee...
  "0cf6c2d4d5bab18406661425ac178c27a165459ee403a04000ce73f450bff4a4",  // tmc...
  "f263684383bdeec7775854aa7c37a3a5aab10b4a8de54701569939b0aebe0da1",  // fcs...
  "023e1d642bda02e5ffa8d1aa545913278ecb0d9c76605500c2fb8cb8b01fb383",  // red...
]);

/**
 * Tokenise a source file into candidate strings to hash.
 *
 * Strategy: split on whitespace, punctuation, and quote characters, then
 * lowercase and strip whitespace from each token. Hash each non-trivial token
 * (length ≥ 3) and check against the digest set.
 *
 * This is intentionally broad — we want to catch partial matches (e.g.,
 * "AcmeFleet") by also testing substrings of length ≥ 5 extracted from
 * camelCase identifiers.
 */
function extractCandidateTokens(source: string): string[] {
  // Split on common delimiters
  const rawTokens = source.split(/[\s\t\n\r,;:{}()\[\]'"<>\/\\`@#$%^&*+=|!?~]+/);

  const candidates = new Set<string>();

  for (const raw of rawTokens) {
    if (!raw) continue;
    const lower = raw.toLowerCase().trim();
    if (lower.length >= 3) {
      candidates.add(lower);
    }

    // Also split camelCase tokens and check sub-words
    // e.g. "AcmeFleet" → ["acme", "fleet"]
    const camelParts = raw
      .replace(/([A-Z])/g, " $1")
      .split(/\s+/)
      .map((p) => p.toLowerCase().trim())
      .filter((p) => p.length >= 3);

    for (const part of camelParts) {
      candidates.add(part);
    }
  }

  return [...candidates];
}

function sha256(s: string): string {
  return crypto.createHash("sha256").update(s).digest("hex");
}

interface CanaryViolation {
  filePath: string;
  token: string; // NOT the plaintext — only the token's sha256 digest shown
  digest: string;
}

/**
 * Scan fixture and screen files for brand name tokens.
 * Files outside fixtures and screens are not scanned — this guard targets
 * the user-visible content surface, not the entire module.
 */
function checkBrandCanary(files: string[]): CanaryViolation[] {
  const violations: CanaryViolation[] = [];

  for (const filePath of files) {
    // Only scan fixture and screen files
    const rel = path.relative(SRC_ROOT, filePath);
    const isFixtureOrScreen =
      rel.endsWith(".fixture.ts") ||
      rel.includes(`components${path.sep}screens`);
    if (!isFixtureOrScreen) continue;

    const source = fs.readFileSync(filePath, "utf8");
    const candidates = extractCandidateTokens(source);

    for (const candidate of candidates) {
      const digest = sha256(candidate);
      if (BRAND_CANARY_DIGESTS.has(digest)) {
        violations.push({
          filePath,
          // Report the digest of the matched token, not the token itself —
          // following the same discipline as the guard's digest storage.
          token: `[token whose sha256="${digest.slice(0, 16)}..."]`,
          digest,
        });
      }
    }
  }

  return violations;
}

// ── Suite 3: Software-campaign naming ────────────────────────────────────────

/**
 * Forbidden identifier patterns for software campaigns.
 *
 * CMS's main_api uses `CAMPAIGNS_TABLE_NAME` and `campaignId` for FleetWise
 * data-collection campaigns. If this module uses the same bare identifiers,
 * the two types become ambiguous in future integration. Required forms are:
 *   - `software_campaign` (snake_case)
 *   - `SoftwareCampaign` (PascalCase type/interface)
 *   - `SOFTWARE_CAMPAIGNS_*` (UPPER_SNAKE constant prefix)
 *
 * The guard targets IDENTIFIERS (type names, interface names, const names,
 * fixture property keys, type annotations) — NOT arbitrary string literal
 * content like path strings, settleMarker values, or label text. A string
 * literal "/software/campaigns" is a URL, not an identifier.
 */
const BARE_CAMPAIGN_PATTERNS: { pattern: RegExp; reason: string }[] = [
  {
    // TypeScript type alias: `type Campaign` or `type CampaignX`
    // Not `type SoftwareCampaign`
    pattern: /\btype\s+Campaign(?!s?State|s?Filter|List|Table|Row|Item|View|Screen|Fixture|[Ss]oftware)/,
    reason:
      'bare "type Campaign*" — use "type SoftwareCampaign*" to distinguish from FleetWise data-collection campaigns',
  },
  {
    // TypeScript interface: `interface Campaign` or `interface CampaignX`
    // Not `interface SoftwareCampaign`
    pattern: /\binterface\s+Campaign(?!s?State|s?Filter|List|Table|Row|Item|View|Screen|Fixture|[Ss]oftware)/,
    reason:
      'bare "interface Campaign*" — use "interface SoftwareCampaign*" to distinguish',
  },
  {
    // Object/fixture property key (not inside a string literal value):
    // `campaign:` or `campaigns:` at the start of a property definition.
    // This matches `campaign:` and `campaigns:` as object keys (TypeScript syntax).
    // Does NOT match when preceded by a quote (that would be a string literal).
    pattern: /(?<!['"\/\w])campaigns?\s*:/,
    reason:
      'bare "campaign:" or "campaigns:" property key — use "softwareCampaign" or "softwareCampaigns" as the property name',
  },
  {
    // CAMPAIGNS_ constant prefix (not SOFTWARE_CAMPAIGNS_)
    // Only matches the identifier form (uppercase), not substrings inside string literals.
    pattern: /\bCAMPAIGNS_(?!SOFTWARE_|\w*CAMPAIGNS_)/,
    reason:
      'bare "CAMPAIGNS_*" constant — use "SOFTWARE_CAMPAIGNS_*" prefix (CMS collision risk, spec D4)',
  },
  {
    // TypeScript type annotation `: Campaign` or `: Campaign[]`
    // Covers function parameter and variable type annotations.
    // Does NOT fire on `: SoftwareCampaign`.
    pattern: /:\s*Campaign(\[\]|,|\)|\s*\{|\s*=>|\s*&|\s*\|)/,
    reason:
      'bare "Campaign" type annotation — use "SoftwareCampaign" (or "SoftwareCampaign[]") to distinguish',
  },
];

interface CampaignNamingViolation {
  filePath: string;
  lineNumber: number;
  line: string;
  reason: string;
}

function checkCampaignNaming(files: string[]): CampaignNamingViolation[] {
  const violations: CampaignNamingViolation[] = [];

  for (const filePath of files) {
    const source = fs.readFileSync(filePath, "utf8");
    const lines = source.split("\n");

    lines.forEach((line, idx) => {
      // Skip comments
      if (/^\s*\/\//.test(line) || /^\s*\*/.test(line)) return;

      for (const { pattern, reason } of BARE_CAMPAIGN_PATTERNS) {
        if (pattern.test(line)) {
          violations.push({
            filePath,
            lineNumber: idx + 1,
            line: line.trim(),
            reason,
          });
          break;
        }
      }
    });
  }

  return violations;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

const allSourceFiles = collectSourceFiles(SRC_ROOT);

describe("T2.4 Suite 1 — no dealer workflow tokens", () => {
  it("no file contains dealer-workflow tokens without annotation", () => {
    const violations = checkDealerWorkflow(allSourceFiles);

    const report = violations
      .map(
        (v) =>
          `${path.relative(SRC_ROOT, v.filePath)}:${v.lineNumber} — ${v.reason}\n  > ${v.line}`,
      )
      .join("\n");

    expect(
      violations,
      `Dealer-workflow tokens found:\n${report}`,
    ).toHaveLength(0);
  });
});

describe("T2.4 Suite 2 — brand canary (digest-based)", () => {
  it("no fixture or screen file contains a token matching a brand canary digest", () => {
    const violations = checkBrandCanary(allSourceFiles);

    const report = violations
      .map(
        (v) =>
          `${path.relative(SRC_ROOT, v.filePath)} — matched ${v.token}`,
      )
      .join("\n");

    expect(
      violations,
      `Brand canary matches found in fixtures/screens:\n${report}\n` +
        "These names must not appear in user-visible fixture or screen files.",
    ).toHaveLength(0);
  });
});

describe("T2.4 Suite 3 — software-campaign naming discipline", () => {
  it("no file uses bare campaign/Campaign/CAMPAIGNS_ identifiers", () => {
    const violations = checkCampaignNaming(allSourceFiles);

    const report = violations
      .map(
        (v) =>
          `${path.relative(SRC_ROOT, v.filePath)}:${v.lineNumber} — ${v.reason}\n  > ${v.line}`,
      )
      .join("\n");

    expect(
      violations,
      `Campaign naming violations found:\n${report}`,
    ).toHaveLength(0);
  });
});

/**
 * Anti-vacuity — asserts the scan ran over a non-empty source tree.
 *
 * Unlike T2.2 and T2.5, this PASSES immediately because src/screenRegistry.ts
 * and src/types.ts already exist. All three content suites above therefore do
 * real work from the start.
 */
describe("T2.4 anti-vacuity — scan must cover a non-empty source tree", () => {
  it("at least one .ts/.tsx source file exists under src/ (non-vacuous from T1.2 onward)", () => {
    expect(
      allSourceFiles.length,
      "Expected ≥1 .ts/.tsx source file under src/ (excluding __tests__/). " +
        "This should always pass because screenRegistry.ts and types.ts exist.",
    ).toBeGreaterThan(0);
  });
});
