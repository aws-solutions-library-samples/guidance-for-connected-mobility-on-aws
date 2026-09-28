// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Application entry point.
 *
 * ── DELETED 2026-09-05: the `?session=` demo-session bootstrap ──────────────
 * Spec: .kiro/specs/2026-09-05-cms-connected-services-auth-integration/ § D2, D3
 * Security review Cycle 1, Critical 1.
 *
 * This file used to run a `bootstrapDemoSession()` IIFE before React mounted. It
 * parsed `?session=<alias>:<group1>,<group2>` from `location.search`, wrote the
 * caller-supplied value to `localStorage.__DEMO_SESSION__`, and logged the alias and
 * groups to the console. It shipped in the built bundle.
 *
 * It is the mechanism behind Talos DAST Critical `69d7f6e6` (`UnauthWebService`).
 *
 * When `auth.ts` was rewritten to read a real Cognito id-token, that deleted the
 * *reader* — `getSession()` no longer consults `localStorage`, so there was no live
 * bypass. But the *parser* survived here, one line away from being load-bearing again:
 * any future change reintroducing a `localStorage` tier to `getSession()` would have
 * reopened the finding instantly, against data an anonymous caller had already planted.
 *
 * Worth knowing why it was missed: the rewrite was reviewed at `auth.ts`, and this file
 * is listed in `vite.config.ts`'s coverage `exclude`, so neither the tests nor the
 * coverage report pointed at it. A guard scoped to one module cannot see a mechanism
 * that moved to another. § D3 property 3 is now asserted against the built bundle, not
 * only against `getSession()`.
 *
 * Do not reintroduce a session-priming path here, behind a build flag or otherwise
 * (§ D2: deletion, not deprecation).
 * ───────────────────────────────────────────────────────────────────────────
 */

// Install the simulation-API fetch interceptor before any component mounts.
// See src/auth/fetchInterceptor.ts for scope and rationale.
import "./auth/fetchInterceptor";

import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found in index.html");
}

const root = createRoot(container);
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
