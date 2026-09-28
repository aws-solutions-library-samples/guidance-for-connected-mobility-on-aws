// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/// <reference types="vitest" />

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
import viteTsconfigPaths from "vite-tsconfig-paths";

/**
 * devRuntimeConfig — serve /runtime-config.js on the dev server ONLY.
 *
 * `index.html` loads `<script src="/runtime-config.js">`, and in a deployed
 * environment that file is written by `ConnectedServicesUiStack`'s dedicated
 * no-cache BucketDeployment. Nothing produces it locally, so before T9.1 the
 * 404 was harmless — no code read `window.runtimeConfig` during boot.
 *
 * T9.1 changed that: an unauthenticated visitor now renders `SignIn`, which
 * calls `getRuntimeConfig()` to build the Hosted UI authorize URL. With the
 * file missing, `npm start` throws at boot instead of showing a sign-in screen,
 * which breaks local UI iteration — the whole point of running the dev server.
 *
 * Why a dev-server middleware rather than the two obvious alternatives:
 *
 *  - **Not a `public/runtime-config.js`.** Vite copies `public/` into `dist/`,
 *    so a placeholder would become a real artifact competing with the stack's
 *    BucketDeployment. Whether the real values or the placeholder win would
 *    depend on upload ordering — and a portal silently running on placeholder
 *    Cognito config is DMS lesson F6 exactly.
 *  - **Not a fallback inside `getRuntimeConfig()`.** That function throws by
 *    design so a missing config fails loudly at boot rather than as a confusing
 *    auth failure later, and `src/__tests__/env.test.ts` asserts the throw.
 *    Weakening it to fix local dev would trade a real production guard for
 *    developer convenience.
 *
 * This middleware exists only in `configureServer`, so it runs for `vite` /
 * `vite preview` and contributes nothing to `vite build`. The values below are
 * deliberately non-functional: sign-in can be *rendered* locally, but the
 * redirect goes nowhere, because a working local Federate round-trip would need
 * a real client id and `http://localhost:5178/auth/callback` registered on the
 * shared pool — which is not something a dev server should require.
 */
function devRuntimeConfig(): Plugin {
  const body = `// Served by vite.config.ts devRuntimeConfig() — DEV ONLY, never built into dist/.
window.runtimeConfig = {
  "cognitoUserPoolId": "<local-dev-pool-id>",
  "cognitoClientId": "<local-dev-client-id>",
  "cognitoRegion": "us-west-2",
  "cognitoDomain": "local-dev.auth.invalid",
  "apiEndpoint": "https://api.example.invalid",
  "callbackOrigin": "http://localhost:5178"
};
`;
  return {
    name: "connected-services-dev-runtime-config",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url?.split("?")[0] !== "/runtime-config.js") return next();
        res.setHeader("Content-Type", "application/javascript");
        res.setHeader("Cache-Control", "no-store");
        res.end(body);
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), viteTsconfigPaths(), devRuntimeConfig()],
  build: {
    // Must output to dist/ — deployment/stacks/connected_services_ui_stack.py
    // reads the built artifact from modules/connected_services_ui/dist/.
    outDir: "dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 4000,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./setupTests.ts",
    pool: "threads",
    coverage: {
      provider: "v8",
      reporter: ["lcov", "text"],
      exclude: [
        "**/node_modules/**",
        "**/dist/**",
        "**/.{git,tmp}/**",
        "src/index.tsx",
      ],
    },
    exclude: ["**/node_modules/**", "**/dist/**", "**/.{git,tmp}/**"],
  },
  server: {
    port: 5178,
    host: true,
    open: true,
  },
});
