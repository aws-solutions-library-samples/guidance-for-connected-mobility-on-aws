// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Runtime configuration module.
 *
 * Values are read from window.runtimeConfig, which is populated by
 * /runtime-config.js at boot time (see index.html <script src="/runtime-config.js">).
 *
 * None of these values are baked into the Vite build artefact.  A redeploy of
 * only runtime-config.js (via BucketDeployment with no-cache headers) is
 * sufficient to rotate credentials without rebuilding the bundle.
 *
 * Spec: .kiro/specs/2026-09-03-cms-connected-services-portal/spec.md
 * DMS lesson F6: missing runtime-config.js injection caused sign-in failure.
 */

export interface RuntimeConfig {
  readonly cognitoUserPoolId: string;
  readonly cognitoClientId: string;
  /** AWS region for the Cognito user pool. Derived from the pool ID in the CDK stack. */
  readonly cognitoRegion?: string;
  readonly cognitoDomain: string;
  /**
   * Connected Services API base URL.
   *
   * The key name must match what ConnectedServicesUiStack writes into
   * runtime-config.js verbatim. This field was declared as `apiEndpoint` until
   * 2026-09-06 while the stack emitted `connectedServicesApiEndpoint`, so the type
   * promised a `string` that was always `undefined` at runtime. Nothing read it, so
   * nothing broke — the fixture-backed release makes no API calls — but the lie would
   * have surfaced as an undefined-URL fetch the moment the API was wired.
   * `runtimeConfigContract.test.ts` now asserts this interface against the stack source.
   */
  readonly connectedServicesApiEndpoint: string;
  /**
   * Connected Services subscription-plane API base URL (spec
   * `2026-09-10-cms-connected-services-subscriptions` T3.5). Consumed by
   * `DataProductsView` when it fetches the real product catalog. Empty
   * string when the subscriptions stack is not deployed — the UI falls
   * back to its fixture catalog rather than making an undefined-URL fetch.
   */
  readonly subscriptionsApiEndpoint: string;
  /**
   * Simulation API base URL (spec
   * `2026-09-12-cs-simulator-oem2-manifest-path` T7.4). Consumed by the
   * simulate-vehicle view when it calls the simulation REST endpoints.
   * Empty string when the simulation stack is not deployed — the view
   * degrades honestly rather than fetching `undefined/...`.
   * Sourced from SimulationApiUrl CfnOutput (simulation_stack.py:1163).
   */
  readonly simulationApiEndpoint: string;
  /**
   * Data-processing API base URL (spec
   * `2026-09-14-cs-portal-data-model-backend` G2). Consumed by the Data
   * Model screens (Signal Catalog, ECUs, Vehicle Models, Decoder Manifests).
   * Empty string when the data-processing endpoint is not threaded — the
   * screens degrade to "not configured" rather than fetching from an
   * undefined URL. The same absent-means-disabled contract as
   * `subscriptionsApiEndpoint` (T3.5).
   *
   * The runtime-config.js key name is `dataProcessingApiEndpoint`, matching
   * the camelCase convention used by the other four endpoints. It is
   * populated by `ConnectedServicesUiStack` from CDK context key
   * `connectedServicesUiDataProcessingApiEndpoint` — T1.2 threads the full
   * chain from staging.env through the Makefile and CDK to here.
   */
  readonly dataProcessingApiEndpoint: string;
  /**
   * Fully-qualified Kafka rule name for the Meridian EV product simulation
   * (spec `2026-09-12-cs-simulator-oem2-manifest-path` T7.4a).
   *
   * Emitted by ConnectedServicesUiStack as
   * `cms_{stage}_cs_product_meridian_ev_rule`, where `stage` is the deployment
   * stage (e.g. `cms_staging_cs_product_meridian_ev_rule`).
   *
   * The simulate-vehicle client sends this as `rule_name` in the POST /start
   * body so the simulation Lambda publishes to the OEM2 manifest-path topic
   * rather than the CMS-native default. An empty value signals "do not send
   * rule_name" — the Lambda applies the CMS-native default rather than
   * receiving an empty string.
   */
  readonly simulationProductRuleName: string;
  readonly callbackOrigin: string;
}

declare global {
  interface Window {
    runtimeConfig?: RuntimeConfig;
  }
}

/**
 * Return the runtime configuration or throw if it has not been injected.
 *
 * Throws rather than returning a partial config so that a missing
 * runtime-config.js produces a clear error at initialisation time rather than
 * a silent auth failure later (DMS lesson F6).
 */
export function getRuntimeConfig(): RuntimeConfig {
  const cfg = window.runtimeConfig;
  if (!cfg) {
    throw new Error(
      "window.runtimeConfig is not set. " +
        "Ensure /runtime-config.js is deployed and loaded before the app bundle. " +
        "See index.html <script src=\"/runtime-config.js\"> and " +
        "ConnectedServicesUiStack BucketDeployment."
    );
  }
  return cfg;
}
