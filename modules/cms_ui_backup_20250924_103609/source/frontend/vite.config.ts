// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/// <reference types="vitest" />

import react from "@vitejs/plugin-react";

import * as path from "path";

import { defineConfig } from "vite";
import viteTsconfigPaths from "vite-tsconfig-paths";
import polyfillNode from "rollup-plugin-polyfill-node";
import commonjs from "@rollup/plugin-commonjs";

export default defineConfig({
  plugins: [react(), viteTsconfigPaths()],
  build: {
    outDir: "build",
    chunkSizeWarningLimit: 4000,
    minify: 'terser',
    terserOptions: {
      keep_classnames: /Command$/,
      keep_fnames: true
    },
    rollupOptions: {
      plugins: [polyfillNode(), commonjs()],
    },
    commonjsOptions: {
      include: [/node_modules/],
      transformMixedEsModules: true
    }
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      os: "os-browserify/browser",
      process: "process/browser",
      stream: "stream-browserify",
      util: "util/",
      path: "path-browserify",
      buffer: "buffer/",
      crypto: "crypto-browserify",
      "balanced-match": "balanced-match/index.js",
    },
  },
  define: {
    global: "globalThis",
    "process.env": {},
  },
  optimizeDeps: {
    include: [
      '@cloudscape-design/components',
      '@cloudscape-design/design-tokens',
      '@cloudscape-design/global-styles',
      '@aws-sdk/client-location',
      '@aws-sdk/client-cognito-identity',
      '@aws-sdk/credential-provider-cognito-identity',
      'react',
      'react-dom',
      'react-router-dom',
      'react-oauth2-code-pkce',
      'react-is',
      'react-map-gl/maplibre',
      'maplibre-gl',
      'buffer',
      'process',
      'balanced-match',
      'prop-types'
    ],
    exclude: [
      '@cloudscape-design/board-components'
    ],
    esbuildOptions: {
      target: 'es2020'
    },
    force: true
  },
  esbuild: {
    target: 'es2020'
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./setupTests.ts",
    coverage: {
      provider: "v8",
      reporter: ["lcov", "text"],
      exclude: [
        "**/node_modules/**",
        "**/build/**",
        "**/.{git,tmp}/**",
        "**/interfaces/**",
        "src/__test__/**",
        "coverage/**",
        "test/*.js",
        "src/App.tsx",
        "src/index.tsx",
      ],
    },
    exclude: ["**/node_modules/**", "**/build/**", "**/.{git,tmp}/**"],
  },
  server: {
    port: 5177,
    host: true,
    open: true,
    fs: {
      strict: false
    }
  },
});
