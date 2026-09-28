// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CMS fetchInterceptor — auth-stamping properties.
 *
 * Spec: `.kiro/specs/2026-09-15-cms-cs-campaign-ownership/tasks.md` FG10.1, FG11.1, FG11.2
 *
 * ## What these tests guard
 *
 * 1. simulationApiEndpoint is in the covered list → requests get an Auth header.
 * 2. commandsApiEndpoint is in the covered list → requests get an Auth header.
 * 3. The other known bases are also covered.
 * 4. An already-present Authorization header is not overwritten (no-clobber).
 * 5. An uncovered URL (e.g. a third-party host) receives no Authorization.
 * 6. Every *Endpoint key emitted by ui_stack.py (inside the dict literal AND via
 *    conditional runtime_config["key"] = … assignments) has a declared auth mechanism
 *    (FG11.1 non-circular guard — derives from stack source, not from this file).
 * 7. A superstring host that begins with a covered base origin does NOT receive
 *    Authorization — origin/path boundary test, not a bare prefix test (FG11.2).
 * 8. Relative and malformed URLs do not throw and are not stamped (FG11.2).
 *
 * ## Mutation coverage
 *
 * (a) remove `simulationApiEndpoint` from `authedBases` key list
 *     → the simulation-stamp test FAILS (FG10.1 mutation).
 *     Verified in decisions.md, FG10.1 section.
 * (b) remove `commandsApiEndpoint` from `authedBases` key list
 *     → the commands-stamp test FAILS (FG11.1 mutation (b)).
 *     Verified in decisions.md, FG11.1 section.
 * (c) add a new *ApiEndpoint to the ui_stack.py runtime_config dict with no
 *     AUTH_MECHANISM entry → the guard in section 6 FAILS (FG11.1 mutation (a)).
 *     Verified in decisions.md, FG11.1 section.
 * (d) change commandsApiEndpoint classification to "explicit-client" while the
 *     interceptor stamps it → the direction-check test in section 6 FAILS
 *     (FG11.1 mutation (d)). Verified in decisions.md, FG11.1 section.
 * (e) delete one classification entry for a base still present in the stack
 *     → the "every emitted key is classified" test FAILS (FG11.1 mutation (c)).
 *     Verified in decisions.md, FG11.1 section.
 * (f) revert matchesBase to url.startsWith(base) → the superstring-host tests FAIL
 *     (FG11.2 mutation (a)). Verified in decisions.md.
 * (g) add a conditional runtime_config["newSvcApiEndpoint"] = … to ui_stack.py
 *     after the dict with no AUTH_MECHANISM entry → the guard in section 7 FAILS
 *     (FG12 mutation (i)). Verified in decisions.md.
 * (h) add a single-quoted 'newSvcApiEndpoint': inside the dict with no AUTH_MECHANISM
 *     → the guard in section 7 FAILS (FG12 mutation (ii)). Verified in decisions.md.
 * (i) remove block-end marker (total parse failure) → corpus-sanity fails loudly
 *     (FG12 mutation (iii)). Verified in decisions.md.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installCmsFetchInterceptor } from "../fetchInterceptor";

// ── Constants ─────────────────────────────────────────────────────────────────

const API_ENDPOINT = "https://api.example.invalid";
const DATA_PROCESSING_ENDPOINT = "https://data-processing.example.invalid";
const VSA_ENDPOINT = "https://vsa.example.invalid";
const SIM_ENDPOINT = "https://simulation.example.invalid";
const CMD_ENDPOINT = "https://commands.example.invalid";
const OTHER_URL = "https://third-party.example.invalid/resource";
const ID_TOKEN = "ID.TOKEN.cms-test";
const EXPLICIT_TOKEN = "Bearer EXPLICIT.TOKEN.cms-test";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeMockFetch() {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({}),
      headers: new Headers(),
    } as unknown as Response;
  });
}

function getAuthHeaderFromCall(
  mock: ReturnType<typeof makeMockFetch>,
  callIndex = 0,
): string | null {
  const init = mock.mock.calls[callIndex]?.[1] as RequestInit | undefined;
  if (!init?.headers) return null;
  if (init.headers instanceof Headers) return init.headers.get("Authorization");
  return (init.headers as Record<string, string>)["Authorization"] ?? null;
}

