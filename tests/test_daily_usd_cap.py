"""M4-T01: daily USD cap + budget reporting tests.

Verifies cost arithmetic, multi-model pricing, env-driven cap, JSONL aggregation
across legacy + new files, pre-flight cap gate, and cmd_budget JSON surface.

Run: python3 tests/test_daily_usd_cap.py
"""
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import director


def _ok(label, cond):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")
    return cond


# ----- Test 1: cost_for_call arithmetic for known model (Opus) -----
print("=" * 60)
print("Test 1: cost_for_call known model arithmetic")
print("=" * 60)
# Opus 4.7: $15/M in, $75/M out, $1.50/M cache_read, $18.75/M cache_write
cost = director.cost_for_call("claude-opus-4-7",
                              input_tokens=1_000_000, output_tokens=0)
_ok(f"opus 1M input = $15.00 (got ${cost:.4f})", abs(cost - 15.0) < 1e-6)
cost = director.cost_for_call("claude-opus-4-7",
                              input_tokens=0, output_tokens=1_000_000)
_ok(f"opus 1M output = $75.00 (got ${cost:.4f})", abs(cost - 75.0) < 1e-6)
cost = director.cost_for_call("claude-opus-4-7",
                              input_tokens=1336, output_tokens=769)
_ok(f"opus historical sample ≈ $0.0777 (got ${cost:.4f})",
    abs(cost - 0.0777) < 0.001)

# DeepSeek V4 Pro arithmetic
cost = director.cost_for_call("deepseek-ai/deepseek-v4-pro",
                              input_tokens=1_000_000, output_tokens=0)
_ok(f"deepseek 1M input non-zero (got ${cost:.4f})", cost > 0)
cost = director.cost_for_call("meta/llama-3.3-70b-instruct",
                              input_tokens=1_000_000, output_tokens=0)
_ok(f"llama 3.3 1M input non-zero (got ${cost:.4f})", cost > 0)


# ----- Test 2: unknown model returns 0.0 with stderr warning, no crash -----
print()
print("=" * 60)
print("Test 2: unknown model graceful fallback")
print("=" * 60)
# Capture stderr to verify warning is emitted.
saved_err = sys.stderr
sys.stderr = io.StringIO()
try:
    cost = director.cost_for_call("unknown/fake-model-xyz",
                                  input_tokens=1_000_000, output_tokens=1_000_000)
    captured = sys.stderr.getvalue()
finally:
    sys.stderr = saved_err
_ok("unknown model returns 0.0", cost == 0.0)
_ok("unknown model warns on stderr",
    "unknown" in captured.lower() or "fake-model" in captured.lower())


# ----- Test 3: daily_usd_spent aggregates across legacy and new JSONL files -----
print()
print("=" * 60)
print("Test 3: daily_usd_spent aggregates legacy + new JSONL")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    legacy = Path(td) / "opus_usage.jsonl"
    new = Path(td) / "director_usage.jsonl"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    legacy.write_text(
        json.dumps({"ts": f"{today}T10:00:00+00:00", "model": "claude-opus-4-7",
                    "input_tokens": 1336, "output_tokens": 769,
                    "cache_read": 0, "cache_write": 0,
                    "cost_usd_est": 0.0777}) + "\n"
        + json.dumps({"ts": "2026-04-30T10:00:00+00:00", "model": "claude-opus-4-7",
                     "cost_usd_est": 99.99}) + "\n"  # different date, must NOT count
    )
    new.write_text(
        json.dumps({"ts": f"{today}T11:00:00+00:00",
                    "model": "deepseek-ai/deepseek-v4-pro",
                    "cost_usd_est": 0.0123}) + "\n"
        + json.dumps({"ts": f"{today}T12:00:00+00:00",
                     "model": "meta/llama-3.3-70b-instruct",
                     "cost_usd_est": 0.0050}) + "\n"
    )
    saved_legacy = director.OPUS_USAGE_PATH
    saved_new = director.USAGE_LOG_PATH
    director.OPUS_USAGE_PATH = legacy
    director.USAGE_LOG_PATH = new
    try:
        spent = director.daily_usd_spent()
        expected = 0.0777 + 0.0123 + 0.0050
        _ok(f"sum today = ${expected:.4f} (got ${spent:.4f})",
            abs(spent - expected) < 1e-4)
        spent_old = director.daily_usd_spent(date="2026-04-30")
        _ok(f"different date isolated ($99.99, got ${spent_old:.4f})",
            abs(spent_old - 99.99) < 1e-4)
    finally:
        director.OPUS_USAGE_PATH = saved_legacy
        director.USAGE_LOG_PATH = saved_new


