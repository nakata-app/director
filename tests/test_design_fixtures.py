"""M3-T02: smoke tests for the design fixture suite.

Verifies that the 5 hand-curated design fixtures load, pass deterministically,
and meet ticket acceptance: 5 distinct archetypes, ground_truth_source recorded,
specific byte_match assertions, deterministic suite runs.

Run: python3 tests/test_design_fixtures.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import director


def _ok(label, cond):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")
    return cond


DESIGN_ROOT = director.DIRECTOR_HOME / "fixtures" / "design"
EXPECTED = {
    "f01_component_scaffold", "f02_wcag_aria", "f03_dark_mode_vars",
    "f04_responsive_breakpoint", "f05_keyboard_focus",
}


# --- Test 1: 5 fixtures present with required files ---
print("=" * 60)
print("Test 1: 5 fixtures present, each with goal+metadata+expected")
print("=" * 60)
for fid in EXPECTED:
    fdir = DESIGN_ROOT / fid
    _ok(f"{fid} dir exists", fdir.exists())
    _ok(f"{fid}/goal.txt", (fdir / "goal.txt").exists())
    _ok(f"{fid}/metadata.yaml", (fdir / "metadata.yaml").exists())
    _ok(f"{fid}/expected.json", (fdir / "expected.json").exists())


# --- Test 2: ground_truth_source recorded ---
print()
print("=" * 60)
print("Test 2: ground_truth_source in every metadata.yaml")
print("=" * 60)
for fid in EXPECTED:
    md = (DESIGN_ROOT / fid / "metadata.yaml").read_text()
    _ok(f"{fid} → ground_truth_source present", "ground_truth_source" in md)


# --- Test 3: every fixture has at least one byte_match assertion ---
print()
print("=" * 60)
print("Test 3: byte_match specificity in every fixture")
print("=" * 60)
for fid in EXPECTED:
    exp = json.loads((DESIGN_ROOT / fid / "expected.json").read_text())
    has = any(a.get("type") == "byte_match" for a in exp.get("assertions", []))
    _ok(f"{fid} → byte_match present", has)


# --- Test 4: suite passes deterministically ---
print()
print("=" * 60)
print("Test 4: ./director.py fixture run design → 5/5 twice")
print("=" * 60)
r1 = director.run_fixture_suite(str(DESIGN_ROOT), dry_run_director=True)
_ok(f"first run: 5 total (got {r1['total']})", r1["total"] == 5)
_ok(f"first run: 5 pass (got {r1['passed']})", r1["passed"] == 5)
r2 = director.run_fixture_suite(str(DESIGN_ROOT), dry_run_director=True)
_ok(f"second run: 5 pass (got {r2['passed']})", r2["passed"] == 5)
s1 = {r["fixture_id"]: r["status"] for r in r1["per_fixture"]}
s2 = {r["fixture_id"]: r["status"] for r in r2["per_fixture"]}
_ok("per-fixture status identical (deterministic)", s1 == s2)


# --- Test 5: archetype coverage — 5 distinct needle sets ---
print()
print("=" * 60)
print("Test 5: 5 distinct needle sets (no archetype duplication)")
print("=" * 60)
needle_sets = {}
for fid in EXPECTED:
    exp = json.loads((DESIGN_ROOT / fid / "expected.json").read_text())
    needles = frozenset(a["needle"] for a in exp.get("assertions", []) if a.get("type") == "byte_match")
    needle_sets[fid] = needles
unique = len({frozenset(s) for s in needle_sets.values()})
_ok(f"5 distinct needle sets (got {unique})", unique == 5)


# --- Test 6: design-specific signals (accessibility/CSS keywords cover the suite) ---
print()
print("=" * 60)
print("Test 6: suite covers accessibility AND theming AND responsive")
print("=" * 60)
all_needles: set[str] = set()
for fid in EXPECTED:
    exp = json.loads((DESIGN_ROOT / fid / "expected.json").read_text())
    for a in exp.get("assertions", []):
        if a.get("type") == "byte_match":
            all_needles.add(a["needle"])
_ok("aria-label needle present (a11y archetype)", "aria-label" in all_needles)
_ok("data-theme needle present (theming archetype)",
    any("data-theme" in n for n in all_needles))
_ok("min-width media query needle present (responsive archetype)",
    any("min-width" in n for n in all_needles))
