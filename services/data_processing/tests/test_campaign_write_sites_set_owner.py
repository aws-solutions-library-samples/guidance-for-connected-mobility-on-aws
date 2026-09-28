"""Structural guard: every campaign-table write site must set ``owner``.

This test walks the repository's non-test, non-backup Python sources and finds
every ``put_item`` / ``update_item`` call that targets the campaigns table.
A write "targets the campaigns table" if EITHER:

  Signal A — the handle name itself contains the word ``campaign``
             (e.g. ``campaigns_table``, ``_campaigns_tbl_write``, ``campaigns``).

  Signal B — the handle is bound to a ``Table(...)`` call in the same file
             whose argument or assignment block contains "campaign".
             Catches short aliases like ``camp_table`` and ``table`` when
             re-bound to a campaigns ``Table()`` in the same scope.

Each detected write must either:

  (a) have ``'owner'`` or ``"owner"`` appearing within the preceding
      ``_OWNER_CONTEXT_LINES`` lines (the Item dict, UpdateExpression, or
      ExpressionAttributeNames / ExpressionAttributeValues block), or
  (b) be a recognised safe ``update_item`` call: an ``update_item`` carrying
      ``ConditionExpression`` with ``attribute_exists(campaignId)`` cannot
      create a new row, so it has no ``owner`` obligation.  This is a real
      property of the call, not an allowlist exception, or
  (c) appear in ``_ALLOWLIST`` with a written reason.

An ``_ALLOWLIST`` entry without a ``reason`` string is a test failure —
an allowlist without reasons becomes a silent bypass, which is the exact
failure mode this guard exists to prevent.

Context-window sizing: the farthest known good site is
``data_processing_api.py:1234`` where ``'owner'`` appears 17 lines before
the ``put_item`` call (inside the item dict built at L1207–1218).  We use 25
lines as a safety margin.  The window is bounded above at the previous
function/method definition (``def `` or ``async def `` at **any** indentation)
so an ``owner`` assignment in a prior function or method cannot bleed into the
check for the current one.

FIX GROUP 4, TASK 4.4 — load-bearing guard added 2026-09-15.
FIX GROUP 5, TASK 5.1 — boundary fixed for indented methods 2026-09-15.
"""
import pathlib
import re
from typing import Iterator

import pytest

# ---------------------------------------------------------------------------
# Repository root — resolved relative to this file's location so the guard
# works regardless of cwd.
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.parent

# ---------------------------------------------------------------------------
# Allowlist: sites explicitly exempt from the "must set owner" check.
#
# Each entry MUST carry a non-empty ``reason`` string.
# An allowlist without reasons is a silent bypass — the failure mode this
# guard exists to prevent.  The test will fail at collection time if any
# entry lacks a reason.
# ---------------------------------------------------------------------------
_ALLOWLIST: dict[str, str] = {
    # ── Entire directories ──────────────────────────────────────────────────
    # modules/campaign_manager is not wired into the CDK application and is
    # therefore never a live write path.  Established in spec.md § "Already built".
    "modules/campaign_manager": (
        "Not wired into the CDK application — never runs in deployed infra."
    ),
    # deployment/scripts/backups are frozen copies of earlier script versions.
    # They are not executed; scanning them would produce phantom findings.
    "deployment/scripts/backups": (
        "Frozen backup copies — not executed in any deploy or CI path."
    ),
    # ── Individual files ────────────────────────────────────────────────────
    # The backfill script's update_item IS the remediation that adds ``owner``
    # to rows written before this spec.  Flagging it inverts the guard's purpose.
    "deployment/scripts/backfill_campaign_owner.py": (
        "This script's update_item is the owner-backfill itself — it adds"
        " 'owner' to rows that predate this spec.  Flagging it would invert"
        " the purpose of the guard."
    ),
}


# ---------------------------------------------------------------------------
# Source-file filtering
# ---------------------------------------------------------------------------

def _reason_is_valid(reason: str) -> bool:
    return isinstance(reason, str) and bool(reason.strip())


