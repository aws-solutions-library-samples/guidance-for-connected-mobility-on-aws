# Connected Services Portal: OEM2 Manifest-Driven Simulation (Path B)

**Spec**: `.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/`  
**Date verified**: 2026-09-13

---

## Portal-to-Topic Trigger Chain

The Connected Services portal (`cs.staging.example.com`) enables vehicle simulation via an HTTP interface that gates traffic by rule-name allowlist and fleet scope before any vehicle produces telemetry.

### 1. Portal sends rule_name in POST /start

The portal **sends** the product rule name in the request body, sourced from `runtimeConfig.simulationProductRuleName`:

```json
POST /api/simulation/start
{
  "vehicles": ["VEH-MRDN-0011"],
  "rule_name": "cms_staging_cs_product_meridian_ev_rule",
  "trips": 1,
  "city": "seattle",
  "route_length": 8
}
```

**Important distinction**: `vehicles` carries the **vehicleId** (`VEH-MRDN-0011`), not the VIN,
even though the field name and the Lambda's own variable names all say VIN. Two different lookups
key on it:

- `resolve_vins_to_fleets` (authorization) queries the fleet-enrollment table's `vehicleId-index`,
  so it resolves **vehicleId → fleet**. Passing a real VIN returns `{}` and the route answers 404.
- `_start` (execution) then normalizes each bare vehicleId into
  `{"vehicleId": ..., "vin": <looked up from the vehicles table>}` and passes that on as
  `--vehicle-config`.

The demo vehicle's two identifiers, which genuinely differ:
- **vehicleId**: `VEH-MRDN-0011` — what you send to `/start`
- **VIN**: `MRDN0000000000013` — stored on the vehicle row and used for the IoT Thing name

Measured 2026-09-13: `resolve_vins_to_fleets(['VEH-MRDN-0011'])` returns
`{'VEH-MRDN-0011': 'flt-meridian-range-001'}`, while
`resolve_vins_to_fleets(['MRDN0000000000013'])` returns `{}`. Related open issue:
`issues/2026-09-05-vehicleid-diverges-from-vin`.

### 2. Lambda allowlists rule_name by EXACT match

The Lambda handler (`simulation_lambda.py`) validates the caller-supplied `rule_name` against an
allowlist of **suffixes**, stage-qualified at use time:

```python
_ALLOWED_RULE_SUFFIXES = (
    "iot_msk_rule",                 # CMS-native: cms_{STAGE}_iot_msk_rule
    "cs_product_meridian_ev_rule",  # CS product:  cms_{STAGE}_cs_product_meridian_ev_rule
)

def _resolve_rule_name(config: dict) -> str:
    default = f"cms_{STAGE}_iot_msk_rule"
    raw = config.get("rule_name", None)
    if not isinstance(raw, str) or not raw:
        return default
    allowed = {f"cms_{STAGE}_{suffix}" for suffix in _ALLOWED_RULE_SUFFIXES}
    if raw in allowed:
        return raw
    return default
```

<!-- verify: sed -n '/^def _resolve_rule_name/,/return default/p' services/simulation/lambda/simulation_lambda.py -->

- The check is **set membership on the full name** — `raw in allowed`. Not a prefix, suffix,
  substring or regex test. `cms_staging_iot_msk_rule_evil` and `evil_cms_staging_iot_msk_rule` both
  contain an allowlisted name in full and are both **rejected**; five regression cases pin this,
  and mutating the test to `startswith`/`in` fails four of them.
- Anything not on the allowlist — arbitrary strings, `""`, `None`, non-string types, whitespace-padded
  or case-shifted variants — **falls back to the CMS-native default**. That default is itself the R6
  regression guard.
- The fallback is **deliberate and silent**: a caller-chosen rule name is a caller-chosen data
  destination, so an unrecognised one must not be honoured. The operational consequence is worth
  knowing — a typo'd `rule_name` does not error, it routes the run to the CMS-native topic. A
  simulation that "worked" but produced nothing on the product topic is the symptom to look for.
- Adding a product means adding a suffix here. There is no denylist.

### 3. Connected-Services authorization scope

The `connected-services` principal is admitted on exactly three routes, each fleet-scoped:

