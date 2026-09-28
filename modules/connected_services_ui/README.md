# Connected Services Portal UI

Vite + React 18 frontend for the Connected Services portal — an OEM operations surface
covering connectivity management, software campaigns, population diagnostics,
manufacturing lifecycle, subscriptions, and market compliance.

**32 screens across 8 nav sections.** 29 are backed by a `*.fixture.ts` module; **3 carry
`dataSource: "live"`** (`available-vehicles`, `subscriptions-per-product`, `simulate-vehicle`) and
call a real backend. There is no backend *in this module*, and two live API clients
(`subscriptionsClient.ts` and `simulationClient.ts`).
<!-- verify: grep -cE '^    id: ' src/screenRegistry.ts; grep -c 'dataSource: "live"' src/screenRegistry.ts; sed -n '/^const SECTION_ORDER/,/^\];/p' src/screenRegistry.ts | grep -cE '^\s+"' -->

<!-- verify: grep -cE '^\s+path: "' src/screenRegistry.ts -->
<!-- verify: sed -n '/^const SECTION_ORDER/,/^\];/p' src/screenRegistry.ts | grep -cE '^\s+"' -->

## Install

First-time install (generates `package-lock.json`):

```bash
npm install --legacy-peer-deps
```

Subsequent installs (reproducible, uses the lockfile):

```bash
npm ci --legacy-peer-deps
```

The `--legacy-peer-deps` flag is required because vitest v4 carries a peer dependency
on `@vitest/coverage-v8` that npm 10's default strict resolution does not satisfy
automatically. The flag does not bypass security checks; it mirrors npm 6 resolution
semantics for this specific peer constraint.

## Local development

```bash
npm start
```

Serves on port 5178. This is the fastest iteration loop — instant HMR, no deploy.

Two things to know about running locally:

1. **`/runtime-config.js` is served by a dev-only Vite middleware**
   (`vite.config.ts` → `devRuntimeConfig()`), carrying deliberately non-functional
   placeholder Cognito values. The sign-in screen renders, but the Federate redirect
   goes nowhere locally. The plugin declares `apply: "serve"`, so it contributes
   nothing to `vite build` — see `src/__tests__/devRuntimeConfig.test.ts`, which pins
   that property.
2. **You will land on the sign-in screen**, because `getSession()` finds no token.
   To walk the screens locally, seed an authorized session in the browser console —
   see `uat-checklist.md` for the exact one-liner.

## Build

Output goes to `dist/` — this is what `ConnectedServicesUiStack` deploys via
`aws_s3_deployment.BucketDeployment`.

```bash
npm run build
```

## Type-check

```bash
npx tsc --noEmit
```

`tsc` and the bundler do not fail on the same set of programs. `@ts-ignore` satisfies
`tsc` while rollup still resolves every dynamic import, and Vite does not type-check at
all — so both commands are load-bearing and neither substitutes for the other. Both
directions of that gap were hit during this module's build.

## Test

```bash
npm run test
```

**792 tests across 61 files.**

<!-- verify: npx vitest run --silent 2>&1 | grep -E 'Test Files|Tests ' | tail -2 -->

## Architecture

### The screen registry is the single source of truth

`src/screenRegistry.ts` holds all 32 entries. Nav sections, the route table in
`App.tsx`, page chrome in `commons/pageConfig.ts`, and the dead-link guard all derive
from it. There is no second list of screens anywhere — a guard asserts the nav item
count equals the registry's entry count, so a hardcoded sidebar fails the build.

Each entry carries a unique `settleMarker`: a string that appears in that screen's own
rendered body and nowhere else. Guards assert on the marker rather than on "content is
non-empty", because shell chrome renders synchronously and satisfies a non-empty
predicate before any lazy chunk has settled.

### Three-layer provenance enforcement

Every displayed value carries a `live | simulated | absent` marker (spec D5).
`measured` is valid in `fleet_intelligence` and is **not** valid here.

1. **Runtime** — `assertProvenance()` throws during render if a marker is missing or
   unrecognised. `src/__tests__/ProvenanceField.test.tsx` asserts the throw.
