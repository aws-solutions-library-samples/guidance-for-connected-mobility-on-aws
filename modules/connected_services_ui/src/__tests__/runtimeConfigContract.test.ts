// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * RuntimeConfig contract guard.
 *
 * Asserts that the `RuntimeConfig` interface in src/env.ts matches the keys that
 * ConnectedServicesUiStack actually writes into runtime-config.js.
 *
 * WHY THIS EXISTS
 * ---------------
 * `getRuntimeConfig()` validates only that `window.runtimeConfig` exists — it does not
 * check individual fields. So a name mismatch between the TypeScript interface and the
 * CDK stack is invisible: the type promises `string`, the value is `undefined`, the build
 * passes, the tests pass, and CloudFront returns HTTP 200. It surfaces later as an
 * undefined URL in a fetch, or an auth call with a missing client id.
 *
 * That is exactly what happened: `apiEndpoint` was declared here while the stack emitted
 * `connectedServicesApiEndpoint`. Nothing read the field, so nothing broke — the defect
 * was latent, waiting for the API to be wired.
 *
 * This is the DMS F6 lesson (runtime-config.js never injected → sign-in failure) turned
 * into an executable check instead of a comment. Per that spec's finding: on a SPA, HTTP
 * 200 measures CloudFront, not the application.
 *
 * BOTH CORPORA ARE ASSERTED NON-EMPTY. A regex that silently matches nothing would make
 * every comparison below vacuously true, which is the failure mode called out in this
 * spec's T7.1 caveat — a green guard is evidence, not proof.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// ── Source locations ──────────────────────────────────────────────────────────

const ENV_TS = resolve(__dirname, "../env.ts");
const STACK_PY = resolve(
  __dirname,
  "../../../../deployment/stacks/connected_services_ui_stack.py"
);

// ── Parse the RuntimeConfig interface ─────────────────────────────────────────

interface InterfaceField {
  readonly name: string;
  readonly optional: boolean;
}

function parseRuntimeConfigInterface(source: string): InterfaceField[] {
  const block = /export interface RuntimeConfig\s*\{([\s\S]*?)\n\}/.exec(source);
  if (!block) {
    throw new Error("Could not locate `export interface RuntimeConfig { … }` in env.ts");
  }
  const body = block[1];

  // Strip block comments and line comments so doc text cannot be read as a field.
  const stripped = body.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");

  const fields: InterfaceField[] = [];
  const fieldRe = /^\s*(?:readonly\s+)?([A-Za-z_$][\w$]*)(\?)?\s*:/gm;
  let m: RegExpExecArray | null;
  while ((m = fieldRe.exec(stripped)) !== null) {
    fields.push({ name: m[1], optional: m[2] === "?" });
  }
  return fields;
}

// ── Parse the keys the stack emits into runtime-config.js ─────────────────────

function parseStackEmittedKeys(source: string): string[] {
  // The stack builds runtime-config.js as a Python format string of the shape:
  //   '  "cognitoUserPoolId": "{pool_id}",\n'
  // Capture the JSON key from each such line.
  const keyRe = /'\s*"([A-Za-z_$][\w$]*)"\s*:\s*"\{[^}]*\}"/g;
  const keys: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = keyRe.exec(source)) !== null) {
    keys.push(m[1]);
  }
  return keys;
}

/**
 * Extract the Python expression (RHS of assignment) for a stack local variable.
 * Used to pin the value shape of emitted runtime-config fields — a presence-only
 * assertion is the recurring adjacent-to-the-property defect in this spec.
 */
function parseStackVariableValue(source: string, varName: string): string {
  // Match `_<varName>: str = <expression>` until the end of the statement.
  // Handles single-line f-string assignments.
  const re = new RegExp(`_${varName}\\s*:\\s*str\\s*=\\s*(.+?)(?:\\n|$)`);
  const m = re.exec(source);
  return m ? m[1].trim() : "";
}