def _is_allowlisted(rel_path: str) -> bool:
    normalised = rel_path.replace("\\", "/")
    for prefix in _ALLOWLIST:
        if normalised.startswith(prefix.replace("\\", "/")):
            return True
    return False


def _is_excluded_source(path: pathlib.Path) -> bool:
    rel = str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
    # Skip test files.
    if "/tests/" in rel or rel.startswith("tests/"):
        return True
    if "_test.py" in rel or rel.endswith("_test.py"):
        return True
    if "test_" in pathlib.Path(rel).name:
        return True
    # Skip generated CDK output and ECR build staging directories.
    # These contain copies of source files — the canonical sources are under
    # services/ and deployment/scripts/.
    if "/cdk.out" in rel:
        return True
    if "/.build/" in rel:
        return True
    if "deployment/ecr/" in rel:
        return True
    # Skip virtual environments.
    if "/.venv/" in rel or "/venv/" in rel or "/site-packages/" in rel:
        return True
    # Skip this guard file itself.
    if "test_campaign_write_sites_set_owner.py" in rel:
        return True
    return False


def _source_files() -> Iterator[pathlib.Path]:
    for path in _REPO_ROOT.rglob("*.py"):
        if not _is_excluded_source(path):
            rel = str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
            if not _is_allowlisted(rel):
                yield path


# ---------------------------------------------------------------------------
# Phase 1 — Signal B: enumerate per-file handles bound to campaign Table().
#
# For each file, finds every assignment of the form:
#
#     <varname> = <expr>.Table(...)
#
# where the Table(...) call's argument block (possibly multi-line) mentions
# "campaign".  Returns {varname: [assign_lineno, ...]} for that file.
#
# This catches aliases like ``camp_table``, ``table`` (when re-bound to the
# campaigns table mid-function), and ``ct`` that don't contain "campaign" in
# their own name.
# ---------------------------------------------------------------------------

def _campaign_table_handles_by_binding(
    lines: list[str],
) -> dict[str, list[int]]:
    """Return {varname: [lineno, ...]} for Table() bindings to campaigns tables."""
    handles: dict[str, list[int]] = {}
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        # Must be an assignment AND contain a .Table( call.
        if ".Table(" not in stripped:
            i += 1
            continue
        m = re.match(r"^(\w+)\s*=", stripped)
        if not m:
            i += 1
            continue
        varname = m.group(1)
        # Collect the full assignment block (balance parentheses).
        depth = stripped.count("(") - stripped.count(")")
        j = i + 1
        while depth > 0 and j < len(lines):
            depth += lines[j].count("(") - lines[j].count(")")
            j += 1
        block_text = "\n".join(lines[i:j]).lower()
        if "campaign" in block_text:
            handles.setdefault(varname, []).append(i + 1)  # 1-based
        i += 1
    return handles


# ---------------------------------------------------------------------------
# Core write-site detection
# ---------------------------------------------------------------------------

_WRITE_OPS = ("put_item", "update_item")

# Lines to look back from the write call when checking for 'owner'.
# Must be >= 17 (farthest known-good distance in data_processing_api.py:1234).
_OWNER_CONTEXT_LINES = 25

_OWNER_RE = re.compile(r"""['"]owner['"]""")


def _effective_lookback_start(lines: list[str], call_lineno: int) -> int:
    """Return the start index (0-based) for the owner-context window.

    We stop at the most recent function or method definition (``def `` or
    ``async def `` at **any** indentation) so that an ``owner`` assignment in
    a sibling or preceding function/method cannot satisfy the check for the
    current function's write call.

    Using ``lstrip()`` rather than a column-0-only ``startswith`` ensures
    that class methods (indented ``def``) stop the walk just as top-level
    functions do.  The original column-0-only check would silently bleed
    backwards across method boundaries, allowing an ``owner`` token in a
    sibling method to satisfy the check for an entirely different method.
    """
    raw_start = max(0, call_lineno - 1 - _OWNER_CONTEXT_LINES)
    # Walk backwards from the line before the call to find a function boundary.
    for idx in range(call_lineno - 2, raw_start - 1, -1):
        stripped = lines[idx].lstrip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            return idx + 1  # Include the def line's *body*, not the def itself.
    return raw_start


