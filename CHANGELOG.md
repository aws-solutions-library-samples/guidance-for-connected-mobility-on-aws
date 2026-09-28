# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

## v0.4.1 — 2026-09-28

### Fixed

- **Public-mirror `lint.yml` frontend typecheck ratchet no longer fails at yarn bootstrap.**
  The frontend's `.yarnrc.yml` had been declaring `yarnPath: .yarn/releases/yarn-4.6.0.cjs`
  while `.publish-exclude` strips `.yarn/` from every public release, so the mirror shipped
  a config pointing at a file the same publish commit had removed. Latent since the
  exclusion was added; became visible at v0.4.0 when `lint.yml` gained a
  `corepack enable` + `yarn install --immutable` step and the mirror ratchet job failed
  with `ENOENT` on `yarn-4.6.0.cjs`. `.yarnrc.yml` now omits `yarnPath` entirely —
  `package.json`'s `"packageManager": "yarn@4.6.0"` already pins the exact version, and
  Corepack downloads Yarn 4.6.0 from `repo.yarnpkg.com` on demand, no on-disk binary
  required. Internal deploy is unaffected (same Corepack + `packageManager` path resolves
  to the still-tracked on-disk binary, which is simply no longer referenced). Verified
  behaviourally against a real publish archive: with `.yarn/` stripped, `corepack enable`
  + `yarn install --immutable` succeeds in the staged frontend dir. Mutation test:
  re-adding `yarnPath` reproduces the exact CI error. Aligns with steering doctrine on
  managing package-manager versions via Corepack rather than on-disk binaries. Commit
  `4dcc1308`. Issue: `issues/2026-09-28-mirror-yarn-lint-red/`.

- **Public-mirror publish CI (`.gitlab-ci.yml` `publish_to_github` job) — HTTPS token URL
  form and fail-fast credential probe.** The three `github.com` push URLs in the CI job
  placed `${GITHUB_TOKEN}` in the *username* position with no password
  (`https://${GITHUB_TOKEN}@github.com/…`), which GitHub's HTTPS endpoint rejects; git
  then fell through to prompting for a password on a runner with no TTY and hung for
  ~4 minutes before dying with `No such device or address`. URLs now use GitHub's
  canonical fine-grained-PAT form, `https://x-access-token:${GITHUB_TOKEN}@github.com/…`,
  which carries the token in the *password* position. The existing `rules` guard
  asserted only presence (`$GITHUB_TOKEN == null`) and let a dead token pass, wasting
  the ~4 min of staging + scan before failing at push. A fail-fast `git ls-remote`
  probe now runs in `before_script`, before the staging work, with
  `GIT_TERMINAL_PROMPT=0` so a rejected credential exits in seconds with a legible
  error rather than a no-TTY prompt hang. The `rules` guard is unchanged. Publish
  content itself is unaffected.

## v0.4.0 — 2026-09-28

### Known Limitations

- **The FWE simulation host runs out of kernel memory within days, and UDS fault injection
  stops.** A running fwe-agent leaks CAN ISO-TP socket references (about 185 an hour, measured
  2026-09-28 on a t4g.medium). After about 2.5 days the host's unreclaimable kernel memory fills,
  DTC fetches fail with `Cannot allocate memory`, and injected faults produce no DTC row, while
  ECS still reports the host and its tasks as healthy. Workaround: replace the FWE host (terminate
  the instance; its Auto Scaling group launches a fresh one) before a demo that injects faults.
  Issue: `issues/2026-08-04-can-rcvlist-orphaned-filter-leak/`.

### Breaking

- **2026-09-04 — CMS is now single-environment; the prod stacks are torn down.**
  Spec: `.kiro/specs/2026-09-04-cms-prod-teardown/` (env-collapse child of the
  `2026-09-02-env-collapse` portfolio initiative). Following the 2026-09-02
  domain cutover, the CMS URL now points at the was-staging CloudFront
  distribution in `us-west-2`. The `cms-prod-*` CFN stacks in `us-east-1`, the
  former prod CloudFront distribution, 41 retained DynamoDB tables, and 6 S3
  buckets were removed. The internal SSO edge gate that had fronted staging
  was removed as part of the same cutover. Operators tracking `main` should
  now expect one deploy target (staging) rather than a staging/prod pair.

- **2026-09-05 — The Virtual Fleet Operator (VFO) Tier 2 agent is retired.**
  Spec: `.kiro/specs/2026-09-05-cms-vfo-teardown/`. The `cms-{stage}-bedrock-agents`
  stack (five Bedrock Agents + aliases + `VfoToolsLambda` + retained KB bucket in
  `us-west-2`), plus a Tier 2 residue in `us-east-2` (9 Lambdas, 3 EventBridge
  rules, a Step Function, an EventBridge Pipe, and two artifact tables) are all
  destroyed. The `DEPLOY_BEDROCK_AGENTS=true` gate is retired. The fleet-view
  surface that VFO's `Fleet Command Center` used to serve is now rendered
  deterministically by `services/fleet_intelligence/` (see "Added" below). The
  web-UI ChatAgent's Assistant surface continues to route via CVX AgentCore —
  the VFO retirement is Tier 2 only.

- **2026-09-25 — `DEPLOY_FLEETWISE` env-var guard removed; FleetWise is now unconditional.**
  Spec: `.kiro/specs/2026-09-25-cms-fleetwise-consolidation/` (Option B, revised
  after a Group 1 audit). The `if os.environ.get('DEPLOY_FLEETWISE') == 'true':`
  guard in `app.py` is gone; `FweTelemetryStack` instantiates on every deploy.
  Two sibling Makefile targets (`start-flink-stack-apps` starting 9 apps and
  `start-flink-campaign-sync-processor` starting the 10th) are replaced with a
  single `start-flink-apps` target. `phase-streaming` collapses from four steps
  to three. The `cms-{stage}-fleetwise` CFN stack name and contents are
  unchanged — no destructive CFN operation.

- **2026-08-29 — Vehicle onboard/offboard classification is now vehicle-owned.**
  Spec: `.kiro/specs/2026-08-29-cms-vehicle-classification/`. Every vehicle now
  carries a `dataSource` attribute (`vehicle-telemetry` for onboard FWE-path,
  `cloud-telemetry` for OEM cloud connectors), written by the create endpoint
  the vehicle went through rather than derived at read time. Supersedes the
  cert-follows-model design decision that *"every CMS-native vehicle is a
  `vehicle-telemetry` vehicle by construction"* — the OEM1 admin-add-vehicle
  handler now writes `dataSource` on the rows it creates, so the classification
  survives regardless of ingress path. Pre-cutover rows with `dataSource == null`
  (356 of 357 vehicles live at spec authoring) require reclassification.

- **2026-08-28 — `POST /api/v1/vehicles` now requires `modelManifestName` and rejects `createCertificate`.**
  Spec: `.kiro/specs/2026-08-28-cms-cert-follows-model/`.
  The `POST /api/v1/vehicles` request body now requires a new field `modelManifestName` (string, required, max 128 chars)
  identifying the model manifest under which to provision the vehicle's IoT certificate. The field is resolved against
  the `cms-{stage}-model-manifest` DynamoDB table; requests referencing non-existent or non-ACTIVE manifests return
  HTTP 400 with an actionable error message. Clients must seed at least one ACTIVE model manifest with a non-empty
  `decoderManifestRef` before creating vehicles — see `docs/tech.md` § 5 "Operator prerequisite for vehicle creation" for setup steps.
  
  The `createCertificate` field (previously accepted but ignored) is now explicitly retired. Requests sending
  `createCertificate` in any form (true, false, or any value) return HTTP 400 with message "The `createCertificate` field is retired.
  Vehicles now auto-provision certificates based on the model manifest."

### Documentation

