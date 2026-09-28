"""Per-repo scan_exclude baseline for the publish-safety ratchet test.

THIS FILE IS DATA, NOT CODE.

  - It is deliberately NOT vendored from ~/.kiro/publish-toolkit/ and is therefore
    EXEMPT from the toolkit drift check (verify-sync.sh).
  - It is consumed by the vendored canonical test
    scripts/lib/test_publish_scan_exclude_invariant.py via _consumer_baseline().
  - Entries are a RATCHET: they should only ever be REMOVED as files get cleaned up
    or publish-excluded. Never add an entry without justification -- doing so silently
    widens the surface of published-but-unscanned content.

Each entry below is a scan_exclude pattern that shadows at least one TEXT file that
ships to the public mirror. The content of those files is verified clean by the
invariant test above this ratchet; the ratchet's job is to ensure the set cannot
grow without a human decision recorded here.

Shrunk 2026-08-04 (shrink-residual task) from 46 entries to 3:

  REMOVED (41 dead exemptions): 33 OEM1-connector-test entries whose scan_exclude
  was revoked with the OEM1 brand pattern deletion; .gitignore and docs/DEPLOYMENT.md
  (content scrubbed clean, dropped from scan_exclude); CODE_OF_CONDUCT.md and
  CONTRIBUTING.md (scan_exclude already revoked, content was already clean);
  .publish-exclude and .publish-secrets-scan.yml (now publish-excluded, no longer
  ship); scripts/lib/test_secret_scan.py and **/test_secret_scan.py (publish-excluded,
  no longer ship); scripts/ios-monitor.py and scripts/rename-oem1-protos.py (load-
  bearing literals in those files, now publish-excluded so the unscanned-shipped
  hole is closed -- the combination scan-excluded AND publish-excluded is the SAFE
  half of the two-mechanism rule).

  REMAINING (3 entries whose files still ship and whose content is clean):
    - **/.yarn/**  : yarn binary (*.cjs) -- excluded for encoding/size, content clean
    - **/tests/fixtures/**  : binary proto fixtures -- excluded for encoding, clean
    - scripts/lib/secret-scan.py  : scanner self-reference; ships to public mirror;
      content clean post toolkit sync (a test canary in the prior canonical was removed upstream)
"""

BASELINE: set = {
    # build / binary noise -- excluded for encoding/size reasons, content clean
    "**/.yarn/**",
    "**/tests/fixtures/**",
    # scanner self-reference -- ships to public mirror; content clean post sync
    "scripts/lib/secret-scan.py",
    # AWS Solutions Library boilerplate -- re-added 2026-08-10.
    # Both files legitimately contain the standard AWS Open Source contact
    # address (opensource-codeofconduct[at]amazon[dot]com, obfuscated here so
    # this comment does not itself trip the internal_email scanner regex —
    # see issues/2026-09-08-publish-scanner-3-findings-post-push/ for the
    # "denylist written in terms of the secret" meta-pattern this fixed).
    # The scanner pattern is `\b[a-zA-Z0-9._-]+@amazon\.com\b` at severity
    # warning, which fires on the un-obfuscated form. LICENSE and NOTICE MUST
    # ship (public repos require them), so publish-exclude is not an option;
    # the only compliant path is scan-exclude + baseline entry with an explicit
    # justification here (this file). Sibling CVX repo has the same entries with
    # the same rationale ("per CMS scanner precedent") -- restoring the precedent
    # this repo lost during the 2026-08-04 shrink, when the internal_email pattern
    # was either not yet defined or not yet CI-fatal.
    #
    # Content narrow-check: the only forbidden-adjacent content in either file is
    # the one email at line 7 (CoC) / line 15 (Contributing), which is intentional.
    # If a future edit adds an @amazon.com address or any other pattern-matching
    # content to these files, the scan_exclude blinds the scanner to it -- so a
    # PR review touching either file must eyeball the diff.
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
}