def _strip_line_comment(line: str) -> str:
    """Return the code portion of a Python line with the comment stripped.

    Finds the first ``#`` character that is not inside a single- or
    double-quoted string literal and returns everything before it.
    This prevents comment text from satisfying the ``owner`` check — a
    comment containing ``'owner'`` must never count as code that sets the
    field.

    Only single-line string literals (no triple-quote spanning) are handled,
    which is sufficient for the guard's lookback window where multi-line
    string literals do not appear.
    """
    in_single = False  # inside '...'
    in_double = False  # inside "..."
    prev = ""
    for i, ch in enumerate(line):
        if ch == "'" and not in_double and prev != "\\":
            in_single = not in_single
        elif ch == '"' and not in_single and prev != "\\":
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
        prev = ch
    return line


def _call_sets_owner(lines: list[str], call_lineno: int) -> bool:
    """Return True if 'owner' appears in the *code* of the context window.

    Comment text is stripped from each line before the search so that a
    comment containing ``'owner'`` cannot satisfy the check.  Only real code
    counts — a comment is never a substitute for the field being set.
    """
    start = _effective_lookback_start(lines, call_lineno)
    code_lines = [_strip_line_comment(l) for l in lines[start : call_lineno]]
    snippet = "\n".join(code_lines)  # up to but not including the call
    return bool(_OWNER_RE.search(snippet))


# Recognise an update_item that cannot create a row.
# An update_item carrying ConditionExpression with attribute_exists(campaignId)
# will raise ConditionalCheckFailedException rather than upsert when the item
# is absent, so it can never produce a row without a pre-existing owner field.
# This is a real property of the call, not an allowlist exception.
_CONDITION_EXISTS_RE = re.compile(
    r"ConditionExpression\s*=.*attribute_exists\s*\(\s*campaignId\s*\)",
    re.DOTALL,
)


def _is_upsert_safe(lines: list[str], call_lineno: int) -> bool:
    """Return True if an update_item call cannot upsert (create a new row).

    An update_item is upsert-safe if its call block contains a
    ``ConditionExpression`` that uses ``attribute_exists(campaignId)``.
    Such a call will raise ConditionalCheckFailedException rather than create
    a new item when the key is absent, so there is no way to produce an
    ownerless row.

    This recognition is a property of the call, not an allowlist exception.
    Burying it in the allowlist would hide the reasoning.
    """
    # Collect the call's argument block by balancing parentheses.
    # call_lineno is 1-based; the opening paren may be on that line or not.
    start_idx = call_lineno - 1  # 0-based index of the line with .update_item(
    depth = lines[start_idx].count("(") - lines[start_idx].count(")")
    end_idx = start_idx + 1
    while depth > 0 and end_idx < len(lines):
        depth += lines[end_idx].count("(") - lines[end_idx].count(")")
        end_idx += 1
    block = "\n".join(lines[start_idx:end_idx])
    return bool(_CONDITION_EXISTS_RE.search(block))


def _find_gaps_in_file(
    path: pathlib.Path,
) -> list[tuple[str, int, str]]:
    """Return (rel_path, lineno, op) for every uncovered campaign write in path."""
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    lines = src.splitlines()
    rel_path = str(path.relative_to(_REPO_ROOT)).replace("\\", "/")

    # Signal B: handles bound via Table() to a campaigns table in this file.
    bound_handles = _campaign_table_handles_by_binding(lines)

    gaps: list[tuple[str, int, str]] = []

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        for op in _WRITE_OPS:
            if f".{op}(" not in stripped:
                continue
            call_match = re.match(r"^(\w+)\." + re.escape(op) + r"\(", stripped)
            if not call_match:
                continue
            handle = call_match.group(1)

            # Determine whether this handle targets the campaigns table.
            # Signal A: handle name contains "campaign" (case-insensitive).
            handle_is_campaign = "campaign" in handle.lower()

            # Signal B: handle was bound to a campaigns Table() earlier in file.
            bound_as_campaign = False
            if handle in bound_handles:
                # Only consider bindings that precede this call.
                prior_bindings = [ln for ln in bound_handles[handle] if ln <= lineno]
                bound_as_campaign = bool(prior_bindings)

            if not (handle_is_campaign or bound_as_campaign):
                continue  # Not a campaign-table write — skip.

            # An update_item with ConditionExpression=attribute_exists(campaignId)
            # cannot create a new row — ConditionalCheckFailedException fires when
            # the item is absent.  There is no owner obligation for such a call.
            # This is a recognised safe pattern, not an allowlist exception.
            if op == "update_item" and _is_upsert_safe(lines, lineno):
                continue

            if not _call_sets_owner(lines, lineno):
                gaps.append((rel_path, lineno, op))

    return gaps