- **2026-08-28 — Positioned CMS as the documented successor path for AWS IoT FleetWise, per AWS's own availability-change notice.**
  Spec: `.kiro/specs/2026-08-28-cms-fleetwise-successor-docs/`.
  README and the CMS Implementation Guide now open with a short "Migrating from AWS IoT FleetWise" block that **quotes AWS's availability-change page verbatim** —
  *"The Guidance for Connected Mobility on AWS provides guidance on how to develop
  and deploy modular services for connected mobility solutions that can be used to
  achieve equivalent capabilities as AWS IoT FleetWise."* — and links to the
  [availability-change notice](https://docs.aws.amazon.com/iot-fleetwise/latest/developerguide/iotfleetwise-availability-change.html).
  Both surfaces also disambiguate **"FleetWise Edge" / "FWE"** (the open-source
  agent at [`github.com/aws/aws-iot-fleetwise-edge`](https://github.com/aws/aws-iot-fleetwise-edge),
  built from source in `deployment/ecr/cms-fwe-agent/Dockerfile`) from the AWS IoT
  FleetWise managed service — a customer arriving from AWS's notice could
  otherwise reasonably conclude CMS depends on the service they can no longer sign
  up for. It does not: no stack imports `aws_cdk.aws_iotfleetwise`, no template
  creates `AWS::IoTFleetWise::*`, and nothing calls `boto3.client("iotfleetwise")`.
  New: [`docs/migrating-from-fleetwise.md`](./docs/migrating-from-fleetwise.md)
  carries the concept map, with both gaps disclosed plainly — **data destinations**
  (FleetWise offers S3 / Timestream / MQTT; CMS pipes IoT Core → MSK → Flink →
  DynamoDB — architecturally different, not missing) and **vision-system data**
  (camera/lidar — not covered by CMS at all).

### Changed

- **2026-09-28 — The iOS driver companion is included in the public release.** For the first
  time, the Meridian Motors Companion source under `clients/ios/` is part of the mirrored
  archive; prior releases (v0.1.x through v0.3.x) shipped the platform without a mobile client.
  The tree was excluded 2026-08-03 pending a tenant-slug rename it depended on; the rename
  landed 2026-08-21, the exclusion outlived its own release condition by five weeks, and this
  release closes it. `clients/ios/art-sources/` (AI-generated Meridian renders) remains
  withheld — that entry is independent, and its own open question (AI-disclosure for generated
  imagery) is unresolved. Per-finding disposition and the executable-guard follow-ons in
  `issues/2026-08-03-ios-tenant-canary-regression/summary.md` and
  `issues/2026-09-28-ios-client-publish-exclusion-stale/report.md`.

- **2026-09-28 — iOS sign-in errors are plain English.** A failed sign-in shows an alert with a
  message mapped from the Cognito error instead of the raw Cognito 400 text. A wrong password and
  an unknown account get the same message, so the screen does not reveal whether an account
  exists. The brand appears once (the logo wordmark), and the controls use the brand color.

- **2026-09-25 — Simulator images republished at `v0.4.0`.**
  `SIM_IMAGE_VERSION` is now `v0.4.0` (`deployment/stacks/_sim_image_config.py`).
  `cms-sim-service:v0.4.0` is built from `6d67193c` (`SIM_IMAGE_SOURCE_COMMIT`), so it carries
  the vehicle-side SOVD handlers and the healthy-trip fix; the previous `v0.3.2` image predates them, so SOVD commands against a
  `v0.3.2` simulator have no vehicle-side responder. `cms-fwe-agent:v0.4.0` has the same
  content as `v0.3.2` (AWS IoT FleetWise Edge v1.3.2). Deployments in the default
  published image mode pull both at the new tag. `deployment/scripts/stage_ecr_resources.sh`
  now stages the `_shared/` overlay the simulator Dockerfile requires.

- **2026-09-20 — Trip-intent parameters take effect on warm-onboard restarts.**
  Spec: `.kiro/specs/2026-09-20-trip-intent-param-contract/`. Starting a trip
  for an onboard (`vehicle-telemetry`) vehicle whose FWE agent was already
  running previously returned `200 {"success": true}` and silently discarded
  the caller's `city`, `route_length`, `safety_scenarios`, and
  `maintenance_scenarios` — the container carried the argv values from its
  first start, so warm restarts ignored the form. The reuse path now applies
  caller-supplied trip parameters on both the CMS `TripSimulatorModal` and the
  CS `SimulateVehicleView` (which post to the same simulation Lambda).

- **2026-09-20 — Transform-manifest contract guards on the Meridian cloud-telemetry path.**
  Spec: `.kiro/specs/2026-09-20-transform-manifest-contract-guards/`. Four
  previously-unowned couplings on the CS-mediated Meridian ingest path are now
  pinned by tests: simulator ↔ manifest `source_path` set (38 source paths,
  all identity mappings, zero gaps against the simulator's emitted dict); IoT
  rule → MSK topic routing (`cms_{stage}_cs_product_meridian_ev_rule` →
  `cs-product-meridian-ev`); manifest ↔ processor pattern
  (`OEMTelemetryProcessor` subscribes to `(cms-telemetry-oem|cs-product-.+)`
  and decodes gzip+base64 before the transform); git-tracked manifest ↔
  deployed S3 copy (byte-identical sha256, via `make sync-manifests` as the
  upload path).

- **2026-08-28 — Removed the inert `signalCatalogArn` field from `deployment/scripts/seed_model_manifests.py`.**
  The field wrote an `arn:aws:iotfleetwise:us-east-1:...:signal-catalog/cms-prod-vss`
  string into a DynamoDB item — metadata only, never invoked as an ARN. In a
  repository AWS now names as the FleetWise-successor path, seeding a FleetWise-
  service ARN as visible metadata implied a dependency a new customer cannot
  satisfy. `docs/tech.md` audit-table entry updated in the same change.

### Added

- **2026-09-01 — Remote vehicle diagnostics via SOVD on the MQTT commands transport.**
  Spec: `.kiro/specs/2026-09-01-cms-remote-diagnostics-sovd/`. Fleet operators can
  now trigger three on-demand operations against any enrolled vehicle: read
  stored DTCs (UDS `0x19 0x02 0xFF`), clear diagnostic information (UDS
  `0x14 0xFF 0xFF 0xFF`), and retrieve freeze-frame data — over the same MQTT
  commands transport CMS already uses. Requests carry a correlation ID and are
  published to `cms/commands/things/<VIN>/executions/<corrId>/request/protobuf`;
  responses land on `.../executions/{corrId}/sovd/response` and are correlated
  back to the caller row. Wire format follows the ASAM SOVD JSON shape
  (ISO 17978-3) for standards-readiness. Fleet-scoped authorization is applied
  at the API edge via `_lib/fleet_membership.py`; per-vehicle rate limits are
  enforced by a shared limiter; response payloads above 100 KB spill to S3.

- **2026-09-02 — Vehicle Diagnostics platform: 9-ECU UDS scan, catalog verdicts, safety-classified routines.**
  Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform/`. Builds on the SOVD
  transport above to make the vehicle-detail Diagnostics tab a full diagnostic
  surface: coordinated UDS reads across nine simulated ECUs (`ECU_BRAKE`,
  `ECU_ENGINE`, `ECU_POWERTRAIN`, `ECU_PCM`, `ECU_COMM`, `ECU_BATTERY_HV`,
  `ECU_BATTERY_12V`, `ECU_EVAP`, and `ECU_BODY` on extended addressing), DTC
  catalog verdicts, ECU identity + version-drift comparison, and an offered
  routines strip. Each routine carries a `safety_class` (`INERT`, `STATIONARY`,
  or `SERVICE_ONLY`); the fleet-operator surface offers only `INERT`, and
  `STATIONARY` / `SERVICE_ONLY` routines are gated to the dealer-technician
  workflow (see below). Sixty-one tasks, all `[x]`.

- **2026-09-10 — Typed SOVD routine result contracts (six pilot routines).**
  Spec: `.kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/`. Six pilot
  routines emit typed result payloads with matching sim producers and CMS + DMS
  renderers: `lamp_self_check`, `o2_heater_check`, `evap_leak_test`,
  `abs_pump_cycle`, `pack_isolation_test`, `cell_balance_check`. A drift-guard
  script (`scripts/verify_renderer_parity.py`) keeps the CMS-side and DMS-side
  renderers byte-identical. **Partial**: 12 additional routines are enumerated
  in the catalog but do not yet have typed schemas or renderers — the session
  log renders the raw sidecar payload verbatim for those.

- **2026-09-10 — CMS ↔ DMS diagnostic sessions (v1.5).**
  Spec: `.kiro/specs/2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5/`.
  Individual routine runs on a vehicle correlate into a UI-level "session" via
  an optional `session_id` attribute on the existing `cms-{stage}-storage-commands`
  rows (no new table). CMS's fleet-operator persona can now dispatch a
  diagnostic session — DTCs, freeze frames, routine transcripts — to the paired
  Dealer Management System (DMS) accelerator's Service view, where a dealer
  technician picks it up and can run `STATIONARY` / `SERVICE_ONLY` routines the
  fleet-operator surface hides. The sidecar's response payload is now persisted
  on the command row (previously discarded) and rendered in the session log.

- **2026-09-25 — Diagnostics tab redesigned around the diagnostic session.**
  Spec: `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign/`. Top to bottom, the tab
  shows a health strip (`DiagnosticsHealthStrip`, with the single **Run diagnostic scan**
  button and **Dispatch to service**), the self-test routines, the sessions table
  (`DiagnosticSessionsTable`), DTC history, and technician detail. A session's detail
  (`SessionDetailView`) opens in a modal. The poll lives on the panel, so a scan keeps
  running while the modal is closed. `RoutineOffering` owns the
  INERT / STATIONARY / SERVICE_ONLY gate. Fixes from the first browser UAT
  (`issues/2026-09-25-diagnostics-ia-uat-defects/`): commands-API rows are normalised once
  at fetch, so sessions group correctly and scans render per ECU. The health strip makes no
  claim while history is loading. Session status reads each routine's latest attempt.
  Review fixes: a scan whose results were offloaded or dropped shows as partial or
  unavailable, never as "No open faults", and only full scans count; self-test failures are
  shown; the STATIONARY prohibition appears once per group. **Dispatch to service** carries
  the vehicle's recorded ACTIVE fault codes, including codes the cloud raises from telemetry
  that no ECU scan returns (marked as not scanned). **View RO** opens the repair order on the
  DMS fleet repair-order page in a new tab: `make regenerate-runtime-config` writes
  `dmsUiOrigin` from the stage's `DMS_UI_CALLBACK_ORIGIN` (https only), and without it the
  repair-order id is shown as plain text.

- **2026-09-02 — Fleet Intelligence surfaces under `/fleet-intelligence/*`.**
  Spec: `.kiro/specs/2026-09-02-cms-fleet-intelligence-v1/`. The flat 15-item
  nav is regrouped around four sections with fleet as a filter. New routes:
  `/fleet-costs` (cost-per-mile with per-OEM dimension, over the existing
  `/api/v1/tco/*` route family), `/pm-compliance` (preventive-maintenance
  scheduling by mileage / engine-hours / calendar), `/fleet-rebalancing`,
  `/warranty` (read-only recalls + coverage), and — added by the follow-on
  `.kiro/specs/2026-09-14-cms-fleet-lifecycle-view/` — `/fleet-intelligence/lifecycle`
  for per-vehicle sell-timing analysis (deterministic linear-fit of maintenance
  cost vs. straight-line depreciation, returning crossover month, fit R², and
  provenance). `/fleet-intelligence/lifecycle` is now the root landing route
  after the VFO teardown swapped it in for the retired Fleet Command Center.

- **2026-09-10 — Fleet Intelligence consumes ADP curated products via cross-region Athena.**
  Spec: `.kiro/specs/2026-09-10-cms-fleet-intelligence-adp-consumer/`.
  `services/fleet_intelligence/adp_source.py` reads maintenance cost per vehicle
  per month from ADP `service_records` + `charging_sessions` + `energy_usage`
  via the cross-region Athena workgroup `cms-{stage}-analytics` in `us-east-1`.
  Replaces the empty local DDB stubs Fleet Intelligence v1 shipped over.
  Requires Lake Formation grants on the ADP producer's side and a cross-account
  KMS decrypt scoped via `kms:ViaService=s3.us-east-1.amazonaws.com`. Fifty
  tasks; two `[!]` are operator/live-verification carry-forwards, not code.

- **2026-09-26 — Fleet Intelligence lifecycle view across all ADP vehicles.**
  `GET /api/v1/fleet-intelligence/lifecycle?scope=adp` returns a lifecycle rollup over every ADP
  vehicle (about 4.7M VINs in staging), by model and model year, for `platform-admin` only; other
  callers get 403. The rollup is precomputed hourly (EventBridge rule `FleetAdpRollupRefreshRule`)
  and never queries Athena in the request path; a missing artifact, or one older than 26 hours,
  returns 503 with the last `computedAt`. ADP carries no purchase price, so the rollup assumes
  60,000 USD depreciated straight-line over 120 months and returns that assumption with every
  result. `FI_LIFECYCLE_WINDOW_MONTHS` (default 36) sets the window for both scopes. The
  Lifecycle page has a "CMS fleets | All ADP vehicles" toggle for admins, and the fleet picker is
  locked while the ADP scope is shown. The Fleet Intelligence role gets `DESCRIBE` on
  `adp_{stage}_dimensions` and `SELECT` on its `vins` table only. See
  `services/fleet_intelligence/README.md` § ADP Scope.

- **2026-09-26 — The Meridian iOS app shows diagnostics Findings.** The Alerts tab renders the
  `health.diagnostics` Finding kind (active DTCs reported by the AVX Tier 2 diagnostics component)
  with its own icon and title, and any Finding kind the app does not know renders as a generic
  card instead of failing to decode.

- **2026-09-04 — Connected Services portal — Cognito-gated SPA on its own subdomain.**
  Specs: `.kiro/specs/2026-09-04-cms-connected-services-portal-v2/`,
  `.kiro/specs/2026-09-14-cs-portal-data-model-backend/`. New standalone
  frontend at `modules/connected_services_ui/`, deployed by a new
  `cms-{stage}-connected-services-ui` CFN stack behind its own CloudFront
  distribution. Six data-model surfaces read directly from CMS's deployed
  `data_processing_api.py` (`cms-{stage}-data-processing-api`) — no new data
  store — namely: **Signal Catalog** (302 signals in a VSS-style tree, grouped
  by 24 functional domains), **Vehicle Models** (8 model manifests),
  **ECUs** (9 distinct, de-duplicated from 65 raw entries),
  **Decoder Manifests** (2), **Data-Collection Campaigns** (list + drill-in
  detail, restructured by the 2026-09-20 spec below), and **Simulation** (see
  Telemetry Source Modes in the README).

- **2026-09-10 — Connected Services subscription plane: subscribers, products, availability.**
  Spec: `.kiro/specs/2026-09-10-cms-connected-services-subscriptions/`. New
  `services/connectors/subscriptions/` module ships a `Subscription` / `Product`
  / `VehicleAvailability` backend, an external `subscriber` Cognito group plus
  `custom:subscriptionIds` claim, and a subscriber-persona UI
  (`DataProductsView`, `SubscriberLookupView`, `AvailableVehiclesView`). Three
  seeded products: telemetry (high-frequency), diagnostics-only (sourced from
  the existing DTC / maintenance-alert pipeline), and charging-session (sourced
  from existing charging tables). **Partial**: `DataProductsView` renders from
  a browser-side fixture and falls back to fixture data when no endpoint is
  configured; the "Create data product" modal is a UI-shape stub (no submission
  target, no persistence, no validation beyond required-field disable) — so
  the data-product **definition** layer is browser-only in this release. The
  read paths, subscription CRUD, availability marking, and consumption round
  trip are real.

- **2026-09-10 — CMS-side subscription consumer.**
  Spec: `.kiro/specs/2026-09-10-cms-connected-services-consumer/`. A CMS-owned
  subscriber identity plus one read-through proxy Lambda plus a CMS Fleet
  Manager portal surface, all consuming a Connected Services telemetry feed via
  the same routes the producer spec creates. Mirrors the existing DMS-proxy
  pattern in `main_api/index.py`; demonstrates end-to-end that "the platform
  produces a feed, and the platform's own operational surface pulls it back"
  without inventing a second integration idiom.

- **2026-09-11 — CS-mediated Meridian ingestion (Pattern 2).**
  Spec: `.kiro/specs/2026-09-11-cms-cs-meridian-ingestion/`. Symmetric with the
  existing OEM1 cloud path but with CS as the intermediary: external OEMs
  (here, a simulated Meridian producer) publish INTO Connected Services; a
  scheduled puller Lambda (1-min cron) reads `cs-source-telemetry`, normalizes
  to canonical shape, and writes to `cms-storage-telemetry`. Complements
  Pattern 1 (third-party subscribers consuming CMS data via the subscription
  plane above). Ships the `deployment/stacks/meridian_ingestion_stack.py` +
  `services/connectors/subscriptions/` puller and `services/data_processing/manifests/meridian-ev-transform.json`.

- **2026-09-15 — Connected Services campaign ownership and per-vehicle assignment UI.**
  Specs: `.kiro/specs/2026-09-15-cms-cs-campaign-ownership/`,
  `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/`. Campaigns carry an
  `owner` attribute (`oem` vs internal CMS); only `owner == "oem"` campaigns
  are visible in the CS portal. The CS Data Collection Campaigns screen now
  lists one row per campaign definition with drill-in per-campaign detail
  showing definition (decoder manifest, collection scheme, category), signals
  resolved against the signal catalog (grouped by `signal_group`), coverage
  (per-campaign ECU roster, sourced from `signalsToFetch` for UDS campaigns),
  and per-vehicle Assign / Unassign actions. Assignment sends the VIN, never a
  `vehicleId`, and refuses when no VIN is on record. Simulation is now gated on
  a campaign being assigned to the target vehicle (previously a dead guard
  present in source but bypassed in the deployed artifact).

- **2026-09-19 — CS Trip Simulator parity on the Simulate Vehicle screen.**
  Spec: `.kiro/specs/2026-09-19-cs-trip-simulator-parity/`. The CS
  `SimulateVehicleView` now offers the same trip-intent controls as CMS's
  `TripSimulatorModal`: city (7 real cities), route length (5 presets, 10–60
  GPS points), and safety / maintenance scenario multiselects. The `mode` and
  `rule_name` remain server-derived from the vehicle's `dataSource` per the CS
  wrapper's authorization design (`connected-services` callers do not choose
  transport) — only trip-intent parameters are caller-supplied.

- **2026-09-01 — Campaign assignment is part of Create Vehicle; simulation is gated on it.**
  Spec: `.kiro/specs/2026-09-01-cms-campaign-follows-enrollment/`. The Create
  Vehicle API and UI wizard accept an optional campaign selection at
  enrollment; the Trip Simulator no longer completes successfully with zero
  telemetry when a vehicle has no campaign assigned (the FWE agent's CAN reads
  are discarded without a campaign, and this failure previously surfaced
  nowhere). Operator override
  `deployment/scripts/deploy_vehicle_campaign.py` remains as an out-of-band
  retro-assignment tool.

- **2026-08-04 — Vehicle presence separated from trip lifecycle; ECU sidecar is now resident.**
  Specs: `.kiro/specs/2026-08-04-cms-vehicle-trip-lifecycle-split/`,
  `.kiro/specs/2026-08-19-cms-vehicle-ecu-presence-resident/`,
  `.kiro/specs/2026-08-19-cms-connection-status-single-source/`. A parked
  FWE-path vehicle now stays commandable outside a trip. The `vehicle-ecu`
  sidecar rides on the `fwe-agent` task definition and stays subscribed to
  `cms/commands/…` across trip boundaries, refreshing `lastSeenAt` on a 60 s
  heartbeat. `connectionStatus` is now single-source-of-truth: the trip worker
  no longer competes with the presence sidecar for writes, and the fleet-active
  count and web `calculateVehicleStatus` consult the same staleness rule as the
  vehicle-live-state read path (~120 s demotion).

### Added
- **2026-08-10 — Demo identity model Group E: auto-sign-in on landing + persona switcher in account dropdown.**
  Spec: `.kiro/specs/2026-08-05-cms-demo-identity-model/` Group E (Interpretation A) + Fix Group E.
  Two staging-only UX features, both gated on `runtimeConfig.demoPasswords` (absent on prod):
  (1) **Auto-sign-in on landing**: on a fresh page load to the staging app, when `demoPasswords` is
  present and `showDemoButtons=true`, the app automatically signs in as the last-used persona
  (`localStorage['cms.lastPersona']`) or defaults to `FleetManager@example.com`. Shows a loading
  spinner rather than flashing the login form. Setting `sessionStorage['cms.suppressAutoSignIn']`
  (set on sign-out) skips auto-sign-in for the current tab session; closing and reopening the tab
  re-enables it.
  (2) **Persona switcher in the account dropdown** (Fix Group E, same day per UX feedback):
  Persona items (🚛 Fleet Manager, 🎧 Agent, 🔬 Product Engineer, 📋 Dispatcher) appear at the
  top of the existing account dropdown menu (`profileActions` in `App.tsx`). Clicking a persona
  calls `simpleAuth.login(email, password, false)` via the existing Path A credential path;
  writes `cms.lastPersona` (email only, never password) to localStorage. Current persona shown
  as disabled with "(current)". Failed switches preserve the current session. Component is
  entirely absent on prod (demoPasswords absent from `runtimeConfig.json`).
  Note: the initial Group E implementation used a separate absolute-positioned overlay (three
  vertical dots) — that was replaced with the dropdown integration per UX feedback the same day.
  Security properties: credentials read from `runtimeConfig.demoPasswords` at click/effect time
  only; never stored in state, props, localStorage, or sessionStorage. The same accepted risks from
  2026-08-07 apply (SSO-gated `runtimeConfig.json` is not a permanent credential boundary). New
  vitest tests: 10 E1 auto-sign-in cases + 15 Fix-Group-E dropdown integration cases
  (replacing 12 overlay tests) — 50+ auth-related tests total.

### Security
- **2026-09-26 — The commands API sends to the vehicle it authorized, and geofence routes are authorized.**
  Issue: `issues/2026-09-26-commands-api-body-vin-bypasses-fleet-check/`.
  `POST /api/commands/{vehicleId}` checked fleet membership on the path `vehicleId` but published
  to the FleetWise topic for the `vin` in the request body, so a caller authorized for one
  vehicle could send a command to a vehicle in another fleet. The topic VIN now comes from the
  authorized `vehicleId`; a body `vin` that differs is rejected with 400, for every role. The
  `/geofences` routes performed no caller authorization; they are now authorized per vehicle,
  and creating or deleting a geofence needs write rights.
- **2026-09-26 — Fleet Intelligence routes enforce the caller's fleet scope.**
  Issue: `issues/2026-09-25-fleet-intelligence-routes-trust-caller-fleet-id/`. Every
  `/api/v1/fleet-intelligence/*` route scoped its data by the `fleetId` the caller sent and
  never checked it, so any signed-in user could read any fleet, or every fleet by passing
  `fleetId=__all__` or omitting it. Each route now checks the requested scope against the
  caller's Cognito groups and `custom:fleetIds` (`services/fleet_intelligence/_auth.py`):
  `platform-admin` and `fleet-viewer` keep cross-fleet access; `fleet-operator`,
  `fleet-guest` and `dispatcher` see only the fleets in `custom:fleetIds`; any other caller
  gets 403. Completing a PM schedule checks the schedule's own fleet. A test enumerates the
  routes from `ui_stack.py`, so a new route cannot ship outside the check.
- **2026-09-25 — The `meridian.driver` demo persona is a driver of one vehicle, with no owner standing.**
  Its `custom:customerId` gave it owner standing over every vehicle under that customer, so
  it saw findings for a vehicle it does not drive. The claim is removed, and
  `assign_standing_claims.py` no longer maps it; a test fails if it is mapped again.
- **2026-08-31 — Simulation API is fleet-scoped across 12 routes; the `/faults` fail-open is closed.**
  Spec: `.kiro/specs/2026-08-31-cms-sim-api-fleet-scoping/`; superseded issue
  `issues/2026-08-11-simulation-api-no-group-authorization/`. Eleven of the 12
  simulation-API routes previously performed no group or fleet check
  whatsoever — any authenticated pool user could start/stop entire ECS
  simulation clusters. The 12th route (`/vehicle/{vehicleId}/faults`) carried
  a fail-open pattern (`_is_admin = "platform-admin" in _user_groups or not _user_groups`) —
  the same pattern the `main_api` P0 removed. Now: all 12 routes read caller
  identity, resolve VIN → fleet via the `vehicleId-index` GSI, and deny when
  the caller's `custom:fleetIds` do not intersect the target VIN's fleet;
  `/faults` fail-open is closed. Ports the `_lib/fleet_membership.py` helper
  into the sim Lambda via a new `_bundle_sim_lambda()` in `simulation_stack.py`.
  Route-coverage and fail-open-absence invariants are pinned by
  `test_route_coverage_invariant` and `test_no_fail_open_invariant`.

- **2026-09-27 — The iOS app reads only the signed-in user's own Findings and vehicle.** The
  Alerts tab reads Agent Findings from `GET /findings/me`, which derives the caller from the token,
  so the app no longer names an owner. A signed-in user with no vehicle of their own no longer
  falls back to the demo vehicle `VEH-0025`: vehicle reads and the telemetry socket are skipped,
  booking refuses, and the Acquire flow never uses the demo identity. Issues:
  `issues/2026-09-25-ios-findings-owner-id-is-email/`,
  `issues/2026-09-27-ios-alerts-tab-stuck-without-vehicle-context/`. Known limitation: the voice
  session and the Assistant tab still use the demo vehicle.

- **2026-08-10 — DTC endpoint family now enforces fleet ownership (IDOR).**
  Issue: `issues/2026-08-10-dtc-endpoint-idor/`. `GET /api/v1/vehicles/{id}/dtcs`,
  `PATCH .../dtcs/{dtcId}`, and `POST .../dtcs/{dtcId}/schedule-service`
  previously carried a role check (`_deny_viewer()`) but no scope check — they
  never asked whether the caller's fleet owned the vehicle whose ID was in the
  path. Distinct from the same-day `main_api` fail-open P0: fixing defaults
  did not reach a check that was never written. All three routes now resolve
  the vehicle, return 404 if the row is absent, 403 if it carries no
  `fleetId`, then call `_check_fleet_access`. `platform-admin` retains
  cross-fleet access. Fleet-scope tests added on the read path (no such test
  existed).

- **2026-08-23 — Fleet-aggregate GET routes now require `platform-admin` or `fleet-viewer`.**
  Issue: `issues/2026-08-23-fleet-endpoints-missing-route-authz/`.
  `GET /api/v1/decision-journal`, `GET /api/v1/fleet-actions`,
  `GET /api/v1/daily-briefing`, and `GET /api/v1/fleet-health` previously
  returned 200 with a bare `table.scan()` — any principal past the API-GW
  authorizer (scoped fleet-operator, fleet-guest, driver, consumer) received
  full cross-fleet aggregate data. A uniform `has_unscoped_access` gate is now
  applied at the top of each of the four routes; scoped principals receive 403.

- **2026-09-03 — Fleet-actions approve/reject now require `platform-admin`.**
  Issue: `issues/2026-09-03-fleet-actions-approve-reject-missing-authz/`.
  `POST /api/v1/fleet-actions/{id}/{approve|reject}` — the write half of the
  same family covered by the 2026-08-23 GET pass — went straight from path
  parsing to `table.scan` + `update_item` with no authorization. Its only
  in-tree caller was the Fleet Command Center panels deleted by
  `.kiro/specs/2026-09-02-cms-fleet-intelligence-v1/`, which changed the
  route's character from "unconsumed" to "unconsumed but internet-reachable
  and mutating." Now returns 403 for non-`platform-admin` callers.
  `has_unscoped_access` was rejected here because `fleet-viewer` is a
  global-read auditor role and granting it write would contradict the
  mutating-route invariant enforced elsewhere in the handler.

- **2026-09-01 — Cognito refresh, ID, and access tokens no longer logged to the browser console.**
  Issue: `issues/2026-09-01-cms-auth-token-logging-and-oauth-hardening/`.
  `SimpleAuthProvider.tsx` previously called `console.log('📥 Auth response:', response)`
  on every successful sign-in; `response.AuthenticationResult` carried
  `AccessToken`, `IdToken`, and the long-lived `RefreshToken`. The refresh
  token is the sharpest edge because the Cognito pool is shared with DMS;
  console output is reachable from browser extensions, screen shares,
  session-replay agents, XSS footholds, and ticket-pasted logs. The log call
  is removed. The same issue records that Hosted-UI `state` and PKCE were
  already fixed under commit `bdba787a` — verified rather than assumed.

- **2026-09-05 — Connected Services portal now enforces real Cognito authentication.**
  Spec: `.kiro/specs/2026-09-05-cms-connected-services-auth-integration/`. The
  v2 portal's earlier stub `getSession()` (reading a `window.__SESSION__`
  global that only the test harness sets) is replaced with real Cognito
  Hosted-UI redirect flow with **PKCE + `state`** hardening, JWT decode, and
  session persistence. Federate SSO and username/password paths are both
  wired. Talos DAST Critical `69d7f6e6-01b4-4cb7-8c18-19947fc211d8`
  (`UnauthWebService`) was cleared on operator direction as part of this
  remediation.

- **Demo Cognito credential was plaintext in CloudFormation template; now rotated to Secrets Manager.**
  `deployment/stacks/ui_stack.py` was passing the demo credential (`FleetManager@example.com` password)
  to CloudFormation as plaintext in three locations: `DefaultUserResource` `TemporaryPassword`,
  `SetPermanentPasswordResource` `Password`, and `CfnOutput` `DefaultUserPassword`. The output was
  the most severe exposure — `cloudformation:DescribeStacks` is granted more broadly than
  `GetTemplate`, and the value was rendered on the stack's Outputs tab. Remediated 2026-08-05 by
  moving credential generation to Secrets Manager (`cms-{stage}-demo-user-password`) and applying
  it via a dedicated custom-resource Lambda. The plaintext was never baked into the deployed stacks
  — synthesis now fails with `ValueError` if a plaintext credential reaches the template. Both
  staging and prod were deployed and rotated on 2026-08-05; `UserLastModifiedDate` confirms the
  credential changed on both. Spec: `.kiro/specs/2026-08-04-cms-demo-credential-out-of-template/`.
- **Prod driver-self guard was inert for 18 days; now fixed and fail-closed.**
  `DRIVER_SELF_GUARD_ENABLED=true` was committed to `deployment/config/prod.env` on
  2026-07-16 and prod was deployed on 2026-07-29, yet the deployed Fleet API Lambda
  still reported `false` on 2026-08-03 — the value is read from the deploying shell's
  environment and defaulted to a silent `'false'`, so a deploy that bypassed the
  env-extracting Make target shipped the control off with no error and an
  `UPDATE_COMPLETE` stack. Remediated by a targeted `cms-prod-ui` deploy; prod now
  reports `true`. `deployment/stacks/ui_stack.py` now **fails synth** rather than
  defaulting to `false` on any stage outside `{"", dev, development, local, test}`,
  so this cannot silently recur (23 tests in
  `deployment/stacks/test_driver_self_guard.py`). Issue:
  `issues/2026-08-03-prod-driver-self-guard-inert/`.
- **2026-08-07 — Demo identity model: one-click login via `runtimeConfig.demoPasswords` (staging only); fail-open authz defaults closed.**
  Spec: `.kiro/specs/2026-08-05-cms-demo-identity-model/`. **Path A design** (chosen 2026-08-07 after user direction): one-click quick-login buttons on staging submit the demo password from `runtimeConfig.demoPasswords`, a map injected by `make regenerate-runtime-config` post-deploy. The file is behind CloudFront + SSO, so only authenticated Amazon employees can fetch it; however, once fetched, the password authenticates to the public `cognito-idp:InitiateAuth` indefinitely until rotation. **Accepted risks**: (1) SSO-authenticated employees can fetch the password from outside the gate. (2) Rotation via `rotate_demo_login.py` is the mitigation. (3) Threat surface is Amazon employees only; post-departure abuse is time-bounded to rotation. (4) Staging bundle-to-prod cross-contamination is blocked by per-stage `runtimeConfig.json` generation and built-asset credential guards. **Components**: (1) All four demo persona passwords are CDK-generated into Secrets Manager (not hardcoded in templates); invalid plaintext in any asset is caught by a guard that runs at deploy time and fails closed. (2) Two fail-open authorization defaults closed: groupless tokens no longer default to `platform-admin`; fleetless tokens no longer get unscoped cross-fleet access. (3) Four demo personas de-privileged: `FleetManager@example.com` moves from `platform-admin` to `fleet-operator` on staging; other personas assigned minimum groups (`connect-agent`, `product-engineer`, `dispatcher`). All passes 87 tests (36+29+21+1 cycles). Deployed to staging 2026-08-07. The endpoint-based design (persona-session) was designed, security-reviewed twice, then reverted in favour of this simpler shape per user direction. See spec decisions.md § "Reverting the 2026-08-06 endpoint design; restoring Path A".

### Changed
- **Prod sync completed** (spec `.kiro/specs/2026-07-16-cms-prod-sync-and-demo-runbook/`).
  All eight prod stacks reconciled against `main`: `cms-prod-ui` (driver-self guard),
  `cms-prod-commands` (remote-commands `NameError` fix — `legacy_topic` hoisted out of a
  conditional so the response and DDB row agree on the reachable topic), and
  `cms-prod-simulation` (sim container images `v0.2.6` → `v0.2.8`) deployed;
  `storage`, `data-processing`, `ws-fanout` and `flink` were already in sync; `msk` had
  only a cosmetic Outputs-ordering difference.
- DTC-dedup backfill applied to prod: 8 duplicate groups collapsed, 12 rows removed
  (734 → 722), 0 errors, verified by a follow-up dry-run reporting 0 groups.
- Deleted the orphaned pre-rename UI frontend bucket left behind by the FrontendBucket
  region-suffix rename (19 objects) after confirming it was referenced by no
  CloudFormation stack and was not a CloudFront origin. Marked
  `docs/DEPLOYMENT.md` § "Renaming a UI FrontendBucket" as EXECUTED.

### Fixed
- **2026-09-25 — Healthy simulated trips no longer raise P0299, P0562 or P0001.** Random
  faults are off by default, and healthy telemetry values sit outside every rule's band. The
  published `cms-sim-service:v0.4.0` image is built from this source.
- **2026-09-25 — Temperature thresholds match the °F signals.** Issue:
  `issues/2026-09-25-coolant-overheat-threshold-unit-mismatch/`. The coolant overheat P0 rules
  compared °C thresholds against a °F signal, so normal readings raised P0 alerts. The event
  catalog thresholds, and the EV battery-cooling and motor-temperature thresholds in the Flink
  `MaintenanceProcessor`, are now in °F.
- **2026-09-25 — The Fleet Intelligence lifecycle landing no longer times out.** Issue:
  `issues/2026-09-25-fleet-lifecycle-athena-queue-delay-504/`. Athena's on-demand queue time is
  unbounded and API Gateway stops at 29 s, so `/fleet-intelligence/lifecycle` returned 504
  whenever the queue spiked. The lifecycle, cost and per-vehicle lifecycle routes now read
  cached portal-wide inputs from S3, narrowed to the requested fleet. An EventBridge rule
  refreshes the cache; a missing, stale (over 6 h) or malformed cache falls back to a live read
  that writes it through.
- **2026-09-25 — Status badges read the backend's actual casing.** Fleet, driver, simulation and
  trip views compared `status` against one casing while the backend stores another (fleets and
  drivers lowercase, trips uppercase), which showed warning icons on healthy fleets and
  "active" badges on completed trips. Comparisons are now case-insensitive.
- **2026-09-25 — UDS fault injection produces DTCs on AL2023 kernel 6.1.186.** The FWE sim host
  could not build `can-isotp` because that kernel's `can_skb_priv` has no `skbcnt`; the host
  build now patches it.
- **2026-09-26 — The Meridian iOS app no longer aborts right after sign-in on a 0 Hz audio device.**
  Issue: `issues/2026-09-25-ios-voice-audio-engine-crash-on-zero-hz-input/`. The audio graph was
  built before the audio session was configured; on devices that report a 0 Hz format at that
  moment, AVFAudio raised an exception no Swift `catch` can stop. The graph is now built only
  after the session is active, and an unusable input or output format becomes a Swift error, so
  the app continues text-only.
- **2026-09-26 — iOS Controls work for drivers again.** Issue:
  `issues/2026-09-26-ios-controls-403-driver-self-commands-api/`. The commands API refused every
  driver token, so the Meridian app's Controls sheet (lock, remote start, preconditioning,
  hazards, find my vehicle, charge door) showed a 403. A driver can now list those six commands,
  send them to the vehicle their active driver record is assigned to, and read the history of
  commands they issued. Every other command route still refuses driver tokens.
- **2026-09-25 — iOS Agent Findings load and read cleanly.** The Alerts tab requested findings
  under the sign-in email, which the findings routes refuse, so every user saw "Couldn't load
  agent findings." It now uses the `custom:customerId` and `sub` claims. Cards no longer repeat
  detail, evidence is readable, routine findings carry no actions, and the section's inset
  matches the Alerts tab. The sign-in screen's text is readable in light mode.
- Amazon Location HERE maps on prod: two-tier map auth (authenticated preferred, guest fallback aligned with `cms.allow_unauth_map_auth=true`, OSM ultimate fallback) plus map-config race fix for four map components that were missed in commit `2f5c435`. Also purges stale `authToken`-without-`idToken` sessions on init and removes the legacy `demo-token` synthetic-session code path. Spec: `.kiro/specs/2026-07-16-cms-demo-mode-maps-osm-fallback/`.

- **2026-09-28 — A Flink restart no longer brings back cleared DTCs.** Issue:
  `issues/2026-09-25-flink-maintenance-restart-replays-retained-topic/`. After a restart the
  maintenance processor re-read the retained telemetry topic, so cleared DTCs came back as ACTIVE
  and their alerts and actions fired again. It now starts from its committed Kafka offsets (latest
  if none), skips a report that is not newer than the code's last clear, updates an ACTIVE row only
  from a newer report, and takes `firstSeenAt`/`lastSeenAt` from the report's own time. One UDS
  poll that reports several codes writes one row per code, and a skipped report writes no alert or
  action. The row key is unchanged.

- **2026-09-27 — The iOS Alerts tab loads for a user with no vehicle.** Signed in without a
  vehicle of their own, the tab showed its loading skeleton forever. It now renders Agent Findings
  whatever happens to the vehicle lookup, and says "all clear" or "no events" only after a
  successful read for that vehicle. The Vehicle and Service tabs show a no-vehicle notice.

- **2026-09-26 — The Meridian iOS app no longer crashes on a real device when the voice session
  starts.** The voice player was connected with a 16-bit integer format, which real devices reject
  with an exception Swift cannot catch (the simulator accepts it). It now connects with the
  standard 32-bit float format and converts each audio chunk.

- **2026-09-26 — Fleet Intelligence pages load for fleet-scoped roles.** The fleet picker defaulted
  every role to "All fleets", which the fleet-scope check now refuses for `fleet-operator`,
  `fleet-guest` and `dispatcher`, so their first Fleet Intelligence page returned 403. The picker
  now defaults those roles to their first fleet and does not offer "All fleets".

## v0.2.7 — 2026-07-14

### Documentation

- **Implementation Guide refresh for v0.2.x architecture** — all 14 published
  chapters updated to match the current release (was last substantively
  updated at the v0.1.x / 6-stack era). Preserves existing v1.3.0 content
  (FleetWise, remote commands, geofencing, FWE v1.3.2) and adds the
  current-release deltas: 14-stack architecture (Bedrock agents, OEM cloud
  connector, WebSocket fan-out, commands, simulation stacks);
  phase-foundation/streaming/seeds/services deployment recipe; three-mode
  telemetry ingestion (MQTT Direct, FleetWise Edge, OEM Cloud); in-UI
  conversational fleet operations (Amazon Bedrock agents + AgentCore text
  runtime + automotive knowledge-base grounding); fleet bulk lifecycle and
  platform-admin/fleet-operator/fleet-viewer roles; driver self-vehicle-claim;
  expanded security chapter (Cognito groups, CDK context flags, WebSocket
  `$connect` JWT authorizer, encryption, bucket retention); split cost model
  (telemetry-processing vs. other components); expanded prerequisites (Java
  11, Yarn 4/Corepack), region posture, troubleshooting entries, and developer
  extension patterns. Diagrams are unchanged pending a follow-on figure
  refresh. README + `documentation/deployment-guide.md` also refreshed;
  the latter now redirects to `docs/DEPLOYMENT.md` as the canonical runbook.

### Added

- **7 new demo-clip scenarios** for the iOS voice-assistant persona demo:
  brake soft pedal, recall check, where nearby, temp gauge red, last
  serviced, DTC P0420 explain, engine rough — each with fleet/OEM/rental
  persona variants (42 total `.wav` clips).

### Fixed

- **CCPPanel double-init crash on route remount** — Amazon Connect Streams'
  `window.connect.core` singleton survives React remounts, but the
  component's per-instance init flag did not; re-navigating to a page
  hosting `CCPPanel` threw "Attempted to call initCCP when an iframe
  generated by initCCP already exists." Now checks the global SDK state
  before re-initializing.
- **Driver account panel false "no account provisioned"** — the lookup
  derived the expected Cognito email purely from a `firstName.lastName`
  convention; persona drivers with a different stored email (e.g. a
  seeder-set address that doesn't follow the naming convention) never
  matched despite a working Cognito account. Now prefers the driver
  row's stored `cognitoEmail`/`email` field.
- **DTC dedup with first/last-seen + occurrence count + Schedule Service action** — processor-sourced
  DTC rows now follow a dedup model: one ACTIVE row per `(vehicleId, code)` instead of accumulating
  duplicates per detection. New attributes on rows:
  - `firstSeenAt` / `lastSeenAt` — set on initial create, refreshed on each re-detection
  - `occurrenceCount` — incremented on each upsert; tracks detection frequency
  - `activeCode` — sparse GSI partition-key; enables O(1) dedup lookup; removed on CLEAR
  - New `active-code-index` GSI on dtc-history keyed `(vehicleId, activeCode)` with projection ALL
  
  Implementation uses Query + UpdateItem-or-PutItem in `MaintenanceProcessor.upsertActiveDtc` helper;
  legacy rows without a `source` attribute are preserved unchanged. See `modules/flink/README.md`
  § "DTC dedup" for the full model.

- **Backfill script for existing duplicates** — `deployment/scripts/backfill_dtc_dedup.py` collapses
  pre-existing ACTIVE duplicates (groups by `(vehicleId, code, source)`, keeps earliest
  `firstSeenAt` winner, sets `lastSeenAt` to latest, `occurrenceCount` to group size). Dry-run
  by default; `--apply` is the mutating step. Idempotent. See runbook at
  `docs/runbooks/dtc-dedup-backfill.md`.

- **DTC Schedule Service action** — new `POST /api/v1/vehicles/{vehicleId}/dtcs/{dtcId}/schedule-service`
  endpoint creates a service-history row with `relatedServiceId` but leaves the DTC ACTIVE
  (decoupled from clear). UI includes "Schedule Service" button on ACTIVE rows in VehicleDTCsTable.
  Allows operators to plan service while the fault is still live.

- **UI columns for dedup metadata** — VehicleDTCsTable now shows:
  - First seen — `firstSeenAt` timestamp
  - Last seen — `lastSeenAt` timestamp (updated on each re-detection)
  - Detections — `occurrenceCount` as a numeric badge (blue if > 1)

### Breaking changes

- **WebSocket API now requires authentication on `$connect`.** The CMS WebSocket
  API (`wss://.../live`) previously accepted anonymous upgrade requests on all
  routes. `$connect` now requires a Cognito JWT passed as `?token=<jwt>` on the
  upgrade URL, validated by a Lambda REQUEST authorizer against the User Pool
  JWKS endpoint. Anonymous clients receive **HTTP 401** on the upgrade. Demo
  deployments may opt in to anonymous WebSocket via
  `cdk synth --context cms.allow_unauth_websocket=true`. Operators with an
  anonymous-WebSocket dependency must either set that flag or update clients to
  pass the JWT. `$disconnect`/`$default` are unchanged (they run only on
  already-authorized connections per the AWS WebSocket-API design).

## v0.2.6 — 2026-06-15

### Fixed

- **Flink stack cold-region deploy race** — first-time deployments into
  a region never previously used (e.g. `ap-northeast-1`) failed with
  `InvalidRequest: Please check the role provided or validity of S3
  location ... unable to get the specified fileKey:
  jars/cms-telemetry-processor-1.0.0.zip` on the `SimulatorPreprocessor`
  Kinesis Analytics application. Root cause: the 9 Flink apps are
  serialized via an `add_dependency` chain (added in v0.2.3 to avoid KDA
  control-plane rate-limiting), but the first app in the chain
  (`SimulatorPreprocessor`) only had an *implicit* dependency on the JAR
  bucket — not on the `BucketDeployment` custom resource that *uploads*
  the JAR into it. In a warm region the upload Lambda finishes before
  KDA validates the `fileKey`; in a cold region the Lambda's cold-start
  plus cross-region asset handshake lost the race, and KDA's strict
  `CREATE_APPLICATION` validation failed, rolling back the whole stack.
  Fix: `flink_stack.py` now adds an explicit
  `simulator_preprocessor.node.add_dependency(FlinkJarDeployment)` so
  the JAR upload provably completes before the first KDA app is created.
  The other 8 apps inherit the dependency transitively via the existing
  serialize chain. Verified via `cdk synth` — `SimulatorPreprocessor`'s
  `DependsOn` now includes the BucketDeployment custom resource + its
  AwsCliLayer. Affects every first-time-region deployment of v0.2.x.

## v0.2.5 — 2026-06-15

### Documentation

- **README Cost section refresh**: the prior table dated October 2024
  pre-dated the Bedrock multi-agent stack, the OEM1 cloud-telemetry
  connector, the 9 additional Flink applications, and the ECS Fargate
  services for simulation / commands / WebSocket fanout. Customers
  evaluating the project against the old "$410/mo" headline would have
  significantly underestimated the v0.2.x deployment cost. This release
  preserves the v0.1.x baseline table (still accurate for the core
  infrastructure components it lists) but adds three new line items
  for the components introduced since: Bedrock invocation
  (usage-dependent, see Bedrock pricing page), ECS Fargate (4
  always-on tasks), and additional KDA Flink apps (~9 × 1 KPU 24/7
  ≈ $972). The headline copy is rewritten to make the baseline-vs-v0.2.x
  framing explicit and points customers at current AWS pricing pages
  for usage-dependent components.

- **README Service Limits — KDA quota fix**: prior text said
  "Kinesis Data Analytics: Default limit of 8 applications per region"
  with no callout that this Guidance now ships 10 Flink applications.
  Fresh customers running `make deploy-all` would hit a CFN error on
  the 9th Flink app and have to debug the limit before proceeding.
  Updated to explicitly direct customers to request a Service Quotas
  increase to ≥10 before deploy.

### Repository hygiene

- **`.publish-exclude`** — `docs/api-authorizer-audit.md` added.
  This was an internal audit document authored 2026-06-11 alongside
  the H1 3775026 auth-fix spec; it referenced the HackerOne report
  ID and `.kiro/specs/` paths verbatim. The H1 finding itself is
  CLOSED in v0.2.3 (fix shipped as `2e8c200`), so the audit doc is
  not a live security disclosure — but it is internal-eyes-only
  documentation that should not have shipped to the public mirror in
  v0.2.4. The customer-facing narrative is in CHANGELOG.md
  v0.2.3 § "Security (HackerOne 3775026 + 1 self-discovered)" and
  in `issues/2026-06-11-h1-3775026-cms-template-auth-gaps/summary.md`,
  both of which remain on the public mirror as the resolution-of-record.

## v0.2.4 — 2026-06-15

### Documentation

- **README.md — Option 2 cleanup for fresh customers**:
  - Added `make deploy-bedrock-agents` as an explicit step 5 (between
    `deploy-all` and `bootstrap-demo`). The Bedrock multi-agent stack
    powers the chat-style fleet operator surface; previously this was
    implicit operator knowledge and a fresh customer following the
    README would deploy CMS without working agents.
  - Removed stale `phase5` bullet from the `deploy-all` phase-group
    list — v0.2.3 commit `90fee7c` dropped `phase5` from `deploy-all`'s
    dependency list (the JAR-prune trap fix), so the bullet was
    misleading.
  - Refreshed the per-stack target list in Option 2 step 4: removed
    stale `(pulls in MSK transitively)` from `phase1` (v0.2.3 commit
    `886629f` reordered `phase-foundation` so `phase3` runs **before**
    `phase1`); corrected `phase5` description to "JAR rotation only —
    property maps now sourced from CDK"; added new targets
    `deploy-flink-fast` (v0.2.3 commit `03dd2c8`, ~5 min iteration)
    and `deploy-bedrock-agents`.
  - Replaced Option 4 (v0.1.x-era `cms-dev` prefix, hardcoded
    `us-east-1`, missing 6+ current stacks: no fleetwise, no commands,
    no bedrock-agents, no msk-topics-vpc-provisioner, etc.) with a
    brief pointer to `docs/DEPLOYMENT.md` for advanced per-stack
    control. Following the previous Option 4 produced a non-working
    partial deploy.

- **docs/DEPLOYMENT.md — new Post-deploy validation section**:
  Documents `deployment/scripts/validate_staging_publish_gate.sh`
  (the 7-check gate added in v0.2.3, previously only described in
  source comments). Covers the seven gates (CFN states, Flink RUNNING,
  Flink IAM auth correctness, fw-telemetry consumption, trip
  materialization, unauth-probe runtime auth, no critical errors),
  invocation, exit semantics, and the Check 5 "needs simulator
  traffic" caveat.

### Repository hygiene

- **`.gitignore`** — added `deployment/cdk.context.json.*` pattern to
  cover operator-side relocate-and-restore backups (e.g.,
  `cdk.context.json.us-west-2.saved`, `cdk.context.json.harness-mid-run`).
  Prevents accidental commit of region-specific CDK context during
  cross-region work — the same pattern the clean-deploy harness uses
  internally (`isolate_cdk_context` phase) and the operator pattern
  documented in `~/.kiro/steering/cross-region-namespace.md` § 3.

### Issue closeout (paperwork)

- `issues/2026-06-12-flink-domain-consumer-backlog-reset/summary.md`
  filed. Code fix shipped in v0.2.3 — the v0.2.3 tag commit itself
  (`391cd14`: domain processor Kafka sources `earliest()→latest()`).
  RESOLVED.

## v0.2.3 — 2026-06-12

### Security (HackerOne 3775026 + 1 self-discovered)

Resolves 6 authorization defects in the CMS reference template — see
`issues/2026-06-11-h1-3775026-cms-template-auth-gaps/summary.md` for the full
breakdown and `.kiro/specs/2026-06-11-cms-api-authorizer-template-fix/` for the
design + remediation track. Companion follow-up filed as
`issues/2026-06-11-cms-websocket-api-auth-gap/` (P1, NOT in v0.2.3).

### Breaking changes (security hardening)

- **Cognito self-signup now disabled by default.** Previously every CMS
  deployment opened User Pool self-signup with email-only verification,
  which any internet user could exploit to obtain a JWT for the
  fleet-management `/api/v1/*` routes. New default:
  `self_sign_up_enabled=False`. Demo deployments that need self-signup
  opt in via `cdk synth --context cms.allow_self_signup=true`.

- **Identity Pool guest credentials now disabled by default.** Previously
  every deployment issued AWS IAM credentials (Access Key + Session
  Token) to anonymous callers via `cognito-identity:GetId` +
  `GetCredentialsForIdentity`. The role was scoped to Location Services
  only — so blast radius was bounded — but issuance is unnecessary for
  a security-sensitive reference architecture. New default:
  `allow_unauthenticated_identities=False`. The unauthenticated_role
  + IdentityPoolRoleAttachment are conditionally created only when
  the flag is opted in. Demo deployments that need anonymous map UI
  opt in via `cdk synth --context cms.allow_unauth_map_auth=true`.

- **Simulation, commands, predictive-agent, and data-processing API
  Gateways now require Cognito JWT.** Previously all four stacks
  exposed `/api/*` routes with NO authorizer (CDK default
  AuthorizationType.NONE). Anonymous callers could spawn ECS Fargate
  tasks, enumerate other users' simulations, retrieve the full VSS
  actuator catalog, and invoke Bedrock-backed predictive analysis.
  Fixed: every method on those gateways now wires
  `apigateway.CognitoUserPoolsAuthorizer`. The User Pool ARN is
  imported from `ui_stack` via cross-stack reference; consumer stacks
  declare `add_dependency(ui_stack)` to enforce deploy ordering.

- **Migration**: customers redeploying v0.2.3 over an existing
  v0.2.x deployment will see existing self-signed-up users retain
  their JWTs (until expiry) but cannot create new ones. Anonymous map
  UI breaks unless `cms.allow_unauth_map_auth=true` is set.
  Anonymous /api/* probes start returning 401/403. To restore the
  prior demo-permissive behavior, set both context flags to true and
  re-deploy. To honor the secure default, audit existing self-signed-up
  users (`aws cognito-idp list-users --user-pool-id ...`) and delete
  any non-platform-admin / non-fleet-operator accounts.

### Security

- Resolved 6 authorization defects in the CMS reference template
  (HackerOne report 3775026 + 1 self-discovered finding). See
  `issues/2026-06-11-h1-3775026-cms-template-auth-gaps/summary.md`
  for the full root-cause + remediation breakdown.

### Fixed

- **OEM1 Way B Kafka path skipped `handle_unknown_vin` and discarded the VIN**
  (`issues/2026-06-11-oem1-kafka-path-skips-auto-register/`). Under
  `OEM1_EMIT_TARGET=kafka` (the only mode deployed in staging), the connector
  run-loop emitted via `_kafka_raw_payload` and never called `_handle_message`,
  so `auto_register.handle_unknown_vin` was never invoked **and** the Kafka
  payload carried no VIN. That forced `OEMTelemetryProcessor` to recover the VIN
  from a vehicles-table device→VIN scan that only the single vestigial row
  satisfied — 47 of 48 enrolled vehicles produced UUID-keyed orphan trips no
  fleet operator could join to a VIN. Fix: **VIN propagated through Way B Kafka
  payload** as a self-describing top-level `vin` field
  (`connector.py:_kafka_raw_payload`); Flink now prefers VIN in Kafka payload
  over resolver lookup (`OEMTelemetryProcessor.extractVehicleId`); and the Kafka
  run-loop now calls `handle_unknown_vin` (throttled ≤1 DDB write per VIN per
  hour, best-effort) to populate the device→VIN mapping for operator visibility.
  The `deviceToVehicleResolver` is retained as the defensive fallback for
  messages that arrive without a `vin` field. Unblocks the C3.3 close-out gate
  of the `2026-06-01-cms-oem1-transform-manifest-staging-e2e` initiative.

### Documentation

- **`modules/flink/README.md` freshened** (was 3 months stale). Now
  describes `OEMTelemetryProcessor` (runtime-driven manifest transform,
  schema v2.2.0+) as the cloud-feed entry point, lists the canonical-DTC
  handler in `MaintenanceProcessor` (Path ε), and documents the
  device-VIN resolver. File tree updated to reflect current source
  layout. Cross-link to `docs/OEM1_DTC_PIPELINE.md`.
- **`deployment/README.md` freshened** (was 6 weeks stale). Adds
  `OEMTelemetryProcessor` to the Phase 5 Flink processor list, adds an
  optional **OEM1 Connector** phase with `make deploy-connector`
  guidance, fixes the env-var examples (`DEPLOYMENT_STAGE=staging|prod`,
  `AWS_REGION=us-west-2`), adds `CMS_DEMO_DEFAULT_PASSWORD` to the
  required-vars list, and cross-links to the top-level README's
  **Build Prerequisites** section.
- **`docs/OEM1_DTC_PIPELINE.md` rewritten** as a full operator runbook
  covering the end-to-end Path-ε pipeline (gRPC → connector → manifest
  engine → MaintenanceProcessor → `dtc-history` + `vfo-action-queue`),
  the 4-state semantics (ACTIVE / ACTIVE_NO_DTC / CLEARED /
  DTC_CLEARED_INDICATOR_ACTIVE), severity vocabulary, and verification
  queries. Companion to the Phase ε spec close-out below.
- **`services/connectors/oem1/README.md`** updated with a Path-ε
  `string_label` TriggeredEvent decode subsection (Group 2 connector
  lift now reflected in user-facing docs).
- **`services/data_processing/manifests/README.md`** updated with the
  `stringLabelEndsWith` predicate, `cms.vha_diagnostic_event` event
  mapping, MaintenanceProcessor canonical-DTC subsection, and v2.2.0
  schema bump entry.

### Issue close-outs (paperwork-of-record)

`summary.md` files filed for four issues whose code fixes shipped in
prior releases (v0.2.0 / v0.2.1):

- `2026-06-04-oem1-vehicle-missing-enrichment-on-list` — admin-add-vehicle
  enrichment + `status` field + badge state from `vehicle.status`.
- `2026-06-09-oem1-dtc-pipeline-investigation` — Path-ε pipeline shipped
  end-to-end; live DTC row materialized in staging.
- `2026-06-09-cms-eventdriven-fanout-gap` — `MAX_RECORD_AGE_MS` filter
  override unblocked Trip/Safety/Maintenance fan-out; 118 trips +
  166 safety events + 1 DTC row materialized post-fix.
- `2026-06-10-oem1-connector-auto-register-clobbers-seeded-fields` —
  pre-seeded `oem1_device_uuid` population via `if_not_exists` shipped
  in v0.2.1; staging operator-backfilled 2026-06-11.

### Spec close-out

- **OEM1 DTC engine-light pipeline (Path ε)** — closeout commit `9605e28`.
  Group 5 live-traffic e2e validation PASS: real OEM1 `dtc-history` rows
  materializing with `source=oem1-uds-dtc`. Case 1 (ACTIVE / CRITICAL /
  TIRE_PRESSURE_MONITOR_SYSTEM_WARNING with B124D) + Case 3 (ACTIVE_NO_DTC
  / LOW / LOW_WASHER_FLUID with empty code) materialized; 4 PENDING
  `vfo-action-queue` rows with `source=dtc-critical`,
  `sourceTag=oem1-uds-dtc`. All 8 schema columns populated. 100%
  reduction of Path-ε DLQ traffic post-deploy (0/100 stratified samples
  vs ~8/hour baseline). Group 6 docs landed (see Documentation section
  above).

### Pending — to be merged before tagging v0.2.3

(none — all v0.2.3 work merged; Fix 1 cluster lands the
flink-stack-deploy-blockers gate as part of this release.)

### Flink CDK migration completion (publish-gate fix)

The `2026-06-08-cms-flink-cfn-config-keys-fix` spec (commit `fe42393`) migrated
property-map sourcing for the 9 KDA Flink apps from the out-of-band
`make configure-flink` loop into CDK's `create_flink_app_config` helper but
DROPPED the MSK auth/connection block that the historical loop set per-app.
Synth verification at the time passed (synth cannot detect missing MSK props);
the regression stayed latent for 3 days because staging was never cdk-deployed
post-`fe42393`. The first post-`fe42393` `cdk deploy` on 2026-06-11 stripped MSK
config from every running app → broad staging telemetry outage. See
`issues/2026-06-11-flink-stack-deploy-blockers/`.

This release ports the historical MSK property block from
`git show fe42393^:deployment/Makefile` into CDK so `cdk deploy` no longer
strips MSK on redeploy. **The IoT Rule SCRAM path is intentionally preserved
unchanged** — only the Flink-app side was migrated to IAM. Two distinct MSK
auth surfaces:

- **IoT Rule** → SCRAM (`BootstrapBrokerStringSaslScram`, port 9096) — wired
  in `telemetry_integration_stack.py`, untouched.
- **Flink app** → IAM (`BootstrapBrokerStringSaslIam`, port 9098) — now wired
  in `flink_stack.py`, restored from history.

The IAM permissions (`kafka-cluster:Connect/ReadData/WriteData/AlterGroup`)
were already attached to `flink_role`; only the wire-protocol config in the
PropertyMap needed restoring.

Also closes a same-class latent regression that was waiting in `app.py`: the
standalone `make deploy-flink` path (which sets `MSK_CLUSTER_ARN` env var so
`app.py` skips msk_stack creation) was falling through to `FlinkStack`'s
`else: msk_available=False` branch — silently producing Flink configs with no
MSK auth at all. Same failure mode an operator would hit pushing a JAR-only
update later (and the trap that caused 2026-06-11's outage). Now `app.py`
propagates `MSK_CLUSTER_ARN`/`MSK_VPC_ID`/`MSK_SECURITY_GROUP_ID`/
`MSK_SUBNET_IDS` to `FlinkStack`, routing through the
`elif msk_cluster_arn:` branch which sets `msk_available=True`.

### Fixed

- **Flink CDK MSK auth restored to AWS_MSK_IAM**
  (`issues/2026-06-11-flink-stack-deploy-blockers/`). `create_flink_app_config`'s
  `msk_available` branch now sets the 5 IAM auth keys (`bootstrap.servers`,
  `security.protocol=SASL_SSL`, `sasl.mechanism=AWS_MSK_IAM`, `sasl.jaas.config`,
  `sasl.client.callback.handler.class`) and drops the SCRAM residue
  (`sasl.username`, `secret.arn`, `msk.cluster.arn`). The shared `group.id`
  default is removed — every call site overrides per-app, matching the
  pre-`fe42393` `configure-flink` per-app `GROUP_ID` semantics (each Flink app
  needs its own consumer group so they don't compete for partitions on shared
  topics). `BOOTSTRAP_SERVERS` is read from env at synth time via
  `os.environ.get` with a soft warn-fallback (mirrors the `REDIS_ENDPOINT`
  pattern that the same issue introduced).
- **`deployment/Makefile` `phase4` + `deploy-flink` targets self-resolve
  `BOOTSTRAP_SERVERS`** via `aws kafka get-bootstrap-brokers --cluster-arn ...
  --query BootstrapBrokerStringSaslIam`. No operator-side env exports required.
- **`phase-foundation` ordering corrected**: `phase3` (MSK) → `phase1`
  (Storage / IoT / UI) → `data-processing` → `phase3b`
  (telemetry-integration). The H1 auth fix made `data_processing_stack` import
  `cms-{stage}-ui-user-pool-arn` from `ui_stack` via `Fn.import_value`; that
  export only exists once `phase1` has run with the post-auth-fix template.
  Old order (`data-processing` first) failed fast on every existing-deploy
  `make deploy-all` with `Resolution error: No export named
  cms-{stage}-ui-user-pool-arn found`. Fresh-account customer deploys are
  also fixed by this reorder.
- **Two pre-existing Flink CDK bugs that emerged during today's recovery**
  (`issues/2026-06-11-flink-stack-deploy-blockers/`): (a)
  `ParallelismConfigurationProperty.configuration_type` was `DEFAULT` while
  custom values were supplied — KDA's UPDATE contract requires `CUSTOM`,
  rolling back the whole stack on `cdk deploy`. Now `CUSTOM`. (b) `REDIS_ENDPOINT`
  rendered empty in the existing-MSK path because `app.py` set
  `msk_stack=None` whenever `MSK_CLUSTER_ARN` was exported. Now falls back to
  `os.environ.get("REDIS_ENDPOINT", "")` and the `deploy-flink` Makefile target
  auto-resolves it from the `cms-{stage}-msk` stack `RedisEndpoint` output.
- **`FWTelemetryProcessor` fail-fast guard** — `params.get("DECODER_TABLE",
  "cms-prod-decoder-manifest")` previously fell back to the **prod** decoder
  table when env was misconfigured. The decode map disagreed by signal_id,
  every FWE signal was mislabeled, and TripProcessor created 0 trips for
  ~11 days. Now `requireParam("TABLE_NAME", "DECODER_TABLE")` throws on
  missing/blank, surfacing the misconfiguration immediately at app startup.
  See `issues/2026-06-11-fw-telemetry-decoder-table-prod-default/`.
- **`FWTelemetryProcessor.loadSignalNames` paginated** — DDB query returned
  only the first 1 MB page; `paginatorFor(...).items()` now retrieves all rows
  (regression risk grew with the staging signal catalog past 600 entries).

### Added

- **`deployment/scripts/validate_staging_publish_gate.sh`** — 7-check
  read-only post-deploy validation gate: CFN stack states, Flink RUNNING,
  Flink IAM auth correctness, fw-telemetry consumption, trip materialization,
  unauth-probe runtime auth, critical-path log errors. Used by operators to
  determine v0.2.3 publish-readiness against staging before tagging.

### Fixed (post-Fix-1 cluster — discovered during publish-gate validation)

- **TripProcessor env-var key mismatch** (`TRIPS_TABLE_NAME` → `TABLE_NAME`,
  same bug class as 851addd's FW fix). Java reads `TABLE_NAME`; CDK was
  setting `TRIPS_TABLE_NAME`, silently falling through to the hardcoded
  `cms-dev-storage-trips` default → every `getActiveTripForVehicle` DDB
  query returned `Requested resource not found` on staging/prod. Latent
  since fe42393 migrated property-map sourcing into CDK with the wrong
  key. Surfaced 2026-06-12 by validate_staging_publish_gate.sh Check 7.
- **JAR-prune trap closed**:
  (a) `configure-flink` (a.k.a. `phase5`) was running `mvn clean package`
  directly, which deletes `target/` and regenerates the `.jar` but NOT
  the `.zip` (the canonical staging step `cp .jar .zip` lives in
  `modules/flink/build.sh`). Subsequent `make deploy-flink` saw a missing
  `.zip` → CDK's BucketDeployment with `prune=True` wiped the live JAR
  from S3 → KDA UpdateApplication failed with
  `InvalidRequest: fileKey ... not found`. **Fix**: `configure-flink` now
  invokes `bash build.sh` (atomic `mvn package` + `cp .jar .zip`).
  (b) `phase5` removed from `deploy-all` — redundant after fe42393's CDK
  migration (phase4's `cdk deploy` already pushes the JAR via
  BucketDeployment), and its `mvn clean` was the prune trigger.
  (c) New `deploy-flink` precondition: fail loud BEFORE invoking `cdk
  deploy` if `target/cms-telemetry-processor-1.0.0.zip` is missing.
- **`validate_staging_publish_gate.sh` false-positive cleanup**: Check 2
  (Flink apps RUNNING) excluded `campaign-sync-processor` (in fleetwise
  stack, not flink); Check 7 (critical errors) switched from coarse
  `?ERROR ?Exception` filter to structured `{$.messageType="ERROR"}`
  filter (was matching Flink's verbose config-dump strings); Check 6
  (auth probes) tolerates predictive-agent stack absence (optional via
  DEPLOY_PREDICTIVE_AGENT=true).

## v0.2.2 — 2026-06-11

### Changed

- **Dependency bumps** (Dependabot-flagged on the public mirror,
  routine non-security; all past the 7-day quarantine per
  `dependency-versions.md`):
  - `urllib3`: 2.6.3 → 2.7.0 (34 days old at bump time) — `services/simulation`
  - `idna`: 3.13 → 3.15 (29 days old at bump time) — `services/simulation`
  - `protobuf`: 5.28.3 → 5.29.6 (126 days old at bump time) — `services/connectors/oem1`

### Pipeline note

Same flow as v0.1.2: the internal-GitLab → public-GitHub publish is a
squash force-push, so Dependabot PRs filed against the public mirror
get wiped on the next release. Dependency updates land via this normal
release flow: bump in GitLab, run Tier 3 evals to verify no regression,
cut a patch tag, trigger the manual publish.

## v0.2.1 — 2026-06-11

### Fixed

- **Public-mirror publish flow: tracked files silently filtered.** The
  `scripts/publish-to-github.sh` script's `git init` + `git add .` step
  in the staging tree was applying the source repo's `.gitignore` rules
  on a fresh-init basis. Broad ignore patterns (`*credentials*`, etc.)
  were dropping ~16 user-facing files from the public mirror tarball:
  - `modules/cms_ui/source/frontend/src/api/credentials-provider.ts`
    (Cognito Identity Pool credentials helper — UI build fails without it)
  - `modules/cms_ui/source/frontend/scripts/pre-build-cleanup.js`
    (yarn `prebuild` hook — UI build fails without it)
  - `modules/cms_ui/source/frontend/src/components/recall-warranty/nhtsaRecallData.ts`
    (NHTSA recall data — auto-regenerated, but build-time required)
  - `services/data_processing/signal-catalog.json` (manifest engine
    reference data)
  - `services/commands/command_request.proto`
  - `modules/campaign_manager/{ARCHITECTURE.md,campaign_api.py,campaign_stack.py}`
  - `.config/eslint.config.js`, `.config/prettier.config.js`
  - `.publish-secrets-scan.yml`

  Fix: `git add -f .` in the publish flow. The source-of-truth for what
  ships is the staging tree (post `.publish-exclude` strip + scanner
  PASS), NOT the source repo's `.gitignore`. v0.2.0's public mirror
  was missing these files; v0.2.1 ships the complete tree.

### Added

- **README: Build Prerequisites** — explicit guidance for the two
  required pre-build artifacts (Flink JAR and UI build), the Yarn 4
  Corepack setup, and the `CMS_DEMO_DEFAULT_PASSWORD` environment
  variable. Closes the gap that prevented external users from
  successfully running `make deploy-all` on a fresh public-mirror
  clone.
- **README: Option 3 — Clean-deploy validation** — documented the
  clean-deploy harness (`make clean-deploy-test REGION=...`) as the
  recommended way to validate a fresh-account, fresh-region deployment
  end-to-end.
- **README: updated Prerequisites** — Java 11 + Maven + Docker now
  explicitly listed; Corepack-managed Yarn 4 setup commands added.
- **README: corrected phase list** — replaced the outdated v0.1.x
  phase numbering with the current grouped-phase model
  (`phase-foundation`, `phase-streaming`, `phase-seeds`, `phase5`,
  `phase-services`) plus all individual deploy targets
  (`deploy-fleetwise`, `deploy-simulation`, `deploy-commands`,
  `deploy-ws-fanout`, `deploy-tco`).

## v0.2.0 — 2026-06-11

### Added

- **OEM1 cloud-telemetry integration** — full pipeline for ingesting
  vendor-shape gRPC streaming feeds into the CMS canonical-event
  topology. New components:
  - **OEM1 connector** (Fargate, Python) — reads gRPC feed, decodes
    vendor protobuf event/telemetry types (Event, TriggeredEvent,
    StateTransition, GeofenceEvent, Metric, RawTelemetry,
    BatchedTelemetry), publishes to `cms-telemetry-oem` MSK topic.
  - **Transform-manifest engine** (`OEMTelemetryProcessor`) — schema-
    versioned (v2.2.0) manifest config drives signal extraction
    + event matching + vehicle-id resolution at runtime. New
    `stringLabelEndsWith` predicate for custom-label TriggeredEvents
    (used for vendor-specific diagnostic events).
  - **Custom Diagnostic Event pipeline** (Path ε) — vendor VHA-shape
    diagnostic events produce canonical `cms.vha_diagnostic_event`
    records with 4 sub-states (active-with-DTC, active-no-DTC,
    cleared-warning, dtc-cleared-indicator-active) materialized in
    `dtc-history`. Severity vocabulary URGENT/HIGH/MEDIUM/LOW with
    defensive default. Vendor-supplied DTC system + symptomKey +
    customerActionKey preserved in canonical row.
  - **Device→VIN resolver** — runtime scan of `vehicles` table
    populates a deviceUuid→vehicleId map at manifest load time,
    refreshed every 5 min on cache TTL. Unenrolled devices DLQ
    with descriptive error.
  - **OEM Fleet Bulk Management** — admin Lambdas for bulk
    enroll / unenroll / refresh-status / preflight, with
    fleet-operator IAM gating + UI affordances (FleetPicker,
    EnrollWizard, BulkUnenrollModal).

- **Trip + Safety canonical-event passthrough** — `TripProcessor` and
  `SafetyProcessor` now accept Path-β canonical events from
  `OEMTelemetryProcessor` alongside their FWE-shape inputs. Cross-OEM
  reporting (harsh-acceleration, harsh-braking, harsh-cornering,
  motion-state-change, ignition-state-change, gear-change,
  trip-report) works the same regardless of source.

- **Maintenance canonical-DTC handler** — `MaintenanceProcessor`
  dispatches on `cms_event_type == cms.vha_diagnostic_event` to
  write `dtc-history` rows with `source: oem1-uds-dtc` parity vs
  FWE's `fwe-uds-dtc`. CRITICAL-severity events fan out to
  `vfo-action-queue` for triage.

- **Data Source model refactor** — vehicles + fleets carry an explicit
  `dataSource` enum (`vehicle-telemetry` / `cloud-telemetry` /
  `vehicle-and-cloud`) replacing prior implicit per-OEM literals.
  Backend dual-read helper (`_lib/data_source.py`) handles the
  rename gracefully; backfill script in
  `deployment/scripts/backfill_data_source_enum.py`.

- **API field normalization** — Lambda boundary normalizes
  `snake_case` DDB attributes to `camelCase` for the vehicle-detail
  REST surface, dropping dual-shape tolerance in the UI.

- **Fleet Manager Cognito role widening** — admin Lambdas + UI
  affordances support both `platform-admin` (cross-fleet) and
  `fleet-operator` (per-fleet via `custom:fleetIds` +
  `vehicleId-index` GSI) groups.

- **OEM1 vehicle UI separation** — vehicles list distinguishes
  `Vehicle Telemetry` (FWE-source) from `Cloud Telemetry`
  (OEM1-source) via Source column; add-OEM1-vehicle UX flow.

- **Bucket retention aspect** — CDK Aspect at
  `deployment/aspects/bucket_retain_aspect.py` walks every L1
  `CfnBucket` in scope; if the bucket has an explicit name (proxy
  for "globally namespaced"), the aspect asserts
  `DeletionPolicy == Retain` and FAILS synth otherwise. Locks the
  invariant against future CDK-major default changes that could
  silently flip the L2 Bucket deletion-policy default away from
  RETAIN.

- **Cross-region namespace discipline** — multiple S3 buckets
  region-suffixed (storage, FrontendBucket, transform-manifests,
  predictive-agent, simple-flink, ui, vfo-knowledge-base) so
  `staging` deployments to alternate regions don't collide on
  partition-global names. IAM role names + ECS task-def names
  similarly suffixed where applicable.

- **Bedrock model bump to Claude Sonnet 4.6** — supervisor + workers
  + predictive-agent on `us.anthropic.claude-sonnet-4-6` for both
  staging and prod; portfolio-aligned across CMS / CVX.

- **MSK topic provisioner** — replaced the prior Fargate-in-VPC topic-
  creator with a Lambda using AWS MSK control-plane API
  (`aws kafka create-topic` via SDK). Smaller, faster, no VPC
  attachment required.

- **Clean-deploy harness** — `deployment/scripts/clean-deploy.sh`
  validates a fresh CDK deploy from an empty AWS account in any
  region, with cdk-context isolation (relocate-and-restore via
  `isolate_cdk_context` phase) so primary-region context doesn't
  leak into a second-region deploy.

- **staging: FWE-agent lifecycle Phase 2** — deploy-time drain script
  (`deployment/scripts/drain_stale_fwe_agents.sh`) prevents Bug 4
  (deploy-time zombie ENOMEM) by reaping any RUNNING `cms-{stage}-fwe-agent`
  task whose `taskDefinitionArn` revision is below the family's latest
  active revision. Wired as a post-deploy step in `make deploy-simulation`
  and as a standalone `make drain-stale-fwe-agents` target for ad-hoc
  operator use. New CloudWatch metrics published every 5 minutes under
  namespace `FWE/Cluster` (`AgentCount`, `OrphanAgentCount`,
  `StaleRevisionAgentCount`) via the new `cms-{stage}-fwe-agent-counter`
  Lambda. Three alarms (`cms-{stage}-fwe-orphan-agent`,
  `cms-{stage}-fwe-stale-revision-agent`,
  `cms-{stage}-fwe-agent-counter-errors`) wired to a new SNS topic
  `cms-{stage}-simulation-alarms` (operators subscribe out-of-band).
  Runbook + manual-drain commands documented in `docs/DEPLOYMENT.md`
  § Simulation lifecycle.

### Fixed

- **iOS voice — bidi WebSocket "not connected" wedge resolved.**
  `VoiceSessionViewModel.connect()` and `sendText()` now detect the
  stale active-state-with-nil-client wedge that previously surfaced as
  `Send: WebSocket not connected` and force a clean reset + reconnect
  instead of silently failing. `AssistantTabView.task` resets a stuck
  `.error`-state view model before reconnecting on tab open. Status:
  MITIGATED — defensive recovery closed the symptom; underlying state
  wedge cause not directly observed; instrumentation retained.

- **iOS voice — empty-KB tool result no longer tears down the session.**
  When `lookup_knowledge` returns `found==0`, the sentinel
  `"Knowledge base not configured."` answer, or an empty answer string,
  the iOS client now injects a deterministic fallback narration
  (`"I don't have detailed information on that. Anything else I can help
  with?"`), shows a transcript bubble synchronously, and re-arms the
  silence watchdog with a fresh window. Empty-KB DTC questions stay
  conversational instead of dropping the session. Status: RESOLVED.

- **iOS voice — diagnostic instrumentation across the voice flow.** Added
  ~125 prefixed `NSLog` sites with `🎤 VOICE:` / `🎤 ATV:` / `🎤 MTV:` /
  `🎤 BIDI:` / `🎤 CRED:` so future voice bugs can be diagnosed from a
  single simulator log capture. See `clients/ios/README.md` § Diagnostics.
  Logging discipline verified clean of JWT/credential bodies.

- **MaintenanceProcessor region resolution** — KDA runtime does NOT
  surface `aws.region` as an OS env var, so the prior fallback to
  `us-east-1` caused all DTC writes to silently land in the wrong
  account/region. Now reads `aws.region` from KDA app properties +
  threads through to the DDB client builder. Same fix in
  `OEMTelemetryProcessor` for the new device-resolver scan.

- **Connector auto-register UUID population** — pre-seeded vehicles'
  `oem1_device_uuid` was never set on first event; prior path only
  updated `last_seen_at` + `status`. Now also SETs `oem1_device_uuid`
  + `oem1_shard_uuid` via `if_not_exists` so the device→VIN resolver
  can map them. Idempotent.

- **Simulator GPS lat/lng = 0** — pass `ROUTE_CALCULATOR_NAME=-here`
  to the simulator ECS task (was missing from task-def env vars).
  Fixes simulated vehicles showing all-zero coordinates on staging.

- **CMS UI fuel level rounding** — round to 1 decimal place to avoid
  `0.49999...` UI artifacts.

- **IoT lifecycle Lambda syntax error** — duplicate `except` clause
  caused `Runtime.UserCodeSyntaxError` on every invocation. Fixed.

### Changed

- **staging: drivers/Cognito parity with prod** — drivers vehicle-aware
  seeding (real `cms-staging-storage-vehicles` IDs replace synthesized
  `VEH-NNNN`), VSA user pool ID wired through CDK
  context (`deployment/cdk.json`), simulator fail-closed when drivers
  table is empty (no more phantom `_ensure_driver_exists` rows), new
  `deployment/scripts/cleanup_phantom_drivers.py` for one-off cleanup of
  legacy phantoms, server-side `status` validation on the drivers
  create/update API (`active|on_leave|terminated`), and
  `Driver.status` TypeScript enum widened from `{active,inactive}` to
  `{active,on_leave,terminated}`. New simulator `assigned` driver-selection
  mode is now the default (picks the active driver bound to the simulated
  vehicle).

## v0.1.3 — 2026-05-27

### Changed

- **Replaced `.github/workflows/codeql.yml` with `lint.yml`**. The
  `aws-solutions-library-samples` org enforces CodeQL Default Setup at
  the org level, which conflicts with our Advanced CodeQL workflow at
  SARIF upload time. Replacing the CodeQL workflow with a minimal lint
  workflow (yamllint + shellcheck) lets Default Setup scan the
  repository's languages (Python, JavaScript/TypeScript, Java, Actions)
  without conflict. CodeQL coverage is unchanged — Default Setup still
  performs the security analysis.

## v0.1.2 — 2026-05-27

### Changed

- **Dependency bumps** (Dependabot-flagged, both routine non-security):
  - `pytest`: 8.3.3 → 9.0.3 (major version bump; verified clean against
    the Tier 3 eval suite — 4/5 passing baseline holds)
  - `requests`: 2.32.5 → 2.34.2 (minor bump to current latest stable;
    skips Dependabot's interim 2.33.0 suggestion)

### Pipeline note

The internal-GitLab → public-GitHub publish flow does not auto-merge
Dependabot PRs from the public mirror (squash force-push wipes them
on the next release). Dependency updates land via this normal release
flow: bump in GitLab, run Tier 3 to verify no regression, cut a patch
tag, trigger the manual publish.

## v0.1.1 — 2026-05-27

### Fixed

- **CodeQL static analysis workflow added** (`.github/workflows/codeql.yml`).
  v0.1.0 was missing this file and the org-level CodeQL default-setup failed
  on every push with "CodeQL detected code written in GitHub Actions but
  could not process any of it." The new workflow scans Python,
  JavaScript/TypeScript, Java, and Actions languages on push to main, every
  PR against main, and weekly. SHA-pinned actions throughout.

### Changed

- **`.publish-exclude` granularity**: replaced the broad `.github/workflows/`
  exclude with specific-file entries (`deploy.yml`, `evals.yml` — the
  internal CI design references). Public-facing workflow files (currently
  just `codeql.yml`) now ship to the public mirror.

## v0.1.0 — 2026-05-26

First public release. CDK-based reference accelerator for fleet management,
telematics, and connected vehicle applications on AWS.

### Added

- **Connected Mobility System (CMS) deployment** — 12 CDK stacks deployable to a
  single AWS account, two-region model (staging + prod):
  - data-processing, storage, iot, ui, msk, telemetry-integration, flink,
    fleetwise (FWE telemetry), simulation, commands, ws-fanout, tco
- **Quick UI deploy** — `make ui-quick-deploy` provides a ~30-second loop
  for UI-only changes (yarn build → S3 sync → CloudFront invalidate),
  bypassing the full CDK round-trip.
- **Tier 3 evaluation pipeline** — REST + WebSocket end-to-end integration
  tests with a 4/5 passing baseline against deployed staging.
- **Pre-sync secret scanner** — Python 3 CLI runs against build outputs and
  publish staging trees, blocks any critical findings (account IDs, internal
  hostnames, Cognito identifiers, customer names).
- **Sanitizing publish flow** — `scripts/publish-to-github.sh` strips
  internal-only paths via `.publish-exclude` and runs the secret scanner
  before push. GitLab CI manual-trigger job (`publish_to_github`) wraps
  the same flow for tag-triggered releases.
- **Customer-rebrand placeholder** — `Acme Motors` is the canonical generic
  customer name throughout mock data and UI strings.
- **AWS Solutions Library boilerplate** — Apache 2.0 LICENSE, NOTICE,
  CONTRIBUTING.md, CODE_OF_CONDUCT.md.

### Known Limitations

- **Tier 3 WebSocket eval (case 04)** is `KNOWN-FAILING`. The CMS WebSocket
  `$connect` Lambda expects `?fleetId=&token=` query params; the eval runner
  doesn't yet substitute these. Tracked for the next minor release.
- **Federate (Corporate SSO) requires runtime configuration**. The Federate
  button in the UI only renders when `runtimeConfig.cognitoDomain` and
  `awsCredentials.userPoolWebClientId` are both supplied at deploy time.
  No hardcoded fallback values ship.
- **Eval-user stack is provisioned separately**. `make staging-deploy`
  does NOT include the eval-user stack; deploy it explicitly with
  `cdk deploy cms-staging-eval-user` after the main staging deploy, then
  promote the auto-generated password to permanent via
  `cognito-idp admin-set-user-password --permanent`.
- **CDK pollution caveat**. `deployment/cdk.context.json` (gitignored) can
  pick up per-developer values that conflict across stages. If a deploy
  fails with "No export named …" errors, clean the relevant entries from
  `cdk.context.json` and retry.
- **npm dependency vulnerabilities**. The CMS UI has known CRITICAL/HIGH
  npm audit findings in `vite`, `serve`, `ajv`. Patches are deferred to a
  follow-up tech-debt release.

### Repository Topology

This release is published from an internal GitLab source-of-truth via a
sanitizing publish flow. The public repository at
`aws-solutions-library-samples/guidance-for-connected-mobility-on-aws`
receives only sanitized, semver-tagged releases. Continuous mirroring
is intentionally NOT used; each release is a deliberate human-gated
publish.

### Acknowledgments

This project was developed in collaboration with the AWS Solutions Library
team. Thanks to all contributors and reviewers who helped shape the
deployment, observability, and security patterns documented here.