// ── Setup / teardown ──────────────────────────────────────────────────────────

const savedFetch = window.fetch;

beforeEach(() => {
  window.runtimeConfig = {
    apiEndpoint: API_ENDPOINT,
    dataProcessingApiEndpoint: DATA_PROCESSING_ENDPOINT,
    vsaApiEndpoint: VSA_ENDPOINT,
    simulationApiEndpoint: SIM_ENDPOINT,
    commandsApiEndpoint: CMD_ENDPOINT,
  } as any;
  sessionStorage.clear();
  localStorage.clear();
  sessionStorage.setItem("idToken", ID_TOKEN);
});

afterEach(() => {
  window.fetch = savedFetch;
  delete window.runtimeConfig;
  sessionStorage.clear();
  localStorage.clear();
  vi.restoreAllMocks();
});

// ── 1. simulationApiEndpoint is covered ───────────────────────────────────────
// Mutation (a): remove "simulationApiEndpoint" from the authedBases key list
//   → simulation requests get no Authorization header → FAIL.

describe("CMS fetchInterceptor — simulationApiEndpoint is now auth-stamped", () => {
  it("adds Authorization to a request to simulationApiEndpoint", async () => {
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(`${SIM_ENDPOINT}/api/simulation/agent/logs/TESTVIN`);
    expect(getAuthHeaderFromCall(mock)).toBe(ID_TOKEN);
  });

  it("adds Authorization to a request to simulationApiEndpoint agent/status route", async () => {
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(`${SIM_ENDPOINT}/api/simulation/agent/status`);
    expect(getAuthHeaderFromCall(mock)).toBe(ID_TOKEN);
  });
});

// ── 2. commandsApiEndpoint is covered ────────────────────────────────────────
// Mutation (b): remove "commandsApiEndpoint" from the authedBases key list
//   → commands requests get no Authorization header → FAIL.
//
// commandsApiEndpoint: 7 of 7 routes COGNITO_USER_POOLS on live API 9xpiwl2269,
// observed 401 unauthenticated 2026-09-15. See
// issues/2026-09-15-cms-commands-api-401-bare-fetches/.
//
// No-double-stamp assertion: sovdScanClient.ts calls authFetch() which sets
// Authorization: Bearer <token> BEFORE the interceptor sees the request.
// The interceptor's has("Authorization") guard means it does NOT overwrite —
// sovdScanClient.ts calls remain stamped exactly once (by authFetch).

describe("CMS fetchInterceptor — commandsApiEndpoint is now auth-stamped", () => {
  it("adds Authorization to a request to commandsApiEndpoint", async () => {
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(`${CMD_ENDPOINT}/api/commands/catalog`);
    expect(getAuthHeaderFromCall(mock)).toBe(ID_TOKEN);
  });

  it("adds Authorization to a POST to commandsApiEndpoint (vehicle command)", async () => {
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(`${CMD_ENDPOINT}/api/commands/VH-0001`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command_type: "lock" }),
    });
    expect(getAuthHeaderFromCall(mock)).toBe(ID_TOKEN);
  });

  it("does NOT overwrite an explicit Authorization already set by authFetch (no-double-stamp)", async () => {
    // sovdScanClient.ts reaches commandsApiEndpoint via authFetch(), which sets
    // Authorization: Bearer <token> before the interceptor runs.
    // The interceptor must honour the no-clobber rule and leave that header intact.
    const ALREADY_STAMPED = "Bearer ALREADY.STAMPED.BY.AUTHFETCH";
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(`${CMD_ENDPOINT}/api/commands/VH-0001`, {
      method: "POST",
      headers: { Authorization: ALREADY_STAMPED },
    });
    expect(getAuthHeaderFromCall(mock)).toBe(ALREADY_STAMPED);
  });
});

// ── 3. Other covered bases ────────────────────────────────────────────────────

