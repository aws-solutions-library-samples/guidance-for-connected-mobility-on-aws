// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Red-phase unit tests for scripts/substitute-gate-template.js
//
// Run with:
//   cd modules/cms_ui/source/frontend && node --test scripts/tests/substitute-gate-template.test.js
//
// All tests are expected to FAIL in red phase (the script does not yet exist).
// node:test + node:assert only — no additional test framework.

import { describe, it, before, after, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, cpSync, rmSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Path to the script under test (relative to frontend root)
const FRONTEND_ROOT = join(__dirname, '..', '..');
const SCRIPT_PATH = join(FRONTEND_ROOT, 'scripts', 'substitute-gate-template.js');

// Real placeholder values from source
const PLACEHOLDER_BINDLE = '4nh4dosbuejyi3ljt6oa';
const PLACEHOLDER_ARN = 'arn:aws:secretsmanager:us-west-2:123456789012:secret:CFSSigningKey-cms-staging-IUBiWx';

// Test env var values
const TEST_BINDLE_ID = 'amzn1.bindle.resource.test-bindle-id-value-001';
const TEST_SECRET_ARN = 'arn:aws:secretsmanager:us-west-2:123456789012:secret:TestCFSSigningKey-ABCDEF';

// --- Fixtures ---

// Standard fixture: has both #bindlediv and #secretArn with placeholder content
const FIXTURE_HTML_STANDARD = `<!DOCTYPE html>
<html>
<head>
    <script src="/js/cfs-handler.js"></script>
</head>
<body>
<p>Redirecting you for Auth</p>
<!-- This one should be a Bindle that doesn't exist, and triggers a 400 response from ECS. -->
<div style="display:none" id="bindlediv">amzn1.bindle.resource.${PLACEHOLDER_BINDLE}</div>
<div style="display:none" id="secretArn">${PLACEHOLDER_ARN}</div>
</body>
</html>
`;

// Fixture with non-placeholder content in the divs (for case 8 — match on id, not literal)
const FIXTURE_HTML_NON_PLACEHOLDER = `<!DOCTYPE html>
<html>
<head>
    <script src="/js/cfs-handler.js"></script>
</head>
<body>
<p>Redirecting you for Auth</p>
<div style="display:none" id="bindlediv">some-completely-different-bindle-value</div>
<div style="display:none" id="secretArn">arn:aws:secretsmanager:us-west-2:111111111111:secret:SomeOtherKey</div>
</body>
</html>
`;

// Fixture with NO #bindlediv or #secretArn (for case 7 — no element matched)
const FIXTURE_HTML_NO_ELEMENTS = `<!DOCTYPE html>
<html>
<head>
    <script src="/js/cfs-handler.js"></script>
</head>
<body>
<p>Redirecting you for Auth</p>
<!-- divs removed intentionally -->
</body>
</html>
`;

// --- Helper ---

/**
 * Run the substitution script as a child process.
 *
 * Asserts the script file exists before invoking — so all 8 tests fail with an
 * AssertionError in red phase rather than some accidentally passing because the
 * missing-module exit code (1) coincidentally satisfies a non-zero assertion.
 *
 * @param {object} opts
 * @param {string} opts.buildDir  - path used as the CWD (script resolves build/ relative to cwd)
 * @param {object} opts.env       - env vars to pass (merged on top of a clean base)
 * @returns {{ status: number|null, stdout: string, stderr: string }}
 */
function runScript({ buildDir, env = {} }) {
  // Fail with an AssertionError if the script doesn't exist yet (red phase guard).
  assert.ok(
    existsSync(SCRIPT_PATH),
    `Script under test does not exist: ${SCRIPT_PATH}\n` +
    `All tests will fail until Group 2 creates the script.`
  );

  const result = spawnSync(
    process.execPath, // node
    [SCRIPT_PATH],
    {
      cwd: buildDir,
      env: {
        PATH: process.env.PATH,
        ...env,
      },
      encoding: 'utf8',
      timeout: 10_000,
    }
  );
  return {
    status: result.status,
    stdout: result.stdout ?? '',
    stderr: result.stderr ?? '',
    error: result.error,
  };
}

/**
 * Set up a temporary directory tree for a test case.
 * Creates:
 *   <tmp>/build/error/403.html   (from buildHtml)
 *   <tmp>/public/error/403.html  (always the standard fixture — never modified by script)
 *
 * Returns { tmpDir, buildFile, publicFile }.
 */
function setupTmpDirs(buildHtml = FIXTURE_HTML_STANDARD) {
  const tmpDir = mkdtempSync(join(tmpdir(), 'cms-gate-test-'));
  const buildErrorDir = join(tmpDir, 'build', 'error');
  const publicErrorDir = join(tmpDir, 'public', 'error');
  mkdirSync(buildErrorDir, { recursive: true });
  mkdirSync(publicErrorDir, { recursive: true });
  const buildFile = join(buildErrorDir, '403.html');
  const publicFile = join(publicErrorDir, '403.html');
  writeFileSync(buildFile, buildHtml, 'utf8');
  writeFileSync(publicFile, FIXTURE_HTML_STANDARD, 'utf8'); // public is always the original
  return { tmpDir, buildFile, publicFile };
}

function teardownTmpDir(tmpDir) {
  if (tmpDir && existsSync(tmpDir)) {
    rmSync(tmpDir, { recursive: true, force: true });
  }
}

// ============================================================
// Test cases
// ============================================================

describe('substitute-gate-template.js', () => {

  // Case 1
  it('substitutes #bindlediv and #secretArn when both env vars are set', () => {
    const { tmpDir, buildFile } = setupTmpDirs();
    try {
      const result = runScript({
        buildDir: tmpDir,
        env: {
          CMS_GATE_BINDLE_ID: TEST_BINDLE_ID,
          CMS_GATE_SECRET_ARN: TEST_SECRET_ARN,
        },
      });

      // Script must exit 0
      assert.equal(result.status, 0, `Expected exit 0, got ${result.status}. stderr: ${result.stderr}`);

      const content = readFileSync(buildFile, 'utf8');

      // Placeholder string must be absent
      assert.ok(
        !content.includes(PLACEHOLDER_BINDLE),
        `Placeholder bindleId "${PLACEHOLDER_BINDLE}" should be absent after substitution`
      );

      // Test bindle id must be present
      assert.ok(
        content.includes(TEST_BINDLE_ID),
        `Expected bindleId "${TEST_BINDLE_ID}" to be present in output`
      );

      // Test secret ARN must be present
      assert.ok(
        content.includes(TEST_SECRET_ARN),
        `Expected secretArn "${TEST_SECRET_ARN}" to be present in output`
      );
    } finally {
      teardownTmpDir(tmpDir);
    }
  });

  // Case 2
  it('leaves placeholder unchanged and exits 0 when CMS_GATE_BINDLE_ID is unset', () => {
    const { tmpDir, buildFile } = setupTmpDirs();
    const originalContent = readFileSync(buildFile, 'utf8');
    try {
      const result = runScript({
        buildDir: tmpDir,
        env: {
          // CMS_GATE_BINDLE_ID deliberately absent
          CMS_GATE_SECRET_ARN: TEST_SECRET_ARN,
        },
      });

      // Must exit 0 (fail-open)
      assert.equal(result.status, 0, `Expected exit 0, got ${result.status}. stderr: ${result.stderr}`);

      // File must be unchanged
      const content = readFileSync(buildFile, 'utf8');
      assert.equal(content, originalContent, 'Build file should be unchanged when BINDLE_ID is unset');

      // Must emit a warning
      const output = result.stdout + result.stderr;
      assert.ok(
        output.toLowerCase().includes('warning') || output.includes('⚠'),
        `Expected a warning in output when BINDLE_ID is unset. Got: ${output}`
      );
    } finally {
      teardownTmpDir(tmpDir);
    }
  });

  // Case 3
  it('leaves placeholder unchanged and exits 0 when CMS_GATE_SECRET_ARN is unset', () => {
    const { tmpDir, buildFile } = setupTmpDirs();
    const originalContent = readFileSync(buildFile, 'utf8');
    try {
      const result = runScript({
        buildDir: tmpDir,
        env: {
          CMS_GATE_BINDLE_ID: TEST_BINDLE_ID,
          // CMS_GATE_SECRET_ARN deliberately absent
        },
      });

      // Must exit 0 (fail-open)
      assert.equal(result.status, 0, `Expected exit 0, got ${result.status}. stderr: ${result.stderr}`);

      // File must be unchanged
      const content = readFileSync(buildFile, 'utf8');
      assert.equal(content, originalContent, 'Build file should be unchanged when SECRET_ARN is unset');

      // Must emit a warning
      const output = result.stdout + result.stderr;
      assert.ok(
        output.toLowerCase().includes('warning') || output.includes('⚠'),
        `Expected a warning in output when SECRET_ARN is unset. Got: ${output}`
      );
    } finally {
      teardownTmpDir(tmpDir);
    }
  });

  // Case 4
  it('leaves placeholder unchanged and exits 0 when both env vars are unset', () => {
    const { tmpDir, buildFile } = setupTmpDirs();
    const originalContent = readFileSync(buildFile, 'utf8');
    try {
      const result = runScript({
        buildDir: tmpDir,
        env: {
          // Both deliberately absent
        },
      });

      // Must exit 0 (fail-open)
      assert.equal(result.status, 0, `Expected exit 0, got ${result.status}. stderr: ${result.stderr}`);

      // File must be unchanged
      const content = readFileSync(buildFile, 'utf8');
      assert.equal(content, originalContent, 'Build file should be unchanged when both vars are unset');

      // Must emit a warning
      const output = result.stdout + result.stderr;
      assert.ok(
        output.toLowerCase().includes('warning') || output.includes('⚠'),
        `Expected a warning in output when both vars are unset. Got: ${output}`
      );
    } finally {
      teardownTmpDir(tmpDir);
    }
  });

  // Case 5
  it('is idempotent: running twice produces the same output', () => {
    const { tmpDir, buildFile } = setupTmpDirs();
    try {
      const env = {
        CMS_GATE_BINDLE_ID: TEST_BINDLE_ID,
        CMS_GATE_SECRET_ARN: TEST_SECRET_ARN,
      };

      // First run
      const result1 = runScript({ buildDir: tmpDir, env });
      assert.equal(result1.status, 0, `First run: expected exit 0, got ${result1.status}. stderr: ${result1.stderr}`);
      const contentAfterFirst = readFileSync(buildFile, 'utf8');

      // Second run on the already-substituted file
      const result2 = runScript({ buildDir: tmpDir, env });
      assert.equal(result2.status, 0, `Second run: expected exit 0, got ${result2.status}. stderr: ${result2.stderr}`);
      const contentAfterSecond = readFileSync(buildFile, 'utf8');

      // Output must be identical
      assert.equal(
        contentAfterSecond,
        contentAfterFirst,
        'File content after second run should equal content after first run (idempotent)'
      );
    } finally {
      teardownTmpDir(tmpDir);
    }
  });

  // Case 6
  it('never writes to public/', () => {
    const { tmpDir, buildFile, publicFile } = setupTmpDirs();
    const publicContentBefore = readFileSync(publicFile, 'utf8');
    try {
      const result = runScript({
        buildDir: tmpDir,
        env: {
          CMS_GATE_BINDLE_ID: TEST_BINDLE_ID,
          CMS_GATE_SECRET_ARN: TEST_SECRET_ARN,
        },
      });

      assert.equal(result.status, 0, `Expected exit 0, got ${result.status}. stderr: ${result.stderr}`);

      // public/error/403.html must be byte-identical to the original
      const publicContentAfter = readFileSync(publicFile, 'utf8');
      assert.equal(
        publicContentAfter,
        publicContentBefore,
        'public/error/403.html must be unchanged — script must only write to build/'
      );
    } finally {
      teardownTmpDir(tmpDir);
    }
  });

  // Case 7
  it('fails (exits non-zero) when both vars are set but no element matched', () => {
    // Fixture has neither #bindlediv nor #secretArn
    const { tmpDir } = setupTmpDirs(FIXTURE_HTML_NO_ELEMENTS);
    try {
      const result = runScript({
        buildDir: tmpDir,
        env: {
          CMS_GATE_BINDLE_ID: TEST_BINDLE_ID,
          CMS_GATE_SECRET_ARN: TEST_SECRET_ARN,
        },
      });

      // Must exit non-zero (spec R5 — post-substitution assertion)
      assert.notEqual(
        result.status,
        0,
        `Expected non-zero exit when no elements matched, but got exit ${result.status}`
      );
    } finally {
      teardownTmpDir(tmpDir);
    }
  });

  // Case 8
  it('matches on element id, not placeholder literal', () => {
    // Fixture has #bindlediv and #secretArn but with different content (not the placeholder string)
    const { tmpDir, buildFile } = setupTmpDirs(FIXTURE_HTML_NON_PLACEHOLDER);
    try {
      const result = runScript({
        buildDir: tmpDir,
        env: {
          CMS_GATE_BINDLE_ID: TEST_BINDLE_ID,
          CMS_GATE_SECRET_ARN: TEST_SECRET_ARN,
        },
      });

      // Must exit 0 — the script matches on id, not on placeholder literal
      assert.equal(result.status, 0, `Expected exit 0, got ${result.status}. stderr: ${result.stderr}`);

      const content = readFileSync(buildFile, 'utf8');

      // Test bindle id must be present
      assert.ok(
        content.includes(TEST_BINDLE_ID),
        `Expected bindleId "${TEST_BINDLE_ID}" to be present after substitution by id`
      );

      // Test secret ARN must be present
      assert.ok(
        content.includes(TEST_SECRET_ARN),
        `Expected secretArn "${TEST_SECRET_ARN}" to be present after substitution by id`
      );
    } finally {
      teardownTmpDir(tmpDir);
    }
  });

});