| Route | Method | Admitted for `connected-services`? |
|---|---|---|
| `/api/simulation/start` | POST | yes — fleet-scoped |
| `/api/simulation/stop/{id}` | POST | yes — fleet-scoped |
| `/api/simulation/status/{id}` | GET | yes — fleet-scoped |
| `/api/simulation/agent/start` | POST | **no — 403** |
| `/api/simulation/agent/logs/{vin}` | GET | **no — 403** |
| `/api/simulation/agent/stop`, `/list`, `/drivers`, `/campaigns`, `/presets` | — | **no — 403** |

<!-- verify: grep -n 'allow_connected_services=True' services/simulation/lambda/simulation_lambda.py -->

The widening is **per-route and explicit**: a flag is passed at the three dispatch sites rather than
adding an unconditional branch inside `_authorize_per_vin`, precisely so it cannot leak to the
`/agent/*` routes. Those routes drive the **FleetWise Edge (FWE) agent** — the onboard telemetry
agent and its container tasks — which is a different capability from starting a cloud simulation,
and they retain their existing admin/operator gates.

**Why the scope is a fleet, not a VIN prefix.** A fleet is the scoping unit every other branch of
`_authorize_per_vin` already uses, so the widening reads as a narrower case of the existing rule
rather than a parallel mechanism. Matching on a `MRDN` VIN prefix was rejected: treating a naming
convention as a proxy for an attribute is the error that produced an earlier miscount of Meridian
vehicles in this spec's own history, and embedding it in a predicate that *decides access* would put
that error somewhere it does real harm.

The `connected-services` group carries **no** `custom:fleetIds` — unlike `fleet-operator`, which is
scoped by that claim. So the allowlisted fleet comes from the environment:
`CS_ALLOWED_FLEET_ID`, defaulting to `flt-meridian-range-001` (env-driven, not a literal in the
authorization branch, so staging and prod can differ without a code change).

**The fleet is resolved from the enrollment table, not from the vehicle row.**
`resolve_vins_to_fleets` queries `cms-{stage}-storage-fleet-enrollment` via its `vehicleId-index`.
The vehicles table carries its own `fleetId` attribute, so a vehicle row alone *looks* correctly
fleeted while the predicate that gates access reads a different table and sees nothing — yielding
**404 `Unknown VIN(s)`**, before the fleet allowlist is even consulted. This is why provisioning the
demo vehicle takes three writes, not two (see § Demo target).

### 4. IoT Policy: the critical `$aws/rules/*` grant

Devices publishing to product-specific rules **must have explicit `iot:Publish` permission** on the rule-topic prefix. This is the **most likely deployment defect** because the resource scope differs from the standard telemetry publish resource:

**What the policy already granted** (5 publish resources before 2026-09-13) — the three
`*_iot_msk_rule` basic-ingest prefixes plus two plain topic trees:
```
"arn:aws:iot:*:*:topic/$aws/rules/cms_{dev,staging,prod}_iot_msk_rule/*"
"arn:aws:iot:*:*:topic/cms/*"
"arn:aws:iot:*:*:topic/fleet/*"
```
`topic/cms/*` does **not** cover the product path: under basic ingest the publish topic begins
`$aws/rules/`, so it never matches `cms/*`.

**Product-rule telemetry (Path B) — must be explicitly added.** Three resources, one per stage,
matching the `*_iot_msk_rule` pattern already in the document so prod needs no later change:
```
"arn:aws:iot:*:*:topic/$aws/rules/cms_dev_cs_product_meridian_ev_rule/*"
"arn:aws:iot:*:*:topic/$aws/rules/cms_staging_cs_product_meridian_ev_rule/*"
"arn:aws:iot:*:*:topic/$aws/rules/cms_prod_cs_product_meridian_ev_rule/*"
```
Note these are `topic/`, not `topicfilter/` — `topicfilter/` governs `iot:Subscribe`, and this is a
publish grant.

The policy document is duplicated in **two source files plus the live policy**, and all three must
agree:

| Where | Role |
|---|---|
| `modules/cms_ui/source/handlers/main_api/index.py` (publish block ~`:578`) | the production writer — mints certs at enrollment |
| `deployment/scripts/backfill_veh_vo_001_cert.py` (`_SHARED_POLICY_DOCUMENT`) | the provisioning copy used to backfill a single vehicle |
| live IoT policy `CMS-Vehicle-IoT-Policy` | what actually authorizes |

<!-- verify: grep -c 'cs_product_meridian_ev_rule' modules/cms_ui/source/handlers/main_api/index.py deployment/scripts/backfill_veh_vo_001_cert.py -->