2. **Fixture shape** — `provenanceFixtures` scans every `*.fixture.ts` and requires each
   leaf to be a `{value, provenance}` pair. The five `Tier2Artifact` envelope fields
   (`computed_at`, `confidence`, `evidence`, `agent_version`, `inputs_hash`) are
   allowlisted: they describe a conclusion rather than being displayed values.
3. **Render path** — `provenanceRender` fails when a `ProvenanceValue` is *displayed*
   unwrapped. `{x.value}` as rendered content is a violation; reading `.value` in a
   conditional or a computed non-display prop is not.

### Tier 2 artifact contract, adopted on fixtures

Four surfaces render the output shape of a Tier 2 agent — Signal Detection, the
diagnosis hypothesis list, the "why is this vehicle offline?" root-cause panel, and
Quality Signals. Their fixtures carry the full artifact contract now, including fields
nothing reads yet, so wiring a real agent later is a data-source swap rather than a
redesign. See `~/.kiro/steering/agentic-tiers.md`.

## Guards

30 guard files. Each carries an anti-vacuity companion asserting it scanned a non-empty
set — a guard that passes on an empty tree is a false green.

This count is **not** the screen count and does not move with it — it is the number of test files
under `src/__tests__/`. It read 23 while the true value was 30, and a 2026-09-13 rollup pass
"corrected" it to 26 by tracking the screen count instead of running the command on the next line.
Run the command.

