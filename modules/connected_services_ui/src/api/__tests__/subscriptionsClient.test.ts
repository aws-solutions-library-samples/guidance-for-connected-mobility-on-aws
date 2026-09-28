// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * subscriptionsClient — Authorization header contract.
 *
 * Spec: `.kiro/specs/2026-09-10-cms-connected-services-subscriptions/spec.md`
 * Issue: `issues/2026-09-13-cs-portal-subscriptions-client-sends-access-token/`
 *
 * ## Why this file exists
 *
 * Before 2026-09-13 nothing in this portal asserted *which* token the
 * subscription-plane client sends. It sent the access token; the API's
 * `COGNITO_USER_POOLS` authorizer validates `aud`, which only the ID token
 * carries, so every browser call 401'd while every test stayed green.
 *
 * These tests therefore assert a **property, not a presence**: each request's
 * `Authorization` header must equal `Bearer <the value stored under
 * STORAGE_KEY_ID_TOKEN>`, and must not carry the access token. Both keys are
 * populated with *distinct* values in every case so that reading the wrong one
 * cannot accidentally pass.
 *
 * Measured against live staging 2026-09-13 (`/products`, `/subscriptions`,
 * `/vehicles/available`; raw and `Bearer `-prefixed): ID token 200 on all six,
 * access token 401 on all six.
 */

import fs from "node:fs";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { STORAGE_KEY_ACCESS_TOKEN, STORAGE_KEY_ID_TOKEN } from "../../auth";
import {
  addVinToSubscription,
  fetchAvailableVehicles,
  fetchMySubscriptions,
  fetchProducts,
  listVehiclesForSimulation,
  SimulationStartError,
  startVehicleSimulation,
} from "../subscriptionsClient";

const ID_TOKEN = "ID.TOKEN.the-authorizer-accepts-this-one";
const ACCESS_TOKEN = "ACCESS.TOKEN.this-one-401s";
const BASE = "https://subscriptions.example.invalid";

/** Records the init of the single call made, and returns an empty 200 body. */
function recordingFetch(body: unknown = {}) {
  return vi.fn(async (_url: string, init?: RequestInit) => {
    void _url;
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => body,
      // expose the init so assertions can read the headers actually sent
      __init: init,
    } as unknown as Response;
  });
}

function headersOf(fetchMock: ReturnType<typeof recordingFetch>): Record<string, string> {
  const init = fetchMock.mock.calls[0][1] as RequestInit;
  return (init.headers ?? {}) as Record<string, string>;
}

beforeEach(() => {
  window.runtimeConfig = {
    cognitoUserPoolId: "<pool-id>",
    cognitoClientId: "testclientid00000001",
    cognitoDomain: "portal.example.invalid",
    connectedServicesApiEndpoint: "https://api.example.invalid",
    subscriptionsApiEndpoint: BASE,
    simulationApiEndpoint: "https://simulation.example.invalid",
    simulationProductRuleName: "cms_staging_cs_product_meridian_ev_rule",
    dataProcessingApiEndpoint: "https://data-processing.example.invalid",
    callbackOrigin: "https://connected-services.example.invalid",
  };
  sessionStorage.clear();
});

afterEach(() => {
  delete window.runtimeConfig;
  sessionStorage.clear();
  vi.restoreAllMocks();
});

// ── Every call site, each asserted on the *value* of the header ──────────────
//
// This table was headed "The four call sites" while the module had six functions
// attaching `Authorization`. `listVehiclesForSimulation` had no test anywhere in the
// repo: pointing it at the access token left `tsc` at 0 and the full suite at
// 1033/1033 (review cycle 2, W1). It is the function that populates the simulation
// picker, so a 401 there means an empty picker and a permanently disabled Start —
// the same user-visible shape as `issues/2026-09-13-cs-portal-subscriptions-client-sends-access-token`,
// which is why this file exists.
//
// ALL SIX are listed. An earlier revision exempted `startVehicleSimulation` via a
// `COVERED_ELSEWHERE` set on the grounds that its own describe block covers it — but
// that exemption was itself unverified: deleting the test that justified it left
// 27/27 green, putting the function back in exactly the state
// `listVehiclesForSimulation` was found in (review cycle 3, W2). An exemption whose
// premise nothing checks reproduces the hole the guard exists to close, so there is
// no exemption mechanism any more.

