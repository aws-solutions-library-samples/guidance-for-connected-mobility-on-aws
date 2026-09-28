// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Runtime config (env.ts) tests.
 *
 * Spec T3.1: "Reads runtime-config.js at boot."
 * getRuntimeConfig() throws if window.runtimeConfig is not injected.
 */

import { afterEach, describe, expect, it } from "vitest";
import { type RuntimeConfig, getRuntimeConfig } from "../env";

const VALID_CONFIG: RuntimeConfig = {
  cognitoUserPoolId: "<pool-id>",
  cognitoClientId: "testclientid00000001",
  cognitoDomain: "portal.example.invalid",
  connectedServicesApiEndpoint: "https://api.example.invalid",
  subscriptionsApiEndpoint: "https://subscriptions.example.invalid",
  simulationApiEndpoint: "https://simulation.example.invalid",
  simulationProductRuleName: "cms_staging_cs_product_meridian_ev_rule",
  dataProcessingApiEndpoint: "https://data-processing.example.invalid",
  callbackOrigin: "https://connected-services.example.invalid",
};

describe("getRuntimeConfig", () => {
  afterEach(() => {
    delete window.runtimeConfig;
  });

  it("returns the runtime config when window.runtimeConfig is set", () => {
    window.runtimeConfig = VALID_CONFIG;
    expect(getRuntimeConfig()).toEqual(VALID_CONFIG);
  });

  it("throws when window.runtimeConfig is not set", () => {
    // DMS lesson F6: missing runtime-config.js causes sign-in failure.
    // This test asserts the fail-fast behaviour.
    delete window.runtimeConfig;
    expect(() => getRuntimeConfig()).toThrow(/window\.runtimeConfig is not set/);
  });

  it("throws with a message referencing runtime-config.js", () => {
    delete window.runtimeConfig;
    expect(() => getRuntimeConfig()).toThrow(/runtime-config\.js/);
  });
});