**Why editing one alone silently reverts.** `_ensure_shared_policy` in the provisioning script
normalizes the live document (`json.dumps(..., sort_keys=True)`) against its own constant and, on
any difference, publishes a **new default policy version built from its own copy**. So a live-only
change is undone by whichever writer runs next, with no error — the grant simply disappears. Update
both source files and the live policy in the same change, then confirm parity by normalizing the
live document the same way the script does.

**Version headroom.** IoT caps a policy at 5 versions. On 2026-09-13 the policy was at the cap, so
the oldest non-default version was deleted before adding version 6 — the same LRU the script
performs. Rollback is preserved because the prior default version still exists and can be re-set.

**Verification**: test a publish to the product-rule prefix before the demo. On 2026-09-13 the live
policy (version 6) published to
`$aws/rules/cms_staging_cs_product_meridian_ev_rule/VEH-MRDN-0011` with `reason_code=Success` on
every message. Before version 6 the same publish was denied at the broker, and the denial is silent
from the portal's point of view — the API returns 200 and the ECS task starts, because publish
failure happens later, inside the simulator.

### 5. Simulator freshness guard and SIM_IMAGE_ALLOW_STALE

`SIM_IMAGE_ALLOW_STALE` is an **environment variable**, not a CDK context flag:

```bash
cd deployment
SIM_IMAGE_ALLOW_STALE=1 make deploy-simulation DEPLOYMENT_STAGE=staging
```

**Why it is needed.** `assert_published_image_fresh` runs at synth from
`simulation_stack._resolve_sim_container_image` and fails closed when anything under
`SIM_SOURCE_PATHS = ("services/simulation",)` has drifted from the commit the pinned public-ECR
image was built at (`SIM_IMAGE_SOURCE_COMMIT`). Its purpose is to stop a deploy silently running a
container built from older source.

**Why setting it is correct rather than expedient here.** The simulation **Lambda** does not ship
inside either container image — it is a separate asset,
`lambda_.Code.from_asset(_bundle_sim_lambda())`. So Lambda edits reach the deployed function
regardless of image staleness, and the guard's actual concern does not apply to the artifact being
changed. The guard's granularity is the whole `services/simulation` tree, which is why a
Lambda-only change trips it.

As of 2026-09-13 the guard was **already** tripping before any of this spec's edits — 21 drifted
paths against the pinned commit — so expect its warning and do not read it as caused by your
change. Tracked as `issues/2026-09-09-sim-container-image-predates-wave3`.

The guard prints its own three remedies. Prefer rebuilding (`SIM_IMAGE_MODE=asset`) or republishing
when you have actually changed **simulator** code, since those are what the container runs. The
`--rule-name` flag itself is consumed by the simulator, and the deployed image does honour an
arbitrary allowlisted value — verified live on 2026-09-13.

<!-- verify: grep -n 'SIM_SOURCE_PATHS\|ENV_ALLOW_STALE' deployment/stacks/_sim_image_config.py -->

---

## Demo target: VEH-MRDN-0011

End-to-end verified 2026-09-13; evidence in
`.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/group5-live-evidence-2026-09-13.md`.

| | |
|---|---|
| vehicleId | `VEH-MRDN-0011` |
| VIN | `MRDN0000000000013` |
| Make / model | Meridian Windrose (Standard) |
| Fleet | `flt-meridian-range-001` |
| `dataSource` | `cloud-telemetry` |
| Model manifest | `MERIDIAN-WINDROSE` (decoder `meridian-windrose-v1`) |

### Provisioning takes THREE writes, in order

The third is the one most easily missed, and omitting it makes the whole path fail closed with a
**404**, not a 403 — see § 3.

**1. The vehicle row** (`cms-staging-storage-vehicles`, key `vehicleId`). Set
`dataSource: cloud-telemetry`, `fleetId`, `vin`, and `connectionStatus: disconnected` with **no**
`lastSeenAt` — a seed must never assert a connection that has not happened
(`issues/2026-07-31-fake-connected-status-regression`). Do **not** pre-set `modelManifestName` or
`certificateId`: the backfill script treats a present `modelManifestName` as "already done" and
exits 0, skipping cert minting entirely.

**2. The fleet-enrollment row** — `cms-staging-storage-fleet-enrollment`, whose keys are `PK`/`SK`,
**not** `fleetId`/`vehicleId`:

