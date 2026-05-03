"""Regression tests for the 2026-05-03 reliability batch:
- verify_artifacts retry race fix (10s window, quick=True bypass).
- monitor_loop catastrophic-exception wrapper (state.main_error preservation).
- cmd_recover (stale 'running' tasks settled to done/failed via disk truth).
- task_status heartbeat (ghost detection on log mtime).

Run standalone: `python3 tests/test_recover_and_heartbeat.py`
Same [OK]/[FAIL] convention as the other test scripts.
"""
import sys, os, json, time, shutil, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import director  # noqa: E402

PASS = 0
FAIL = 0

def check(label, ok):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")

# --- 1. verify_artifacts retry race window ---
print("=" * 60)
print("verify_artifacts: retry window + quick mode")
print("=" * 60)

import tempfile
tmp = Path(tempfile.mkdtemp())
try:
    # No artifacts declared → pass instantly
    r = director.verify_artifacts({"expected_artifacts": []})
    check("no-artifact task is pass", r["verdict"] == "pass")

    # Artifact missing, quick=True → fail in <2s (single attempt)
    t0 = time.time()
    r = director.verify_artifacts({"expected_artifacts": [str(tmp / "missing.txt")]}, quick=True)
    elapsed = time.time() - t0
    check(f"quick=True missing returns fail under 2s (got {elapsed:.2f}s)", r["verdict"] == "fail" and elapsed < 2.0)

    # Artifact present + content match
    artifact = tmp / "ok.txt"
    artifact.write_text("HELLO")
    r = director.verify_artifacts({
        "expected_artifacts": [str(artifact)],
        "expected_content": {str(artifact): "HELLO"},
    }, quick=True)
    check("content match → pass", r["verdict"] == "pass")

    # Artifact present + content mismatch
    r = director.verify_artifacts({
        "expected_artifacts": [str(artifact)],
        "expected_content": {str(artifact): "WORLD"},
    }, quick=True)
    check("content mismatch → fail", r["verdict"] == "fail" and "content_mismatch" in r["reason"])
finally:
    shutil.rmtree(tmp)

# --- 2. monitor_loop catastrophic-exception wrapper ---
print()
print("=" * 60)
print("monitor_loop: state.main_error preservation on crash")
print("=" * 60)

RUN_ID = f"test-monitor-wrap-{int(time.time())}"
rd = director.RUNS_DIR / RUN_ID
(rd / "logs").mkdir(parents=True, exist_ok=True)
state = {
    "run_id": RUN_ID, "goal": "test wrap", "summary": "test",
    "started": "2026-05-03T03:00:00+00:00", "cwd": "/tmp", "max_retries": 0,
    "tasks": [
        {"id": "t1", "title": "boom", "brief": "x", "depends_on": [], "timeout_min": 5,
         "expected_artifacts": [], "status": "running", "pid": os.getpid(),
         "started": "BOZUK-ISO", "attempts": 1}
    ]
}
director.write_state(rd, state)

# Run monitor in subprocess to isolate the raise
crash = subprocess.run(
    [sys.executable, "-c", f"""
import sys, json, os
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
import director
from pathlib import Path
rd = Path({str(rd)!r})
state = json.loads((rd / 'state.json').read_text())
state['tasks'][0]['pid'] = os.getpid()
director.write_state(rd, state)
director.monitor_loop(rd, state, {RUN_ID!r})
"""],
    capture_output=True, text=True, timeout=15
)
check("subprocess raised (non-zero exit)", crash.returncode != 0)
check("stderr has crash banner", "Director main loop crashed" in crash.stderr)
check("stderr has recover hint", f"director recover {RUN_ID}" in crash.stderr)

saved = json.loads((rd / "state.json").read_text())
check("state.main_error written", "main_error" in saved)
check("state.main_error_at written", "main_error_at" in saved)
check("main_error contains exception class", saved.get("main_error", "").startswith("ValueError"))

# --- 3. cmd_recover settles stale running tasks ---
print()
print("=" * 60)
print("cmd_recover: stale running → done/failed via disk truth")
print("=" * 60)

# Reuse the crashed run; first task is still 'running' at this point
recover = subprocess.run(
    [sys.executable, str(Path(director.__file__)), "recover", RUN_ID],
    capture_output=True, text=True, timeout=15
)
check("recover exit 0", recover.returncode == 0)
check("recover output mentions prior crash", "Önceki crash" in recover.stdout)

final = json.loads((rd / "state.json").read_text())
t1 = final["tasks"][0]
check("t1 settled (not 'running')", t1["status"] != "running")
check("t1 marked recovered", t1.get("recovered") is True)
check("state.finished set", "finished" in final)
check("state.recovered_at set", "recovered_at" in final)

shutil.rmtree(rd)

# --- 4. task_status heartbeat (ghost detection) ---
print()
print("=" * 60)
print("task_status: log-mtime heartbeat → ghost branch")
print("=" * 60)

RUN_ID2 = f"test-heartbeat-{int(time.time())}"
rd2 = director.RUNS_DIR / RUN_ID2
(rd2 / "logs").mkdir(parents=True, exist_ok=True)
log = rd2 / "logs" / "x.log"
log.write_text("recent")

# Backward compat: rd=None → no heartbeat, alive PID = running
status, _ = director.task_status({"id": "x", "pid": os.getpid()})
check("rd=None backward compat returns running", status == "running")

# Fresh log + alive PID = running
status, _ = director.task_status({"id": "x", "pid": os.getpid()}, rd2)
check("fresh log + alive PID returns running", status == "running")

# Stale log (10min ago) + alive PID = ghost
old = time.time() - 600
os.utime(log, (old, old))
status, _ = director.task_status({"id": "x", "pid": os.getpid()}, rd2)
check("stale log (10min) + alive PID returns ghost", status == "ghost")

# Dead PID always returns done regardless of heartbeat
status, _ = director.task_status({"id": "x", "pid": 999999}, rd2)
check("dead PID returns done", status == "done")

# Explicit subprocess test for env-var disable (HEARTBEAT_SEC=0)
disabled = subprocess.run(
    [sys.executable, "-c", f"""
import sys, os, time
os.environ['DIRECTOR_HEARTBEAT_SEC'] = '0'
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
import director
from pathlib import Path
rd = Path({str(rd2)!r})
status, _ = director.task_status({{'id': 'x', 'pid': os.getpid()}}, rd)
print('STATUS:', status)
"""],
    capture_output=True, text=True, timeout=10
)
check("HEARTBEAT_SEC=0 disables ghost (stale log → running)",
      "STATUS: running" in disabled.stdout)

shutil.rmtree(rd2)

# --- summary ---
print()
print("=" * 60)
print(f"TOTAL: {PASS+FAIL}, passed: {PASS}, failed: {FAIL}")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
