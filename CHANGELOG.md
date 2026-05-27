# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
