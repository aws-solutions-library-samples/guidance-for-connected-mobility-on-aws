// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * simulationClient — request body and auth header contract.
 *
 * Spec: `.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/tasks.md` T7.7
 *
 * ## What these tests guard
 *
 * 1. **Empty-endpoint degradation**: when simulationApiEndpoint is empty,
 *    every function returns null rather than fetching undefined/...
 * 2. **Auth header**: the bearer token is the access token from sessionStorage.
 * 3. **POST /start body — rule_name**: the serialized request body sent to
 *    /start contains `rule_name` equal to `simulationProductRuleName` when
 *    that value is non-empty. (T7.7 amendment — this is the SEVENTH edge-table
 *    instance: the portal is the only supplier; the Lambda does not fill it in.)
 * 4. **POST /start body — vehicles**: the serialized body contains
 *    `vehicles: ["VEH-MRDN-0011"]` — the vehicleId, NOT the VIN. A VIN here
 *    resolves to `{}` → 404 (see issues/2026-09-05-vehicleid-diverges-from-vin).
 * 5. **Absent rule_name when empty**: when simulationProductRuleName is "",
 *    the key is absent from the body — not present-and-empty. The Lambda
 *    applies its CMS-native default only when the key is absent.
 *
 * Assertion style: we assert on the **body actually handed to `fetch`**, not
 * on an argument passed to a helper — the only shape that proves the wire-level
 * serialization is correct.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { STORAGE_KEY_ACCESS_TOKEN, STORAGE_KEY_ID_TOKEN } from "../../auth";
import {
  getAgentStatus,
  getSimulationApiBase,
  getSimulationStatus,
  listSimulations,
  startSimulation,
  stopSimulation,
} from "../simulationClient";

const BASE = "https://simulation.example.invalid";
const RULE_NAME = "cms_staging_cs_product_meridian_ev_rule";
const VEHICLE_ID = "VEH-MRDN-0011";
const ACCESS_TOKEN = "ACCESS.TOKEN.bearer";
const ID_TOKEN = "ID.TOKEN.bearer";

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Records the fetch call and returns a 200 response with the given body. */
function recordingFetch(body: unknown = {}) {
  return vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => body,
    } as unknown as Response;
  });
}

/** Extract the Authorization header from the first mock call. */
function authHeaderOf(mockFn: ReturnType<typeof recordingFetch>): string | undefined {
  const init = mockFn.mock.calls[0][1] as RequestInit;
  return (init?.headers as Record<string, string> | undefined)?.Authorization;
}

/** Deserialize the request body from the first mock call. */
function bodyOf(mockFn: ReturnType<typeof recordingFetch>): Record<string, unknown> {
  const init = mockFn.mock.calls[0][1] as RequestInit;
  if (!init?.body) return {};
  return JSON.parse(init.body as string) as Record<string, unknown>;
}

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  window.runtimeConfig = {
    cognitoUserPoolId: "<pool-id>",
    cognitoClientId: "testclientid00000001",
    cognitoDomain: "portal.example.invalid",
    connectedServicesApiEndpoint: "https://api.example.invalid",
    subscriptionsApiEndpoint: "https://subscriptions.example.invalid",
    simulationApiEndpoint: BASE,
    simulationProductRuleName: RULE_NAME,
    dataProcessingApiEndpoint: "https://data-processing.example.invalid",
    callbackOrigin: "https://connected-services.example.invalid",
  };
  sessionStorage.clear();
  sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, ID_TOKEN);
  sessionStorage.setItem(STORAGE_KEY_ACCESS_TOKEN, ACCESS_TOKEN);
});

afterEach(() => {
  delete window.runtimeConfig;
  sessionStorage.clear();
});

// ── Empty-endpoint degradation ────────────────────────────────────────────────

