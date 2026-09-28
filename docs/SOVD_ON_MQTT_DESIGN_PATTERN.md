---
title: "SOVD Data Model on an MQTT Transport Binding"
subtitle: "A Design Pattern for Remote Diagnostics on Connected Fleets"
author: "AWS Guidance for Connected Mobility on AWS"
date: "September 2026"
---

# SOVD Data Model on an MQTT Transport Binding: A Design Pattern for Remote Diagnostics on Connected Fleets

## TL;DR

ASAM SOVD (in the process of ISO standardisation as ISO 17978) defines a **service-oriented resource model** for vehicle diagnostics, together with a **REST binding** that expresses that model over HTTP. The reference deployment topology places a SOVD server on the vehicle's central gateway and exposes it to a diagnostic client on the local network. Extending that topology to a connected fleet — millions of vehicles reachable from a cloud back-office — surfaces transport-level problems (inbound reachability, per-vehicle TLS terminators, idle cost, duplication of an already-deployed connectivity substrate) that the standard does not address, because they are outside its scope.

This paper describes an alternative implementation pattern that:

1. **Preserves** the SOVD data model, resource semantics, and JSON payload schemas.
2. **Replaces** the REST transport binding with an MQTT binding over AWS IoT Core.
3. **Runs** on a small on-vehicle diagnostic agent — an MQTT client that bridges cloud requests to the vehicle bus via UDS or DoIP. Standalone deployments are the simpler case; the pattern also composes cleanly alongside the AWS IoT FleetWise Edge Agent (FWE) where FWE is already present.
4. **Restores** REST-client interoperability, when required, through a stateful gateway component — described here as a distinct piece of work, not a URL rewrite.

The trade-off is explicit: the on-wire protocol is not a standards-compliant SOVD REST implementation. A partner scan tool built to the SOVD REST binding does not talk to the fleet directly; it talks to the gateway. In exchange, the fleet-scale reachability, identity, and connectivity-substrate-reuse story becomes tractable.

**Origin.** This pattern was developed for the AWS Guidance for Connected Mobility on AWS — a fleet management platform that OEMs deliver to fleet operators, in which a fully integrated remote diagnostics stack sits alongside telemetry ingest, campaign delivery, and third-party data delivery as first-class platform capabilities. The pattern applies to any AWS IoT Core-based connected-fleet architecture, whether or not it uses AWS IoT FleetWise on the vehicle side.

![End-to-end SOVD-on-MQTT flow. Grey = operator/partner client; blue = AWS Cloud services; tan = on-vehicle components. Solid edges are the request/response path (steps 1–9); dashed edges are the payload-size fallback path (7b, 9b) invoked only when a response exceeds ~80 KB.](sovd-on-mqtt-flow.png){width=100%}

*Figure 1 — End-to-end flow. Steps 1 through 9 trace one complete request-response cycle. In Topology B (§ 3.3), the AWS IoT FleetWise Edge Agent runs on the vehicle alongside the on-vehicle diagnostic agent, using its own protobuf topic path in the same MQTT tree; that path is not shown here.*

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## Part 1 — Context (L200)

### What SOVD standardises

ASAM SOVD (Service-Oriented Vehicle Diagnostics), being standardised as ISO 17978, defines two related things:

- **A service-oriented resource model** for vehicle diagnostics. Vehicles expose *components* (ECUs, subsystems), which carry *data* (readable identifiers), *faults* (DTCs and their snapshots), *routines* (invokable diagnostic sequences), and other resources. Operations are expressed against these resources — read a component's faults, clear its faults, start a routine on it — without the client having to know which underlying protocol (UDS over CAN, DoIP over Ethernet, K-Line on older vehicles) actually implements the operation on the ECU.
- **A REST binding** that expresses that resource model over HTTP with JSON payloads. Resources become URIs; operations become HTTP verbs; state transitions and long-running operations follow REST conventions.

The value proposition is client portability. A diagnostic front-end built against SOVD — a technician tablet, an OEM dashboard, a fleet-management console — does not need to know per-vehicle UDS byte layouts or per-model DBC decoding. The SOVD server on the vehicle side owns that translation. The client sees resources and JSON.

### Reference deployment topology

The most common deployment described in the standard places a **SOVD server on the vehicle's central gateway ECU**, exposing REST endpoints on the vehicle's internal network, reachable by:

- A wired diagnostic tester connected to the OBD-II port or a service Ethernet connector.
- A tablet on the vehicle's local Wi-Fi in a service bay.
- Onboard applications running elsewhere in the vehicle's software stack.

This topology works well for its target use case: **one vehicle, one client, local reachability**. The client and server are on the same network segment, mTLS is bounded to that segment, and the SOVD server's lifetime is tied to ignition or a service session.

### Where the topology stops fitting

Extending the same topology to a connected fleet — cloud-hosted operator UI, thousands to millions of vehicles distributed across mobile carrier networks — surfaces a set of transport-level problems that the standard's REST binding was not designed to solve:

| Problem | Why it bites at fleet scale |
|---|---|
| **Inbound reachability** | Vehicles sit behind mobile-carrier CGNAT. The cloud has no route to publish a request to a specific vehicle's HTTP endpoint; the vehicle's transient IP is neither stable nor knowable from outside. |
| **TLS terminator per vehicle** | An HTTPS server per vehicle requires a TLS certificate per vehicle, whose renewal, revocation, and pinning become an ongoing operational surface distinct from any telemetry cert already deployed. |
| **Idle cost** | The SOVD server is running for events that occur intermittently. Every vehicle pays connectivity, memory, and battery cost for a listener that is silent the vast majority of the time. |
| **Substrate duplication** | The vehicle already has a persistent, authenticated MQTT connection to a cloud platform, carrying telemetry and receiving actuator commands. An HTTPS server is a second transport, second auth model, second failure mode. |
| **Address brokering** | Cloud → vehicle addressing requires a broker that knows "vehicle V is currently at IP X on carrier Y." That broker is a bespoke system, and its correctness governs whether SOVD requests reach the vehicle at all. |
| **Ecosystem friction** | Both the mobile carrier and any corporate networks in the path must permit inbound HTTPS to vehicle endpoints. The permission surface is per-vehicle, not per-service, and both sides have reasons to say no. |

These are not diagnostics problems. They are properties of *request-response over HTTP with the server addressed by the client*, and they apply to any cloud-to-device protocol that inherits that assumption.

### The trade-space

Any solution has to choose which of three things to sacrifice:

- **Sacrifice the fleet-scale reachability problem** by keeping the standard's REST binding on the vehicle and building or buying a session broker, VPN mesh, or reverse-tunnel platform to reach it. Preserves SOVD compliance; adds a large new operational surface.
- **Sacrifice the data model** by inventing a proprietary protocol that ignores SOVD's resource semantics entirely. Simple to build; loses every interop benefit the standard exists to provide.
- **Sacrifice the transport binding** by preserving the SOVD data model, resource semantics, and JSON payload schemas, but expressing them over a transport that fits the fleet-scale reachability model. Loses direct plug-in compatibility with SOVD REST client tools; preserves everything above the transport layer, and admits an explicit gateway for interop.

This paper describes the third choice.

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## Part 2 — Compliance stance

Because this is where a knowledgeable reader will interrogate the design first, we state it explicitly.

### What is preserved from SOVD

- **The resource model.** Vehicles are modelled as sets of components. Components carry data, faults, and routines. Operations are expressed against components.
- **The JSON payload schemas.** Fault records carry the SOVD-defined fields (`code`, `status`, `occurrence_count`, `first_seen_ms`, `last_seen_ms`, `freeze_frame` with signal-name / value / unit / timestamp). Component descriptors, data-identifier reads, and routine invocations follow the standard's shape.
- **The protocol-independence of the client contract.** A caller does not know or care whether the vehicle-side implementation is UDS-over-CAN or DoIP-over-Ethernet.
- **The semantic operations.** Read faults, clear faults, read data by identifier, start routine, read snapshot — the operation set is the SOVD operation set.

### What is not preserved from SOVD

- **The REST binding.** No HTTP verbs, no URI templates, no hypermedia discovery, no HTTP status-code error taxonomy. The transport is MQTT topics with JSON payloads and a `command_type` discriminator.
- **Direct plug-in compatibility with a SOVD REST client.** A client library built against the SOVD REST binding does not connect to a vehicle in this system. Concretely: a workshop scan tool that ships with SOVD REST support — expecting to `POST /components/{ecu-id}/routines/{routine-id}/executions` and receive an HTTP response — cannot be pointed at a vehicle in this fleet and made to work. There is no HTTP endpoint listening on the vehicle.
- **The standard's discovery semantics.** SOVD's REST resources are self-describing via hypermedia; our JSON payloads are not.

### How REST interoperability is recovered

Where a partner tool or an ecosystem client requires the REST binding — a workshop scan tool, an aftermarket diagnostic application, a supplier's compliance-testing harness — REST access is provided through a **REST-to-MQTT gateway** described in Part 4. This gateway is deliberately treated as a **separate, stateful component** rather than a URL rewrite. It performs:

