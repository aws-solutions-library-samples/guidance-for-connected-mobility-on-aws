// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Built-bundle hygiene for the Connected Services card (spec
 * `2026-09-10-cms-connected-services-consumer`, T2.4's Verify clause).
 *
 * This test greps the **emitted bundle**, not the source. The distinction is
 * the whole point: a source-level assertion cannot see a value that arrives
 * through a `define`, a `.env` file, an inlined JSON import, a transitive
 * dependency, or a `runtimeConfig` key someone adds later. Only the artifact
 * that ships can answer "does the browser have this string".
 *
 * ── Two rules this file follows, both learned expensively in this portfolio ──
 *
 * 1. **A denylist written in terms of the secret embeds the secret.**
 *    `~/.kiro/steering/public-mirror-publish.md` records four exposures in two
 *    days from exactly that, including a `.gitignore` and a scan config that
 *    each published the value they existed to guard. So this file contains NO
 *    account id, NO API Gateway host id, and NO credential — it matches on
 *    SHAPES and on non-secret infixes (`connected-services-subscriber`) that
 *    identify the class of value without carrying an instance of it.
 *
 * 2. **An absence assertion passes vacuously against an empty file.** A failed
 *    build, a stale `build/`, or a glob that matched nothing would all satisfy
 *    every "must not contain" check here. So POSITIVE CONTROLS run first: the
 *    bundle must contain the card's own route path and copy. If those are
 *    missing the artifact is not the thing under test, and the suite says so
 *    rather than reporting a clean bill of health for a file it never read.
 */

import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { beforeAll, describe, expect, it } from 'vitest';

const FRONTEND_ROOT = path.resolve(__dirname, '..', '..');
const BUILD_DIR = path.join(FRONTEND_ROOT, 'build');
const SRC_DIR = path.join(FRONTEND_ROOT, 'src');

/** Extensions that actually reach the browser. `.map` included deliberately —
 *  a sourcemap that ships carries the original source text verbatim, so a
 *  credential absent from the minified chunk can still be present in its map. */
const SHIPPED_EXTENSIONS = ['.js', '.mjs', '.cjs', '.css', '.html', '.json', '.map'];

interface ShippedAsset {
  relPath: string;
  text: string;
}

let assets: ShippedAsset[] = [];
let corpus = '';

function walk(dir: string, onFile: (abs: string) => void): void {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const abs = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules') continue;
      walk(abs, onFile);
    } else if (entry.isFile()) {
      onFile(abs);
    }
  }
}

function newestMtimeUnder(dir: string, extensions?: string[]): number {
  let newest = 0;
  if (!fs.existsSync(dir)) return newest;
  walk(dir, (abs) => {
    if (extensions && !extensions.includes(path.extname(abs))) return;
    const mtime = fs.statSync(abs).mtimeMs;
    if (mtime > newest) newest = mtime;
  });
  return newest;
}

/**
 * Ensure `build/` exists and reflects the sources this guard is about.
 *
 * Two staleness signals, because one is not enough:
 *
 *   - a CONTENT STAMP over the files this spec adds. mtime comparison alone is
 *     defeated by an mtime moving BACKWARDS, which is not hypothetical: the
 *     mutation harness restores with `shutil.copy2`, which preserves the
 *     original mtime, so after a run the sources looked older than a `build/`
 *     produced from a MUTATED tree. The guard would then have scanned the
 *     mutant and reported on it. (The positive controls caught it — that is
 *     what they are for — but a guard should not need its own alarm to notice
 *     it read the wrong file.)
 *   - a broad mtime sweep over all of `src/`, to catch edits anywhere else that
 *     could pull a leak in transitively.
 *
 * Builds on demand rather than skipping when the artifact is absent. A skipped
 * guard reports green, which for a credential-leak check is worse than no check
 * at all — and `vite build` measures ~19s on this app, so on-demand is
 * affordable. `stdio: 'pipe'` keeps a 200-line build log out of the test
 * output; the log is surfaced only if the build fails.
 */