```
PK          FLEET#flt-meridian-range-001
SK          VEHICLE#VEH-MRDN-0011
vehicleId   VEH-MRDN-0011
fleetId     flt-meridian-range-001
enrolledAt  <iso8601>
```

This is the row `resolve_vins_to_fleets` actually reads. Without it, authorization returns
404 `Unknown VIN(s)`.

**3. The certificate**, via the existing generic script. The cert is **mandatory**:
`create_mqtt_connection` returns `None` when no certificate resolves (keyed on **vehicleId**), so a
bare vehicle row yields a vehicle that looks enrolled, reports success and publishes nothing.

```bash
cd deployment/scripts
# dry-run first — the script is dry-run by default; --apply writes
python3 backfill_veh_vo_001_cert.py \
  --vin MRDN0000000000013 \
  --vehicle-id VEH-MRDN-0011 \
  --model-manifest-name MERIDIAN-WINDROSE
# then re-run with --apply
```

Stage comes from `DEPLOYMENT_STAGE` (default `staging`), not a flag.

<!-- verify: grep -oE '"--[a-z-]+"' deployment/scripts/backfill_veh_vo_001_cert.py | sort -u -->

### Two gotchas in that script, both hit on 2026-09-13

1. **Its default model manifest does not exist.** `--model-manifest-name` defaults to
   `CMS-Fleet-Default`, and the script fails closed with "not found or not ACTIVE". Live
   `cms-staging-model-manifest` holds eight manifests — seven per-model Meridian ones plus
   `CMS-FLEET-MODEL` — and no `CMS-Fleet-Default`. Pass `MERIDIAN-WINDROSE`.

2. **It overwrites `dataSource` to `vehicle-telemetry`.** Visible in the dry-run plan's
   `UpdateExpression`. It is the generic cert-follows-model tool for the onboard/FWE path; the CS
   demo needs the cloud path. Correct it afterwards and re-verify:

   ```bash
   aws dynamodb update-item \
     --table-name cms-staging-storage-vehicles \
     --key '{"vehicleId": {"S": "VEH-MRDN-0011"}}' \
     --update-expression 'SET dataSource = :ds' \
     --expression-attribute-values '{":ds": {"S": "cloud-telemetry"}}'
   ```

   The conflict is silent — the script reports complete success while leaving the vehicle
   classified on the wrong ingest path, which is UI- and routing-visible.

### Verify provisioning worked

Check the **predicate**, not just the rows — they are different claims:

```bash
FLEET_ENROLLMENT_TABLE_NAME=cms-staging-storage-fleet-enrollment AWS_REGION=us-west-2 \
python3 -c "
import sys; sys.path.insert(0,'services/connectors/oem1')
from _lib.fleet_membership import resolve_vins_to_fleets
print(resolve_vins_to_fleets(['VEH-MRDN-0011']))"
# expect {'VEH-MRDN-0011': 'flt-meridian-range-001'}
```

---

## Testing the D5 unmatched-topic control

The negative control for a product topic that matches the convention but has **no manifest** — it
must raise an ERROR log, the `UnmatchedTopicRecordsTotal` metric and the `...-unmatched-topic` alarm,
never silently drop.

Full recipe, tear-down, and the three assertions to make (not one) are in
[`docs/runbooks/oem-telemetry-processor-c1-deploy.md`](runbooks/oem-telemetry-processor-c1-deploy.md)
§ "Testing the D5 unmatched-topic control". Two things worth knowing before you start:

- Build it with a **dedicated** IoT cert and policy. The obvious approach edits the shared
  `CMS-Vehicle-IoT-Policy`; it is not necessary and costs a version at the 5-version cap.
- It needs a **processor restart**, because new product topics are not discovered by the running
  job — `issues/2026-09-13-oem-processor-does-not-discover-new-product-topics/`. Run the
  committed-offsets check first; snapshots are disabled, so committed offsets are the only thing
  preventing a full-retention replay.

<!-- verify: test -f docs/runbooks/oem-telemetry-processor-c1-deploy.md && grep -c 'D5 unmatched-topic control' docs/runbooks/oem-telemetry-processor-c1-deploy.md -->

---

## Rollup verification commands

### Screen count (CS portal) — 26 as of 2026-09-13
<!-- verify: grep -cE '^    id: ' modules/connected_services_ui/src/screenRegistry.ts -->

Count entry `id:` lines, not `settleMarker:` — two `settleMarker` occurrences are the docstring and
the interface field, which is why a naive count returns 28. Three different figures (23, 26, 27)
appear across this spec's own history; re-derive rather than trust any of them.

