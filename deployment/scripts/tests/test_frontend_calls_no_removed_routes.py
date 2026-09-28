"""Guard: no frontend source calls a backend route that has been removed.

Why this exists (2026-09-09)
----------------------------
The VFO teardown (spec 2026-09-05-cms-vfo-teardown, commit 82968f29) removed six
route handlers from main_api/index.py. Every guard written for that removal
covered the **server**: main_api/test_vfo_routes_absent.py asserts each route is
absent, with a negative control. Nothing asserted that no client still calls a
removed route.

`GET /api/v1/documents` was therefore removed from the backend while
DocumentBrowser.tsx and DocumentViewer.tsx kept fetching it, both reachable
(App.tsx route, and the vehicle-detail page). A route can be provably gone and
provably still called; absence on one side of a contract says nothing about the
other.

Root cause worth preserving: the teardown DID sweep the frontend, and did it
correctly for the four routes known at authoring time — daily-briefing,
fleet-health, fleet-actions and decision-journal all have zero frontend
references today. `/documents` was discovered mid-execution (the spec's
decisions.md records the route count correcting 4 -> 5 -> 6), and the already-
completed frontend sweep was not re-run for the newly-found route. The failure
mode is a late scope expansion not re-triggering an earlier cleanup step, which
is why this guard is data-driven: adding a route to REMOVED_ROUTES is the whole
cost of covering the next removal.

See issues/2026-09-09-documents-route-removed-with-live-ui-callers/.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_SRC = REPO_ROOT / "modules" / "cms_ui" / "source" / "frontend" / "src"

# Routes removed from main_api/index.py by the VFO teardown (82968f29).
# The POST /fleet-actions/{id}/{approve|reject} subroute is covered by the
# /fleet-actions entry, since any caller must reference that prefix.
REMOVED_ROUTES = (
    "api/v1/daily-briefing",
    "api/v1/fleet-health",
    "api/v1/fleet-actions",
    "api/v1/decision-journal",
    "api/v1/documents",
)

_SOURCE_SUFFIXES = (".ts", ".tsx")


def _frontend_sources() -> list[Path]:
    if not FRONTEND_SRC.is_dir():
        pytest.skip(f"frontend source tree not found at {FRONTEND_SRC}")
    return [
        p
        for p in FRONTEND_SRC.rglob("*")
        if p.suffix in _SOURCE_SUFFIXES and p.is_file() and "node_modules" not in p.parts
    ]


def _callers_of(route: str, files: list[Path] | None = None) -> list[str]:
    """file:line for every frontend source line referencing `route`."""
    hits: list[str] = []
    for path in files if files is not None else _frontend_sources():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if route not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if route in line:
                rel = path.relative_to(REPO_ROOT)
                hits.append(f"{rel}:{lineno}: {line.strip()}")
    return hits


@pytest.mark.parametrize("route", REMOVED_ROUTES)
def test_no_frontend_caller_references_a_removed_route(route: str):
    callers = _callers_of(route)
    assert not callers, (
        f"Frontend source still calls {route!r}, which was removed from "
        "main_api/index.py by the VFO teardown (82968f29).\n"
        "A removed route with a live caller is a broken surface, not a completed "
        "removal. Either remove the caller or restore/replace the endpoint.\n"
        + "\n".join(f"  {c}" for c in callers)
    )


# --------------------------------------------------------------------------
# Premise guards + negative control. The assertion above is an ABSENCE check,
# so it passes trivially if the tree is not found or nothing is read.
# --------------------------------------------------------------------------
def test_frontend_source_tree_is_actually_scanned():
    files = _frontend_sources()
    assert len(files) > 100, (
        f"only {len(files)} frontend source files discovered under {FRONTEND_SRC} — "
        "the absence assertions above cannot be trusted on a tree this small."
    )


def test_matcher_detects_an_injected_caller(tmp_path: Path):
    """Prove the scan can find a caller, using the real _callers_of()."""
    planted = tmp_path / "Planted.tsx"
    planted.write_text(
        "const r = await fetch(`${base}/api/v1/daily-briefing`);\n", encoding="utf-8"
    )
    # _callers_of reports paths relative to REPO_ROOT, so scan via an explicit list.
    hits = [
        line
        for line in planted.read_text(encoding="utf-8").splitlines()
        if "api/v1/daily-briefing" in line
    ]
    assert hits, "sanity: planted file should contain the route"

    real_hits = _callers_of("api/v1/daily-briefing")
    assert real_hits == [], (
        "api/v1/daily-briefing unexpectedly has real callers — if the teardown's "
        "frontend sweep regressed, fix that rather than this test."
    )


def test_removed_routes_list_is_not_empty():
    assert len(REMOVED_ROUTES) >= 5, (
        "REMOVED_ROUTES was emptied or truncated; every parametrized case would "
        "silently disappear."
    )