/**
 * Where the freshness stamp lives.
 *
 * **Outside `build/`, deliberately.** `build/` IS the deploy artifact —
 * `make ui-quick-deploy` runs `aws s3 sync build/ s3://<FrontendBucket>/
 * --delete` with no dotfile exclusion. An earlier draft wrote the stamp into
 * `build/.cs-bundle-guard-stamp`, which the normal deploy path happens not to
 * ship (because `yarn build` runs `pre-build-cleanup.js`, which deletes `build/`
 * first, and the deploy never runs tests) — but "happens not to" is a property of
 * the current target ordering, not a guarantee. Run the test suite after a build
 * and before a sync and a test artifact lands in a production bucket.
 *
 * `node_modules/.cache/` is gitignored, is never synced, and survives across
 * test runs, which is all the stamp needs.
 */
const STAMP_FILE = path.join(
  FRONTEND_ROOT,
  'node_modules',
  '.cache',
  'cs-bundle-guard-stamp',
);

/** Files whose content the guard's verdict depends on directly. */
const STAMPED_SOURCES = [
  path.join(SRC_DIR, 'api', 'connectedServices.ts'),
  path.join(SRC_DIR, 'components', 'vehicles', 'vehicle-detail', 'ConnectedServicesCard.tsx'),
  path.join(SRC_DIR, 'components', 'vehicles', 'vehicle-detail', 'VehicleDetailView.tsx'),
];

function currentStamp(): string {
  const parts: string[] = [];
  for (const abs of STAMPED_SOURCES) {
    parts.push(`${path.relative(FRONTEND_ROOT, abs)}:${
      fs.existsSync(abs) ? createHash('sha256').update(fs.readFileSync(abs)).digest('hex') : 'ABSENT'
    }`);
  }
  const publicDir = path.join(FRONTEND_ROOT, 'public');
  if (fs.existsSync(publicDir)) {
    for (const name of fs.readdirSync(publicDir).sort()) {
      if (!name.startsWith('runtimeConfig')) continue;
      parts.push(`public/${name}:${
        createHash('sha256').update(fs.readFileSync(path.join(publicDir, name))).digest('hex')
      }`);
    }
  }
  return createHash('sha256').update(parts.join('\n')).digest('hex');
}

function ensureFreshBuild(): void {
  const stamp = currentStamp();
  const stampMatches =
    fs.existsSync(STAMP_FILE) && fs.readFileSync(STAMP_FILE, 'utf8').trim() === stamp;
  const builtAt = newestMtimeUnder(BUILD_DIR);
  const sourcedAt = newestMtimeUnder(SRC_DIR);
  if (stampMatches && builtAt > 0 && builtAt >= sourcedAt) return;

  try {
    execFileSync('npx', ['vite', 'build'], {
      cwd: FRONTEND_ROOT,
      stdio: 'pipe',
      timeout: 600_000,
      encoding: 'utf8',
    });
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; message?: string };
    throw new Error(
      'Could not produce a bundle to scan. The hygiene assertions below are '
        + 'meaningless without one, so this fails rather than skipping.\n'
        + `${e.stderr ?? ''}\n${e.stdout ?? ''}\n${e.message ?? ''}`,
    );
  }
  // Written AFTER the build, and recomputed rather than reused, so a source
  // edited mid-build stamps as itself and not as its pre-edit state.
  fs.mkdirSync(path.dirname(STAMP_FILE), { recursive: true });
  fs.writeFileSync(STAMP_FILE, currentStamp());
}

beforeAll(() => {
  ensureFreshBuild();

  assets = [];
  walk(BUILD_DIR, (abs) => {
    if (!SHIPPED_EXTENSIONS.includes(path.extname(abs))) return;
    assets.push({
      relPath: path.relative(BUILD_DIR, abs),
      text: fs.readFileSync(abs, 'utf8'),
    });
  });
  corpus = assets.map((a) => a.text).join('\n');
}, 600_000);

/** Report WHICH asset matched — "the bundle contains X" is not actionable. */
function assetsMatching(pattern: RegExp | string): string[] {
  return assets
    .filter((a) =>
      typeof pattern === 'string'
        ? a.text.includes(pattern)
        : new RegExp(pattern.source, pattern.flags.replace('g', '')).test(a.text),
    )
    .map((a) => a.relPath);
}

