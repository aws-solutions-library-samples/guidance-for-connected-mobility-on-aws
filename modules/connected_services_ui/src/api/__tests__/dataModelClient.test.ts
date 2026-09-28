// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * dataModelClient — auth header, null-degradation, and error-throws contract.
 *
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` G2.
 *
 * ## What these tests guard
 *
 * 1. **Null-on-unconfigured**: when `dataProcessingApiEndpoint` is empty or
 *    absent, every function returns `null` rather than fetching from
 *    `undefined/signals` — the DMS F6 / subscriptions T3.5 degradation pattern.
 *
 * 2. **Non-2xx throws**: a non-ok response throws an Error rather than
 *    resolving to an empty list. Resolving empty on error renders as "no
 *    signals exist" rather than "the service is unavailable", which masks
 *    configuration and auth errors. This is the constraint from tasks.md G2:
 *    "No catch that converts an error into an empty array."
 *
 * 3. **Auth header**: the bearer token is the ID token (`STORAGE_KEY_ID_TOKEN`),
 *    not the access token. The data-processing API uses a COGNITO_USER_POOLS
 *    authorizer that validates the `aud` claim (ID-token only). Verified live
 *    2026-09-14: CMS-pool ID token → 200; access token → 401.
 *
 * Both token storage keys are set to DISTINCT values in every test so that
 * reading the wrong key cannot accidentally pass. Same discipline as
 * subscriptionsClient.test.ts.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { STORAGE_KEY_ACCESS_TOKEN, STORAGE_KEY_ID_TOKEN } from "../../auth";
import {
  fetchDecoderManifests,
  fetchEventCatalog,
  fetchSignals,
  fetchVehicleModels,
  getConnectedServicesApiBase,
  getDataProcessingApiBase,
} from "../dataModelClient";

const BASE = "https://data-processing.example.invalid";
const ID_TOKEN = "ID.TOKEN.the-authorizer-accepts-this-one";
const ACCESS_TOKEN = "ACCESS.TOKEN.this-one-401s";

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Returns a successful 200 fetch mock with the given body. */
function okFetch(body: unknown = {}) {
  return vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => body,
    } as unknown as Response;
  });
}

/** Returns a failing fetch mock with the given status. */
function errorFetch(status: number, statusText = "Internal Server Error") {
  return vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
    return {
      ok: false,
      status,
      statusText,
      json: async () => ({ message: "error" }),
    } as unknown as Response;
  });
}

/** Extract the Authorization header from the first mock call. */
function authHeaderOf(
  mockFn: ReturnType<typeof okFetch> | ReturnType<typeof errorFetch>,
): string | undefined {
  const init = mockFn.mock.calls[0][1] as RequestInit;
  return (init?.headers as Record<string, string> | undefined)?.Authorization;
}

/** Extract the full URL from the first mock call. */
function urlOf(
  mockFn: ReturnType<typeof okFetch> | ReturnType<typeof errorFetch>,
): string {
  return mockFn.mock.calls[0][0] as string;
}

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  window.runtimeConfig = {
    cognitoUserPoolId: "<pool-id>",
    cognitoClientId: "testclientid00000001",
    cognitoDomain: "portal.example.invalid",
    connectedServicesApiEndpoint: "https://api.example.invalid",
    subscriptionsApiEndpoint: "https://subscriptions.example.invalid",
    simulationApiEndpoint: "https://simulation.example.invalid",
    simulationProductRuleName: "cms_staging_cs_product_meridian_ev_rule",
    dataProcessingApiEndpoint: BASE,
    callbackOrigin: "https://connected-services.example.invalid",
  };
  sessionStorage.clear();
  sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, ID_TOKEN);
  sessionStorage.setItem(STORAGE_KEY_ACCESS_TOKEN, ACCESS_TOKEN);
});

afterEach(() => {
  delete window.runtimeConfig;
  sessionStorage.clear();
  vi.restoreAllMocks();
});

// ── getDataProcessingApiBase ──────────────────────────────────────────────────

describe("getDataProcessingApiBase", () => {
  it("returns the endpoint trimmed of trailing slash", () => {
    window.runtimeConfig = { ...window.runtimeConfig!, dataProcessingApiEndpoint: `${BASE}/` };
    expect(getDataProcessingApiBase()).toBe(BASE);
  });

  it("returns null when endpoint is empty string", () => {
    window.runtimeConfig = { ...window.runtimeConfig!, dataProcessingApiEndpoint: "" };
    expect(getDataProcessingApiBase()).toBeNull();
  });

  it("returns null when endpoint is absent (undefined)", () => {
    // Runtime runtime-config.js may emit an empty string rather than omitting the key;
    // test that the empty-string path also produces null.
    window.runtimeConfig = { ...window.runtimeConfig!, dataProcessingApiEndpoint: "" };
    expect(getDataProcessingApiBase()).toBeNull();
  });

  it("returns null when endpoint is whitespace only", () => {
    window.runtimeConfig = { ...window.runtimeConfig!, dataProcessingApiEndpoint: "   " };
    expect(getDataProcessingApiBase()).toBeNull();
  });
});

// ── Null-on-unconfigured ──────────────────────────────────────────────────────

describe("null-on-unconfigured — all three functions return null when endpoint is absent", () => {
  beforeEach(() => {
    window.runtimeConfig = { ...window.runtimeConfig!, dataProcessingApiEndpoint: "" };
  });

  it("fetchSignals() returns null", async () => {
    expect(await fetchSignals(okFetch())).toBeNull();
  });

  it("fetchVehicleModels() returns null", async () => {
    expect(await fetchVehicleModels(okFetch())).toBeNull();
  });

  it("fetchDecoderManifests() returns null", async () => {
    expect(await fetchDecoderManifests(okFetch())).toBeNull();
  });

  it("fetchSignals() makes no fetch call when unconfigured", async () => {
    const f = okFetch();
    await fetchSignals(f as unknown as typeof fetch);
    expect(f).not.toHaveBeenCalled();
  });

  it("fetchVehicleModels() makes no fetch call when unconfigured", async () => {
    const f = okFetch();
    await fetchVehicleModels(f as unknown as typeof fetch);
    expect(f).not.toHaveBeenCalled();
  });

  it("fetchDecoderManifests() makes no fetch call when unconfigured", async () => {
    const f = okFetch();
    await fetchDecoderManifests(f as unknown as typeof fetch);
    expect(f).not.toHaveBeenCalled();
  });
});

// ── Non-2xx throws (constraint: no catch→empty-array) ────────────────────────

describe("non-2xx response throws rather than resolving empty", () => {
  it("fetchSignals() throws on 500", async () => {
    await expect(
      fetchSignals(errorFetch(500) as unknown as typeof fetch),
    ).rejects.toThrow(/500/);
  });

  it("fetchSignals() throws on 401", async () => {
    await expect(
      fetchSignals(errorFetch(401, "Unauthorized") as unknown as typeof fetch),
    ).rejects.toThrow(/401/);
  });

  it("fetchSignals() does not resolve to empty array on error", async () => {
    // The critical assertion: error must not silently render as "no signals exist".
    const result = fetchSignals(errorFetch(503) as unknown as typeof fetch);
    await expect(result).rejects.toThrow();
  });

  it("fetchVehicleModels() throws on 500", async () => {
    await expect(
      fetchVehicleModels(errorFetch(500) as unknown as typeof fetch),
    ).rejects.toThrow(/500/);
  });

  it("fetchVehicleModels() throws on 401", async () => {
    await expect(
      fetchVehicleModels(errorFetch(401, "Unauthorized") as unknown as typeof fetch),
    ).rejects.toThrow(/401/);
  });

  it("fetchDecoderManifests() throws on 500", async () => {
    await expect(
      fetchDecoderManifests(errorFetch(500) as unknown as typeof fetch),
    ).rejects.toThrow(/500/);
  });

  it("fetchDecoderManifests() throws on 401", async () => {
    await expect(
      fetchDecoderManifests(errorFetch(401, "Unauthorized") as unknown as typeof fetch),
    ).rejects.toThrow(/401/);
  });
});

// ── Auth header: ID token, not access token ───────────────────────────────────

describe("Authorization header carries the ID token, not the access token", () => {
  const FUNCTIONS = [
    { name: "fetchSignals", invoke: (f: typeof fetch) => fetchSignals(f) },
    { name: "fetchVehicleModels", invoke: (f: typeof fetch) => fetchVehicleModels(f) },
    { name: "fetchDecoderManifests", invoke: (f: typeof fetch) => fetchDecoderManifests(f) },
  ] as const;

  for (const { name, invoke } of FUNCTIONS) {
    it(`${name}() sends Bearer <idToken>`, async () => {
      const f = okFetch();
      await invoke(f as unknown as typeof fetch);
      expect(authHeaderOf(f)).toBe(`Bearer ${ID_TOKEN}`);
    });

    it(`${name}() does not send the access token`, async () => {
      const f = okFetch();
      await invoke(f as unknown as typeof fetch);
      expect(authHeaderOf(f)).not.toContain(ACCESS_TOKEN);
    });

    it(`${name}() sends no Authorization header when only the access token is stored`, async () => {
      // Anti-regression: if the client is ever switched back to reading
      // STORAGE_KEY_ACCESS_TOKEN, a header appears here and this test fails.
      sessionStorage.removeItem(STORAGE_KEY_ID_TOKEN);
      const f = okFetch();
      await invoke(f as unknown as typeof fetch);
      expect(authHeaderOf(f)).toBeUndefined();
    });
  }
});

// ── URL routing ───────────────────────────────────────────────────────────────

describe("correct API paths are called", () => {
  it("fetchSignals() calls GET /signals", async () => {
    const f = okFetch({ signals: [], count: 0 });
    await fetchSignals(f as unknown as typeof fetch);
    expect(urlOf(f)).toBe(`${BASE}/signals`);
  });

  it("fetchVehicleModels() calls GET /model-manifests", async () => {
    const f = okFetch({ modelManifests: [], count: 0 });
    await fetchVehicleModels(f as unknown as typeof fetch);
    expect(urlOf(f)).toBe(`${BASE}/model-manifests`);
  });

  it("fetchDecoderManifests() calls GET /decoder-manifests", async () => {
    const f = okFetch({ decoderManifests: [], count: 0 });
    await fetchDecoderManifests(f as unknown as typeof fetch);
    expect(urlOf(f)).toBe(`${BASE}/decoder-manifests`);
  });

  it("fetchSignals() uses trailing-slash-trimmed base", async () => {
    window.runtimeConfig = {
      ...window.runtimeConfig!,
      dataProcessingApiEndpoint: `${BASE}/`,
    };
    const f = okFetch({ signals: [], count: 0 });
    await fetchSignals(f as unknown as typeof fetch);
    expect(urlOf(f)).toBe(`${BASE}/signals`);
  });
});

// ── Response body pass-through ────────────────────────────────────────────────

describe("response body is returned verbatim (no reshaping)", () => {
  it("fetchSignals() returns the parsed body as-is", async () => {
    const body = {
      signals: [
        {
          data_type: "DOUBLE",
          signal_group: "gps",
          signal_id: "sig-001",
          signal_name: "Vehicle.Speed",
          status: "ACTIVE",
          vss_path: "Vehicle.Speed",
        },
      ],
      count: 1,
    };
    const result = await fetchSignals(okFetch(body) as unknown as typeof fetch);
    expect(result).toEqual(body);
  });

  it("fetchVehicleModels() returns the parsed body as-is, including ecus[]", async () => {
    const body = {
      modelManifests: [
        {
          modelManifestName: "MERIDIAN-TRAILWIND",
          modelManifestVersion: "1",
          displayName: "Meridian Trailwind",
          modelLine: "Trailwind",
          platform: "EV_PLATFORM",
          productionPhase: "PRODUCTION",
          status: "ACTIVE",
          description: "Meridian Trailwind EV",
          isDefault: false,
          decoderManifestRef: "cms-fleet-v3",
          signalCount: 45,
          ecuConfigId: "ecu-config-001",
          fleetIds: [],
          ecus: [
            { ecu: "TCU", displayName: "Telematics Control Unit" },
            { ecu: "BMS", displayName: "Battery Management System" },
          ],
          pk: "MODEL#MERIDIAN-TRAILWIND",
          sk: "MANIFEST#1",
          createTimestamp: "2024-01-01T00:00:00Z",
          updateTimestamp: "2024-06-01T00:00:00Z",
        },
      ],
      count: 1,
    };
    const result = await fetchVehicleModels(okFetch(body) as unknown as typeof fetch);
    expect(result).toEqual(body);
  });

  it("fetchDecoderManifests() returns the parsed body as-is", async () => {
    const body = {
      decoderManifests: [
        {
          decoderManifestName: "cms-fleet-v4",
          decoderManifestVersion: "4",
          description: "Fleet decoder manifest v4",
          modelName: "cms-fleet-model",
          status: "ACTIVE",
          createTimestamp: "2024-01-01T00:00:00Z",
        },
      ],
      count: 1,
    };
    const result = await fetchDecoderManifests(okFetch(body) as unknown as typeof fetch);
    expect(result).toEqual(body);
  });
});

// ── credentials: omit ────────────────────────────────────────────────────────

describe("fetch options", () => {
  it("fetchSignals() uses credentials: omit", async () => {
    const f = okFetch({ signals: [], count: 0 });
    await fetchSignals(f as unknown as typeof fetch);
    const init = f.mock.calls[0][1] as RequestInit;
    expect(init?.credentials).toBe("omit");
  });

  it("fetchVehicleModels() uses credentials: omit", async () => {
    const f = okFetch({ modelManifests: [], count: 0 });
    await fetchVehicleModels(f as unknown as typeof fetch);
    const init = f.mock.calls[0][1] as RequestInit;
    expect(init?.credentials).toBe("omit");
  });

  it("fetchDecoderManifests() uses credentials: omit", async () => {
    const f = okFetch({ decoderManifests: [], count: 0 });
    await fetchDecoderManifests(f as unknown as typeof fetch);
    const init = f.mock.calls[0][1] as RequestInit;
    expect(init?.credentials).toBe("omit");
  });
});


// ── getConnectedServicesApiBase / fetchEventCatalog ───────────────────────────
//
// Spec: `.kiro/specs/2026-09-19-cs-trip-simulator-parity` T3.2.
//
// Distinct from every function above: this base reads
// `connectedServicesApiEndpoint` (CMS's own API), NOT `dataProcessingApiEndpoint`
// — see `getConnectedServicesApiBase()`'s docstring. Tests below use their own
// beforeEach override of `connectedServicesApiEndpoint` rather than the file's
// top-level fixture (which points it at a placeholder `api.example.invalid`
// unrelated to this spec's `BASE`), so a call routed to the wrong base would
// hit an unmocked URL and fail loudly rather than silently pass.

const CS_BASE = "https://cms-main-api.example.invalid";

describe("getConnectedServicesApiBase", () => {
  it("returns the endpoint trimmed of trailing slash", () => {
    window.runtimeConfig = { ...window.runtimeConfig!, connectedServicesApiEndpoint: `${CS_BASE}/` };
    expect(getConnectedServicesApiBase()).toBe(CS_BASE);
  });

  it("returns null when endpoint is empty string", () => {
    window.runtimeConfig = { ...window.runtimeConfig!, connectedServicesApiEndpoint: "" };
    expect(getConnectedServicesApiBase()).toBeNull();
  });

  it("returns null when endpoint is whitespace only", () => {
    window.runtimeConfig = { ...window.runtimeConfig!, connectedServicesApiEndpoint: "   " };
    expect(getConnectedServicesApiBase()).toBeNull();
  });

  it("is independent of dataProcessingApiEndpoint — clearing one does not affect the other", () => {
    window.runtimeConfig = {
      ...window.runtimeConfig!,
      connectedServicesApiEndpoint: CS_BASE,
      dataProcessingApiEndpoint: "",
    };
    expect(getConnectedServicesApiBase()).toBe(CS_BASE);
    expect(getDataProcessingApiBase()).toBeNull();
  });
});

describe("fetchEventCatalog", () => {
  beforeEach(() => {
    window.runtimeConfig = { ...window.runtimeConfig!, connectedServicesApiEndpoint: CS_BASE };
  });

  it("returns null when connectedServicesApiEndpoint is unconfigured", async () => {
    window.runtimeConfig = { ...window.runtimeConfig!, connectedServicesApiEndpoint: "" };
    expect(await fetchEventCatalog(okFetch())).toBeNull();
  });

  it("makes no fetch call when unconfigured", async () => {
    window.runtimeConfig = { ...window.runtimeConfig!, connectedServicesApiEndpoint: "" };
    const f = okFetch();
    await fetchEventCatalog(f as unknown as typeof fetch);
    expect(f).not.toHaveBeenCalled();
  });

  it("calls GET <connectedServicesApiEndpoint>/api/v1/event-catalog, not dataProcessingApiEndpoint's base", async () => {
    const f = okFetch({ events: [], count: 0 });
    await fetchEventCatalog(f as unknown as typeof fetch);
    expect(urlOf(f)).toBe(`${CS_BASE}/api/v1/event-catalog`);
  });

  it("uses trailing-slash-trimmed base", async () => {
    window.runtimeConfig = { ...window.runtimeConfig!, connectedServicesApiEndpoint: `${CS_BASE}/` };
    const f = okFetch({ events: [], count: 0 });
    await fetchEventCatalog(f as unknown as typeof fetch);
    expect(urlOf(f)).toBe(`${CS_BASE}/api/v1/event-catalog`);
  });

  it("sends Bearer <idToken>, not the access token", async () => {
    const f = okFetch({ events: [], count: 0 });
    await fetchEventCatalog(f as unknown as typeof fetch);
    expect(authHeaderOf(f)).toBe(`Bearer ${ID_TOKEN}`);
    expect(authHeaderOf(f)).not.toContain(ACCESS_TOKEN);
  });

  it("uses credentials: omit", async () => {
    const f = okFetch({ events: [], count: 0 });
    await fetchEventCatalog(f as unknown as typeof fetch);
    const init = f.mock.calls[0][1] as RequestInit;
    expect(init?.credentials).toBe("omit");
  });

  it("returns the parsed body as-is, including optional dtc_code/severity_hint", async () => {
    const body = {
      events: [
        {
          event_id: "hard_braking_event",
          category: "safety",
          severity: 3,
          description: "Hard braking event",
          trigger_signal: "Vehicle.Chassis.Brake.PedalPosition",
          threshold_operator: ">=",
          threshold_value: 80,
        },
        {
          event_id: "low_tire_pressure_P0520",
          category: "maintenance",
          severity: 2,
          description: "Low tire pressure",
          trigger_signal: "Vehicle.Chassis.Axle.Row1.Wheel.Left.Tire.Pressure",
          threshold_operator: "<",
          threshold_value: 28,
          dtc_code: "P0520",
          severity_hint: "P2",
        },
      ],
      count: 2,
    };
    const result = await fetchEventCatalog(okFetch(body) as unknown as typeof fetch);
    expect(result).toEqual(body);
  });

  it("throws on 500 rather than resolving to an empty catalog", async () => {
    await expect(
      fetchEventCatalog(errorFetch(500) as unknown as typeof fetch),
    ).rejects.toThrow(/500/);
  });

  it("throws on 401 rather than resolving to an empty catalog", async () => {
    await expect(
      fetchEventCatalog(errorFetch(401, "Unauthorized") as unknown as typeof fetch),
    ).rejects.toThrow(/401/);
  });

  it("sends no Authorization header when only the access token is stored", async () => {
    sessionStorage.removeItem(STORAGE_KEY_ID_TOKEN);
    const f = okFetch({ events: [], count: 0 });
    await fetchEventCatalog(f as unknown as typeof fetch);
    expect(authHeaderOf(f)).toBeUndefined();
  });
});