describe("simulationClient — empty-endpoint degradation", () => {
  it("getSimulationApiBase() returns null when simulationApiEndpoint is empty", () => {
    window.runtimeConfig = {
      ...window.runtimeConfig!,
      simulationApiEndpoint: "",
    };
    expect(getSimulationApiBase()).toBeNull();
  });

  it("startSimulation() returns null when endpoint is empty", async () => {
    window.runtimeConfig = {
      ...window.runtimeConfig!,
      simulationApiEndpoint: "",
    };
    const result = await startSimulation([VEHICLE_ID], recordingFetch());
    expect(result).toBeNull();
  });

  it("listSimulations() returns null when endpoint is empty", async () => {
    window.runtimeConfig = {
      ...window.runtimeConfig!,
      simulationApiEndpoint: "",
    };
    const result = await listSimulations(recordingFetch());
    expect(result).toBeNull();
  });

  it("getAgentStatus() returns null when endpoint is empty", async () => {
    window.runtimeConfig = {
      ...window.runtimeConfig!,
      simulationApiEndpoint: "",
    };
    const result = await getAgentStatus("VIN-001", recordingFetch());
    expect(result).toBeNull();
  });
});

// ── Auth header ───────────────────────────────────────────────────────────────

describe("simulationClient — auth header carries access token", () => {
  it("startSimulation() sends Bearer access-token header", async () => {
    const mock = recordingFetch({ simulation_id: "sim-001" });
    await startSimulation([VEHICLE_ID], mock);
    expect(authHeaderOf(mock)).toBe(`Bearer ${ID_TOKEN}`);
  });

  it("stopSimulation() sends Bearer access-token header", async () => {
    const mock = recordingFetch({});
    await stopSimulation("sim-001", mock);
    expect(authHeaderOf(mock)).toBe(`Bearer ${ID_TOKEN}`);
  });

  it("listSimulations() sends Bearer access-token header", async () => {
    const mock = recordingFetch({ simulations: [] });
    await listSimulations(mock);
    expect(authHeaderOf(mock)).toBe(`Bearer ${ID_TOKEN}`);
  });

  it("getAgentStatus() sends Bearer ID-token header", async () => {
    const mock = recordingFetch({ agents: [] });
    await getAgentStatus("VIN-001", mock);
    expect(authHeaderOf(mock)).toBe(`Bearer ${ID_TOKEN}`);
  });
});

// ── POST /start body — rule_name (T7.7 amendment) ────────────────────────────

describe("simulationClient — POST /start body contains rule_name and vehicles", () => {
  it("the serialized request body contains rule_name equal to simulationProductRuleName", async () => {
    const mock = recordingFetch({ simulation_id: "sim-002" });
    await startSimulation([VEHICLE_ID], mock);
    const body = bodyOf(mock);
    // Assert on the body actually handed to fetch — not on an intermediate arg.
    expect(body.rule_name).toBe(RULE_NAME);
  });

  it("the serialized request body contains vehicles: ['VEH-MRDN-0011'] (vehicleId, not VIN)", async () => {
    const mock = recordingFetch({ simulation_id: "sim-003" });
    await startSimulation([VEHICLE_ID], mock);
    const body = bodyOf(mock);
    expect(body.vehicles).toEqual([VEHICLE_ID]);
  });

  // ── Mutation 1: drop rule_name from the request builder ───────────────────
  // Confirmed separately (see T7.7 Verify section).
  // This assertion is the in-suite equivalent: it fails if the key is absent.
  it("rule_name key is present in the body when simulationProductRuleName is non-empty", async () => {
    const mock = recordingFetch({ simulation_id: "sim-004" });
    await startSimulation([VEHICLE_ID], mock);
    const body = bodyOf(mock);
    expect(Object.prototype.hasOwnProperty.call(body, "rule_name")).toBe(true);
  });
});

// ── Absent rule_name when empty (T7.7 mutation 2) ────────────────────────────