const envSource = readFileSync(ENV_TS, "utf8");
const stackSource = readFileSync(STACK_PY, "utf8");

const interfaceFields = parseRuntimeConfigInterface(envSource);
const emittedKeys = parseStackEmittedKeys(stackSource);

// ── Corpus sanity — a silent zero-match would make everything below vacuous ────

describe("RuntimeConfig contract — corpus is non-empty", () => {
  it("parsed a non-trivial number of interface fields", () => {
    expect(interfaceFields.length).toBeGreaterThanOrEqual(5);
  });

  it("parsed a non-trivial number of emitted runtime-config keys", () => {
    expect(emittedKeys.length).toBeGreaterThanOrEqual(5);
  });

  it("positive control — a known field is present in the parsed interface", () => {
    expect(interfaceFields.map((f) => f.name)).toContain("cognitoClientId");
  });

  it("positive control — a known key is present in the parsed stack output", () => {
    expect(emittedKeys).toContain("cognitoClientId");
  });

  it("negative control — the parser does not invent fields", () => {
    expect(interfaceFields.map((f) => f.name)).not.toContain("thisFieldDoesNotExist");
    expect(emittedKeys).not.toContain("thisFieldDoesNotExist");
  });
});

// ── The contract itself ───────────────────────────────────────────────────────

describe("RuntimeConfig contract — interface matches deployed runtime-config.js", () => {
  it("every REQUIRED interface field is emitted by the stack", () => {
    // A required field that the stack never writes is a type-level lie: consumers get
    // `undefined` where the signature promises a value.
    const required = interfaceFields.filter((f) => !f.optional).map((f) => f.name);
    const missing = required.filter((name) => !emittedKeys.includes(name));
    expect(missing).toEqual([]);
  });

  it("every emitted key is declared on the interface", () => {
    // An emitted key with no declaration is dead payload, or a field consumers must
    // reach via a cast — both mean the contract is not the source of truth.
    const declared = interfaceFields.map((f) => f.name);
    const undeclared = emittedKeys.filter((key) => !declared.includes(key));
    expect(undeclared).toEqual([]);
  });

  it("the Cognito fields sign-in depends on are all emitted", () => {
    // signInWithPassword() reads exactly these three. If any is absent from
    // runtime-config.js, password sign-in fails at runtime with a config error.
    for (const key of ["cognitoUserPoolId", "cognitoClientId", "cognitoRegion"]) {
      expect(emittedKeys).toContain(key);
    }
  });

  it("callbackOrigin is emitted — the Federate redirect_uri depends on it", () => {
    expect(emittedKeys).toContain("callbackOrigin");
  });

  it("does not declare a bare `apiEndpoint` — the stack emits the qualified name", () => {
    // Regression pin for the specific drift this guard was written to catch.
    const declared = interfaceFields.map((f) => f.name);
    expect(declared).not.toContain("apiEndpoint");
    expect(declared).toContain("connectedServicesApiEndpoint");
  });

  it("simulationProductRuleName is emitted with the OEM2 product rule value (T7.4a value-shape pin)", () => {
    // A presence-only assertion here would be the spec's recurring
    // adjacent-to-the-property defect: a test that only checks the key exists
    // cannot catch the rule name being set to an arbitrary string that the
    // Lambda's allowlist rejects.
    //
    // Pin the shape: the value must be built from `stage` and must embed the
    // expected product rule segment `cs_product_meridian_ev_rule`.
    const ruleExpr = parseStackVariableValue(stackSource, "simulation_product_rule_name");
    expect(ruleExpr).not.toBe("");
    // The value expression must reference `{stage}` (as an f-string param)
    // rather than baking in a literal stage name.
    expect(ruleExpr).toContain("{stage}");
    // The product rule segment must match exactly — this is what the Lambda's
    // allowlist pins (T7.2, test_simulation_lambda.py:5306).
    expect(ruleExpr).toContain("cs_product_meridian_ev_rule");
  });
});