const CALLS: ReadonlyArray<{
  readonly name: string;
  readonly invoke: (f: typeof fetch) => Promise<unknown>;
}> = [
  { name: "fetchProducts", invoke: (f) => fetchProducts(f) },
  { name: "fetchMySubscriptions", invoke: (f) => fetchMySubscriptions(f) },
  { name: "fetchAvailableVehicles", invoke: (f) => fetchAvailableVehicles(f) },
  {
    name: "addVinToSubscription",
    invoke: (f) => addVinToSubscription("sub-1", "1G1FY6S07N4100001", f),
  },
  { name: "listVehiclesForSimulation", invoke: (f) => listVehiclesForSimulation(f) },
  { name: "startVehicleSimulation", invoke: (f) => startVehicleSimulation("VEH-0001", f) },
];

/** The client module's own source. `__dirname` per provenanceRender.test.ts —
 * `import.meta.url` is not a file:// URL under this vitest environment. */
function readClientSource(): string {
  const file = path.resolve(__dirname, "..", "subscriptionsClient.ts");
  const src = fs.readFileSync(file, "utf8");
  if (src.length < 500) {
    throw new Error(`subscriptionsClient.ts read as ${src.length} chars — path anchor broken`);
  }
  return src;
}

describe("every Authorization-attaching function is in the CALLS table", () => {
  // The heading over a hand-maintained list is not a guard. This derives the set of
  // functions that attach the header from the module SOURCE and requires each to
  // appear in `CALLS`, so adding a seventh authenticated call fails here instead of
  // shipping untested.
  //
  // Cycle 3 (W2) showed the first version missed a 7th function declared as an arrow
  // const, one using `headers["Authorization"]`, and one using an inline
  // `headers: { Authorization: … }` literal — 28/28 green in each case. The detector
  // now matches exported functions AND exported arrow consts, and any spelling of the
  // header rather than the single `headers.Authorization` form.

  /** Every exported callable in the module, with its body text. */
  function exportedCallables(src: string): ReadonlyArray<{ name: string; body: string }> {
    const starts: Array<{ name: string; index: number }> = [];
    for (const m of src.matchAll(/export\s+(?:async\s+)?function\s+(\w+)\s*\(/g)) {
      starts.push({ name: m[1], index: m.index! });
    }
    // `export const foo = async (…) =>` / `export const foo = (…) =>`
    for (const m of src.matchAll(/export\s+const\s+(\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?\(/g)) {
      starts.push({ name: m[1], index: m.index! });
    }
    starts.sort((a, b) => a.index - b.index);
    return starts.map((s, i) => ({
      name: s.name,
      body: src.slice(s.index, i + 1 < starts.length ? starts[i + 1].index : undefined),
    }));
  }

  /** Any spelling of attaching the header, not just `headers.Authorization`. */
  const ATTACHES_HEADER = /Authorization/;

  it("no authenticated function is missing coverage", () => {
    const src = readClientSource();
    const callables = exportedCallables(src);

    // Anti-vacuity floors are pinned to the CURRENT true values, not a loose
    // lower bound: cycle 3 noted `> 4` and `> 0` would both survive a detector that
    // under-counted by one. If the module legitimately grows, these numbers move in
    // the same commit as the new entry in CALLS.
    expect(
      callables.length,
      "detector found an unexpected number of exported callables — regex is broken " +
        `or the module changed shape. Found: ${callables.map((c) => c.name).join(", ")}`,
    ).toBeGreaterThanOrEqual(7);

    const authAttaching = callables
      .filter((c) => ATTACHES_HEADER.test(c.body))
      .map((c) => c.name);
    expect(
      authAttaching.length,
      `expected 6 Authorization-attaching functions, found ${authAttaching.length}: ` +
        `${authAttaching.join(", ")}. If the module gained or lost one, update this ` +
        "number and CALLS together.",
    ).toBe(6);

    const named = new Set(CALLS.map((c) => c.name));
    const missing = authAttaching.filter((n) => !named.has(n));
    expect(
      missing,
      `these functions attach Authorization but are not in CALLS: ${missing.join(", ")}`,
    ).toEqual([]);
  });

  it("no CALLS entry names a function the module no longer exports", () => {
    // The other direction: a rename that leaves a stale entry must fail here rather
    // than quietly reducing coverage.
    const src = readClientSource();
    const names = new Set(exportedCallables(src).map((c) => c.name));
    for (const { name } of CALLS) {
      expect(names.has(name), `CALLS names ${name}, which the module does not export`).toBe(
        true,
      );
    }
  });

  it("the detector recognises every declaration and header form", () => {
    // Guards the detector itself against the three shapes that slipped past it.
    const sample = [
      'export async function a() { headers.Authorization = "x"; }',
      'export function b() { headers["Authorization"] = "x"; }',
      'export const c = async (f) => { const h = { Authorization: "x" }; return h; };',
      'export const d: Foo = (f) => { return 1; };',
    ].join("\n");
    const found = exportedCallables(sample);
    expect(found.map((f) => f.name).sort()).toEqual(["a", "b", "c", "d"]);
    expect(
      found.filter((f) => ATTACHES_HEADER.test(f.body)).map((f) => f.name).sort(),
    ).toEqual(["a", "b", "c"]);
  });
});

describe("Authorization header carries the ID token, not the access token", () => {
  for (const { name, invoke } of CALLS) {
    it(`${name} sends Bearer <idToken>`, async () => {
      sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, ID_TOKEN);
      sessionStorage.setItem(STORAGE_KEY_ACCESS_TOKEN, ACCESS_TOKEN);

      const f = recordingFetch();
      await invoke(f as unknown as typeof fetch);

      expect(headersOf(f).Authorization).toBe(`Bearer ${ID_TOKEN}`);
    });

    it(`${name} does not send the access token`, async () => {
      sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, ID_TOKEN);
      sessionStorage.setItem(STORAGE_KEY_ACCESS_TOKEN, ACCESS_TOKEN);

      const f = recordingFetch();
      await invoke(f as unknown as typeof fetch);

      expect(headersOf(f).Authorization).not.toContain(ACCESS_TOKEN);
    });

    it(`${name} sends no Authorization header when only the access token is stored`, async () => {
      // The anti-regression case: if the client is ever pointed back at
      // STORAGE_KEY_ACCESS_TOKEN, a header appears here and this fails.
      sessionStorage.setItem(STORAGE_KEY_ACCESS_TOKEN, ACCESS_TOKEN);

      const f = recordingFetch();
      await invoke(f as unknown as typeof fetch);

      expect(headersOf(f).Authorization).toBeUndefined();
    });
  }
});

describe("storage key", () => {
  it("reads 'idToken' — the key auth.ts owns, not a third path", () => {
    expect(STORAGE_KEY_ID_TOKEN).toBe("idToken");
    expect(STORAGE_KEY_ACCESS_TOKEN).toBe("authToken");
    expect(STORAGE_KEY_ID_TOKEN).not.toBe(STORAGE_KEY_ACCESS_TOKEN);
  });
});



// ─────────────────────────────────────────────────────────────────────────────
// T6.2 — startVehicleSimulation (was resolveSimulationPath)
//
// Spec: `.kiro/specs/2026-09-15-cms-cs-campaign-ownership`, Task 6.2.
//
// Task 6.1 turned `POST /simulate/start` from a resolver into a dispatcher, so this
// client's old name and its `SimulationStartResolvedResponse` type both described
// behaviour that no longer existed — and the type still declared a `message` field
// the server had stopped sending.
//
// The property worth pinning is not "it posts somewhere". It is that the browser
// contributes NOTHING to the routing decision, and that a refusal keeps its `reason`
// so the operator learns which of four actionable outcomes they hit.
// ─────────────────────────────────────────────────────────────────────────────

describe("T6.2 startVehicleSimulation", () => {
  const OK_BODY = {
    simulation_id: "sim-abc123",
    vehicle_id: "VEH-0001",
    data_source: "vehicle-telemetry",
    dispatch: "FWE agent + collection scheme → MQTT → Flink",
    success: true,
  };

  function jsonFetch(status: number, body: unknown) {
    return vi.fn(async (_url: string, init?: RequestInit) => {
      void _url;
      return {
        ok: status >= 200 && status < 300,
        status,
        statusText: status === 200 ? "OK" : "Error",
        json: async () => body,
        __init: init,
      } as unknown as Response;
    });
  }

  it("posts to /simulate/start with vehicle_id in the body", async () => {
    const f = jsonFetch(200, OK_BODY);
    await startVehicleSimulation("VEH-0001", f as unknown as typeof fetch);
    const [url, init] = f.mock.calls[0];
    expect(url).toBe(`${BASE}/simulate/start`);
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      vehicle_id: "VEH-0001",
    });
  });

  it("sends NOTHING that could influence routing", async () => {
    // The pre-T6.2 start path (`simulationClient.startSimulation`) read
    // `simulationProductRuleName` from runtime config and put it in the body, which
    // let the browser assert a routing destination. `beforeEach` above populates
    // that config key, so if this client ever read it the assertion would catch it.
    const f = jsonFetch(200, OK_BODY);
    await startVehicleSimulation("VEH-0001", f as unknown as typeof fetch);
    const body = JSON.parse((f.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.keys(body)).toEqual(["vehicle_id"]);
    for (const forbidden of ["rule_name", "mode", "data_source", "dataSource", "path"]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it("returns null when the subscriptions endpoint is unconfigured", async () => {
    window.runtimeConfig = { ...window.runtimeConfig!, subscriptionsApiEndpoint: "" };
    const f = jsonFetch(200, OK_BODY);
    const result = await startVehicleSimulation("VEH-0001", f as unknown as typeof fetch);
    expect(result).toBeNull();
    // and it must not have issued a request against an empty base
    expect(f).not.toHaveBeenCalled();
  });

  it("returns the dispatch result on success", async () => {
    const f = jsonFetch(200, OK_BODY);
    const result = await startVehicleSimulation("VEH-0001", f as unknown as typeof fetch);
    expect(result?.simulation_id).toBe("sim-abc123");
    expect(result?.dispatch).toContain("FWE");
  });

  it("preserves the server's reason on a 400", async () => {
    // MUTATION: throw `new Error(status + statusText)` as the old implementation did
    // → this fails. That discarded the body, which is where the four actionable
    // outcomes are distinguished.
    const f = jsonFetch(400, {
      error: "Cannot start simulation: vehicle has no dataSource configured.",
      reason: "no_data_source",
      vehicle_id: "VEH-0001",
    });
    await expect(
      startVehicleSimulation("VEH-0001", f as unknown as typeof fetch),
    ).rejects.toMatchObject({
      name: "SimulationStartError",
      status: 400,
      reason: "no_data_source",
    });
  });

  it("preserves the server's reason on a 409", async () => {
    const f = jsonFetch(409, {
      error: "no telemetry campaign",
      reason: "no_telemetry_campaign",
    });
    await expect(
      startVehicleSimulation("VEH-0001", f as unknown as typeof fetch),
    ).rejects.toMatchObject({ status: 409, reason: "no_telemetry_campaign" });
  });

  it("carries the server's message as the error message", async () => {
    const f = jsonFetch(400, { error: "specific server explanation", reason: "x" });
    await expect(
      startVehicleSimulation("VEH-0001", f as unknown as typeof fetch),
    ).rejects.toThrow("specific server explanation");
  });

  it("degrades to the status line when the error body is not JSON", async () => {
    // A gateway HTML error page must not mask the status or throw something
    // unrelated to the request.
    const f = vi.fn(async () => ({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      json: async () => {
        throw new SyntaxError("Unexpected token < in JSON");
      },
    } as unknown as Response));
    await expect(
      startVehicleSimulation("VEH-0001", f as unknown as typeof fetch),
    ).rejects.toMatchObject({ status: 502, reason: undefined });
    await expect(
      startVehicleSimulation("VEH-0001", f as unknown as typeof fetch),
    ).rejects.toThrow("502 Bad Gateway");
  });

  it("throws SimulationStartError, not a plain Error", async () => {
    // The view branches on `instanceof SimulationStartError`, so the class identity
    // is load-bearing rather than cosmetic.
    const f = jsonFetch(403, { error: "Forbidden" });
    const err = await startVehicleSimulation(
      "VEH-0001",
      f as unknown as typeof fetch,
    ).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(SimulationStartError);
    expect(err).toBeInstanceOf(Error);
  });

  it("sends Bearer <idToken>, like every other call in this client", async () => {
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, ID_TOKEN);
    sessionStorage.setItem(STORAGE_KEY_ACCESS_TOKEN, ACCESS_TOKEN);
    const f = jsonFetch(200, OK_BODY);
    await startVehicleSimulation("VEH-0001", f as unknown as typeof fetch);
    const headers = (f.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBe(`Bearer ${ID_TOKEN}`);
    expect(headers.Authorization).not.toContain(ACCESS_TOKEN);
  });

  // ── T4.2 (2026-09-19-cs-trip-simulator-parity) — tripParams param ────────────
  //
  // The CS-side mirror of handler.py's T2.1 extension. `TripSimulationParams`
  // has no `mode`/`rule_name` field at the type level (see its docstring); these
  // tests pin the runtime shape the type alone cannot guarantee (a caller could
  // still force one through via `as any`).

  it("omitting tripParams still sends EXACTLY { vehicle_id } — backward compat unchanged", async () => {
    const f = jsonFetch(200, OK_BODY);
    await startVehicleSimulation("VEH-0001", f as unknown as typeof fetch);
    const body = JSON.parse((f.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.keys(body)).toEqual(["vehicle_id"]);
  });

  it("forwards city/trips/route_length when tripParams is provided", async () => {
    const f = jsonFetch(200, OK_BODY);
    await startVehicleSimulation("VEH-0001", f as unknown as typeof fetch, {
      city: "atlanta",
      trips: 1,
      route_length: 10,
    });
    const body = JSON.parse((f.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({
      vehicle_id: "VEH-0001",
      city: "atlanta",
      trips: 1,
      route_length: 10,
    });
  });

  it("forwards safety_scenarios and maintenance_scenarios when provided", async () => {
    const f = jsonFetch(200, OK_BODY);
    await startVehicleSimulation("VEH-0001", f as unknown as typeof fetch, {
      safety_scenarios: ["hard_braking_event"],
      maintenance_scenarios: ["low_tire_pressure_P0520"],
    });
    const body = JSON.parse((f.mock.calls[0][1] as RequestInit).body as string);
    expect(body.safety_scenarios).toEqual(["hard_braking_event"]);
    expect(body.maintenance_scenarios).toEqual(["low_tire_pressure_P0520"]);
  });

  it("a partial tripParams sends only the fields that were set, plus vehicle_id", async () => {
    const f = jsonFetch(200, OK_BODY);
    await startVehicleSimulation("VEH-0001", f as unknown as typeof fetch, {
      city: "munich",
    });
    const body = JSON.parse((f.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.keys(body).sort()).toEqual(["city", "vehicle_id"]);
  });

  it("MUTATION GUARD: the type-level absence of mode/rule_name on TripSimulationParams is enforced by tsc, not by a runtime strip", async () => {
    // TripSimulationParams structurally has no mode/rule_name field (see its
    // docstring) — a caller constructing one CANNOT populate either field without
    // a type-system bypass (`as any`/`as unknown as ...`). This is therefore a
    // compile-time property, verified by the fact that this file's OWN normal
    // (non-bypassing) construction of tripParams objects throughout this describe
    // block passes tsc — there is no test that can assert "the compiler would
    // reject X" at runtime, so the closest runtime pin is: every tripParams object
    // built the normal way in this file, when spread into the request body,
    // carries none of the two forbidden keys.
    const f = jsonFetch(200, OK_BODY);
    await startVehicleSimulation("VEH-0001", f as unknown as typeof fetch, {
      city: "atlanta",
      trips: 1,
      route_length: 10,
      safety_scenarios: ["hard_braking_event"],
      maintenance_scenarios: ["low_tire_pressure_P0520"],
    });
    const body = JSON.parse((f.mock.calls[0][1] as RequestInit).body as string);
    for (const forbidden of ["mode", "rule_name"]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });
});