describe("simulationClient — rule_name absent when simulationProductRuleName is empty", () => {
  it("rule_name is NOT in the body when simulationProductRuleName is empty string", async () => {
    // Mutation 2: simulationProductRuleName = "" → key must be absent
    window.runtimeConfig = {
      ...window.runtimeConfig!,
      simulationProductRuleName: "",
    };
    const mock = recordingFetch({ simulation_id: "sim-005" });
    await startSimulation([VEHICLE_ID], mock);
    const body = bodyOf(mock);
    expect(Object.prototype.hasOwnProperty.call(body, "rule_name")).toBe(false);
  });

  it("vehicles is still present in the body when simulationProductRuleName is empty", async () => {
    window.runtimeConfig = {
      ...window.runtimeConfig!,
      simulationProductRuleName: "",
    };
    const mock = recordingFetch({ simulation_id: "sim-006" });
    await startSimulation([VEHICLE_ID], mock);
    const body = bodyOf(mock);
    expect(body.vehicles).toEqual([VEHICLE_ID]);
  });
});


// ── Request URLs (review-group7-cycle1 Critical) ──────────────────────────────

/**
 * Every simulation route sits behind `/api/simulation` on the API Gateway
 * (`simulation_stack.py:972-990`; confirmed against the live API `tekm196qb5`,
 * which exposes `/api/simulation/stop/{simulationId}`, `/api/simulation/status/
 * {simulationId}` and `/api/simulation/list`).
 *
 * The first cut of this client spelled the prefix inline per call site and got
 * it right for `/start` and wrong for the other three. Every test above still
 * passed, because they assert on the request **body** and **headers** — and a
 * mocked `fetch` never resolves the URL. The stop, status and list controls
 * would have 403'd at the gateway.
 *
 * So: assert the URL too. These are the assertions that make a mocked `fetch`
 * able to catch a routing defect, and the reason the prefix is now one constant
 * rather than four literals.
 */
describe("simulationClient — request URLs carry the /api/simulation prefix", () => {
  it("startSimulation() POSTs to /api/simulation/start", async () => {
    const f = recordingFetch({ simulationId: "abc123" });
    await startSimulation([VEHICLE_ID], f);
    expect(f.mock.calls[0][0]).toBe(`${BASE}/api/simulation/start`);
  });

  it("stopSimulation() POSTs to /api/simulation/stop/{id}", async () => {
    const f = recordingFetch({});
    await stopSimulation("abc123", f);
    expect(f.mock.calls[0][0]).toBe(`${BASE}/api/simulation/stop/abc123`);
  });

  it("getSimulationStatus() GETs /api/simulation/status/{id}", async () => {
    const f = recordingFetch({ status: "running" });
    await getSimulationStatus("abc123", f);
    expect(f.mock.calls[0][0]).toBe(`${BASE}/api/simulation/status/abc123`);
  });

  it("listSimulations() GETs /api/simulation/list", async () => {
    const f = recordingFetch({ simulations: [] });
    await listSimulations(f);
    expect(f.mock.calls[0][0]).toBe(`${BASE}/api/simulation/list`);
  });

  it("a simulationId needing escaping is encoded, and stays under the prefix", async () => {
    const f = recordingFetch({});
    await stopSimulation("a b/c", f);
    expect(f.mock.calls[0][0]).toBe(`${BASE}/api/simulation/stop/a%20b%2Fc`);
  });

  it("getAgentStatus() GETs /api/simulation/agent/status", async () => {
    const f = recordingFetch({ agents: [] });
    await getAgentStatus("VIN-001", f);
    expect(f.mock.calls[0][0]).toBe(`${BASE}/api/simulation/agent/status`);
  });
});

// ── Token type (measured against the live authorizer) ─────────────────────────

/**
 * The simulation API's COGNITO_USER_POOLS authorizer validates `aud`, which
 * access tokens do not carry. Measured against live `tekm196qb5` on 2026-09-13,
 * same request and user: ID token -> 200, access token -> 401.
 *
 * These tests exist because the client originally sent the ACCESS token and every
 * portal call 401'd, while all 16 tests above passed — they mock `fetch`, so no
 * authorizer ever runs. Asserting *which* token is sent is the closest a unit test
 * can get to the property, so it is asserted explicitly and negatively.
 */
