"""Tests for secret-scan.py"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCANNER = Path(__file__).parent / "secret-scan.py"


def make_config(tmp_path, extra_patterns=None, extra_strings=None, scan_exclude=None):
    """Write a minimal YAML config and return its path."""
    patterns = {
        "aws_account_id": {
            "regex": r"\b195026230833\b",
            "severity": "critical",
            "description": "Staging AWS account ID",
        },
        "warning_pattern": {
            "regex": r"\bSECRET_WARN\b",
            "severity": "warning",
            "description": "Warning-level test pattern",
        },
    }
    if extra_patterns:
        patterns.update(extra_patterns)

    strings = ["Mahindra"]
    if extra_strings:
        strings.extend(extra_strings)

    excludes = list(scan_exclude or [])

    import yaml
    cfg = {
        "forbidden_patterns": patterns,
        "forbidden_strings": strings,
        "scan_exclude": excludes,
    }
    config_path = tmp_path / "scan-config.yml"
    config_path.write_text(yaml.dump(cfg))
    return config_path


def run_scanner(*args):
    """Run the scanner and return (returncode, parsed_json_or_None, stderr)."""
    result = subprocess.run(
        [sys.executable, str(SCANNER)] + list(args),
        capture_output=True,
        text=True,
    )
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        data = None
    return result.returncode, data, result.stderr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_clean_tree(tmp_path):
    """Empty/scrubbed tree returns clean=true, exit 0."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("Hello world\n")
    config = make_config(tmp_path)

    rc, data, _ = run_scanner("--config", str(config), "--root", str(root))
    assert rc == 0
    assert data is not None
    assert data["clean"] is True
    assert data["findings"] == []


def test_dirty_tree_account_id(tmp_path):
    """File containing the account ID triggers a critical finding."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config.py").write_text("ACCOUNT = '195026230833'\n")
    config = make_config(tmp_path)

    rc, data, _ = run_scanner("--config", str(config), "--root", str(root))
    assert rc == 1
    assert data is not None
    assert data["clean"] is False
    assert any(f["pattern_name"] == "aws_account_id" for f in data["findings"])


def test_dirty_tree_forbidden_string(tmp_path):
    """File containing 'Mahindra' triggers a critical finding."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "labels.ts").write_text("const brand = 'Mahindra Motors';\n")
    config = make_config(tmp_path)

    rc, data, _ = run_scanner("--config", str(config), "--root", str(root))
    assert rc == 1
    assert data is not None
    assert data["clean"] is False
    assert any("Mahindra" in f["pattern_name"] for f in data["findings"])


def test_allow_finding_suppresses(tmp_path):
    """--allow-finding suppresses the specific match; result is clean."""
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "config.py"
    target.write_text("ACCOUNT = '195026230833'\n")
    config = make_config(tmp_path)

    # Determine the relative path the scanner will use
    rel = str(target.relative_to(root))
    allow_arg = f"aws_account_id:{rel}:1"

    rc, data, _ = run_scanner(
        "--config", str(config), "--root", str(root),
        "--allow-finding", allow_arg,
    )
    assert rc == 0
    assert data is not None
    assert data["clean"] is True
    assert data["findings"] == []


