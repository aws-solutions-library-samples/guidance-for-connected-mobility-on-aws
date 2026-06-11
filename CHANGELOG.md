# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

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