# ---------------------------------------------------------------------------
# Public: collect all gaps across the repo
# ---------------------------------------------------------------------------

def _collect_gaps() -> list[tuple[str, int, str]]:
    all_gaps: list[tuple[str, int, str]] = []
    for path in _source_files():
        all_gaps.extend(_find_gaps_in_file(path))
    return sorted(all_gaps)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_all_campaign_writes_set_owner() -> None:
    """Every campaign-table write must set owner or be explicitly allowlisted.

    EXPECTED FAILURE AT HEAD (before FG4.1–4.3 are applied).
    The following 6 sites were named in the Fix Group 4 reviewer enumeration:

      modules/cms_ui/source/handlers/main_api/index.py:2666  [put_item]
          create-vehicle-inline path — ``_campaign_item`` dict has no ``owner``

      modules/cms_ui/source/handlers/main_api/index.py:9194  [update_item]
          /fleet-campaigns/status fleet-level update — only sets ``status``

      modules/cms_ui/source/handlers/main_api/index.py:9210  [update_item]
          /fleet-campaigns/status child-vehicle update — only sets ``status``

      deployment/scripts/deploy_vehicle_campaign.py:186  [put_item]
          operator deployment script — ``item`` dict has no ``owner``

      deployment/scripts/seed_decoder_and_campaign.py:416  [put_item]
          seed_campaigns_table() — seeded row has no ``owner``

      deployment/scripts/seed_decoder_and_campaign.py:623  [put_item]
          seed_extra_templates() — template rows have no ``owner``

    The mechanical enumeration also discovers 3 additional gaps not in the
    reviewer's list (the spec's own enumeration was incomplete — that's the
    problem this guard was written to prevent):

      deployment/scripts/seed_uds_dtc_template.py:87  [put_item]
          standalone UDS-DTC template seeder — item dict has no ``owner``

      services/simulation/simulation_api.py:605  [put_item]
          Flask simulation API _assign_campaigns — item dict has no ``owner``

      services/simulation/simulation_api.py:628  [update_item]
          Flask simulation API _unassign_campaigns — update has no ``owner``

    All 9 sites will appear in the failure message at HEAD.
    Once FG4.1–4.3 fix the 6 primary sites, the remaining 3 must be either
    fixed or allowlisted (with a written reason) before the guard can pass.

    MUST PASS AT HEAD (already fixed before this guard was written):

      services/data_processing/lambda/data_processing_api.py:1106   put_item
      services/data_processing/lambda/data_processing_api.py:1146   update_item
      services/data_processing/lambda/data_processing_api.py:1234   put_item
      services/simulation/lambda/simulation_lambda.py:1118           put_item
      services/simulation/lambda/simulation_lambda.py:1177           put_item
      modules/cms_ui/source/handlers/main_api/index.py:9119          put_item
      modules/cms_ui/source/handlers/main_api/index.py:9148          put_item
    """
    # Validate allowlist before doing anything else.
    for key, reason in _ALLOWLIST.items():
        assert _reason_is_valid(reason), (
            f"_ALLOWLIST entry {key!r} has an empty or missing reason string."
            " An allowlist without reasons is a silent bypass."
        )

    gaps = _collect_gaps()
    if not gaps:
        return  # All sites set owner — test passes.

    msg_lines = [
        "",
        "Campaign-table write sites that do NOT set 'owner':",
        "(each must either set owner or be added to _ALLOWLIST with a reason)",
        "",
    ]
    for rel_path, lineno, op in gaps:
        msg_lines.append(f"  {rel_path}:{lineno}  [{op}]")
    msg_lines.append("")
    pytest.fail("\n".join(msg_lines))