<!-- verify: ls src/__tests__/*.test.ts* | wc -l -->

| Guard | Catches |
|---|---|
| `registryCompleteness` P1–P4 | a route that renders the wrong screen, or no screen; duplicate/substring markers |
| `clickPath` | the six-hop Goal-3 journey breaking at any hop, driven through the real router |
| `fixtureReferentialIntegrity` | a cross-screen link pointing at a VIN another screen has no record of |
| `deadLinks` | a nav href or in-screen navigation target that resolves to no registry path |
| `provenanceFixtures` / `provenanceRender` | an unlabelled value in a fixture or on screen |
| `contentBoundary` | dealer-workflow tokens, brand canaries (sha256 digests, never plaintext), bare `campaign` identifiers |
| `commonsIsolated` | a fixture or API import leaking into `commons/` |
| `cssScoped` | an unscoped CSS selector restyling the whole app |
| `placeholderInventory` | a screen still carrying scaffolding while marked active |
| `scaffoldingExpiry` | `@ts-ignore` scaffolding outliving the missing modules it covers |
| seam guards 1, 2, 4, 7 | a deterministic seam being crossed — approval-as-modal, artifact leaking into the target preview, a hardcoded halt threshold, an unauthorized hold |

**A green guard column is evidence, not proof.** Four guards in this module were
defective while reporting exactly the verdict expected of them; three of those were red,
and red reads as working. What upgrades a guard from claim to finding is observing it
fail on a deliberately introduced defect. Where that has been done it is recorded in the
relevant `issues/` report or the spec's `decisions.md`.

## Runtime configuration

`/runtime-config.js` is deployed by `ConnectedServicesUiStack` via a separate
`BucketDeployment` with `no-cache` headers. It sets **`window.runtimeConfig`** at boot
with:

- `cognitoUserPoolId`
- `cognitoClientId`
- `cognitoRegion`
- `cognitoDomain`
- `apiEndpoint`
- `callbackOrigin`

Reading from `window` means none of these values are baked into the Vite bundle —
redeploying only `runtime-config.js` rotates credentials without a rebuild.

`getRuntimeConfig()` **throws** when the global is absent, deliberately: a missing config
fails loudly at boot rather than as a confusing auth failure later. Do not add a
fallback (DMS lesson F6); `src/__tests__/env.test.ts` asserts the throw.

> Earlier revisions of this file documented the global as `window.__RUNTIME_CONFIG__`,
> which was never the name the code reads. Corrected 2026-09-04.

## Authentication and authorization

**Sign-in** uses the same Cognito user pool and the same "Sign in with Amazon (Federate)"
button as the primary CMS portal, via the Cognito Hosted UI:

- authorization-code redirect to `https://<cognitoDomain>/oauth2/authorize` with
  `identity_provider=AmazonFederate`
- callback at `/auth/callback`, exchanged at `/oauth2/token`
- `access_token` + `id_token` in `sessionStorage`; `preAuthUrl` restores the originally
  requested route

The flow carries **RFC 7636 PKCE (S256) and an OAuth `state` CSRF token**, both single-use
in `sessionStorage`. `state` is what binds a callback to a sign-in that began in this
browser — without it, `handleOAuthCallback` would redeem any `code` placed in the URL.

> This reverses a decision recorded earlier for T9.1, which matched CMS's
> `SimpleAuthProvider` exactly — including its lack of PKCE — on the grounds that
> diverging from a known-good path was risky while nobody was available to UAT a redirect
> flow. That call was overruled by explicit direction once the flow was working, and the
> hardening landed in `2026-09-05-cms-connected-services-auth-integration`. The earlier
> reasoning is preserved in this spec's `decisions.md`; treat this section as current.

CMS and DMS share this user-pool client and do **not** yet carry PKCE, so the client-level
weakness persists for them.

**Authorization** is fail-closed. `getSession()` decodes the `id_token` and reads
`cognito:groups`; an absent, expired, or malformed token yields no session. A caller
lacking the `connected-services` group gets an explicit `Unauthorized` page, never a
blank one.

> **Portal is fully operational end-to-end.** The callback origin is registered on the
> shared user-pool client via `connectedServicesUiCallbackOrigin` context flag in
> `deployment/Makefile` (GUARD_CTX_FLAGS). The flag is consumed by `ui_stack.py`'s
> registry on every CMS deploy. Authorization uses hand-rolled Cognito Hosted UI with
> RFC 7636 PKCE (S256) and an OAuth `state` CSRF token — no Amplify, no third-party
> auth library. There is no legacy client-side session mechanism (no URL parameter, no
> `localStorage` key); `src/__tests__/noUrlParamSession.test.ts` asserts that in three
> layers, including a scan of the built bundle.

## Deploy

```bash
cd ../../deployment
make deploy-connected-services DEPLOYMENT_STAGE=staging
```

Requires the Cognito and custom-domain values in `config/staging.env` **and**
`WAF_WEB_ACL_ARN` for the CloudFront web ACL attachment. The target passes all five
required context keys plus `wafWebAclArn`.

The portal is live on staging at the domain configured via `CS_UI_DOMAIN_NAME`
in `config/staging.env` (an internal Amazon-network URL — see that file for the
concrete value; not repeated here so this README stays clean of internal hostnames
per `~/.kiro/steering/public-mirror-publish.md`).
`ConnectedServicesUiStack` reads `wafWebAclArn` the same way `cms-<stage>-ui` does —
the same key, deliberately, so both distributions attach the same `cms-<stage>-ui-waf`
WebACL — and defaults `Enabled=false` in IaC so a re-enable is a declared state
change, not a routine deploy side effect. See § D6 and § D7 of the spec.

An HTTP 200 from the CloudFront URL measures CloudFront, not this application. Every SPA
route returns the same `index.html` whether or not the app can initialise, so 200 is
necessary and nowhere near sufficient — the UAT checklist in
`.kiro/specs/2026-09-05-cms-connected-services-auth-integration/uat-checklist.md`
records what the application-layer observations must be.

## What is deliberately not built

- **Security Monitor** (`/software/security`) is a placeholder by decision — the route
  exists and is reachable, and states its own absence in words.
- **Service denial** and **stop-ship holds** are client-side state only. Nothing
  persists; no API is called.
- **No backend.** Every screen reads a fixture.

## Spec

`.kiro/specs/2026-09-04-cms-connected-services-portal-v2/`

Supersedes `2026-09-03-cms-connected-services-portal`, which shipped this module's
infrastructure and two screens.
