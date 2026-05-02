"""M2-T04: fixture runner — pure-IO + deterministic assertion tests.

The runner reads expected.json (structural assertions: presence, byte-match,
JSON schema) and produces pass/fail without invoking any LLM. Tests mock
the Director run path to keep the suite hermetic and deterministic.

Run: python3 tests/test_fixture_runner.py
"""
import json
import os
import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import director


def _ok(label, cond):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")
    return cond


# --- Test 1: structural assertions — file presence ---
print("=" * 60)
print("Test 1: assertion 'file_present' on existing/missing artifact")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    p_exists = Path(td) / "found.md"
    p_exists.write_text("hello")
    expected = {
        "assertions": [
            {"type": "file_present", "path": str(p_exists)},
            {"type": "file_present", "path": str(Path(td) / "missing.md")},
        ]
    }
    result = director.evaluate_fixture_assertions(expected, work_dir=Path(td))
    _ok(f"first assertion passes (got {result['per_assertion'][0]['ok']})",
        result["per_assertion"][0]["ok"] is True)
    _ok(f"second assertion fails (got {result['per_assertion'][1]['ok']})",
        result["per_assertion"][1]["ok"] is False)
    _ok(f"overall fail (any miss → fail)", result["pass"] is False)


# --- Test 2: byte-match assertion ---
print()
print("=" * 60)
print("Test 2: assertion 'byte_match' literal substring check")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "out.md"
    p.write_text("CRITICAL: SQL injection at api.py:8")
    expected = {
        "assertions": [
            {"type": "byte_match", "path": str(p), "needle": "SQL injection"},
            {"type": "byte_match", "path": str(p), "needle": "ABSENT_STRING"},
        ]
    }
    r = director.evaluate_fixture_assertions(expected, work_dir=Path(td))
    _ok("matching needle → pass", r["per_assertion"][0]["ok"] is True)
    _ok("missing needle → fail", r["per_assertion"][1]["ok"] is False)


# --- Test 3: json_schema assertion (presence of required keys) ---
print()
print("=" * 60)
print("Test 3: assertion 'json_schema' required-keys check")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "report.json"
    p.write_text(json.dumps({"findings": [{"severity": "CRITICAL"}, {"severity": "HIGH"}]}))
    expected = {
        "assertions": [
            {"type": "json_schema", "path": str(p), "required_keys": ["findings"]},
            {"type": "json_schema", "path": str(p), "required_keys": ["nonexistent_field"]},
        ]
    }
    r = director.evaluate_fixture_assertions(expected, work_dir=Path(td))
    _ok("schema with present key → pass", r["per_assertion"][0]["ok"] is True)
    _ok("schema with missing key → fail", r["per_assertion"][1]["ok"] is False)


# --- Test 4: deterministic run (same fixture twice → identical result) ---
print()
print("=" * 60)
print("Test 4: deterministic — same fixture twice → identical result")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    fixture_dir = Path(td) / "fix1"
    fixture_dir.mkdir()
    (fixture_dir / "goal.txt").write_text("goal text")
    (fixture_dir / "metadata.yaml").write_text("domain: security\npersona: security\ndifficulty: easy\n")
    target = fixture_dir / "produced.md"
    target.write_text("OK")
    expected = {"assertions": [{"type": "file_present", "path": str(target)}]}
    (fixture_dir / "expected.json").write_text(json.dumps(expected))
    r1 = director.evaluate_fixture_assertions(expected, work_dir=fixture_dir)
    r2 = director.evaluate_fixture_assertions(expected, work_dir=fixture_dir)
    # Strip latency-ish fields if present (none currently)
    _ok(f"two runs match", r1 == r2)


# --- Test 5: setup.sh failure is reported as setup-failure, not test failure ---
print()
print("=" * 60)
print("Test 5: setup.sh non-zero exit → setup-failure, not assertion fail")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    fix = Path(td) / "fix_setup_fail"
    fix.mkdir()
    (fix / "goal.txt").write_text("test goal")
    (fix / "metadata.yaml").write_text("domain: security\npersona: security\n")
    (fix / "expected.json").write_text(json.dumps({"assertions": []}))
    setup = fix / "setup.sh"
    setup.write_text("#!/bin/bash\nexit 17\n")
    setup.chmod(0o755)
    res = director.run_fixture(fix, persona_id="security", dry_run_director=True)
    _ok(f"status=setup-failure (got {res.get('status')})", res.get("status") == "setup-failure")
    _ok(f"exit code surfaced ({res.get('setup_exit_code')})", res.get("setup_exit_code") == 17)


# --- Test 6: suite runner aggregates per-fixture results ---
print()
print("=" * 60)
print("Test 6: run_fixture_suite aggregates pass/fail across fixtures")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    domain_root = Path(td) / "fixtures" / "security"
    domain_root.mkdir(parents=True)
    # Fixture A: passes (file_present on a file we pre-create)
    fa = domain_root / "f01_pass"
    fa.mkdir()
    (fa / "goal.txt").write_text("a")
    (fa / "metadata.yaml").write_text("domain: security\npersona: security\n")
    target_a = fa / "out.md"
    target_a.write_text("ok")
    (fa / "expected.json").write_text(json.dumps(
        {"assertions": [{"type": "file_present", "path": str(target_a)}]}
    ))
    # Fixture B: fails (file_present on missing path)
    fb = domain_root / "f02_fail"
    fb.mkdir()
    (fb / "goal.txt").write_text("b")
    (fb / "metadata.yaml").write_text("domain: security\npersona: security\n")
    (fb / "expected.json").write_text(json.dumps(
        {"assertions": [{"type": "file_present", "path": str(fb / "ghost.md")}]}
    ))
    report = director.run_fixture_suite(str(domain_root), dry_run_director=True)
    _ok(f"suite ran 2 fixtures (got {report['total']})", report["total"] == 2)
    _ok(f"1 pass (got {report['passed']})", report["passed"] == 1)
    _ok(f"1 fail (got {report['failed']})", report["failed"] == 1)
    _ok("per_fixture has 2 entries", len(report["per_fixture"]) == 2)
    _ok("each entry has fixture_id, status, latency",
        all("fixture_id" in r and "status" in r and "latency_sec" in r for r in report["per_fixture"]))