describe("simulationClient — sends the ID token, never the access token", () => {
  it("Authorization carries the ID token", async () => {
    const f = recordingFetch({ simulationId: "x" });
    await startSimulation([VEHICLE_ID], f);
    expect(authHeaderOf(f)).toBe(`Bearer ${ID_TOKEN}`);
  });

  it("Authorization does NOT carry the access token", async () => {
    const f = recordingFetch({ simulationId: "x" });
    await startSimulation([VEHICLE_ID], f);
    expect(authHeaderOf(f)).not.toBe(`Bearer ${ACCESS_TOKEN}`);
  });

  it("no token is sent when only the access token is present", async () => {
    sessionStorage.clear();
    sessionStorage.setItem(STORAGE_KEY_ACCESS_TOKEN, ACCESS_TOKEN);
    const f = recordingFetch({ simulationId: "x" });
    await startSimulation([VEHICLE_ID], f);
    expect(authHeaderOf(f)).toBeUndefined();
  });
});


// ── getAgentStatus — agent matching and error paths ───────────────────────────

/**
 * getAgentStatus returns {reachable, agentRunning} based on the `agents` array
 * from GET /api/simulation/agent/status.
 *
 * An agent is "running" when its status is RUNNING, PENDING, or PROVISIONING
 * AND it matches the requested VIN via `a.vin === vin || a.vehicleName === vin`.
 *
 * ## What these tests guard
 *
 * - All three live statuses are recognised (RUNNING, PENDING, PROVISIONING).
 *   Mutation (a): drop PROVISIONING → the PROVISIONING test FAILS.
 * - Matching on `vehicleName` as well as `vin`.
 *   Mutation (b): remove the `|| a.vehicleName === vin` branch → the
 *   vehicleName test FAILS.
 * - A non-live status (STOPPED) returns agentRunning: false.
 * - A VIN that matches nothing returns agentRunning: false.
 * - !resp.ok (e.g. 401) → {reachable: false, agentRunning: false}, not a throw.
 * - A thrown network error → {reachable: false, agentRunning: false}, not rethrown.
 * - A response whose `agents` key is absent → agentRunning: false (d.agents ?? []).
 */
