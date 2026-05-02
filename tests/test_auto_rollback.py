"""M3-T03: auto-rollback wiring tests.

The rollback decision is purely structural (disk-truth pass/fail flips), no
LLM consulted. These tests exercise the math + atomic-write paths without
making any network calls.

Run: python3 tests/test_auto_rollback.py
"""
import json
import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import director


def _ok(label, cond):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")
    return cond


# ===== Test 1: fixture_compare math — no flip =====
print("=" * 60)
print("Test 1: fixture_compare — no flip → regression_signal False")
print("=" * 60)
before = {"f01": "pass", "f02": "pass", "f03": "fail"}
after = {"f01": "pass", "f02": "pass", "f03": "fail"}
r = director.fixture_compare(before, after)
_ok(f"regression_signal False (got {r['regression_signal']})", r["regression_signal"] is False)
_ok(f"regressed=0 (got {r['regressed']})", r["regressed"] == 0)
_ok(f"improved=0 (got {r['improved']})", r["improved"] == 0)


# ===== Test 2: single PASS→FAIL flip → regression =====
print()
print("=" * 60)
print("Test 2: single flip → regression_signal True")
print("=" * 60)
before = {"f01": "pass", "f02": "pass"}
after = {"f01": "pass", "f02": "fail"}
r = director.fixture_compare(before, after)
_ok("regression_signal True", r["regression_signal"] is True)
_ok(f"regressed=1 (got {r['regressed']})", r["regressed"] == 1)
_ok(f"regressed_ids = ['f02']", r["regressed_ids"] == ["f02"])


# ===== Test 3: multiple regressions =====
print()
print("=" * 60)
print("Test 3: multiple flips counted")
print("=" * 60)
before = {"f01": "pass", "f02": "pass", "f03": "pass"}
after = {"f01": "fail", "f02": "fail", "f03": "pass"}
r = director.fixture_compare(before, after)
_ok("regression_signal True", r["regression_signal"] is True)
_ok(f"regressed=2 (got {r['regressed']})", r["regressed"] == 2)
_ok("regressed_ids contains f01 and f02",
    set(r["regressed_ids"]) == {"f01", "f02"})


# ===== Test 4: mixed improvement + regression — flip still wins =====
print()
print("=" * 60)
print("Test 4: improvement does not cancel a flip")
print("=" * 60)
before = {"f01": "fail", "f02": "pass"}
after = {"f01": "pass", "f02": "fail"}  # f01 improved, f02 regressed
r = director.fixture_compare(before, after)
_ok("regression_signal True (flip wins over improvement)", r["regression_signal"] is True)
_ok(f"improved=1 (got {r['improved']})", r["improved"] == 1)
_ok(f"regressed=1 (got {r['regressed']})", r["regressed"] == 1)


# ===== Test 5: auto_rollback_persona — atomic restore + regressed backup =====
print()
print("=" * 60)
print("Test 5: rollback writes regressed-state backup before restoring prior")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    # Patch DIRECTOR_HOME constants for isolation
    saved_personas = director.PERSONAS_PATH
    saved_log = director.MNEMONICS_LOG_PATH
    director.PERSONAS_PATH = tmp / "personas.json"
    director.MNEMONICS_LOG_PATH = tmp / "mnemonics.log"
    # Seed: prior backup has the "good" version, current personas.json has the "bad"
    good = {"security": {"name": "Sec", "description": "good_v1", "trigger": []}}
    bad = {"security": {"name": "Sec", "description": "BAD_TIGHTENED", "trigger": []}}
    backup_path = tmp / "personas.json.bak.20260503-010000-auto-tighten"
    backup_path.write_text(json.dumps(good))
    director.PERSONAS_PATH.write_text(json.dumps(bad))
    # Stub mnemonics_record + append_evolution_log to avoid side effects
    saved_mr = director.mnemonics_record
    saved_ev = director.append_evolution_log
    director.mnemonics_record = lambda *a, **k: None
    director.append_evolution_log = lambda *a, **k: None
    try:
        ok = director.auto_rollback_persona("security", str(backup_path),
                                            regressed_ids=["f02"])
        _ok("rollback returned True", ok is True)
        # personas.json should now have "good_v1"
        restored = json.loads(director.PERSONAS_PATH.read_text())
        _ok("personas.json restored to good_v1",
            restored["security"]["description"] == "good_v1")
        # A backup of the regressed state must exist
        regressed_backups = list(tmp.glob("personas.json.bak.*-pre-rollback"))
        _ok(f"pre-rollback backup of regressed state present (got {len(regressed_backups)})",
            len(regressed_backups) == 1)
        if regressed_backups:
            saved_state = json.loads(regressed_backups[0].read_text())
            _ok("pre-rollback backup contains the bad version",
                saved_state["security"]["description"] == "BAD_TIGHTENED")
    finally:
        director.PERSONAS_PATH = saved_personas
        director.MNEMONICS_LOG_PATH = saved_log
        director.mnemonics_record = saved_mr
        director.append_evolution_log = saved_ev


# ===== Test 6: reentrancy cap — rollback cannot chain =====
print()
print("=" * 60)
print("Test 6: rollback reentrancy guard — at most 1 rollback per event")
print("=" * 60)
# Simulate the guard counter directly (the wrapper enforces this)
director.reset_rollback_guard("decomposer")
allowed1 = director.rollback_guard_acquire("decomposer")
allowed2 = director.rollback_guard_acquire("decomposer")
director.reset_rollback_guard("decomposer")
allowed3 = director.rollback_guard_acquire("decomposer")
_ok("first acquire allowed", allowed1 is True)
_ok("second acquire blocked (cap=1)", allowed2 is False)
_ok("after reset, third acquire allowed", allowed3 is True)