def test_guard_does_not_flag_non_campaign_tables() -> None:
    """The handle-binding must be precise: decoder-manifest and signal-catalog
    writes in ``seed_decoder_and_campaign.py`` must NOT be flagged.

    The ``table`` variable in that file is first bound to ``DECODER_TABLE``
    (L307) then re-bound to the campaigns table (L414, L534).  Writes made
    while ``table`` is bound to the decoder table (L312, L325, L361) must be
    invisible to the guard.
    """
    gaps = _collect_gaps()
    gap_keys = {(rp, ln) for rp, ln, _ in gaps}

    seed_file = "deployment/scripts/seed_decoder_and_campaign.py"
    # These three writes use ``table`` bound to DECODER_TABLE, not campaigns.
    for decoder_write_lineno in [312, 325, 361]:
        assert (seed_file, decoder_write_lineno) not in gap_keys, (
            f"Non-campaign write at {seed_file}:{decoder_write_lineno} was"
            " incorrectly flagged.  The guard's handle-binding is too broad."
        )


def test_allowlist_entries_have_reasons() -> None:
    """Every allowlist entry must carry a non-empty reason string."""
    for key, reason in _ALLOWLIST.items():
        assert _reason_is_valid(reason), (
            f"_ALLOWLIST[{key!r}] has an empty reason string."
            " Add a reason explaining why this site is exempt from the check."
        )



# ---------------------------------------------------------------------------
# FG5.1 — structural tests for the boundary fix and safe-pattern recognition
# ---------------------------------------------------------------------------

def test_boundary_stops_at_indented_method() -> None:
    """_effective_lookback_start must stop at a method def, not just a top-level def.

    Before FG5.1 the check was ``startswith("def ")``, column-0 only.  An
    indented ``def`` (i.e. a class method) would be skipped, allowing the
    lookback to read back into a sibling method's body.

    This test builds the exact bleed shape that occurs in simulation_api.py:
    a sibling method (_assign_campaigns) sets 'owner', then a second method
    (_unassign_campaigns) has a put_item with no 'owner'.  With the broken
    boundary the put_item passes because the owner from the sibling bleeds in.
    With the fixed boundary it is correctly flagged.

    This IS Mutation A's inverse — the test fails when the boundary fix is
    reverted to column-0-only ``startswith``.
    """
    # Craft a minimal file with two indented methods: first sets 'owner',
    # second has a put_item with no 'owner'.  The methods are close enough
    # that the broken 25-line window would see the 'owner' from the first.
    src = "\n".join([
        "class Manager:",
        "    def _assign(self, t):",
        "        item = {",
        "            'owner': 'platform',",
        "            'campaignId': 'x',",
        "        }",
        "        t.put_item(Item=item)",
        "",
        "    def _unassign(self, campaigns):",
        "        campaigns.put_item(Item={'campaignId': 'y'})",
    ])
    lines = src.splitlines()

    # The put_item in _unassign is at line 10 (1-based).
    call_lineno = 10

    start = _effective_lookback_start(lines, call_lineno)
    # The fixed boundary should stop at line 9 ("    def _unassign(...)"),
    # which is idx 8 (0-based); the function returns idx+1 = 9.
    # So the window starts at line 10 (idx 9), which is the _unassign body.
    # That body has no 'owner', so _call_sets_owner must return False.
    owner_found = _call_sets_owner(lines, call_lineno)
    assert not owner_found, (
        "_call_sets_owner returned True for a put_item in _unassign whose "
        "sibling method _assign has 'owner'.  The indented-method boundary "
        "is not stopping the lookback — the FG5.1 fix is not active."
    )

    # Also verify that the overall helper sees this as a gap.
    import tempfile, os
    with tempfile.NamedTemporaryFile(
        mode="w", suffix="_campaigns_test_boundary.py",
        dir=_REPO_ROOT, delete=False, encoding="utf-8"
    ) as f:
        f.write(src)
        tmp_path = pathlib.Path(f.name)
    try:
        gaps = _find_gaps_in_file(tmp_path)
        gap_linenos = {ln for _, ln, _ in gaps}
        assert call_lineno in gap_linenos, (
            f"_find_gaps_in_file did not flag the indented put_item at line "
            f"{call_lineno}.  Indented-method boundary is broken."
        )
    finally:
        tmp_path.unlink(missing_ok=True)


