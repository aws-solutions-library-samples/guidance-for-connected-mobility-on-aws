// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * devRuntimeConfig.test.ts — the dev-only /runtime-config.js shim.
 *
 * `index.html` loads `<script src="/runtime-config.js">`. In a deployed
 * environment that file comes from `ConnectedServicesUiStack`'s dedicated
 * no-cache BucketDeployment. Nothing produced it locally, and before T9.1 that
 * was harmless because no code read `window.runtimeConfig` during boot.
 *
 * T9.1 made an unauthenticated visitor render `SignIn`, which calls
 * `getRuntimeConfig()` to build the Hosted UI authorize URL — so a missing
 * config turned `npm start` into a boot-time throw, breaking local UI
 * iteration. `vite.config.ts`'s `devRuntimeConfig()` plugin serves the file on
 * the dev server only.
 *
 * This suite pins the two properties that make that shim safe rather than
 * merely convenient:
 *
 *   1. It CANNOT reach a production artifact (`apply: "serve"`).
 *   2. It actually serves a body that defines `window.runtimeConfig`.
 *
 * Property 1 is the one worth guarding. The tempting alternative was a
 * `public/runtime-config.js`, which Vite copies into `dist/` — that would make
 * a placeholder Cognito config compete with the real deployed values, decided
 * by upload ordering. A portal silently running on placeholder auth config is
 * DMS lesson F6. If someone later "simplifies" this plugin into a public file
 * or drops `apply`, this suite fails.
 */

import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const MODULE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const VITE_CONFIG = resolve(MODULE_ROOT, "vite.config.ts");

describe("dev-only runtime-config shim", () => {
  const source = readFileSync(VITE_CONFIG, "utf8");

  it("anti-vacuity: vite.config.ts is readable and registers the plugin", () => {
    expect(source.length).toBeGreaterThan(0);
    expect(
      source.includes("devRuntimeConfig()"),
      "vite.config.ts must register devRuntimeConfig() in its plugins array"
    ).toBe(true);
  });

  it('is gated to the dev server with apply: "serve"', () => {
    // Without this, the middleware would be registered for `vite build` too.
    expect(
      /apply:\s*["']serve["']/.test(source),
      'devRuntimeConfig must declare apply: "serve" so it cannot affect a production build'
    ).toBe(true);
  });

  it("serves a body that assigns window.runtimeConfig", () => {
    expect(source).toContain("window.runtimeConfig =");
    expect(source).toContain("/runtime-config.js");
  });

  it("carries only non-functional placeholder credentials", () => {
    // The local values must not be real. A real client id here would be both a
    // published credential and a way for local dev to appear to work while
    // pointing at the shared pool.
    expect(source).toContain("<local-dev-pool-id>");
    expect(source).toContain("<local-dev-client-id>");
    // The live staging client id must not appear anywhere in the config file.
    expect(source).not.toMatch(/6fig3o3ndv7a0j29irl0lm4qi5/);
    // Nor the live Hosted UI domain.
    expect(source).not.toContain("connected-mobility-staging.auth");
  });

  it("does NOT introduce a public/runtime-config.js, which would ship in dist/", () => {
    expect(
      existsSync(resolve(MODULE_ROOT, "public", "runtime-config.js")),
      "public/runtime-config.js must not exist — Vite copies public/ into dist/, " +
        "so it would compete with the stack's BucketDeployment and could ship " +
        "placeholder Cognito config to a real environment (DMS lesson F6)"
    ).toBe(false);
  });
});