# ----- Test 4: daily_usd_cap_blocks honors env var override -----
print()
print("=" * 60)
print("Test 4: daily_usd_cap_blocks env override + zero-disable")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    new = Path(td) / "director_usage.jsonl"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new.write_text(
        json.dumps({"ts": f"{today}T10:00:00+00:00", "model": "claude-opus-4-7",
                    "cost_usd_est": 4.50}) + "\n"
    )
    saved_new = director.USAGE_LOG_PATH
    saved_legacy = director.OPUS_USAGE_PATH
    saved_cap = director.DAILY_USD_CAP
    director.USAGE_LOG_PATH = new
    director.OPUS_USAGE_PATH = Path(td) / "absent_legacy.jsonl"  # missing on purpose
    try:
        director.DAILY_USD_CAP = 5.00
        _ok("under cap (4.50<5.00) does NOT block",
            director.daily_usd_cap_blocks(estimate_usd=0.0) is False)
        _ok("over cap (4.50+0.60>5.00) blocks",
            director.daily_usd_cap_blocks(estimate_usd=0.60) is True)

        director.DAILY_USD_CAP = 0.0  # disabled
        _ok("cap=0 disables gate even with spend",
            director.daily_usd_cap_blocks(estimate_usd=10.0) is False)

        director.DAILY_USD_CAP = -1.0  # negative also disables
        _ok("cap<0 disables gate",
            director.daily_usd_cap_blocks(estimate_usd=10.0) is False)
    finally:
        director.USAGE_LOG_PATH = saved_new
        director.OPUS_USAGE_PATH = saved_legacy
        director.DAILY_USD_CAP = saved_cap


# ----- Test 5: record_usage appends JSONL line, idempotent on disk error -----
print()
print("=" * 60)
print("Test 5: record_usage writes line, survives disk error")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    new = Path(td) / "director_usage.jsonl"
    saved_new = director.USAGE_LOG_PATH
    director.USAGE_LOG_PATH = new
    try:
        director.record_usage("claude-opus-4-7", {
            "input_tokens": 1000, "output_tokens": 500,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        })
        lines = new.read_text().strip().splitlines()
        _ok("one line written", len(lines) == 1)
        rec = json.loads(lines[0])
        _ok("model recorded", rec["model"] == "claude-opus-4-7")
        _ok("ts present", "ts" in rec and rec["ts"])
        _ok("cost_usd_est computed",
            isinstance(rec.get("cost_usd_est"), (int, float)) and rec["cost_usd_est"] > 0)
        _ok("input_tokens preserved", rec["input_tokens"] == 1000)
    finally:
        director.USAGE_LOG_PATH = saved_new

# Disk-error path: point USAGE_LOG_PATH to an unwritable location.
saved_new = director.USAGE_LOG_PATH
director.USAGE_LOG_PATH = Path("/dev/null/cannot/write/here.jsonl")
try:
    raised = False
    try:
        director.record_usage("claude-opus-4-7",
                              {"input_tokens": 1, "output_tokens": 1})
    except Exception:
        raised = True
    _ok("disk error swallowed (best-effort)", raised is False)
finally:
    director.USAGE_LOG_PATH = saved_new


# ----- Test 6: pre-flight cap gate raises before LLM call (mocked) -----
print()
print("=" * 60)
print("Test 6: pre-flight cap gate raises RuntimeError")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    new = Path(td) / "director_usage.jsonl"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new.write_text(
        json.dumps({"ts": f"{today}T10:00:00+00:00", "model": "claude-opus-4-7",
                    "cost_usd_est": 4.99}) + "\n"
    )
    saved_new = director.USAGE_LOG_PATH
    saved_legacy = director.OPUS_USAGE_PATH
    saved_cap = director.DAILY_USD_CAP
    director.USAGE_LOG_PATH = new
    director.OPUS_USAGE_PATH = Path(td) / "absent.jsonl"
    director.DAILY_USD_CAP = 5.0
    try:
        raised = None
        try:
            director.assert_under_daily_cap(estimate_usd=0.10)
        except RuntimeError as e:
            raised = e
        _ok("over-cap raises RuntimeError", raised is not None)
        _ok("error mentions cap value",
            raised is not None and "5" in str(raised))
        _ok("error mentions current spend",
            raised is not None and "4.99" in str(raised))
    finally:
        director.USAGE_LOG_PATH = saved_new
        director.OPUS_USAGE_PATH = saved_legacy
        director.DAILY_USD_CAP = saved_cap


