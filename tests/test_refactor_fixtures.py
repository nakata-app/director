"""M3-T01: smoke tests for the refactor fixture suite.

Verifies that the 5 hand-curated refactor fixtures load, pass deterministically
under the existing runner, and meet ticket acceptance criteria (one fixture per
archetype; metadata.yaml has ground_truth_source; assertions are specific).

Run: python3 tests/test_refactor_fixtures.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import director


def _ok(label, cond):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")
    return cond


REFACTOR_ROOT = director.DIRECTOR_HOME / "fixtures" / "refactor"
EXPECTED_ARCHETYPES = {
    "f01_dead_code", "f02_unused_import", "f03_function_rename",
    "f04_simplify", "f05_comment_normalize",
}


# --- Test 1: all 5 fixtures present with required files ---
print("=" * 60)
print("Test 1: 5 fixtures present, each with goal+metadata+expected")
print("=" * 60)
for fid in EXPECTED_ARCHETYPES:
    fdir = REFACTOR_ROOT / fid
    _ok(f"{fid} dir exists", fdir.exists() and fdir.is_dir())
    _ok(f"{fid}/goal.txt", (fdir / "goal.txt").exists())
    _ok(f"{fid}/metadata.yaml", (fdir / "metadata.yaml").exists())
    _ok(f"{fid}/expected.json", (fdir / "expected.json").exists())


# --- Test 2: every metadata.yaml has ground_truth_source ---
print()
print("=" * 60)
print("Test 2: ground_truth_source recorded in every metadata.yaml")
print("=" * 60)
for fid in EXPECTED_ARCHETYPES:
    md = (REFACTOR_ROOT / fid / "metadata.yaml").read_text()
    _ok(f"{fid} → ground_truth_source present", "ground_truth_source" in md)


# --- Test 3: assertions are specific (byte_match needles non-trivial) ---
print()
print("=" * 60)
print("Test 3: every fixture asserts at least one byte_match (specificity)")
print("=" * 60)
for fid in EXPECTED_ARCHETYPES:
    exp = json.loads((REFACTOR_ROOT / fid / "expected.json").read_text())
    has_byte_match = any(a.get("type") == "byte_match" for a in exp.get("assertions", []))
    _ok(f"{fid} → byte_match present (not just file_present)", has_byte_match)


# --- Test 4: suite runs and all 5 pass deterministically ---
print()
print("=" * 60)
print("Test 4: ./director.py fixture run refactor → 5/5 pass, twice")
print("=" * 60)
report1 = director.run_fixture_suite(str(REFACTOR_ROOT), dry_run_director=True)
_ok(f"first run: 5 fixtures (got {report1['total']})", report1["total"] == 5)
_ok(f"first run: 5 pass (got {report1['passed']})", report1["passed"] == 5)
report2 = director.run_fixture_suite(str(REFACTOR_ROOT), dry_run_director=True)
_ok(f"second run: same 5 pass (got {report2['passed']})", report2["passed"] == 5)
# Determinism: per-fixture status identical across runs
status1 = {r["fixture_id"]: r["status"] for r in report1["per_fixture"]}
status2 = {r["fixture_id"]: r["status"] for r in report2["per_fixture"]}
_ok("per-fixture status identical across runs (deterministic)", status1 == status2)


# --- Test 5: archetype coverage — no two fixtures duplicate the same byte_match needle set ---
print()
print("=" * 60)
print("Test 5: each archetype distinct (no duplicate needle sets)")
print("=" * 60)
needle_sets = {}
for fid in EXPECTED_ARCHETYPES:
    exp = json.loads((REFACTOR_ROOT / fid / "expected.json").read_text())
    needles = frozenset(a["needle"] for a in exp.get("assertions", []) if a.get("type") == "byte_match")
    needle_sets[fid] = needles
unique = len({frozenset(s) for s in needle_sets.values()})
_ok(f"5 distinct needle sets (got {unique})", unique == 5)
