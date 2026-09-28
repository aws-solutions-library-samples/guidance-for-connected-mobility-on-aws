#!/usr/bin/env node
/**
 * typecheck-ratchet — keep `tsc --noEmit` errors from growing in files a commit touches.
 *
 * Why this exists
 * ---------------
 * The CMS frontend went its whole life without a type-check. `tsc --noEmit` reported
 * only 18 parse errors in one dead, unreferenced file (`src/api/mock/client.ts`), and
 * TypeScript aborts program construction on parse errors, so semantic checking never ran
 * on anything. Deleting that file revealed 1,311 semantic errors across 166 files —
 * including 634 `TS2339` "property does not exist", the exact class behind the Fleet
 * Intelligence API-contract defect. See
 * issues/2026-09-24-cms-frontend-has-no-typecheck-gate/.
 *
 * Fixing 1,311 errors is a separate effort. This script stops the count from growing in
 * the meantime, and blocks ONLY on files the commit actually touches — so pre-existing
 * mess cannot wedge unrelated work, while nobody can add new errors to a file they are
 * already editing.
 *
 * Comparison unit
 * ---------------
 * Per file, a multiset of {error code: count}. Deliberately NOT line-anchored:
 *   - moving code within a file shifts every line number and must not be a failure
 *   - but swapping a TS2322 for a TS2339 IS a real change and must be caught,
 *     which a bare per-file total would miss
 *
 * Usage
 * -----
 *   node scripts/typecheck-ratchet.mjs --write        # regenerate the baseline
 *   node scripts/typecheck-ratchet.mjs --check        # compare touched files (CI)
 *   node scripts/typecheck-ratchet.mjs --check --all  # compare every file
 *   node scripts/typecheck-ratchet.mjs --check --base origin/main
 *
 * Exit codes: 0 ok, 1 regression found, 2 usage/internal error.
 */

import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, '..');
const BASELINE = join(FRONTEND, 'typecheck-baseline.json');

const argv = process.argv.slice(2);
const has = (f) => argv.includes(f);
const valOf = (f, dflt) => {
  const i = argv.indexOf(f);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : dflt;
};

/** Run tsc and return raw stdout. tsc exits non-zero when errors exist; that is normal. */
function runTsc() {
  try {
    return execFileSync('npx', ['tsc', '--noEmit'], {
      cwd: FRONTEND,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
      maxBuffer: 64 * 1024 * 1024,
    });
  } catch (e) {
    // Errors present: tsc wrote diagnostics to stdout and exited 1/2.
    if (typeof e.stdout === 'string') return e.stdout + (e.stderr || '');
    throw e;
  }
}

/** `src/foo/bar.ts(12,34): error TS2339: ...` -> { 'src/foo/bar.ts': { TS2339: 1 } } */
function parse(raw) {
  const byFile = {};
  const re = /^(.+?)\((\d+),(\d+)\): error (TS\d+):/;
  let total = 0;
  for (const line of raw.split('\n')) {
    const m = re.exec(line.trim());
    if (!m) continue;
    const [, file, , , code] = m;
    const key = file.replace(/\\/g, '/');
    byFile[key] ??= {};
    byFile[key][code] = (byFile[key][code] || 0) + 1;
    total++;
  }
  return { byFile, total };
}

function sortDeep(byFile) {
  const out = {};
  for (const f of Object.keys(byFile).sort()) {
    out[f] = {};
    for (const c of Object.keys(byFile[f]).sort()) out[f][c] = byFile[f][c];
  }
  return out;
}

function fileTotal(codes) {
  return Object.values(codes).reduce((a, b) => a + b, 0);
}

/** Files changed vs a git base, restricted to ts/tsx under the frontend. */
function touchedFiles(base) {
  const gitRoot = execFileSync('git', ['rev-parse', '--show-toplevel'], {
    cwd: FRONTEND, encoding: 'utf8',
  }).trim();
  const relPrefix = resolve(FRONTEND).slice(gitRoot.length + 1) + '/';

  const args = base
    // Three-dot: changes on this branch since it diverged, not changes that
    // landed on the base meanwhile — otherwise every unrelated merge to main
    // makes unrelated files look "touched".
    ? ['diff', '--name-only', '--diff-filter=ACMR', `${base}...HEAD`]
    : ['diff', '--name-only', '--diff-filter=ACMR', 'HEAD'];

  let out = '';
  try {
    out = execFileSync('git', args, { cwd: FRONTEND, encoding: 'utf8', stdio: ['ignore','pipe','pipe'] });
  } catch {
    return null; // base not resolvable (shallow clone, etc.) — caller decides
  }
  if (!base) {
    // Also include untracked files when comparing against the working tree.
    try {
      out += execFileSync('git', ['ls-files', '--others', '--exclude-standard'], {
        cwd: FRONTEND, encoding: 'utf8',
      });
    } catch { /* ignore */ }
  }

  const set = new Set();
  for (const line of out.split('\n')) {
    const p = line.trim();
    if (!p || !/\.tsx?$/.test(p)) continue;
    // git reports repo-relative; the baseline keys are frontend-relative.
    if (p.startsWith(relPrefix)) set.add(p.slice(relPrefix.length));
    else if (existsSync(join(FRONTEND, p))) set.add(p);
  }
  return set;
}