describe("simulationClient — getAgentStatus agent matching and error paths", () => {
  const VIN = "1HGBH41JXMN109186";

  // Helper that returns an ok response with the given agents array.
  function agentsFetch(agents: unknown[]): ReturnType<typeof recordingFetch> {
    return recordingFetch({ agents });
  }

  // ── Live status matching ────────────────────────────────────────────────────

  it("RUNNING status matching on vin → {reachable: true, agentRunning: true}", async () => {
    const f = agentsFetch([{ vin: VIN, status: "RUNNING" }]);
    const result = await getAgentStatus(VIN, f);
    expect(result).toEqual({ reachable: true, agentRunning: true });
  });

  it("PENDING status matching on vin → {reachable: true, agentRunning: true}", async () => {
    const f = agentsFetch([{ vin: VIN, status: "PENDING" }]);
    const result = await getAgentStatus(VIN, f);
    expect(result).toEqual({ reachable: true, agentRunning: true });
  });

  // Mutation guard (a): if PROVISIONING is dropped from the status set, this test fails.
  it("PROVISIONING status matching on vin → {reachable: true, agentRunning: true}", async () => {
    const f = agentsFetch([{ vin: VIN, status: "PROVISIONING" }]);
    const result = await getAgentStatus(VIN, f);
    expect(result).toEqual({ reachable: true, agentRunning: true });
  });

  // ── Non-live status ─────────────────────────────────────────────────────────

  it("STOPPED status → agentRunning: false even when VIN matches", async () => {
    const f = agentsFetch([{ vin: VIN, status: "STOPPED" }]);
    const result = await getAgentStatus(VIN, f);
    expect(result).toEqual({ reachable: true, agentRunning: false });
  });

  // ── vehicleName match ───────────────────────────────────────────────────────

  // Mutation guard (b): if `|| a.vehicleName === vin` is removed, this test fails.
  it("RUNNING status matching on vehicleName → {reachable: true, agentRunning: true}", async () => {
    const f = agentsFetch([{ vehicleName: VIN, status: "RUNNING" }]);
    const result = await getAgentStatus(VIN, f);
    expect(result).toEqual({ reachable: true, agentRunning: true });
  });

  it("agent with different vehicleName and no vin → agentRunning: false", async () => {
    const f = agentsFetch([{ vehicleName: "OTHER-VIN", status: "RUNNING" }]);
    const result = await getAgentStatus(VIN, f);
    expect(result).toEqual({ reachable: true, agentRunning: false });
  });

  // ── No matching VIN ─────────────────────────────────────────────────────────

  it("non-empty agents array with no matching VIN → agentRunning: false", async () => {
    const f = agentsFetch([
      { vin: "OTHER-VIN-001", status: "RUNNING" },
      { vin: "OTHER-VIN-002", status: "PENDING" },
    ]);
    const result = await getAgentStatus(VIN, f);
    expect(result).toEqual({ reachable: true, agentRunning: false });
  });

  // ── !resp.ok branch (the 401 path) ──────────────────────────────────────────

  it("!resp.ok → {reachable: false, agentRunning: false, httpStatus}, not a throw", async () => {
    const f = vi.fn(async (): Promise<Response> => {
      return {
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ message: "Unauthorized" }),
      } as unknown as Response;
    });
    // Must not throw — the caller expects a value, not an exception.
    const result = await getAgentStatus(VIN, f);
    // httpStatus is the load-bearing addition: it is what lets the caller say
    // "your session expired" instead of "Simulator offline", which was observed
    // false on 2026-09-22 while the simulator was running. Asserted by exact value,
    // not just presence — a probe that reported some arbitrary number would pass a
    // presence check and still mislead.
    expect(result).toEqual({
      reachable: false,
      agentRunning: false,
      httpStatus: 401,
    });
  });

  it("a 403 is reported distinctly from a 401", async () => {
    // Separate from the 401 case because the two produce different operator-facing
    // guidance (re-authenticate vs missing fleet assignment). Collapsing them to a
    // bare boolean is what the caller used to do.
    const f = vi.fn(async (): Promise<Response> => {
      return {
        ok: false,
        status: 403,
        statusText: "Forbidden",
        json: async () => ({ error: "Caller has no fleet assignments." }),
      } as unknown as Response;
    });
    const result = await getAgentStatus(VIN, f);
    expect(result).toEqual({
      reachable: false,
      agentRunning: false,
      httpStatus: 403,
    });
  });

  // ── Network error ───────────────────────────────────────────────────────────

  it("fetch() throwing a network error → httpStatus null, distinguishing it from a refusal", async () => {
    const f = vi.fn(async (): Promise<Response> => {
      throw new TypeError("network error");
    });
    const result = await getAgentStatus(VIN, f);
    // null, not a number: the request never completed, so there is no status to
    // report and no auth guidance to give. A number here would claim the gateway
    // answered when it did not.
    expect(result).toEqual({
      reachable: false,
      agentRunning: false,
      httpStatus: null,
    });
  });

  // ── Missing agents key ──────────────────────────────────────────────────────

  it("response without an 'agents' key → agentRunning: false (d.agents ?? [] fallback)", async () => {
    // No 'agents' key in the response body — simulates a shape that predates the field
    // or an unexpected server response. The ?? [] guard must prevent a TypeError.
    const f = recordingFetch({});
    const result = await getAgentStatus(VIN, f);
    expect(result).toEqual({ reachable: true, agentRunning: false });
  });
});