describe("CMS fetchInterceptor — all configured bases are auth-stamped", () => {
  it("stamps apiEndpoint requests", async () => {
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(`${API_ENDPOINT}/api/vehicles`);
    expect(getAuthHeaderFromCall(mock)).toBe(ID_TOKEN);
  });

  it("stamps dataProcessingApiEndpoint requests", async () => {
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(`${DATA_PROCESSING_ENDPOINT}/api/campaigns`);
    expect(getAuthHeaderFromCall(mock)).toBe(ID_TOKEN);
  });

  it("stamps vsaApiEndpoint requests", async () => {
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(`${VSA_ENDPOINT}/api/vsa`);
    expect(getAuthHeaderFromCall(mock)).toBe(ID_TOKEN);
  });
});

// ── 4. No-clobber ─────────────────────────────────────────────────────────────

describe("CMS fetchInterceptor — explicit Authorization header is NOT overwritten", () => {
  it("does not replace an already-present Authorization header", async () => {
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(`${SIM_ENDPOINT}/api/simulation/start`, {
      method: "POST",
      headers: { Authorization: EXPLICIT_TOKEN },
    });
    expect(getAuthHeaderFromCall(mock)).toBe(EXPLICIT_TOKEN);
  });
});

// ── 5. Non-covered URLs are not stamped ───────────────────────────────────────

describe("CMS fetchInterceptor — non-covered URLs receive no Authorization", () => {
  it("does NOT add Authorization to a third-party URL", async () => {
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(OTHER_URL);
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });

  it("does NOT stamp when simulationApiEndpoint is empty string", async () => {
    window.runtimeConfig = {
      ...(window.runtimeConfig as any),
      simulationApiEndpoint: "",
    };
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(`${SIM_ENDPOINT}/api/simulation/agent/status`);
    // With empty endpoint, this URL is NOT in authedBases — no stamp.
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });
});

// ── 6. Origin/path boundary — superstring host and malformed URLs (FG11.2) ────
//
// FG11.2 finding: url.startsWith(base) is a string-prefix test, not an origin
// test. `"https://simulation.example.invalid".startsWith("https://simulation.example.invalid")`
// is true for `"https://simulation.example.invalid.attacker.test/steal"`, so an
// attacker host that begins with the configured base would receive the ID token.
//
// matchesBase() in the interceptor parses both URLs and compares origins
// structurally. These tests assert that property.
//
// Mutation (f) / FG11.2(a): revert matchesBase to url.startsWith(base) →
//   the superstring-host tests FAIL. Verified in decisions.md.
//
// Note: not reachable today — every URL at these call sites is built from
// runtimeConfig plus a literal path, so no attacker-controlled URL flows in.

describe("CMS fetchInterceptor — superstring host receives no Authorization (FG11.2)", () => {
  it("does NOT stamp a URL whose host begins with the simulation base host", async () => {
    // A bare startsWith check would stamp this. The origin/path-boundary check must not.
    const attackerUrl = `${SIM_ENDPOINT}.attacker.test/steal`;
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(attackerUrl);
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });

  it("does NOT stamp a URL whose host begins with the commands base host", async () => {
    const attackerUrl = `${CMD_ENDPOINT}.attacker.test/steal`;
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await window.fetch(attackerUrl);
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });
});

describe("CMS fetchInterceptor — malformed and relative URLs do not throw and are not stamped (FG11.2)", () => {
  it("does NOT throw and does NOT stamp a relative URL", async () => {
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await expect(window.fetch("/relative/path")).resolves.toBeDefined();
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });

  it("does NOT throw and does NOT stamp a malformed URL", async () => {
    const mock = makeMockFetch();
    installCmsFetchInterceptor(mock);
    await expect(window.fetch("not-a-url")).resolves.toBeDefined();
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });
});