# ----- Test 7: opus_quota_available extended to honor USD cap -----
print()
print("=" * 60)
print("Test 7: opus_quota_available respects USD cap")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    new = Path(td) / "director_usage.jsonl"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Spend exceeds cap.
    new.write_text(
        json.dumps({"ts": f"{today}T10:00:00+00:00", "model": "claude-opus-4-7",
                    "cost_usd_est": 6.00}) + "\n"
    )
    saved_new = director.USAGE_LOG_PATH
    saved_legacy = director.OPUS_USAGE_PATH
    saved_cap = director.DAILY_USD_CAP
    saved_key = director.OPUS_API_KEY
    saved_quota_path = director.OPUS_QUOTA_PATH
    director.USAGE_LOG_PATH = new
    director.OPUS_USAGE_PATH = Path(td) / "absent.jsonl"
    director.DAILY_USD_CAP = 5.0
    director.OPUS_API_KEY = "fake-key"  # so call-count branch alone would allow
    director.OPUS_QUOTA_PATH = Path(td) / "fake_quota.json"
    director.OPUS_QUOTA_PATH.write_text(json.dumps({"date": today, "used": 0}))
    try:
        _ok("opus blocked when USD spend exceeds cap",
            director.opus_quota_available() is False)
        director.DAILY_USD_CAP = 100.0  # high cap, only call-count would block
        _ok("opus available when both gates allow",
            director.opus_quota_available() is True)
    finally:
        director.USAGE_LOG_PATH = saved_new
        director.OPUS_USAGE_PATH = saved_legacy
        director.DAILY_USD_CAP = saved_cap
        director.OPUS_API_KEY = saved_key
        director.OPUS_QUOTA_PATH = saved_quota_path


# ----- Test 8: cmd_budget --json returns expected schema -----
print()
print("=" * 60)
print("Test 8: cmd_budget --json schema")
print("=" * 60)
import argparse
with tempfile.TemporaryDirectory() as td:
    new = Path(td) / "director_usage.jsonl"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new.write_text(
        json.dumps({"ts": f"{today}T10:00:00+00:00",
                    "model": "claude-opus-4-7", "cost_usd_est": 0.50}) + "\n"
        + json.dumps({"ts": f"{today}T11:00:00+00:00",
                     "model": "deepseek-ai/deepseek-v4-pro",
                     "cost_usd_est": 0.10}) + "\n"
    )
    saved_new = director.USAGE_LOG_PATH
    saved_legacy = director.OPUS_USAGE_PATH
    saved_cap = director.DAILY_USD_CAP
    director.USAGE_LOG_PATH = new
    director.OPUS_USAGE_PATH = Path(td) / "absent.jsonl"
    director.DAILY_USD_CAP = 5.0

    captured = io.StringIO()
    saved_stdout = sys.stdout
    sys.stdout = captured
    try:
        ns = argparse.Namespace(date=None, by_model=True, json=True)
        rc = director.cmd_budget(ns)
    finally:
        sys.stdout = saved_stdout
        director.USAGE_LOG_PATH = saved_new
        director.OPUS_USAGE_PATH = saved_legacy
        director.DAILY_USD_CAP = saved_cap

    out = captured.getvalue().strip()
    _ok("cmd_budget exit code 0", rc == 0)
    payload = None
    try:
        payload = json.loads(out)
    except Exception:
        payload = None
    _ok("output is valid JSON", payload is not None)
    if payload is not None:
        _ok("payload has 'date'", "date" in payload)
        _ok("payload has 'total_usd'", "total_usd" in payload)
        _ok(f"total_usd ≈ 0.60 (got {payload.get('total_usd')})",
            abs(payload.get("total_usd", 0.0) - 0.60) < 1e-4)
        _ok("payload has 'cap_usd' = 5.0", payload.get("cap_usd") == 5.0)
        _ok("payload has 'by_model'", "by_model" in payload)
        _ok("by_model includes opus", "claude-opus-4-7" in payload.get("by_model", {}))
        _ok("by_model includes deepseek",
            "deepseek-ai/deepseek-v4-pro" in payload.get("by_model", {}))


# ----- Test 9: every model in MODEL_PRICING has fixed-arithmetic invariants -----
print()
print("=" * 60)
print("Test 9: MODEL_PRICING table integrity (no zero rows for paid)")
print("=" * 60)
required_models = [
    "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
    "deepseek-ai/deepseek-v4-pro", "meta/llama-3.3-70b-instruct",
]
for m in required_models:
    _ok(f"{m} in MODEL_PRICING", m in director.MODEL_PRICING)
    if m in director.MODEL_PRICING:
        p = director.MODEL_PRICING[m]
        _ok(f"{m} has positive input price",
            p.get("input_per_million", 0) > 0)
        _ok(f"{m} has positive output price",
            p.get("output_per_million", 0) > 0)
