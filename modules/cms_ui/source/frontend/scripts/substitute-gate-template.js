#!/usr/bin/env node
// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// postbuild script — substitutes staging auth-gate placeholder values in build/error/403.html
//
// Usage (automatic via npm postbuild hook):
//   CMS_GATE_BINDLE_ID=<bindle-id> CMS_GATE_SECRET_ARN=<arn> yarn build
//
// When either env var is unset, emits a boxed warning and exits 0 (fail-open by design).
// See the staging auth-gate runbook in docs/ (§8) for context.
//
// Spec: .kiro/specs/2026-08-04-gate-403-substitution/spec.md  (see .kiro/ for the full slug)

import fs from 'node:fs';
import path from 'node:path';

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const GATE_FILE = path.join(process.cwd(), 'build', 'error', '403.html');
const RUNBOOK_REF = 'the staging auth-gate runbook in docs/ (§8)';

// Regex helpers — match <div ... id="ID" ...>CONTENT</div>
// Uses a non-greedy content match and handles attributes in any order.
// Does NOT match across newlines inside the tag itself, but does allow the
// content to span one line (which covers the current 403.html template).
function buildIdRegex(id) {
  // Matches: <div[attrs] id="ID"[attrs]>CONTENT</div>
  // Also matches:  <div[attrs] id='ID'[attrs]>CONTENT</div>
  return new RegExp(
    `(<div[^>]*\\bid=["']${id}["'][^>]*>)([\\s\\S]*?)(</div>)`,
    'i'
  );
}

// ---------------------------------------------------------------------------
// Boxed warning helper
// ---------------------------------------------------------------------------

function printBoxedWarning(lines) {
  const width = Math.max(...lines.map((l) => l.length)) + 4;
  const bar = '─'.repeat(width);
  process.stderr.write(`\n┌${bar}┐\n`);
  for (const line of lines) {
    const pad = ' '.repeat(width - line.length - 2);
    process.stderr.write(`│  ${line}${pad}  │\n`);
  }
  process.stderr.write(`└${bar}┘\n\n`);
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const bindleId = process.env.CMS_GATE_BINDLE_ID ?? '';
const secretArn = process.env.CMS_GATE_SECRET_ARN ?? '';

if (!bindleId || !secretArn) {
  // Fail-open: warn but do not break the build.
  const missing = [];
  if (!bindleId) missing.push('CMS_GATE_BINDLE_ID');
  if (!secretArn) missing.push('CMS_GATE_SECRET_ARN');

  printBoxedWarning([
    `⚠️  WARNING: Gate template substitution SKIPPED`,
    ``,
    `Missing env var(s): ${missing.join(', ')}`,
    ``,
    `File left un-substituted: build/error/403.html`,
    `The deployed /error/403.html will contain the placeholder values.`,
    ``,
    `To fix: add cmsGateBindleId and cmsGateSecretArn to`,
    `deployment/cdk.context.json and re-run yarn build.`,
    `See: ${RUNBOOK_REF}`,
  ]);

  process.exit(0);
}

// Both vars are set — perform substitution.

if (!fs.existsSync(GATE_FILE)) {
  process.stderr.write(
    `\n❌ ERROR: Gate template not found: ${GATE_FILE}\n` +
    `Run yarn build first so vite copies public/error/403.html into build/.\n\n`
  );
  process.exit(1);
}

let html = fs.readFileSync(GATE_FILE, 'utf8');

// Replace #bindlediv content
const bindleRegex = buildIdRegex('bindlediv');
const secretRegex = buildIdRegex('secretArn');

const bindleMatch = bindleRegex.test(html);
const secretMatch = secretRegex.test(html);

if (!bindleMatch && !secretMatch) {
  // Spec R5: a restructured template must not silently substitute nothing.
  process.stderr.write(
    `\n❌ ERROR: Neither #bindlediv nor #secretArn found in ${GATE_FILE}\n` +
    `The 403.html template may have been restructured. Update the element IDs\n` +
    `or restore the expected <div id="bindlediv"> and <div id="secretArn"> elements.\n\n`
  );
  process.exit(1);
}

// Shape-validate before inserting. These values land as HTML text-node content in a page
// served on the auth path, so a hostile or fat-fingered cdk.context.json value such as
// `</div><script>...</script><div>` would become script in that page. Defence in depth:
// cdk.context.json is operator-controlled and gitignored, but "operator-controlled" is not
// the same as "validated", and the cost of checking is one regex.
const SHAPES = {
  CMS_GATE_BINDLE_ID: /^amzn1\.bindle\.resource\.[A-Za-z0-9._-]+$/,
  CMS_GATE_SECRET_ARN: /^arn:aws:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@-]+$/,
};
for (const [name, value] of [['CMS_GATE_BINDLE_ID', bindleId], ['CMS_GATE_SECRET_ARN', secretArn]]) {
  if (!SHAPES[name].test(value)) {
    console.error(
      `[substitute-gate-template] ${name} does not match its expected shape.\n` +
      `  Refusing to substitute rather than inserting an unvalidated value into a page on\n` +
      `  the auth path. Check the key in deployment/cdk.context.json.\n` +
      `  (value not echoed)`
    );
    process.exit(1);
  }
}

// Perform replacements — regex is rebuilt fresh (test() advances lastIndex on /g flags;
// we use plain new RegExp without /g so there is no lastIndex issue).
html = html.replace(buildIdRegex('bindlediv'), `$1${bindleId}$3`);
html = html.replace(buildIdRegex('secretArn'), `$1${secretArn}$3`);

// Post-substitution assertion (spec R5): verify both values are now present.
if (!html.includes(bindleId) || !html.includes(secretArn)) {
  process.stderr.write(
    `\n❌ ERROR: Substitution produced no change in ${GATE_FILE}\n` +
    `Expected #bindlediv and #secretArn to carry the supplied values after replacement.\n` +
    `Check that the element IDs are present and the regex matched correctly.\n\n`
  );
  process.exit(1);
}

fs.writeFileSync(GATE_FILE, html, 'utf8');

process.stdout.write(
  `✅ Gate template substituted: build/error/403.html\n` +
  `   #bindlediv  → [redacted]\n` +
  `   #secretArn  → [redacted]\n`
);