def test_upsert_safe_update_item_is_not_flagged() -> None:
    """An update_item with ConditionExpression=attribute_exists(campaignId) is safe.

    Such a call raises ConditionalCheckFailedException rather than upsert when
    the item is absent, so it cannot produce an ownerless row.  The guard must
    recognise this as a safe pattern — not flag it and not require an allowlist
    entry.

    This IS Mutation B's inverse — the test fails when ConditionExpression is
    stripped from the call (making it a true upsert that can create rows).
    """
    # Minimal file: _unassign uses update_item with ConditionExpression.
    safe_src = "\n".join([
        "class Manager:",
        "    def _unassign(self, campaigns):",
        "        campaigns.update_item(",
        "            Key={'campaignId': 'x'},",
        "            UpdateExpression='SET #s = :s',",
        "            ConditionExpression='attribute_exists(campaignId)',",
        "        )",
    ])
    safe_lines = safe_src.splitlines()
    call_lineno = 3  # the update_item line

    # _is_upsert_safe must return True
    assert _is_upsert_safe(safe_lines, call_lineno), (
        "_is_upsert_safe returned False for an update_item that has "
        "ConditionExpression=attribute_exists(campaignId).  The safe-pattern "
        "recognition is broken."
    )

    # Without ConditionExpression the same call is NOT safe.
    unsafe_src = "\n".join([
        "class Manager:",
        "    def _unassign(self, campaigns):",
        "        campaigns.update_item(",
        "            Key={'campaignId': 'x'},",
        "            UpdateExpression='SET #s = :s',",
        "        )",
    ])
    unsafe_lines = unsafe_src.splitlines()
    assert not _is_upsert_safe(unsafe_lines, call_lineno), (
        "_is_upsert_safe returned True for an update_item with NO "
        "ConditionExpression.  That call can upsert ownerless rows."
    )

    # The safe call must not appear in gaps reported by _find_gaps_in_file.
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix="_campaigns_safe_pattern.py",
        dir=_REPO_ROOT, delete=False, encoding="utf-8"
    ) as f:
        f.write(safe_src)
        tmp_path = pathlib.Path(f.name)
    try:
        gaps = _find_gaps_in_file(tmp_path)
        gap_linenos = {ln for _, ln, _ in gaps}
        assert call_lineno not in gap_linenos, (
            f"_find_gaps_in_file flagged the upsert-safe update_item at line "
            f"{call_lineno} as a gap.  The safe-pattern check is not applied."
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    # The UNSAFE call (no ConditionExpression) MUST appear in gaps.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix="_campaigns_unsafe_pattern.py",
        dir=_REPO_ROOT, delete=False, encoding="utf-8"
    ) as f:
        f.write(unsafe_src)
        tmp_path = pathlib.Path(f.name)
    try:
        gaps = _find_gaps_in_file(tmp_path)
        gap_linenos = {ln for _, ln, _ in gaps}
        assert call_lineno in gap_linenos, (
            f"_find_gaps_in_file did NOT flag the unsafe update_item at line "
            f"{call_lineno}.  An ownerless upsert must always be flagged."
        )
    finally:
        tmp_path.unlink(missing_ok=True)



# ---------------------------------------------------------------------------
# FG6.1 — comment-stripping tests
# ---------------------------------------------------------------------------

