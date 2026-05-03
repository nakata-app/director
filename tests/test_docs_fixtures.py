"""docs domain: fixture suite for Documentation Reviewer persona.

5 fixtures testing doc quality detection:
  - missing-return-type: function doc without return type
  - missing-example:     API doc without code example
  - wrong-param-name:    parameter name mismatch between signature and doc
  - stale-version:       install instructions referencing old version
  - no-quickstart:       README with architecture/config but no quickstart

Run: python3 tests/test_docs_fixtures.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import director

PASS = 0
FAIL = 0

def ok(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "docs"
FIXTURES = [
    "missing-return-type",
    "missing-example",
    "wrong-param-name",
    "stale-version",
    "no-quickstart",
]

print("=== docs persona registered ===")
personas = json.loads(Path(director.__file__).parent.joinpath("personas.json").read_text()
                      if hasattr(director, '__file__') else
                      Path("/Users/macmini/.claude/director/personas.json").read_text())
ok("'docs' key in personas.json", "docs" in personas)
if "docs" in personas:
    ok("docs.name set", bool(personas["docs"].get("name")))
    ok("docs.trigger non-empty", len(personas["docs"].get("trigger", [])) >= 3)
    ok("docs.description set", len(personas["docs"].get("description", "")) >= 20)

print("\n=== docs domain drift thresholds ===")
cfg_path = Path("/Users/macmini/.claude/director/domain_drift_config.json")
cfg = json.loads(cfg_path.read_text())
ok("'docs' in domain_drift_config.json", "docs" in cfg)
if "docs" in cfg:
    t = cfg["docs"]
    ok("persona_fidelity_floor present", "persona_fidelity_floor" in t)
    ok("persona_log_inflation_threshold present", "persona_log_inflation_threshold" in t)
    ok("critic_precision_floor present", "critic_precision_floor" in t)

print("\n=== fixture files exist ===")
for name in FIXTURES:
    fdir = FIXTURES_DIR / name
    ok(f"{name}/input.md exists", (fdir / "input.md").exists())
    ok(f"{name}/needles.json exists", (fdir / "needles.json").exists())

print("\n=== needles.json valid JSON ===")
for name in FIXTURES:
    fdir = FIXTURES_DIR / name
    nf = fdir / "needles.json"
    if nf.exists():
        try:
            n = json.loads(nf.read_text())
            ok(f"{name} needles parseable", True)
            ok(f"{name} needles has 'type'", "type" in n)
            ok(f"{name} needles has 'values'", isinstance(n.get("values"), list) and len(n["values"]) >= 1)
        except Exception as e:
            ok(f"{name} needles parseable ({e})", False)

print("\n=== domain_drift_thresholds() API ===")
t = director.domain_drift_thresholds("docs")
ok("returns dict for 'docs'", isinstance(t, dict))
ok("persona_fidelity_floor value sane (>0)", t.get("persona_fidelity_floor", 0) > 0)
ok("persona_log_inflation_threshold <= 1.0", t.get("persona_log_inflation_threshold", 0) <= 1.0)

print(f"\n{'='*50}")
print(f"  {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
