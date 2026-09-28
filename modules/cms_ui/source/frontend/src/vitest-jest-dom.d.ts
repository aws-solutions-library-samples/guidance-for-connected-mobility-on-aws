// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// Register @testing-library/jest-dom's matcher types with vitest's Assertion
// interface, so `expect(...).toBeInTheDocument()` and siblings type-check.
//
// The runtime side is `import '@testing-library/jest-dom'` in setupTests.ts,
// which extends vitest's expect at test time. TypeScript needs a separate
// augmentation because tsconfig `include` is `src` only — setupTests.ts is not
// compiled by tsc, so its imports do not reach the type checker. This file is
// under src/ and so is picked up.
//
// See jest-dom docs "With Vitest": https://github.com/testing-library/jest-dom
import '@testing-library/jest-dom/vitest';