### Portal live backend API clients — 2 (`subscriptionsClient.ts`, `simulationClient.ts`)
<!-- verify: ls modules/connected_services_ui/src/api/*Client.ts -->

### Runtime-config keys
<!-- verify: grep -oE '"[a-zA-Z]+":' deployment/stacks/connected_services_ui_stack.py | sort -u -->

### Meridian vehicle count
The vehicles table has only a `vin-index`, so there is no `make` index to query — count by scanning
and filtering on the `make` attribute:
<!-- verify: aws dynamodb scan --table-name cms-staging-storage-vehicles --filter-expression "make = :m" --expression-attribute-values '{":m":{"S":"Meridian"}}' --select COUNT --region us-west-2 -->

Filtering on the `MRDN` VIN prefix instead **undercounts** — that proxy produced a wrong figure
earlier in this spec's history, because several `make: Meridian` vehicles carry non-`MRDN` VINs.
---

## Payload Shape and Transform Coupling

The simulator publishes the same `generate_telemetry_data()` payload on both paths (269 keys). Offboard compresses it (`gzip` → `base64`) before publishing to IoT Core Basic Ingest; the Flink processor decompresses it before transformation. This section documents the three-name distinction that selects the transform manifest and guards the coupling between payload and manifest.

### The Three Names That Route Telemetry

When the processor receives a message, three pieces of metadata determine which transform to apply. The distinction matters because **manifest selection** (which transform to load) is not the same as **manifest reading** (which field gets tagged):

| Name | Lives in | Role |
|---|---|---|
| **Kafka topic** | Topic subscription | **Selects the manifest.** The topic name `cs-product-meridian-ev` is parsed by `deriveSourceKeyFromTopic()` (OEMTelemetryProcessor.java:1340), which strips the `cs-product-` prefix and yields `meridian-ev`. This key is then used to construct the S3 path `manifests/meridian-ev-transform.json` (:752). |
| **Payload `oem_source`** | JSON payload field | **Fallback only.** Used when topic derivation returns null — for example, the OEM1 path uses topic `cms-telemetry-oem` which yields null, so the processor falls back to reading `payload.oem_source` field (set to `"oem1"`). For the Meridian cloud path, the topic derivation succeeds so the `oem_source` field is ignored. |
| **Manifest `source_name`** | Manifest JSON | **Read after selection, not used for selection.** The `source_name` field (e.g., `"cs-meridian"`) is loaded from the already-selected manifest and becomes the `oem` tag on output records. It does not select anything — the topic and payload field control selection via `resolveSourceKey()`. |

**The `resolveSourceKey()` logic** (OEMTelemetryProcessor.java:1370-1373):
```
derived = deriveSourceKeyFromTopic(topic)
sourceKey = derived != null ? derived : payloadOemSource
```

Topic wins; payload `oem_source` is only a fallback for paths where topic derivation cannot work.

<!-- verify: grep -A 3 'resolveSourceKey.*payloadOem' modules/flink/src/main/java/com/cms/telemetry/OEMTelemetryProcessor.java | head -5 -->

### Four Guards Pin the Couplings

This spec (2026-09-20) adds 3 CI lint steps and 1 make target to catch divergence between the simulator, the manifest, and the routing logic. Each guard detects a different coupling failure mode:

| Guard | What it checks | Failure mode if missed | Runs where |
|---|---|---|---|
| **D1 — Field parity** | Every manifest `signal_mappings[].source_path` exists in the simulator's emitted key set. | A renamed or deleted simulator field silently becomes `0.0` because 12 of 38 mappings carry `default_value` and `validation.required_fields` is empty. Renaming `tire_pressure_fl` silently fills the maintenance table with zeros. | CI lint (`scripts/check_transform_manifest_parity.py` + `pytest`) |
| **D3 — Topic ↔ filename coupling** | Every `cs-product-*` topic provisioned in MSK or allowlisted in the simulator has a matching git-tracked `manifests/<id>-transform.json`. | A new product topic added to provisioning but no manifest file → runtime `UnmatchedTopicException` on every record, audible only at runtime. | CI lint (`scripts/check_transform_manifest_parity.py --only coupling` + `pytest`) |
| **D4 — Git ↔ S3 drift** | The manifest files in git match the deployed S3 copies byte-for-byte (sha256 digest). | Reviewer sees a manifest, approves it, but Flink loads a different version from S3. Silent divergence. | Opt-in: `make verify-manifests` (requires AWS credentials; not wired to CI per spec constraint) |
| **D5 — End-to-end fixture** | A Java test asserts all 7 properties of the transformed output (normalizeIngress, topic resolution, field presence, vehicle ID, timestamp, `source_name` value, range checks). The fixture is regenerated whenever the simulator's emitted key set changes. | The transform silently accepts a simulator-payload shape it was never meant to handle, discovered only if offboard simulation reaches production. | Local only: `cd modules/flink && mvn test -Dtest=OEMTelemetryProcessorMeridianFixtureTest`. Not wired to GitHub Actions — Maven+JDK setup is expensive relative to the coverage delta over the Python guards D1/D2/D3, which cover the same manifest surface statically and DO run in CI. |

<!-- verify: grep -c 'check_transform_manifest_parity\|verify-manifests' .github/workflows/lint.yml deployment/Makefile && test -f modules/flink/src/test/java/com/cms/telemetry/OEMTelemetryProcessorMeridianFixtureTest.java && echo "Java D5 test file present (local-only, per D5 row above)" -->

**All four guards pass on arrival.** Per the spec's Risks section, a guard that has only ever been green has not been shown to guard anything. Every guard was mutation-verified — defects were intentionally introduced (renamed fields, deleted manifests, corrupted files) and each guard observed failing with a clear error message, then restored. Mutation results are recorded in the spec's `decisions.md`.

### Why `source_name: "cs-meridian"` Must Not Be Changed

In `meridian-ev-transform.json`, the field reads `"source_name": "cs-meridian"`. This is deliberate (user decision, 2026-09-20) and should **never** be "tidied" to match the filename:

- **It does not select the manifest.** Manifest selection is via topic derivation (`cs-product-` → `meridian-ev` → `meridian-ev-transform.json`). The `source_name` field is only read **after** this selection succeeds, so its value has no effect on which manifest is loaded.
- **It is an override.** The code at OEMTelemetryProcessor.java:761 calls `.asText(oemSource)`, which means an absent `source_name` would default to `meridian-ev`. The explicit `cs-meridian` value is therefore overriding that default — not a coincidence or leftover.
- **It is pinned by a test.** Test T3.3 (mutation M11) in the Java fixture test asserts that changing `source_name` from `"cs-meridian"` to `"meridian-ev"` causes the output's `oem` field assertion to fail. That test must pass for any manifest edit to clear CI.

If a future consumer of the `oem` output field requires the tag to change, update `source_name`, regenerate the fixture via `python3 scripts/gen_meridian_telemetry_fixture.py`, and update the test's M11 expected value — all three steps are required.

### Verifying Payload-Manifest Coupling

Before testing an offboard simulation on VEH-MRDN-0011, verify that the simulator can reach the deployed manifest:

```bash
# Check that S3 has the manifest
aws s3 ls s3://cms-staging-transform-manifests-us-west-2-*/manifests/ --region us-west-2
# expect: meridian-ev-transform.json and oem1-transform.json