def test_strict_mode_escalates(tmp_path):
    """Warning-level pattern exits 0 normally but exits 1 with --strict."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "note.txt").write_text("This is a SECRET_WARN value\n")
    config = make_config(tmp_path)

    # Without --strict: exit 0, clean=True
    rc, data, _ = run_scanner("--config", str(config), "--root", str(root))
    assert rc == 0
    assert data["clean"] is True

    # With --strict: exit 1, clean=False
    rc2, data2, _ = run_scanner("--config", str(config), "--root", str(root), "--strict")
    assert rc2 == 1
    assert data2["clean"] is False


def test_scan_exclude_skips(tmp_path):
    """Files matching scan_exclude glob are not scanned."""
    root = tmp_path / "repo"
    root.mkdir()
    node_modules = root / "node_modules"
    node_modules.mkdir()
    (node_modules / "pkg.js").write_text("var x = '195026230833';\n")
    config = make_config(tmp_path, scan_exclude=["**/node_modules/**"])

    rc, data, _ = run_scanner("--config", str(config), "--root", str(root))
    assert rc == 0
    assert data["clean"] is True
    assert data["findings"] == []


def test_binary_files_skipped(tmp_path):
    """Binary file (contains NULL byte) is not scanned."""
    root = tmp_path / "repo"
    root.mkdir()
    binary = root / "image.bin"
    binary.write_bytes(b"195026230833\x00binary data here")
    config = make_config(tmp_path)

    rc, data, _ = run_scanner("--config", str(config), "--root", str(root))
    assert rc == 0
    assert data["clean"] is True
    assert data["findings"] == []


def test_large_files_skipped(tmp_path):
    """File >5MB is skipped with a log message (not a finding)."""
    root = tmp_path / "repo"
    root.mkdir()
    large = root / "bundle.cjs"
    # Write just over 5MB
    chunk = b"195026230833 " * 1000
    with open(large, "wb") as f:
        while f.tell() < 5 * 1024 * 1024 + 1:
            f.write(chunk)
    config = make_config(tmp_path)

    rc, data, stderr = run_scanner("--config", str(config), "--root", str(root))
    assert rc == 0
    assert data["clean"] is True
    assert data["findings"] == []
    assert "skipping large file" in stderr


def test_brand_canary_case_insensitive(tmp_path):
    """Brand-canary regexes match case-insensitively after the IGNORECASE
    default added 2026-06-04 (issue 2026-06-03-public-mirror-scanner-case-
    sensitivity). Word-bounded patterns like `\\bford\\b` and `\\bautonomic\\b`
    must catch real-world casing variants (`Ford`, `FORD`, `Autonomic`,
    `AUTONOMIC`) — not only the all-lowercase form authored in the YAML.
    """
    root = tmp_path / "repo"
    root.mkdir()
    # File contents mirror real-world brand attributions found in source comments
    # and Postman-collection references that previously slipped past the scanner.
    (root / "leak.py").write_text(
        "# Header per Ford Pro Postman collection FCS Vehicle Enrollment 2.0\n"
        "# vendor: Autonomic\n"
        "# all-caps: FORD\n"
        "# mixed-case fleet id: FCSFleet\n"
        "# tmc external pipe: TMC-External\n"
    )
    config = make_config(
        tmp_path,
        extra_patterns={
            "oem1_ford": {
                "regex": r"\bford\b",
                "severity": "critical",
                "description": "OEM1 Ford canary",
            },
            "oem1_autonomic": {
                "regex": r"\bautonomic\b",
                "severity": "critical",
                "description": "OEM1 Autonomic canary",
            },
            "oem1_fcsfleet": {
                "regex": r"\bfcsfleet\b",
                "severity": "critical",
                "description": "OEM1 FCSFleet canary",
            },
            "oem1_tmc_external": {
                "regex": r"\btmc-external\b",
                "severity": "critical",
                "description": "OEM1 tmc-external canary",
            },
        },
    )

    rc, data, _ = run_scanner("--config", str(config), "--root", str(root))
    assert rc == 1, "scanner must exit non-zero when brand canaries fire"
    assert data is not None
    assert data["clean"] is False

    matched = {(f["pattern_name"], f["matched"]) for f in data["findings"]}
    # Every variant authored in the leak file MUST trip the corresponding
    # canary regardless of case.
    assert ("oem1_ford", "Ford") in matched, f"missing Ford match; got {matched}"
    assert ("oem1_ford", "FORD") in matched, f"missing FORD match; got {matched}"
    assert ("oem1_autonomic", "Autonomic") in matched, f"missing Autonomic match; got {matched}"
    assert ("oem1_fcsfleet", "FCSFleet") in matched, f"missing FCSFleet match; got {matched}"
    assert ("oem1_tmc_external", "TMC-External") in matched, f"missing TMC-External match; got {matched}"


def test_nhtsa_recall_data_scan_excluded(tmp_path):
    """Files matching the NHTSA recall-data scan_exclude entries must not
    fire brand canaries. Regression for issue
    2026-06-08-cms-recall-data-scan-exclude — confirms that real NHTSA
    payloads containing `"manufacturer": "Ford Motor Company"` (factual
    public-domain regulatory data) do not trip the `\\bford\\b` canary
    once the path is on the exclude list.
    """
    root = tmp_path / "repo"
    # Mirror the production repo layout that the scan_exclude entries target.
    recall_int = root / "services" / "recall-integration"
    recall_int.mkdir(parents=True)
    recall_warranty = (
        root / "modules" / "cms_ui" / "source" / "frontend" / "src"
        / "components" / "recall-warranty"
    )
    recall_warranty.mkdir(parents=True)

    # Realistic NHTSA recall payload — would fire `oem1_ford` canary if scanned.
    nhtsa_json = (
        '{"recalls":[{"manufacturer":"Ford Motor Company","make":"Ford",'
        '"model":"F-150","yearFrom":2015,"yearTo":2020,"campaignId":"23V123"}]}\n'
    )
    nhtsa_ts = (
        "// Auto-generated from NHTSA Recalls API\n"
        "export const nhtsaRecalls = ["
        '{ manufacturer: "Ford Motor Company", make: "Ford", model: "F-150" },'
        '{ manufacturer: "Lincoln", make: "Lincoln", model: "Navigator" }'
        "];\n"
    )

    (recall_int / "nhtsa_fleet_recalls.json").write_text(nhtsa_json)
    (recall_int / "nhtsaRecallData.ts").write_text(nhtsa_ts)
    (recall_warranty / "nhtsaRecallData.ts").write_text(nhtsa_ts)

    # Sanity: a non-excluded sibling with the same content MUST still fire,
    # so we know the canary is active in this test environment.
    (root / "leaked_recall_dump.ts").write_text(nhtsa_ts)

    config = make_config(
        tmp_path,
        extra_patterns={
            "oem1_ford": {
                "regex": r"\bford\b",
                "severity": "critical",
                "description": "OEM1 Ford canary",
            },
            "oem1_lincoln": {
                "regex": r"\blincoln\b",
                "severity": "critical",
                "description": "OEM1 Lincoln canary",
            },
        },
        scan_exclude=[
            "services/recall-integration/nhtsa_fleet_recalls.json",
            "services/recall-integration/nhtsaRecallData.ts",
            "modules/cms_ui/source/frontend/src/components/recall-warranty/nhtsaRecallData.ts",
        ],
    )

    rc, data, _ = run_scanner("--config", str(config), "--root", str(root))
    # Sibling outside the exclude list must still fire — confirms canary
    # is active and the IGNORECASE default is working.
    assert rc == 1
    assert data is not None

    finding_files = {f["file"] for f in data["findings"]}
    # Excluded paths MUST NOT appear in findings.
    excluded_paths = {
        "services/recall-integration/nhtsa_fleet_recalls.json",
        "services/recall-integration/nhtsaRecallData.ts",
        "modules/cms_ui/source/frontend/src/components/recall-warranty/nhtsaRecallData.ts",
    }
    leaked_through_exclude = excluded_paths & finding_files
    assert not leaked_through_exclude, (
        f"scan_exclude failed for: {leaked_through_exclude}"
    )
    # Sibling MUST appear (control assertion).
    assert "leaked_recall_dump.ts" in finding_files, (
        f"control sibling did not fire — canary not active in this test? "
        f"findings={data['findings']}"
    )