def test_comment_owner_token_does_not_satisfy_check() -> None:
    """A comment containing ``'owner'`` must NOT satisfy _call_sets_owner.

    This is the bypass FG6.1 closes.  Before the fix, a call block preceded
    only by a comment mentioning ``'owner'`` passed the check — the guard was
    reading comment text as if it were code.  After the fix, only code counts.

    Mutation A inverse: strip ConditionExpression from the status update_item
    and leave the comment — _call_sets_owner must return False (no owner in code).
    The full guard must then FLAG the call.
    """
    # update_item with ConditionExpression removed, comment still mentions owner.
    src = "\n".join([
        "def _update(campaigns, campaign_id, new_status):",
        "    # 'owner' is not set — ConditionExpression prevents upsert",
        "    campaigns.update_item(",
        "        Key={'campaignId': campaign_id},",
        "        UpdateExpression='SET #s = :s',",
        "    )",
    ])
    lines = src.splitlines()
    call_lineno = 3  # the update_item line (1-based)

    # Comment-only 'owner' must NOT satisfy the owner check.
    assert not _call_sets_owner(lines, call_lineno), (
        "_call_sets_owner returned True when 'owner' appears only in a comment."
        " Comment text must not satisfy the ownership check."
    )

    # The full guard must flag this site via _find_gaps_in_file.
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix="_campaigns_comment_bypass.py",
        dir=_REPO_ROOT, delete=False, encoding="utf-8"
    ) as f:
        f.write(src)
        tmp_path = pathlib.Path(f.name)
    try:
        gaps = _find_gaps_in_file(tmp_path)
        gap_linenos = {ln for _, ln, _ in gaps}
        assert call_lineno in gap_linenos, (
            f"_find_gaps_in_file did NOT flag the update_item at line "
            f"{call_lineno} even though 'owner' appears only in a comment."
            " The comment-bypass is still open."
        )
    finally:
        tmp_path.unlink(missing_ok=True)


def test_owner_string_literal_still_counts() -> None:
    """A genuine ``'owner'`` dict key in code must still satisfy _call_sets_owner.

    Stripping comments must not strip string literals.  The ``'owner'`` key
    appearing in the item-dict built *before* the put_item call is real code
    and must still be detected.  (The lookback window covers lines BEFORE the
    call line, not the call's own argument block.)
    """
    # put_item where 'owner' is built into item dict above the call.
    src = "\n".join([
        "def _create(campaigns, campaign_id):",
        "    item = {",
        "        'owner': 'oem',",
        "        'campaignId': campaign_id,",
        "    }",
        "    campaigns.put_item(Item=item)",
    ])
    lines = src.splitlines()
    # put_item is at line 6 (1-based); 'owner' is at line 3 — before the call.
    call_lineno = 6

    assert _call_sets_owner(lines, call_lineno), (
        "_call_sets_owner returned False when 'owner' is a genuine dict key."
        " Code-level 'owner' tokens must still satisfy the check."
    )


def test_strip_line_comment_preserves_string_with_hash() -> None:
    """_strip_line_comment must not truncate a string that contains a ``#``."""
    # A string literal containing # is not a comment.
    line = "    x = {'key': '#not-a-comment', 'owner': 'oem'}"
    stripped = _strip_line_comment(line)
    # The whole line should survive intact because the # is inside a string.
    assert "'owner'" in stripped, (
        "_strip_line_comment wrongly stripped a # that was inside a string "
        f"literal.  Input: {line!r}  Output: {stripped!r}"
    )
    assert "#not-a-comment" in stripped, (
        "_strip_line_comment removed content from inside a string literal."
        f"  Input: {line!r}  Output: {stripped!r}"
    )


def test_strip_line_comment_removes_trailing_comment() -> None:
    """_strip_line_comment must strip a trailing # comment from a code line."""
    line = "    x = something  # 'owner' mentioned here should be stripped"
    stripped = _strip_line_comment(line)
    assert "'owner'" not in stripped, (
        "_strip_line_comment did not remove 'owner' from the comment portion."
        f"  Input: {line!r}  Output: {stripped!r}"
    )
    assert "something" in stripped, (
        "_strip_line_comment removed the code portion of the line."
        f"  Input: {line!r}  Output: {stripped!r}"
    )