function loadBaseline() {
  if (!existsSync(BASELINE)) {
    console.error(`✗ no baseline at ${BASELINE}\n  generate it with: node scripts/typecheck-ratchet.mjs --write`);
    process.exit(2);
  }
  return JSON.parse(readFileSync(BASELINE, 'utf8'));
}

// ── --write ──────────────────────────────────────────────────────────────────
if (has('--write')) {
  const { byFile, total } = parse(runTsc());
  let commit = 'unknown';
  try {
    commit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: FRONTEND, encoding: 'utf8' }).trim();
  } catch { /* ignore */ }

  const baseline = {
    _comment: 'Pre-existing tsc --noEmit errors, per file, as {code: count}. Generated by scripts/typecheck-ratchet.mjs --write. Do NOT hand-edit. This is a ratchet: the numbers may only go DOWN. See issues/2026-09-24-cms-frontend-has-no-typecheck-gate/.',
    _commitNote: 'measuredAgainstCommit is HEAD at the moment --write ran, i.e. normally the PARENT of the commit that contains this file. It cannot equal its own commit (the SHA is not known until after the commit exists). Use `git log -1 -- typecheck-baseline.json` for the authoritative "when was this last regenerated".',
    generatedAt: new Date().toISOString(),
    measuredAgainstCommit: commit,
    totalErrors: total,
    fileCount: Object.keys(byFile).length,
    files: sortDeep(byFile),
  };
  writeFileSync(BASELINE, JSON.stringify(baseline, null, 2) + '\n');
  console.log(`✓ baseline written: ${total} errors across ${Object.keys(byFile).length} files (at ${commit.slice(0, 8)})`);
  process.exit(0);
}

// ── --check ──────────────────────────────────────────────────────────────────
if (has('--check')) {
  const baseline = loadBaseline();
  const { byFile: current, total } = parse(runTsc());
  const all = has('--all');
  const base = valOf('--base', process.env.TYPECHECK_RATCHET_BASE || '');

  let scope;
  if (all) {
    scope = new Set([...Object.keys(current), ...Object.keys(baseline.files)]);
  } else {
    scope = touchedFiles(base || null);
    if (scope === null) {
      console.error(`✗ could not resolve git base '${base}' to compute touched files.`);
      console.error('  Fix the base ref, or run with --all to check every file.');
      console.error('  Refusing to pass vacuously: an unresolvable base would check nothing.');
      process.exit(2);
    }
  }

  const regressions = [];
  for (const file of [...scope].sort()) {
    const was = baseline.files[file] || {};
    const now = current[file] || {};
    const codes = new Set([...Object.keys(was), ...Object.keys(now)]);
    for (const code of [...codes].sort()) {
      const b = was[code] || 0;
      const c = now[code] || 0;
      if (c > b) regressions.push({ file, code, was: b, now: c });
    }
  }

  // Report improvements so a stale baseline is visible rather than silently generous.
  const improved = [];
  for (const file of Object.keys(baseline.files).sort()) {
    const b = fileTotal(baseline.files[file]);
    const c = fileTotal(current[file] || {});
    if (c < b) improved.push({ file, was: b, now: c });
  }

  if (regressions.length) {
    console.error(`\n✗ typecheck ratchet: ${regressions.length} new error(s) in ${new Set(regressions.map(r => r.file)).size} touched file(s)\n`);
    for (const r of regressions) {
      console.error(`  ${r.file}  ${r.code}: ${r.was} → ${r.now}`);
    }
    console.error(`\n  These files are modified by this change, so their type errors are yours.`);
    console.error(`  Fix them, or if a pre-existing error genuinely moved code, regenerate:`);
    console.error(`    node scripts/typecheck-ratchet.mjs --write   # only ever to DECREASE counts\n`);
    process.exit(1);
  }

  const scopeDesc = all ? 'all files' : `${scope.size} touched file(s)${base ? ` vs ${base}` : ''}`;
  if (!all && scope.size === 0) {
    // Not an error — a change can touch only non-frontend files, or only the CI
    // config — but say so plainly. A bare "no new errors" here would read as
    // "checked and clean" when nothing was compared at all.
    console.log(`✓ typecheck ratchet: no frontend .ts/.tsx files changed${base ? ` vs ${base}` : ''} — nothing to compare`);
    console.log(`  repo total: ${total} (baseline ${baseline.totalErrors}, taken at ${String(baseline.measuredAgainstCommit).slice(0, 8)})`);
    console.log(`  run with --all to check every file regardless of what changed`);
    process.exit(0);
  }
  console.log(`✓ typecheck ratchet: no new errors in ${scopeDesc}`);
  console.log(`  repo total: ${total} (baseline ${baseline.totalErrors}, taken at ${String(baseline.measuredAgainstCommit).slice(0, 8)})`);
  if (improved.length) {
    const delta = improved.reduce((a, i) => a + (i.was - i.now), 0);
    console.log(`  ${improved.length} file(s) improved by ${delta} error(s) — regenerate the baseline to lock the win in:`);
    for (const i of improved.slice(0, 10)) console.log(`    ${i.file}: ${i.was} → ${i.now}`);
    if (improved.length > 10) console.log(`    … and ${improved.length - 10} more`);
  }
  process.exit(0);
}

console.error('usage: typecheck-ratchet.mjs (--write | --check [--all] [--base <ref>])');
process.exit(2);