# Verify the manifest's structure
aws s3 cp s3://cms-staging-transform-manifests-us-west-2-*/manifests/meridian-ev-transform.json - | jq '.signal_mappings | length'
# expect: 38

# Cross-check git vs S3
sha256sum services/data_processing/manifests/meridian-ev-transform.json
aws s3 cp s3://cms-staging-transform-manifests-us-west-2-*/manifests/meridian-ev-transform.json - | sha256sum
# expect: identical digests

# Verify via the make target (if AWS credentials available)
cd deployment && make verify-manifests DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
# expect: exit 0, both manifests in sync
```

---

## See also

- [`services/connectors/subscriptions/README.md`](../services/connectors/subscriptions/README.md) — subscription plane API and data model
- [`docs/connected-services-consumer.md`](./connected-services-consumer.md) — CMS as a Connected Services subscriber
- [`deployment/scripts/backfill_veh_vo_001_cert.py`](../deployment/scripts/backfill_veh_vo_001_cert.py) — device certificate provisioning
- [`services/data_processing/manifests/README.md`](../services/data_processing/manifests/README.md) — manifest structure and deployment
- Spec: `.kiro/specs/2026-09-20-transform-manifest-contract-guards/` — full design, guards, and mutation verification
- Spec: `.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/` — foundational design and test coverage