// ── 7. Guard: every CMS API base emitted by ui_stack.py has a declared auth mechanism ──
//
// FG11.1 CRITICAL fix: derive the *Endpoint key list from ui_stack.py's runtime_config
// dict (the authoritative source of what is actually delivered at runtime), not from a
// literal in this file. An object literal that only iterates its own keys is circular:
// adding a new endpoint to the stack leaves the guard green even though the new base
// has no declared auth mechanism.
//
// FG12 (cycle-3 Warning 1): the original slice covered only the dict literal; keys
// added via conditional `runtime_config["key"] = …` assignments (the idiom dmsUiUrl
// uses) and single-quoted dict keys both escaped undetected. parseCmsStackEndpointKeys
// now covers both idioms (see its docstring).
//
// Direction that matters: stack emission → AUTH_MECHANISM (not the reverse).
// Mutation (c) / FG11.1(a): adding a new *Endpoint to the runtime_config dict without
//   an AUTH_MECHANISM entry causes this test to FAIL.
// Mutation (d) / FG11.1(d): marking commandsApiEndpoint as "explicit-client" while the
//   interceptor actually stamps it → the direction-check test FAILS.
// Mutation (e) / FG11.1(c): deleting one classification entry for a key still in the
//   dict → the "every emitted key is classified" test FAILS.
//
// Parse failure is fail-closed: if the regex stops matching the stack source, the
// corpus-sanity test fails loudly. That is the vacuity failure named in testing.md
// that this spec has shipped twice.

const UI_STACK_PY = resolve(
  __dirname,
  "../../../../../../../deployment/stacks/ui_stack.py",
);

/**
 * Extract *Endpoint keys from ui_stack.py's runtime_config dict and
 * conditional runtime_config["key"] = … assignments.
 *
 * Two emission idioms are covered:
 *
 * 1. Keys inside the dict literal (both quote styles):
 *      "commandsApiEndpoint": commands_endpoint,
 *      'newSvcApiEndpoint': new_svc,
 *
 * 2. Conditional assignments anywhere in the source (both quote styles):
 *      runtime_config["dmsUiUrl"] = _dms_ui_url
 *      runtime_config['newSvcApiEndpoint'] = _new_svc
 *
 * Only idiom 2 keys that end in "Endpoint" are included; non-Endpoint
 * conditional additions (e.g. "dmsUiUrl") are ignored.
 *
 * Only keys in the runtime_config = { ... } block are matched for idiom 1;
 * the block ends at the first "runtime_config[" occurrence.
 *
 * Fail-closed: returns an empty array only if no lines match, which the
 * corpus-sanity test turns into a loud failure.
 */
