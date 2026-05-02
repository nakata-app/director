"""M2-T03: mnemonics ingest robustness — pure-math/IO tests.

Imports director.mnemonics_* helpers and exercises retry + fallback paths
via monkeypatched subprocess.run. The mnemonics CLI is never actually called.

Run: python3 tests/test_mnemonics_robustness.py
"""
import os
import sys
import json
import time
import tempfile
import subprocess
import unittest.mock as mock
from pathlib import Path

# Stub heavy imports the director module pulls in at import time, just enough to load it.
sys.path.insert(0, str(Path(__file__).parent.parent))
import director


class _Result:
    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def _ok(label, cond):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")
    return cond


# ----- helpers shared across tests -----
def _temp_paths(tmpdir: Path):
    """Patch director's MNEMONICS_LOG_PATH and FALLBACK_PATH to a tmp dir for isolation."""
    director.MNEMONICS_LOG_PATH = tmpdir / "mnemonics.log"
    director.MNEMONICS_FALLBACK_PATH = tmpdir / "mnemonics.fallback.jsonl"


# ----- Test 1: success on first try -----
print("=" * 60)
print("Test 1: ingest success on first try → no retry, no fallback")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    _temp_paths(tmp)
    calls = []
    def fake_run_ok(cmd, **kw):
        calls.append(cmd)
        return _Result(returncode=0)
    with mock.patch.object(subprocess, "run", side_effect=fake_run_ok), \
         mock.patch.object(time, "sleep") as fake_sleep:
        director.mnemonics_record("first try success", ns="director")
        _ok(f"called once (got {len(calls)})", len(calls) == 1)
        _ok("no sleeps", fake_sleep.call_count == 0)
        _ok("no fallback file written",
            not director.MNEMONICS_FALLBACK_PATH.exists())


# ----- Test 2: retry with exponential backoff (transient failure → eventual success) -----
print()
print("=" * 60)
print("Test 2: 2 transient fails then success → 3 attempts + 2 backoff sleeps")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    _temp_paths(tmp)
    calls = []
    seq = [_Result(returncode=1, stderr="transient"),
           _Result(returncode=1, stderr="transient"),
           _Result(returncode=0)]
    def fake_run_seq(cmd, **kw):
        calls.append(cmd)
        return seq[len(calls) - 1]
    sleeps = []
    with mock.patch.object(subprocess, "run", side_effect=fake_run_seq), \
         mock.patch.object(time, "sleep", side_effect=lambda s: sleeps.append(s)):
        director.mnemonics_record("retry then ok", ns="director")
        _ok(f"3 attempts (got {len(calls)})", len(calls) == 3)
        _ok(f"2 backoff sleeps {sleeps} (expect [1, 3])", sleeps == [1, 3])
        _ok("no fallback (eventually succeeded)",
            not director.MNEMONICS_FALLBACK_PATH.exists())


# ----- Test 3: all retries exhausted → fallback file written -----
print()
print("=" * 60)
print("Test 3: 4 fails → fallback JSONL entry, FALLBACK in mnemonics.log")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    _temp_paths(tmp)
    def fake_run_fail(cmd, **kw):
        return _Result(returncode=1, stderr="server down")
    sleeps = []
    with mock.patch.object(subprocess, "run", side_effect=fake_run_fail), \
         mock.patch.object(time, "sleep", side_effect=lambda s: sleeps.append(s)):
        director.mnemonics_record("falls back", ns="director")
        # Backoff: 1, 3, 9 between 4 attempts (3 retries) = 3 sleeps
        _ok(f"3 backoff sleeps {sleeps} (expect [1, 3, 9])", sleeps == [1, 3, 9])
        _ok("fallback file exists", director.MNEMONICS_FALLBACK_PATH.exists())
        line = director.MNEMONICS_FALLBACK_PATH.read_text().strip()
        rec = json.loads(line)
        _ok("fallback entry has ts/ns/text",
            all(k in rec for k in ("ts", "ns", "text")))
        _ok("fallback ns=director", rec["ns"] == "director")
        log_text = director.MNEMONICS_LOG_PATH.read_text() if director.MNEMONICS_LOG_PATH.exists() else ""
        _ok("FALLBACK marker in mnemonics.log", "FALLBACK" in log_text)


# ----- Test 4: replay reads fallback, ingests, prunes successful entries -----
print()
print("=" * 60)
print("Test 4: replay ingests + prunes fallback entries on success")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    _temp_paths(tmp)
    # Seed fallback file with 3 entries
    entries = [
        {"ts": "2026-05-02T23:00:00", "ns": "director", "text": "rec A"},
        {"ts": "2026-05-02T23:01:00", "ns": "director", "text": "rec B"},
        {"ts": "2026-05-02T23:02:00", "ns": "director", "text": "rec C"},
    ]
    director.MNEMONICS_FALLBACK_PATH.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    # First two ingest succeed, third fails
    seq = [_Result(returncode=0), _Result(returncode=0), _Result(returncode=1, stderr="still down")]
    idx = [0]
    def fake_run_partial(cmd, **kw):
        r = seq[idx[0]]
        idx[0] += 1
        return r
    with mock.patch.object(subprocess, "run", side_effect=fake_run_partial), \
         mock.patch.object(time, "sleep"):
        report = director.mnemonics_replay_fallback()
        _ok(f"report ingested=2 (got {report.get('ingested')})", report.get("ingested") == 2)
        _ok(f"report remaining=1 (got {report.get('remaining')})", report.get("remaining") == 1)
        remaining = director.MNEMONICS_FALLBACK_PATH.read_text().strip().splitlines()
        _ok(f"file has 1 line left (got {len(remaining)})", len(remaining) == 1)
        rec = json.loads(remaining[0])
        _ok("remaining entry is rec C", rec["text"] == "rec C")


# ----- Test 5: concurrent records do not corrupt fallback file -----
print()
print("=" * 60)
print("Test 5: concurrent ingest fallbacks → all entries preserved, no torn lines")
print("=" * 60)
import threading
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    _temp_paths(tmp)
    def fake_run_fail_fast(cmd, **kw):
        return _Result(returncode=1, stderr="down")
    with mock.patch.object(subprocess, "run", side_effect=fake_run_fail_fast), \
         mock.patch.object(time, "sleep"):  # zero out backoff for speed
        threads = []
        for i in range(8):
            t = threading.Thread(target=director.mnemonics_record,
                                 args=(f"concurrent record {i}", "director"))
            threads.append(t); t.start()
        for t in threads:
            t.join()
    lines = director.MNEMONICS_FALLBACK_PATH.read_text().splitlines()
    _ok(f"8 entries preserved (got {len(lines)})", len(lines) == 8)
    parsed = []
    parse_ok = True
    for ln in lines:
        try:
            parsed.append(json.loads(ln))
        except Exception:
            parse_ok = False
    _ok("all lines parse as JSON (no torn writes)", parse_ok)
    texts = sorted(p["text"] for p in parsed)
    expected = sorted(f"concurrent record {i}" for i in range(8))
    _ok("all 8 distinct texts present", texts == expected)