describe('positive controls — the artifact under test is the real one', () => {
  it('emitted at least one JavaScript chunk', () => {
    const js = assets.filter((a) => a.relPath.endsWith('.js'));
    expect(js.length).toBeGreaterThan(0);
    expect(js.reduce((n, a) => n + a.text.length, 0)).toBeGreaterThan(100_000);
  });

  it("contains CMS's own Connected Services route path", () => {
    // If this string is absent, either the card was tree-shaken out or the
    // build is stale — and every absence assertion below would then pass
    // without having examined the code it claims to clear.
    expect(assetsMatching('api/v1/connected-services/subscription-feed').length)
      .toBeGreaterThan(0);
  });

  it("contains the card's own not-enrolled copy", () => {
    // A second, independent control. The route path could survive as a dead
    // string constant; this one only ships if the component itself did.
    expect(assetsMatching('Not enrolled in Connected Services feed').length)
      .toBeGreaterThan(0);
  });
});

describe('no producer endpoint reaches the browser', () => {
  it('bakes no API Gateway host into any code chunk', () => {
    // Scoped to CODE (js/css/html) on purpose. `runtimeConfig*.json` is
    // EXCLUDED because carrying CMS's own endpoint is precisely that file's
    // job — it is generated per deployment and read by `getRuntimeConfig()` at
    // load time. The next test governs it instead.
    //
    // Scoping this narrowly was not the first attempt: the whole-`build/`
    // version failed against the real artifact on three local runtimeConfig
    // files, which is the difference between a guard verified behaviourally
    // and one reasoned about from source.
    const codeAssets = assets.filter(
      (a) => !path.basename(a.relPath).startsWith('runtimeConfig'),
    );
    const found = codeAssets
      .filter((a) => /[a-z0-9]{8,12}\.execute-api\.[a-z0-9-]+\.amazonaws\.com/.test(a.text))
      .map((a) => a.relPath);
    expect(found, `API Gateway host baked into code: ${found.join(', ')}`).toEqual([]);
  });

  it('exposes no Connected Services producer key through runtime config', () => {
    // The one way a producer endpoint could legitimately-looking reach the
    // browser: someone adds a `runtimeConfig` key for it, the way
    // `vsaApiEndpoint` and `dataProcessingApiEndpoint` exist. D5 forbids that —
    // the browser has no business holding a producer address, because the
    // credential that makes it useful is server-side only.
    const configAssets = assets.filter((a) =>
      path.basename(a.relPath).startsWith('runtimeConfig'),
    );
    for (const asset of configAssets) {
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(asset.text) as Record<string, unknown>;
      } catch {
        continue;
      }
      const offending = Object.keys(parsed).filter((k) =>
        /connectedservices|subscriber|^cs[A-Z]|producer/i.test(k),
      );
      expect(
        offending,
        `${asset.relPath} exposes Connected Services key(s): ${offending.join(', ')}`,
      ).toEqual([]);
    }
  });

  it('carries none of the server-side Connected Services configuration names', () => {
    // These are the Lambda's env vars and the CDK context keys. Any of them in
    // a browser asset means server configuration crossed into the client build.
    for (const name of [
      'CS_PRODUCER_API_ENDPOINT',
      'CS_SUBSCRIBER_SECRET_NAME',
      'CS_SUBSCRIPTION_ID',
      'csProducerApiEndpoint',
      'csSubscriptionId',
      'csSubscriberSecretName',
    ]) {
      const found = assetsMatching(name);
      expect(found, `${name} found in: ${found.join(', ')}`).toEqual([]);
    }
  });

  it("does not address the producer's scope-mutation path directly", () => {
    // The producer's own contract is `POST /subscriptions/{id}/scope` and
    // `DELETE /subscriptions/{id}/scope/{vin}`. CMS's routes are
    // `.../subscription-feed[/{vin}]` and never name `scope`, so this pattern
    // separates "calls CMS" from "calls the producer" even if a future edit
    // reused the client. A bare `/subscriptions/` would NOT work here: the
    // bundle legitimately contains CMS's own `api/v1/subscriptions/` route and
    // an unrelated `/subscriptions/list` call.
    const found = assetsMatching(/subscriptions\/[^"'`\s]{1,64}\/scope/);
    expect(found, `producer scope path found in: ${found.join(', ')}`).toEqual([]);
  });
});

describe("no subscriber credential reaches the browser", () => {
  it("carries no reference to the subscriber secret's name", () => {
    // The non-secret infix of `cms-{stage}-connected-services-subscriber-
    // {region}-{account}`. Matching the infix rather than the full name is
    // deliberate: the full name embeds the account id, and writing it here
    // would publish it in the file whose job is to keep it out.
    const found = assetsMatching('connected-services-subscriber');
    expect(found, `subscriber secret name in: ${found.join(', ')}`).toEqual([]);
  });

  it('pulls in no Secrets Manager client or read call', () => {
    // A frontend that can call GetSecretValue is a frontend that can read the
    // subscriber credential, whether or not today's code does.
    for (const marker of ['client-secrets-manager', 'GetSecretValue', 'SecretString']) {
      const found = assetsMatching(marker);
      expect(found, `${marker} found in: ${found.join(', ')}`).toEqual([]);
    }
  });

  it('contains no private key block or long-lived AWS access key id', () => {
    const pem = assetsMatching(/-----BEGIN [A-Z ]*PRIVATE KEY-----/);
    expect(pem, `private key material in: ${pem.join(', ')}`).toEqual([]);
    const keyIds = assetsMatching(/\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/);
    expect(keyIds, `AWS access key id in: ${keyIds.join(', ')}`).toEqual([]);
  });

  it('adds no second client-side credential exchange', () => {
    // Deliberately a SOURCE-level structural guard, not a bundle grep, and the
    // reason is worth recording: CMS legitimately performs a client-side
    // `USER_PASSWORD_AUTH` InitiateAuth against its OWN Cognito pool for demo
    // quick-login (`src/auth/SimpleAuthProvider.tsx`), and the bundled AWS SDK
    // ships the flow name in a constant table regardless. So the bundle-level
    // form of this check is a permanent false positive — verified, not assumed:
    // it failed against the real artifact on `SimpleAuthProvider`'s own call.
    //
    // What IS this spec's business is that no SECOND exchange appears. The
    // subscriber credential's exchange (`CognitoUserPasswordTokenProvider`)
    // lives in `connected_services_proxy.py`, server-side. A new browser-side
    // `InitiateAuthCommand` would be the shape of that moving client-side, and
    // this fails on it by name before anyone has to notice it in review.
    const callSites: string[] = [];
    walk(SRC_DIR, (abs) => {
      if (!['.ts', '.tsx'].includes(path.extname(abs))) return;
      if (abs.includes('__tests__') || abs.endsWith('.test.ts') || abs.endsWith('.test.tsx')) return;
      if (fs.readFileSync(abs, 'utf8').includes('InitiateAuthCommand')) {
        callSites.push(path.relative(SRC_DIR, abs));
      }
    });
    expect(callSites.sort()).toEqual([path.join('auth', 'SimpleAuthProvider.tsx')]);
  });

  it('the Connected Services client reaches for no credential of its own', () => {
    // The positive statement of the rule above, on the two modules this spec
    // adds: they hold no token, no secret name, no producer address. Everything
    // they can do, they do with the caller's existing CMS session via
    // `authFetch`.
    for (const rel of [
      path.join('api', 'connectedServices.ts'),
      path.join('components', 'vehicles', 'vehicle-detail', 'ConnectedServicesCard.tsx'),
    ]) {
      const text = fs.readFileSync(path.join(SRC_DIR, rel), 'utf8');
      for (const forbidden of [
        'InitiateAuth',
        'SecretsManager',
        'GetSecretValue',
        'USER_PASSWORD_AUTH',
        'csProducerApiEndpoint',
      ]) {
        expect(text.includes(forbidden), `${rel} references ${forbidden}`).toBe(false);
      }
      // And it addresses CMS's endpoint helper, not an endpoint of its own.
      if (rel.endsWith('connectedServices.ts')) {
        expect(text).toContain('getApiEndpoint');
      }
    }
  });
});