- Resource discovery synthesis (SOVD REST clients discover the vehicle's component tree via hypermedia; the MQTT protocol here does not carry that; the gateway synthesizes it from a per-vehicle catalog).
- HTTP status-code ↔ SOVD-in-JSON error translation.
- Long-running-operation state (SOVD REST expresses routine executions as resources that a client can poll; the gateway holds this state and translates to/from the MQTT correlation-ID model).
- Pagination cursor management.
- Authentication translation (the vehicle certificate identifies the vehicle to the MQTT broker; the gateway maps a client's authenticated identity to fleet-scoped authorization).

The gateway is real engineering, not a shim. It exists to restore standards-compliant interop where the ecosystem requires it, at the cost of operating one more component. For internal operator UIs and integrations we control end-to-end, the gateway is not on the path; those clients call the MQTT-binding directly via our own APIs.

### Why the trade-off

For operators who own both ends of the client-vehicle relationship — a fleet's own operator UI, an OEM's own dashboard, a service company's internal tooling — the loss of SOVD REST compatibility on the vehicle side is not a cost, because those clients were never going to speak SOVD REST directly. They speak the operator's own API, which the operator can define however it likes.

For ecosystem partners who *do* rely on SOVD REST compatibility — the workshop scan tool above, an aftermarket diagnostic vendor's fleet-wide analytics platform, an OEM's supplier-facing compliance-testing harness — the gateway restores it. The gateway is one component to build and operate. In exchange, the entire fleet becomes reachable without solving inbound-addressability per vehicle, without provisioning a second TLS terminator per vehicle, and without introducing a transport that duplicates the substrate the vehicle already carries.

The trade-off is defensible in front of ASAM, in the following form: *the data model and JSON schemas are preserved; the transport binding is replaced for fleet-scale operational reasons; standards-compliant client access is restored through a documented gateway component*. It is not defensible as "we implement SOVD" without qualification.

### A note on local diagnostic access

This pattern covers *cloud-mediated fleet diagnostics*. It does not replace the on-vehicle SOVD REST server that some OEMs ship for workshop diagnosis, regulatory inspection, or right-to-repair access — that server is a complementary component operating at a different interface (typically the vehicle's service Ethernet or OBD-II port, service-mode gated) with a different threat model (one client at the vehicle, no cellular exposure).

The two share vehicle-side UDS/DoIP dispatch code below the transport layer, but they are otherwise independent. Neither requires the other; neither precludes the other. An OEM choosing to build the local server adds it as a complementary component alongside this pattern, sharing the underlying diagnostic implementation but exposing a different attack surface for a different use case.

The paper does not describe the local-server component in detail because it is a smaller, well-understood engineering problem — a plain HTTPS server bound to the service network, implementing the SOVD REST binding, activated when the vehicle is in service mode. The interesting design decisions live at the fleet-scale end of the spectrum, and that is what this paper covers.

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## Part 3 — Design pattern (L400)

### 3.1 Substrate: MQTT over AWS IoT Core

The vehicle establishes a persistent outbound TLS connection to AWS IoT Core, authenticated with a device certificate provisioned at manufacture or first boot. Everything that follows is enabled by one property of that connection: **the vehicle opens it, outbound**.

- **Reachability.** No inbound path is required. NAT, carrier CGN, and corporate firewalls are non-issues.
- **Identity.** The client certificate identifies the device to the broker. Every publish and subscribe carries that identity.
- **Authorization.** AWS IoT policies attached to the certificate constrain the topics the device may publish and subscribe to. A compromised device cannot publish on another vehicle's response topic; the topic-level authorization is enforced by the broker.
- **Session semantics.** MQTT sessions carry keep-alive, last-will, and clean-session behaviour. QoS 1 delivers at-least-once, which is the correct guarantee for a diagnostic command that must not silently disappear.
- **Namespacing.** MQTT's topic tree is a hierarchical namespace with wildcards, so a single certificate policy can grant access to a scoped subtree without enumerating individual topics.

For any vehicle running an AWS IoT-based telemetry stack, this connection already exists. Adding a diagnostic surface does not add a connection; it adds a subscription.

### 3.2 The on-vehicle diagnostic agent

The pattern places a small process on the vehicle — the *on-vehicle diagnostic agent* — whose only job is to bridge cloud MQTT to the vehicle bus. It is not a server. It has no listening socket, no separately managed TLS certificate, and no inbound firewall requirement. The MQTT client is its only network I/O.

Concretely, the agent:

- Opens and maintains one persistent outbound MQTT connection to AWS IoT Core, authenticated with the vehicle's device certificate.
- Subscribes to a topic pattern the fleet operator controls.
- Receives JSON-encoded diagnostic requests, dispatches them on a worker thread, and executes them against the vehicle bus using UDS over ISO 15765-2 (CAN) or UDS over DoIP (Ethernet).
- Marshals responses back into SOVD-shaped JSON and publishes them on the correlated response topic.

Typical implementation size is a few hundred lines of code — MQTT client wiring, JSON parsing, a `command_type` dispatch table, and thin wrappers around the ISO 14229 library the vehicle already uses. Nothing about the agent is diagnostic-vendor-specific; the same shape works whether the vehicle speaks native UDS-on-CAN, UDS-on-DoIP, or a mix.

The agent runs wherever the fleet's telematics stack already runs a Linux (or comparable) process — the central gateway, the telematics control unit, an in-vehicle Linux SoC. If a partner is already running an MQTT-connected telematics stack on the vehicle, this agent typically drops into the same runtime environment. If the partner is greenfield, the agent is the vehicle-side deliverable.

### 3.3 Deployment topology

The pattern supports two on-vehicle topologies. The choice is a deployment concern, not a design change.

**Topology A — Standalone.** The diagnostic agent is the only (or the primary) MQTT-attached process on the vehicle. The fleet operator designs the topic tree, sets the IoT policy, and provisions certificates however the rest of the fleet's infrastructure works. This is the simpler case and the one to recommend when a partner is choosing the on-vehicle stack.

**Topology B — Alongside AWS IoT FleetWise.** The fleet already runs the AWS IoT FleetWise Edge Agent (FWE) on the vehicle for telemetry-campaign execution, DBC-decoded signal upload, and actuator-command delivery via FWE's protobuf envelope. Source is at `github.com/aws/aws-iot-fleetwise-edge` under Apache 2.0. In this topology, the diagnostic agent runs *as a second MQTT-attached process* alongside FWE, and the two coexist by MQTT-topic construction (see § 3.4 below). We treat FWE as an unmodified component here so upstream FWE releases flow into the fleet without vendor-specific patches.

Both topologies use the same code path for everything above the transport — payload contract, safety envelope, size handling, rate limiting, cloud-side dispatch. The only meaningful difference between them is topic design and IoT-policy scope, described next.

### 3.4 Topic design

The general shape of a diagnostic operation is a request published from cloud to vehicle and a response published from vehicle to cloud, correlated by an `execId`. Topic names carry the vehicle's IoT Thing name so the broker can enforce per-vehicle authorisation via the certificate's policy.

**Topology A (standalone) — recommended topic shape:**

```
Request  (cloud → vehicle):
  {prefix}/vehicles/{ThingName}/diagnostics/{execId}/request

Response (vehicle → cloud):
  {prefix}/vehicles/{ThingName}/diagnostics/{execId}/response
```

The choice of prefix and mid-path segments is a fleet-operator decision. Any hierarchy that lets a single IoT policy grant the agent access to the diagnostic subtree without granting access to unrelated topics will work. The mid-path segment (`diagnostics` here) is not load-bearing — it exists to leave room in the tree for future non-diagnostic surfaces.

**Topology B (alongside FleetWise) — recommended topic shape:**

```
Request  (cloud → vehicle):
  {prefix}/things/{ThingName}/executions/{execId}/diag/request

Response (vehicle → cloud):
  {prefix}/things/{ThingName}/executions/{execId}/diag/response
```

Here the `diag/` sub-path sits in the same tree FWE already uses (`{prefix}/things/{ThingName}/executions/{execId}/...`). This design is deliberate: it lets the existing IoT policy — which already grants the certificate access to that subtree for FWE's use — cover the diagnostic agent without a policy version bump or fleet-wide certificate rotation.

> The sub-path segment is a naming choice, not part of the coexistence guarantee — any literal segment that FWE's subscribe filter cannot match works. The CMS reference implementation uses `sovd/` in place of `diag/` for this reason; see the *Mapping to the reference implementation* appendix.

The design works because FWE's own subscription filter ends in a *literal* trailing segment for its protobuf commands:

```
{prefix}/things/{ThingName}/executions/+/request/protobuf
```

MQTT's `+` wildcard matches exactly one topic level; it does not wildcard within a level. A subscription filter ending in `/protobuf` receives only publishes whose final segment is literally `/protobuf`. A publish ending in `diag/request` is not matched, so FWE does not consume diagnostic traffic — and cannot, regardless of upstream FWE changes. **This is a construction-time guarantee enforced by the MQTT broker, not a runtime check.** No `command_type` discriminator, no envelope-inspection code path, nothing to misconfigure.

The two topologies do not compose across each other — a partner running Topology A but who later adopts FleetWise migrates the topic shape at that point, or continues to run the diagnostic subtree separately. The migration is a coordinated deployment: the agent's subscription filter changes, and the cloud-side publisher's topic template changes. There is no vehicle-side firmware change beyond the agent update.

### 3.5 Reconnect discipline

Persistent MQTT clients disconnect eventually — cellular handoff, tower reboot, TCP timeout, roaming. Reconnect discipline is where MQTT command surfaces most commonly fail silently.

Two invariants:

**(a) The MQTT client does not auto-resubscribe.** Common client libraries (paho-mqtt, AWS IoT SDKs, mosquitto) require the application to re-issue every SUBSCRIBE on the new session. A subscribe issued once at startup is silently dropped on the first reconnect; the underlying TCP/TLS connection is healthy but the vehicle stops receiving commands, and there is no visible signal that anything is wrong.

**(b) SUBACK, not CONNACK, is the point at which the session is safely established.** CONNACK confirms the transport is up; the broker's SUBACK confirms the subscription is active. A presence signal that fires on CONNACK will lie whenever SUBACK is delayed or rejected.

The pattern in practice:

1. Subscribe inside the `on_connect` (or equivalent) callback — never once at startup.
2. Accumulate SUBACK grants in `on_subscribe`. When all required subscriptions are granted (with a non-failure return code), and only then, emit a `connected` presence event.
3. If any SUBACK is rejected, the session is a partial outage. Do not emit `connected`; log the specific rejected topic; continue reconnect attempts.

When the vehicle carries multiple MQTT-attached processes — for example, the diagnostic agent alongside FWE — presence must gate on **all** SUBACKs from every subscription the vehicle depends on. A vehicle whose telemetry channel is up but whose diagnostic channel is down is a partial outage the operator UI must not paper over. In a standalone deployment, one SUBACK is sufficient.

### 3.6 Cloud-side dispatch

A small cloud handler — Lambda, Fargate task, whatever the deployment prefers — receives a diagnostic request from the operator UI or partner API, authorizes it, and publishes to the vehicle:

```
Client → HTTPS POST /vehicles/{vehicleId}/diagnostics
         Authorization: Bearer <token>
         Body: SOVD-shaped JSON

         ↓ handler validates + authorizes

Handler → durable store  (command record: execId, vehicleId, command_type,
                          caller, submittedAt, status=PENDING)
Handler → MQTT publish
          Topic:   {prefix}/vehicles/{ThingName}/diagnostics/{execId}/request
          Payload: SOVD-shaped JSON
          QoS:     1
```

(The topic template follows Topology A; the equivalent for Topology B is in § 3.4.)

The command record in the durable store serves three purposes: audit trail, response correlation, and the API surface for "was this executed and what happened?" It carries `execId` (unique per request), `ThingName`/`vehicleId`, `command_type`, `caller`, `submittedAt`, `status`, and — after response — `respondedAt`, `latency_ms`, `resultRef`.

The response arrives on the vehicle → cloud direction and is caught by an IoT Rule:

```
Vehicle → MQTT publish
          Topic:   {prefix}/vehicles/{ThingName}/diagnostics/{execId}/response
          Payload: SOVD-shaped result JSON, correlation_id=execId

          ↓ IoT Rule matches ".../diagnostics/+/response"

Rule    → invoke response handler
Handler → update command record (status, latency, resultRef)
Handler → write any resource-level records (per-fault rows, etc.)
```

Note what is not required: no bespoke session broker, no per-vehicle-IP lookup, no VPN concentrator, no reverse-proxy fleet. The MQTT topic pattern and the IoT Rule do the routing.

### 3.7 Edge-side dispatch: how the agent handles a request

Once a request arrives on the subscribed topic, the on-vehicle diagnostic agent (§ 3.2):

1. Parses the JSON payload.
2. Dispatches on `command_type` to a **worker thread**. The MQTT event loop must not be blocked by a synchronous multi-second UDS transaction — doing so times out MQTT keep-alive and produces spurious disconnects.
3. The worker performs the diagnostic operation against the vehicle bus. Where the vehicle speaks UDS over CAN, the worker uses an ISO 14229 implementation on top of an ISO 15765-2 (ISO-TP) transport, typically a library such as `python-can` + `python-udsoncan` or an equivalent C++ stack:

| `command_type` | UDS service | Purpose |
|---|---|---|
| `read_faults` | `0x19 02 <mask>` | ReadDTCInformation by status mask (per ECU, or across all addressable ECUs for a full scan) |
| `read_freeze_frame` | `0x19 04 <DTC>` | DTC Snapshot Record — signal values at time of fault |
| `clear_faults` | `0x14 <group>` | ClearDiagnosticInformation (group code selects scope) |
| `read_identity` | `0x22 <DID>` | ReadDataByIdentifier for VIN (`0xF190`), part numbers, software versions |
| `read_data` | `0x22 <DID>` | Live data reads from a per-model DID allow-list |
| `run_routine` | `0x31 01 <RID>` | RoutineControl start (safety-gated — see § 3.10) |

The `command_type` values above are the paper's SOVD-aligned vocabulary. The CMS reference implementation uses OBD-II-native names for the fault-oriented cases (`read_dtcs`, `clear_dtcs`, `read_freeze_frames`); see the *Mapping to the reference implementation* appendix for the full correspondence.

For vehicles that speak DoIP over Ethernet (ISO 13400), the same operation set is delivered via the DoIP transport with the same UDS semantics; the agent's dispatch layer is protocol-aware, and the wire operations differ only in the transport below UDS.

4. The worker marshals the ECU response into SOVD-shaped JSON and publishes on the correlated response topic.

### 3.8 Payload contract: SOVD data model, preserved

The wire payload preserves the SOVD data model. A read-faults response looks like:

```json
{
  "correlation_id": "…",
  "status": "SUCCEEDED",
  "components": {
    "ECU_ENGINE": {
      "id": "ECU_ENGINE",
      "faults": [
        {
          "code": "P0420",
          "status": "confirmed",
          "occurrence_count": 3,
          "first_seen_ms": 1735000000000,
          "last_seen_ms":  1735000000000,
          "freeze_frame": {
            "engineRpm":    { "value": 2800, "unit": "rpm",  "timestamp": "..." },
            "coolantTemp":  { "value":   88, "unit": "degC", "timestamp": "..." },
            "vehicleSpeed": { "value":   65, "unit": "km/h", "timestamp": "..." }
          }
        }
      ]
    }
  },
  "latency_ms": 3400,
  "storage_uri": null
}
```

Fields and their semantics track the SOVD standard's schemas. What is **not** here — and what a REST-binding client would expect — is HTTP status code as the error carrier, hypermedia links to related resources, and self-describing discovery. Those are provided by the gateway in Part 4, when a client requires them.

> The paper renders each component's fault list under the standard-aligned key `faults`. The CMS reference implementation uses `dtcs` for the same list; keys inside each record are unchanged. See the *Mapping to the reference implementation* appendix.

### 3.9 Payload size and the MQTT ceiling

AWS IoT Core, at time of writing, caps a single MQTT publish at 128 KB. QoS 1 envelope overhead and cellular-link retry budget reduce practical inline payload to approximately 80 KB. A worst-case full-fleet-diagnostic scan with freeze frames can exceed this bound:

```
9 ECUs × 10 faults/ECU × 12 freeze-frame signals × ~90 bytes each ≈ 95 KB
```

The pattern is to size-check the encoded payload before publishing and split above a threshold:

- **Below threshold** (approximately 80 KB): publish inline on the response topic.
- **Above threshold**: upload the full payload to object storage (S3), publish a small summary message with a pointer:

```json
{
  "correlation_id": "…",
  "status": "SUCCEEDED",
  "storage_uri": "s3://.../{ThingName}/{correlation_id}.json",
  "summary": { "componentCount": 9, "faultCount": 42, "hasFreezeFrame": true }
}
```

The cloud response handler generates a presigned GET URL, scoped to the caller, so the operator UI or gateway can fetch the full payload without a new authenticated path.

The threshold is measured, not asserted — a unit test constructs a synthetic 100 KB payload and verifies the S3 branch fires. A documented threshold that isn't executable drifts silently; a threshold enforced by a test cannot.

The pattern generalises: **any transport with a per-message ceiling should carry a pointer to large payloads, not the payload itself.** The transport is optimised for control-plane traffic; the object store is optimised for bulk. AWS IoT Core quotas may change over time; the pattern of size-check-then-fallback is stable regardless of the specific number.

### 3.10 Safety at Layer 7

UDS `0x14` (Clear Diagnostic Information) and `0x31` (RoutineControl) *change vehicle state*. Some routines — an ABS pump cycle, an injector cut-out test, an EVAP purge — are physically unsafe if executed at road speed or with the engine unexpectedly running. This is where the transport-layer design ends and the application-layer safety envelope begins.

The pattern is a **routine safety class** on every actuation-capable operation:

| Class | Preconditions | Remotely invocable | Enforcement point |
|---|---|---|---|
| `INERT` | Vehicle connected | Yes — reads, self-tests, identity queries | UI affordance + cloud allow-list + agent allow-list |
| `STATIONARY` | Speed = 0, engine on, transmission in park or neutral | Yes, from an authorised bay-side operator | Cloud-side check at execution time against vehicle-state cache; agent re-checks against live vehicle state immediately before dispatch |
| `SERVICE_ONLY` | Facts the platform cannot observe (vehicle on a lift, key removed, battery disconnected) | No | Refused at cloud-side catalog lookup; agent refuses defence-in-depth |

Two invariants:

**(a) Preconditions are enforced in executable code, not in prose.** A comment stating "do not run this off a moving vehicle" is not a control. An assertion in the cloud handler that rejects a `STATIONARY` invocation when the vehicle-state cache reports speed > 0 is a control. An assertion in the agent that re-checks CAN-observed vehicle state before dispatching `0x31` is a defence-in-depth control. Both are required, because the cloud check can be bypassed by a compromised credential and the agent cannot be bypassed by anything short of firmware compromise.

**(b) Verdict narration is verbatim.** If a diagnostic result carries a safety verdict — "stop driving, engine at critical temperature" — that phrase is narrated verbatim to the operator, sourced from a deterministic catalog, never summarised. This matters especially when an LLM is added to the read path: the LLM may summarise conversational output, but summarisation of a safety verdict is a behaviour change disguised as UX. The pattern separates the deterministic result — the catalog-derived phrase — from any conversational surface that describes it.

### 3.11 Rate limiting: protect the resource, not the label

A vehicle's CAN bus has physical bandwidth limits, and ECU diagnostic response windows are bounded. A rate limiter that caps "full-scan requests" but not "single-ECU requests" creates a bypass: nine sequential single-ECU reads impose the same CAN load as one nine-ECU scan.

The pattern: **rate-limit the underlying resource, not the label**. Account tokens per ECU-read, so N single-ECU reads cost N tokens whether they arrive as one request or nine. This lives in the on-vehicle agent, which sees the actual bus traffic.

### 3.12 Rejected alternatives

| Alternative | Why not |
|---|---|
| **Preserve SOVD REST binding on the vehicle** (session broker, VPN mesh, or reverse-tunnel platform to reach it) | Preserves standards compliance at the cost of a large new operational surface: broker correctness governs whether requests arrive at all, per-vehicle addressing must be maintained, cert lifecycle and key management for the tunnel layer, and firewall coordination with every carrier. Solves the transport problem by adding a bigger one. |
| **Extend FWE's protobuf envelope with a `command_type` discriminator** (Topology B only) | FWE is not the extension point. Adding a field FWE does not implement produces a field FWE silently ignores. Additionally, stuffing SOVD JSON into a protobuf `bytes` slot discards the schema for zero benefit compared to the peer-topic approach. Only relevant if you're already running FWE; standalone deployments do not face this question. |
| **Publish diagnostic requests directly to MSK / Kafka** | Kafka is a broker for services, not for devices. No per-device certificate authentication, no last-will, no session semantics, no policy-scoped topic authorization. |
| **Poll-based command distribution** — vehicle polls the cloud for pending commands | Adds latency proportional to poll interval and cost proportional to (fleet size × poll rate × idle-time percentage). MQTT's push model is strictly better for control-plane use. |
| **Design a proprietary protocol from scratch, ignoring SOVD entirely** | Loses the ecosystem-interop story, which was the reason for choosing SOVD in the first place. The gateway in Part 4 is the correct response to the standards-compliance question, not abandonment of the standard. |

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## Part 4 — REST interoperability via a gateway

Where a client requires the SOVD REST binding — a partner scan tool, a compliance-testing harness, an aftermarket diagnostic application — a **REST-to-MQTT gateway** provides it. The gateway is a distinct cloud-side component with its own operational envelope. This section describes what it must do, so that a reader planning the work knows the scope.

### 4.1 What the gateway is not

It is not a URL-rewrite proxy. A rewrite that mapped `GET /vehicles/{vin}/components/{id}/faults` to `publish read_faults` and returned the correlated response would satisfy the simplest case, but it would not deliver SOVD REST semantics. SOVD REST clients rely on properties that require gateway state.

### 4.2 What the gateway must do

**Resource discovery.** SOVD REST is self-describing via hypermedia links. A client discovers a vehicle's component tree by walking `GET /vehicles/{vin}`, following links to components, following links from components to their data / faults / routines resources. The MQTT protocol described in Part 3 does not carry a component tree per request; the gateway synthesizes discovery responses from a per-vehicle catalog (which model, which ECUs, which DIDs are supported, which routines are available). That catalog is state the gateway holds.

**Error taxonomy translation.** SOVD REST expresses errors via HTTP status codes, with SOVD-specific error resources in the body. The MQTT payloads described here carry error information inside the JSON. The gateway translates between the two error taxonomies, including generating the correct HTTP status code (400 vs 401 vs 403 vs 404 vs 409 vs 5xx) from the JSON error type.

**Long-running-operation state.** SOVD REST expresses routine executions as first-class resources — `POST /routines/{id}/executions` returns a resource URI that a client polls until the execution completes. The MQTT protocol here uses a correlation-ID model; the gateway holds the mapping from execution resource URI to correlation ID, holds the current state of the execution, and translates client polls into reads against the command-record store.

**Pagination.** SOVD REST supports pagination cursors for large result sets. The gateway generates cursors, holds them for the client's session, and translates cursor advances into filtered reads.

**Authentication translation.** The vehicle certificate identifies the vehicle to the MQTT broker. The gateway maps the client's authenticated identity to the fleet-scoped authorization model — the same authorization model the internal operator API uses.

**Content-negotiation compatibility.** SOVD REST supports content negotiation. The gateway serves JSON by default, honours the standard's `Accept` header for the SOVD-defined content types, and returns appropriate 406 responses when the negotiation fails.

### 4.3 What the gateway costs

One additional stateful cloud component to build and operate. Its correctness governs whether standards-compliant clients can talk to the fleet. If the gateway is down, standards-compliant clients cannot reach the fleet, though internal callers that use the native MQTT-binding API remain unaffected.

This is a real cost. It is stated openly rather than folded into "a URL rewrite" because it is the specific trade the design makes. In exchange, the *transport layer* — the connection from cloud to every vehicle — is not per-vehicle state, and is not per-client state; it is one broker with one namespace, and it scales as the MQTT broker scales.

For fleets whose ecosystem does not include SOVD REST clients, the gateway can be deferred and added when needed. The core design does not depend on it.

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## Part 5 — Operational notes

### Authentication and authorization

**Vehicle → cloud** — mTLS with a per-device certificate, checked by AWS IoT Core against a certificate authority operated by the fleet. Fleet-wide revocation is a certificate CRL update, propagated on the order of minutes. IoT policies attached to the certificate constrain the topics the vehicle may publish and subscribe to, so a compromised device cannot publish on another vehicle's response topic.

**Cloud API → cloud handler** — standard cloud-native authentication and authorization. The operator UI authenticates a human via the fleet's identity provider; the resulting token accompanies every diagnostic request. The cloud handler performs fleet-scoped authorization per vehicle — a caller with scope for fleet A cannot issue diagnostics against a vehicle in fleet B, and a caller with no fleet scope is denied by default. The most common regression to guard against is "empty scope means all scope" — treat an empty caller-scope set as *no* access, not as *unrestricted* access.

**Cloud handler → vehicle** — the handler publishes with an IAM-authenticated identity. The IoT Rule that catches responses is invoked with a role scoped to the specific tables and buckets involved.

### Secrets management

No secrets are shipped in the agent's environment. Cross-service credentials (for example, S3 upload of oversized responses) come from a task or instance role, not from environment variables. Runtime task-parameter overrides and Lambda environment variables are readable by the corresponding `Describe*` API and should not carry credentials.

### Deployment coupling

Deploying the on-vehicle agent and the cloud handler is asynchronous. A cloud handler that supports a new `command_type` before any on-vehicle agent knows how to answer it produces `UNSUPPORTED_COMMAND` responses — graceful degradation, not failure. An on-vehicle agent that supports a new `command_type` before any cloud handler publishes it is dormant code. The peer-topic pattern makes rolling deployments non-blocking.

### Observability

Every request carries an `execId`; every response carries the same value as `correlation_id`. Latency is `response_timestamp - request_timestamp`, recorded on the command record. IoT Core emits per-topic publish metrics; DynamoDB or equivalent emits per-table write metrics; the on-vehicle agent emits per-command-type latency and success-rate metrics. A dashboard with four panels — publish rate, SUBACK rate, response latency, success/error breakdown by command_type — is sufficient to see the entire system.

### Where the pattern generalises

The pattern is applicable beyond diagnostics. Any cloud-to-device control plane on a connected fleet — remote configuration, firmware campaign staging, in-vehicle experiment toggles, over-the-air feature-flag delivery — benefits from the same substrate:

- MQTT over IoT Core for outbound-only reachability
- Peer topic sub-paths for coexistence with existing agents
- Reconnect discipline tied to SUBACK
- Correlation-ID request/response with a durable command record
- Object-store fallback above the transport's payload ceiling
- Safety enforcement at Layer 7, expressed as executable assertions rather than prose

The rest is domain modelling.

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## Appendix — Mapping to the reference implementation

The paper uses SOVD-aligned nomenclature — the vocabulary a client built to the standard's data model would expect. The CMS reference implementation (`connected-mobility-guidance-on-aws`) chose OBD-II-native vocabulary for its internal `command_type` discriminator, its response payload key, and its Topology B sub-path segment. Both vocabularies describe the same operations against the same data model; the difference is which audience they read most cleanly to.

**Command-type discriminator (§ 3.7):**

| Paper (§ 3.7)          | Reference implementation |
|------------------------|--------------------------|
| `read_faults`          | `read_dtcs`              |
| `read_freeze_frame`    | `read_freeze_frames`     |
| `clear_faults`         | `clear_dtcs`             |
| `read_identity`        | `read_identity`          |
| `read_data`            | `read_data`              |
| `run_routine`          | `run_routine`            |

`read_dtcs` / `clear_dtcs` / `read_freeze_frames` name the underlying OBD-II Diagnostic Trouble Code vocabulary that ISO 14229's `0x19` / `0x14` services execute against. The remaining three names are identical.

**Response payload key (§ 3.8):** the paper renders each component's fault list under `"faults": [...]`; the reference implementation uses `"dtcs": [...]`. The keys inside each record (`code`, `status`, `occurrence_count`, `first_seen_ms`, `last_seen_ms`, `freeze_frame` and its signal-name / value / unit / timestamp shape) are unchanged.

**Topology B sub-path (§ 3.4):** the paper's Topology B uses `.../executions/{execId}/diag/{request|response}`; the reference implementation uses `.../executions/{execId}/sovd/{request|response}`. The peer-topic coexistence guarantee described in § 3.4 holds identically — FWE subscribes on a literal `/protobuf` suffix, and the MQTT broker's topic-filter matching admits no wildcard within a level. Any literal sub-path segment other than the one FWE binds works; `diag/` and `sovd/` are interchangeable choices, not different mechanisms.

**Why keep the paper generic.** The audience for the paper includes ASAM readers, ISO reviewers, and ecosystem partners familiar with the standard's resource model. `faults` and `read_faults` track ASAM SOVD vocabulary; `dtcs` and `read_dtcs` track OBD-II vocabulary. Both are correct in their respective contexts, and collapsing one into the other loses the connection to whichever audience is not being addressed.

**Where the reference implementation lives.** `services/commands/commands_lambda.py` dispatches the six `command_type` values above onto the SOVD topic path (`_send_sovd_command`, topic template `cms/commands/things/{vin}/executions/{correlation_id}/sovd/request`). `deployment/stacks/commands_stack.py` (see `SovdResponseRule`) routes the response half `cms/commands/things/+/executions/+/sovd/response` to the response handler. `services/simulation/lambda/simulation_lambda.py` and `services/simulation/realtime_telemetry_simulator.py` carry the on-vehicle sidecar and the deterministic sim that stands in for `services/_shared/routine_result_schemas.py`-typed routines during staging smoke.

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## Appendix — Standards references

- **ASAM SOVD** — Service-Oriented Vehicle Diagnostics. The ASAM specification defines the resource model, JSON schemas, and REST binding. Currently in the process of ISO standardisation as **ISO 17978** (multi-part series, in development).
- **ISO 14229 (UDS)** — Unified Diagnostic Services. The application-layer diagnostic protocol that SOVD servers typically front on the vehicle side.
- **ISO 15765-2** — Diagnostic communication over CAN: Transport protocol and network layer services. This is the ISO-TP layer that carries UDS PDUs over CAN frames; the on-vehicle agent's transport library sits here.
- **ISO 13400 (DoIP)** — Diagnostic communication over Internet Protocol. Alternative to ISO 15765-2 where the vehicle bus is Ethernet rather than CAN. The pattern described here works identically over DoIP; only the on-vehicle agent's bus-side implementation differs.
- **MQTT 3.1.1 / 5.0** — In particular the topic-filter matching rules (which make peer-topic coexistence with FWE a construction-time guarantee) and the SUBSCRIBE / SUBACK semantics (which are the correct presence signal).
- **AWS IoT Core** — MQTT broker with mTLS device authentication, IoT Rules for topic-triggered handlers, and IoT Policies for topic-level authorization.
- **AWS IoT FleetWise Edge Agent (FWE)** — Open-source vehicle-side agent for telemetry campaign execution and remote actuator commands. Source at `github.com/aws/aws-iot-fleetwise-edge`, Apache 2.0. **Optional in this pattern:** deployments that already run FWE use the Topology B coexistence approach (FWE owns the `.../request/protobuf` half of the topic tree; the diagnostic agent owns the `.../diag/*` half). Deployments that do not run FWE use Topology A (standalone), which is the simpler case.
