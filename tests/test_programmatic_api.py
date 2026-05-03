"""M4-T03: Programmatic API surface tests.

Verifies that all __all__ exports exist, have correct signatures,
and that run() returns a valid state dict on a trivial goal.

Run: python3 tests/test_programmatic_api.py
"""
import inspect
import os
import sys
import tempfile
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

# --- __all__ surface ---
EXPECTED_EXPORTS = [
    "run", "ab", "recover",
    "tighten_persona_if_drift", "tighten_critic_if_drift",
    "tighten_decomposer_if_drift", "tighten_persona_with_rollback",
    "verify_artifacts", "load_state", "RUNS_DIR", "domain_drift_thresholds",
]

print("=== Public surface ===")
for name in EXPECTED_EXPORTS:
    ok(f"{name} in __all__", name in director.__all__)
    ok(f"{name} importable", hasattr(director, name))

# --- Callable signatures ---
print("\n=== Signatures ===")
sig_run = inspect.signature(director.run)
ok("run has 'goal' param", "goal" in sig_run.parameters)
ok("run has 'yes' param (auto-confirm)", "yes" in sig_run.parameters)
ok("run has 'skip_critic' param", "skip_critic" in sig_run.parameters)

sig_ab = inspect.signature(director.ab)
ok("ab has 'goal' param", "goal" in sig_ab.parameters)

sig_recover = inspect.signature(director.recover)
ok("recover has 'run_id' param", "run_id" in sig_recover.parameters)

# --- RUNS_DIR is a Path ---
print("\n=== Constants ===")
ok("RUNS_DIR is a Path", isinstance(director.RUNS_DIR, Path))
ok("RUNS_DIR exists", director.RUNS_DIR.exists())

# --- load_state on a real run dir ---
print("\n=== load_state ===")
runs = sorted(director.RUNS_DIR.iterdir()) if director.RUNS_DIR.exists() else []
if runs:
    state = director.load_state(runs[-1])
    ok("load_state returns dict", isinstance(state, dict))
    ok("state has 'tasks' key", "tasks" in state)
    ok("state has 'goal' key", "goal" in state)
else:
    ok("no runs dir to test load_state (skip)", True)

# --- domain_drift_thresholds ---
print("\n=== domain_drift_thresholds ===")
t = director.domain_drift_thresholds("security")
ok("returns dict for 'security'", isinstance(t, dict))
ok("has persona_log_inflation_threshold", "persona_log_inflation_threshold" in t)

# --- run() end-to-end (NIM only, --yes, --skip-critic) ---
print("\n=== run() end-to-end (NIM, trivial goal) ===")
orig_key = os.environ.pop("ANTHROPIC_API_KEY", None)
orig_opus = director.OPUS_API_KEY
director.OPUS_API_KEY = ""  # force NIM for decompose
os.environ["DIRECTOR_DAILY_USD_CAP"] = "1.0"
try:
    with tempfile.TemporaryDirectory() as tmp:
        state = director.run(
            goal=f"echo 'api-smoke' > {tmp}/api_test.txt",
            yes=True,
            skip_critic=True,
            skip_task_critic=True,
            timeout_min=5,
        )
    ok("run() returns dict", isinstance(state, dict))
    ok("run() has 'tasks'", "tasks" in state)
    done = [t for t in state.get("tasks", []) if t.get("status") == "done"]
    ok(f"at least 1 task done (got {len(done)})", len(done) >= 1)
    ok("state has finished timestamp", bool(state.get("finished")))
except Exception as e:
    ok(f"run() raised: {e}", False)
finally:
    director.OPUS_API_KEY = orig_opus
    if orig_key is not None:
        os.environ["ANTHROPIC_API_KEY"] = orig_key

print(f"\n{'='*50}")
print(f"  {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
