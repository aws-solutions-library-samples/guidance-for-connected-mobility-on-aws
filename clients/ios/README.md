# VSA Companion — iOS Driver App

A SwiftUI native iOS application that provides fleet drivers with voice-powered vehicle assistance, real-time alerts, service scheduling, and vehicle status — all backed by the Connected Mobility platform.

> **Status: In Development** — Functional demo app. Voice features require the [Connected Vehicle Experience](https://github.com/aws-solutions-library-samples/guidance-for-connected-vehicle-experience-on-aws) backend deployed.

## Features

- **Voice Assistant** — Hands-free interaction via Amazon Bedrock AgentCore (Nova Sonic bidirectional voice)
- **Vehicle Dashboard** — Real-time telemetry, trip history, DTC alerts
- **Service Scheduling** — View upcoming and past service appointments
- **Safety Alerts** — Push-style alerts for DTCs, recalls, maintenance due
- **Biometric Auth** — Face ID / Touch ID with Cognito JWT session management
- **Multi-tenant Theming** — OEM-branded UI via tenant configuration

## Prerequisites

- macOS with Xcode 16+
- iOS 18.0+ device or simulator
- Deployed backend stacks:
  1. [Connected Mobility (CMS)](../../README.md) — fleet data platform
  2. [Connected Vehicle Experience (VFO)](https://github.com/aws-solutions-library-samples/guidance-for-connected-vehicle-experience-on-aws) — AI agent + REST API

## Configuration

All backend connection details live in a single file:

```
VSACompanion/Config/VSAConfig.swift
```

After deploying CMS and VFO, update these values from your stack outputs:

| Field | Source |
|-------|--------|
| `restApiUrl` | VFO stack output: `VsaApiEndpoint` |
| `wsApiUrl` | VFO stack output: `VsaWsEndpoint` |
| `agentCoreBidiRuntimeArn` | AgentCore deploy output (runtime ARN) |
| `tenantPools[].userPoolId` | VFO stack output: `CognitoUserPoolId` |
| `tenantPools[].clientId` | VFO stack output: `CognitoClientId` |
| `tenantPools[].identityPoolId` | VFO stack output: `IdentityPoolId` |
| `awsRegion` | Your deployment region |

### Getting Stack Outputs

```bash
# From the CMS repo deployment/ directory:
aws cloudformation describe-stacks \
  --stack-name vsa-prod-api \
  --query 'Stacks[0].Outputs' \
  --output table
```

## Build & Run

> **Demo presenter?** For the iOS→Amazon Connect demo runbook at
> `docs/runbooks/ios-connect-demo.md`, skip Xcode entirely and use
> `clients/ios/scripts/install_ios_sim_demo_app.sh` (once the developer
> has produced a `.app.zip` via `build_ios_simulator_app.sh`). See
> `docs/DEPLOYMENT.md` § "iOS demo app (VSACompanion): rebuild cadence"
> for the two-script flow. The instructions below are for the developer
> iteration loop.

1. Open the project in Xcode:
   ```bash
   open clients/ios/VSACompanion.xcodeproj
   ```

2. Update `VSAConfig.swift` with your deployment values (see Configuration above)

3. Select your target device/simulator and build (⌘B) then run (⌘R)

No external package dependencies — the app uses only Apple frameworks (SwiftUI, AVFoundation, LocalAuthentication, CryptoKit).

## Project Structure

```
VSACompanion/
├── Config/VSAConfig.swift       # ← Edit this to point at your deployment
├── Api/
│   ├── VSAClient.swift          # REST client (vehicles, trips, service-history)
│   ├── Models.swift             # API response models
│   └── TriageCoordinator.swift  # Auto-triage orchestration
├── Auth/
│   ├── AuthService.swift        # Cognito sign-in flow
│   ├── BiometricAuth.swift      # Face ID / Touch ID
│   └── KeychainStore.swift      # Secure token storage
├── Voice/
│   ├── VoiceSessionViewModel.swift  # Voice UI state machine
│   ├── VSABidiClient.swift      # AgentCore WebSocket client
│   ├── AudioCapture.swift       # Microphone → PCM frames
│   └── AudioPlayer.swift        # PCM frames → speaker
├── Views/
│   ├── HomeTabView.swift        # Dashboard with vehicle summary
│   ├── VehicleTabView.swift     # Telemetry + trip history
│   ├── AlertsTabView.swift      # DTCs, recalls, maintenance
│   ├── ServiceTabView.swift     # Service history + scheduling
│   ├── AssistantTabView.swift   # Voice + text chat
│   └── AccountTabView.swift     # Driver profile + settings
└── Theming/TenantTheme.swift    # OEM brand colors/logos
```

## How It Connects to CMS

```
┌──────────────┐     Cognito JWT      ┌──────────────────┐
│  iOS App     │ ──────────────────── │  VFO REST API    │
│              │     REST calls        │  (API Gateway)   │
│  VSAClient   │ ──────────────────── │                  │
│              │                       │  Reads CMS DDB:  │
│              │     SigV4 WebSocket   │  vehicles,       │
│  VSABidi     │ ──────────────────── │  drivers, trips, │
│  Client      │                       │  service-history │
└──────────────┘                       └──────────────────┘
                                              │
                                              ▼
                                       ┌──────────────────┐
                                       │  CMS Data Layer  │
                                       │  (DynamoDB +     │
                                       │   Redis)         │
                                       └──────────────────┘
```

The iOS app does **not** talk to CMS directly — it goes through the VFO API layer which handles auth, data aggregation, and AI agent orchestration.

## Demo Users

After running `make bootstrap-demo` in the CMS repo and `make seed-staging` in the CVX repo (see CVX `docs/DEPLOYMENT.md`), these demo accounts are available:

| Email | Driver | Vehicle |
|-------|--------|---------|
| `stephanie.johnson@example.com` | DRV-0055 | VEH-0025 (2022 Chevrolet Equinox) |

Password: Set during VFO seed script. The demo password is hardcoded only in DEBUG builds; production builds require real authentication. See `VSAConfig.swift` for the DEBUG-only fallback.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "Network error" on launch | Verify `restApiUrl` in VSAConfig.swift matches your deployed API |
| Sign-in fails | Check Cognito user pool ID + client ID match your VFO deployment |
| Voice not connecting | Verify `agentCoreBidiRuntimeArn` and that AgentCore runtime is deployed |
| Empty vehicle data | Run `make bootstrap-demo` in the CMS repo to seed fleet data |

## Diagnostics

The voice flow is instrumented with prefixed `NSLog` lines so a single
simulator log capture yields a complete causal timeline. Prefixes:

| Prefix | Subsystem |
|---|---|
| `🎤 VOICE:` | `VoiceSessionViewModel` — state machine, connect/disconnect, sendText, tool results, watchdog |
| `🎤 ATV:` | `AssistantTabView` — task lifecycle, viewModel resolution, seed message |
| `🎤 MTV:` | `MainTabView` — FAB tap, (i)-tap, cover dismiss/teardown |
| `🎤 BIDI:` | `VSABidiClient` — WebSocket handshake, send/throw paths |
| `🎤 CRED:` | `AwsCredentialProvider` — Cognito Identity Pool credential exchange |
| `💬 CHAT:` | `ConnectChatClient` — Amazon Connect chat over WebSocket |

Capture filtered logs from a booted simulator:

```bash
xcrun simctl spawn booted log stream --style syslog 2>/dev/null \
  | grep -E '🎤|💬|VSACompanion' \
  | tee /tmp/cms-voice.log
```

> Use `--style syslog` (not `--predicate 'eventMessage CONTAINS "🎤"'`) —
> predicate filters do not capture `NSLog` from apps started via
> `simctl launch`. See `~/.kiro/skills/ios-debug/SKILL.md` for the
> CMS-specific debugging patterns.

The instrumentation discipline (verified by `security-review.md` for spec
`2026-05-27-ios-voice-bidi-and-watchdog-fixes`):

- JWT bodies, secret keys, and session tokens are never logged.
- Token lengths are logged (`jwtLen=%d`, `idTokenLen=%d`).
- Access key IDs are logged as a 4-character prefix only — always `ASIA*`
  for Cognito Identity Pool temp creds, zero entropy disclosed.
- Cognito error response bodies are logged on non-2xx only (success bodies
  containing credentials log `bodyLen` only).