function parseCmsStackEndpointKeys(source: string): string[] {
  const keys = new Set<string>();

  // Idiom 1: keys inside the dict literal (single or double quotes)
  const blockStart = source.indexOf("runtime_config = {");
  const blockEnd = source.indexOf("runtime_config[");
  if (blockStart !== -1 && blockEnd !== -1) {
    const block = source.slice(blockStart, blockEnd);
    const dictKeyRe = /["']([A-Za-z_$][\w$]*Endpoint)["']:/g;
    let m: RegExpExecArray | null;
    while ((m = dictKeyRe.exec(block)) !== null) {
      keys.add(m[1]);
    }
  }

  // Idiom 2: conditional runtime_config["key"] = … or runtime_config['key'] = …
  // assignments anywhere in the source (only *Endpoint keys)
  const assignRe = /runtime_config\[["']([A-Za-z_$][\w$]*Endpoint)["']\]\s*=/g;
  let m2: RegExpExecArray | null;
  while ((m2 = assignRe.exec(source)) !== null) {
    keys.add(m2[1]);
  }

  return Array.from(keys);
}

const uiStackSource = readFileSync(UI_STACK_PY, "utf8");
const cmsStackEndpointKeys = parseCmsStackEndpointKeys(uiStackSource);

/**
 * Auth mechanism classifications for every *Endpoint key the CMS ui_stack.py emits.
 *
 * Classification reasons (per-base, not a boolean):
 *   interceptor        — this module stamps it (url.startsWith check in authedBases)
 *   explicit-client    — a named utility or client attaches Authorization explicitly
 *   not-http           — not an HTTP/HTTPS endpoint (e.g. WebSocket, raw protocol)
 *   alias-of:<key>     — delivers the same runtime URL value as another named key
 *
 * Source verification for COGNITO_USER_POOLS claims:
 *   aws apigateway get-resources --rest-api-id <id> --region us-west-2 --embed methods
 *
 * See decisions.md, FG11.1 section for the per-base evidence.
 */
const AUTH_MECHANISM: Record<string, { mechanism: string; reason: string }> = {
  apiEndpoint: {
    mechanism: "interceptor",
    reason:
      "Main CMS Fleet API, Cognito user pool authorizer. Stamped via authedBases loop.",
  },
  wsEndpoint: {
    mechanism: "not-http",
    reason:
      "WebSocket upgrade (`wss://`). The browser's WebSocket handshake uses " +
      "the Sec-WebSocket-Protocol header, not Authorization. fetch() is not used " +
      "for the WebSocket connection; this URL never flows through this interceptor.",
  },
  userPreferencesApiEndpoint: {
    mechanism: "alias-of:apiEndpoint",
    reason:
      "Emitted as `self.api.url` in ui_stack.py — the same value as apiEndpoint. " +
      "Requests to this base are stamped because apiEndpoint is in authedBases; " +
      "the alias expands to the same URL and the startsWith check fires.",
  },
  dataProcessingApiEndpoint: {
    mechanism: "interceptor",
    reason:
      "Signal Catalog + Campaigns API, Cognito authorizer. Stamped via authedBases loop.",
  },
  simulationApiEndpoint: {
    mechanism: "interceptor",
    reason:
      "All routes are COGNITO_USER_POOLS (confirmed against live API tekm196qb5, " +
      "observed 401 unauthenticated 2026-09-15). Stamped via authedBases loop.",
  },
  commandsApiEndpoint: {
    mechanism: "interceptor",
    reason:
      "7 of 7 routes are COGNITO_USER_POOLS on live API 9xpiwl2269 (observed 401 " +
      "unauthenticated 2026-09-15). Stamped via authedBases loop. " +
      "sovdScanClient.ts uses authFetch() which sets Authorization before the " +
      "interceptor runs; the no-clobber guard prevents double-stamping.",
  },
};

describe("CMS fetchInterceptor — every ui_stack.py *Endpoint key (dict and conditional assignments) has a declared auth mechanism", () => {
  // Corpus sanity: fail loudly if the regex stopped matching.
  it("parsed a non-trivial number of *Endpoint keys from ui_stack.py (fail-closed guard)", () => {
    expect(
      cmsStackEndpointKeys.length,
      `parseCmsStackEndpointKeys returned 0 keys from ${UI_STACK_PY} — ` +
        "the regex no longer matches the ui_stack.py runtime_config dict. " +
        "Fix the regex in this file before the corpus-derived assertions are meaningful.",
    ).toBeGreaterThanOrEqual(4);
  });

  it("every stack-emitted *Endpoint key is classified in AUTH_MECHANISM", () => {
    // Mutation (c) / FG11.1(a): adding a new endpoint to the stack dict without
    // an AUTH_MECHANISM entry causes this test to FAIL.
    const unclassified = cmsStackEndpointKeys.filter((key) => !(key in AUTH_MECHANISM));
    expect(
      unclassified,
      `ui_stack.py emits ${JSON.stringify(unclassified)} but they have no AUTH_MECHANISM entry. ` +
        "Add a classification with a reason for each.",
    ).toEqual([]);
  });

  it("every AUTH_MECHANISM key is actually emitted by the stack (no phantom entries)", () => {
    // Mutation (e) / FG11.1(c): deleting one entry while the key is still in the dict
    // causes the "every emitted key is classified" test to FAIL, not this one.
    // This test catches the reverse: stale entries for removed stack keys.
    const phantom = Object.keys(AUTH_MECHANISM).filter(
      (key) => !cmsStackEndpointKeys.includes(key),
    );
    expect(
      phantom,
      `AUTH_MECHANISM contains ${JSON.stringify(phantom)} which are not emitted by ui_stack.py. ` +
        "Remove or rename the stale entries.",
    ).toEqual([]);
  });

  // Mutation (d) / FG11.1(d): marking commandsApiEndpoint as "explicit-client"
  // while the interceptor actually stamps it → FAILS here.
  it("commandsApiEndpoint is classified as 'interceptor' (not missed, not mis-classified)", () => {
    expect(AUTH_MECHANISM.commandsApiEndpoint?.mechanism).toBe("interceptor");
  });

  it("simulationApiEndpoint is classified as 'interceptor'", () => {
    expect(AUTH_MECHANISM.simulationApiEndpoint?.mechanism).toBe("interceptor");
  });

  it("every classification has a non-empty reason", () => {
    for (const [key, val] of Object.entries(AUTH_MECHANISM)) {
      expect(
        val.reason.length,
        `AUTH_MECHANISM["${key}"].reason is empty — state why this mechanism applies.`,
      ).toBeGreaterThan(0);
    }
  });
});


// ── FWELogViewer must rely on the interceptor, not its own headers ────────────
//
// 2026-09-24. The CMS copy of FWELogViewer used to pass
// `{ headers: { ...getAuthHeaders() } }` on its log fetch. That was removed, because
// `useAuth.getAuthHeaders()` returns `Authorization: Bearer <idToken>` while this
// interceptor sets the RAW token, and the interceptor will not overwrite an
// Authorization header that is already present. The result was that FWELogViewer was
// the ONLY one of the CMS simulation call sites sending the Bearer form; every other
// one sent the raw token. Both authenticate, but the divergence is the kind that
// rots: the next person to touch auth has two mechanisms to reason about on one
// route.
//
// Removing it also restored byte-parity with the connected_services_ui copy, which
// `scripts/verify_cs_log_viewer_parity.py` guards — that guard had been red for five
// days precisely because of this divergence. See
// issues/2026-09-24-blocking-guards-gate-only-the-public-mirror/.
//
// Why this test exists when the parity guard already exists: the parity guard
// compares the two copies against EACH OTHER. If someone re-added explicit auth
// headers to BOTH copies in the same change, parity would still pass and the
// Bearer-vs-raw inconsistency would silently return. This asserts the property
// directly, per copy, so it holds regardless of what the other copy does.
//
// The stamping half — that the interceptor really does cover this exact URL — is
// asserted by "adds Authorization to a request to simulationApiEndpoint" above,
// which fetches `${SIM_ENDPOINT}/api/simulation/agent/logs/TESTVIN` and expects the
// raw ID_TOKEN. The two tests together cover the whole path.

// Paths are resolved against the vitest working directory (the frontend package
// root) rather than `__dirname`, deliberately: `__dirname` is not typed without
// @types/node, which this package does not configure — the three existing uses in
// this file are the repo's only TS2304 errors here, and adding more would trip the
// typecheck ratchet (scripts/typecheck-ratchet.mjs). The "exists and is readable"
// test below is what makes this safe: a wrong working directory fails loudly there
// instead of silently asserting against an empty string.
const FWE_LOG_VIEWER_COPIES: ReadonlyArray<readonly [string, string]> = [
  [
    "cms_ui",
    resolve("src/components/vehicles/vehicle-detail/FWELogViewer.tsx"),
  ],
  [
    "connected_services_ui",
    resolve(
      "../../../connected_services_ui/src/components/screens/connectivity/FWELogViewer.tsx",
    ),
  ],
];

describe("FWELogViewer delegates auth to the interceptor", () => {
  it.each(FWE_LOG_VIEWER_COPIES)(
    "%s copy exists and is readable (anti-vacuity for the assertions below)",
    (_label, path) => {
      // Without this, a moved or renamed file would make every assertion below
      // pass against an empty string.
      const src = readFileSync(path, "utf8");
      expect(src.length).toBeGreaterThan(500);
      expect(src).toContain("/api/simulation/agent/logs/");
    },
  );

  it.each(FWE_LOG_VIEWER_COPIES)(
    "%s copy sets no Authorization header of its own",
    (_label, path) => {
      const src = readFileSync(path, "utf8");
      expect(src).not.toMatch(/Authorization/i);
    },
  );

  it.each(FWE_LOG_VIEWER_COPIES)(
    "%s copy does not call getAuthHeaders()",
    (_label, path) => {
      const src = readFileSync(path, "utf8");
      expect(src).not.toContain("getAuthHeaders");
    },
  );

  it.each(FWE_LOG_VIEWER_COPIES)(
    "%s copy does not import useAuth",
    (_label, path) => {
      const src = readFileSync(path, "utf8");
      expect(src).not.toMatch(/from\s+["'][^"']*useAuth["']/);
    },
  );
});
