# Embedding Director in a Downstream Product

Director is a Python module first, a CLI second. To run goals from your own Python service without spawning a subprocess, import the module and call its API directly.

## Install path

Director currently lives at `~/.claude/director/director.py`. Add the parent dir to your `sys.path` (or vendor the file into your project), then `import director`.

```python
import sys
sys.path.insert(0, "/path/to/.claude/director")
import director
```

A pip-installable package layout is on the M5 roadmap; until then, treat the source path as the install path.

## Public surface

Imported names (also listed in `director.__all__`):

| Name | Purpose |
|---|---|
| `run(goal, ...)` | Single-arm goal execution. Returns final state dict. |
| `ab(goal, ...)` | A/B persona-on vs persona-off comparison. Returns `{arms, run_ids, _exit_code}`. |
| `recover(run_id, ...)` | Settle stale 'running' tasks after a silent main-process death. |
| `tighten_persona_if_drift(signals, a_state, ...)` | M3-T04 persona drift gate + tighten + apply. |
| `tighten_critic_if_drift(decisions, ...)` | Critic prompt auto-tighten gate. |
| `tighten_decomposer_if_drift(goal, plan, ...)` | Decomposer prompt auto-tighten gate. |
| `tighten_persona_with_rollback(persona_id, sample_output, ...)` | Lower-level: tighten one persona with auto-rollback on regression. |
| `verify_artifacts(task, quick=False)` | Disk-truth artifact check. Use in your tests. |
| `load_state(rd)` | Read `state.json` for any run dir. |
| `RUNS_DIR` | `Path` to the runs directory. |
| `domain_drift_thresholds(domain)` | Read per-domain threshold set from `domain_drift_config.json`. |

## Minimum example: run a goal

```python
import director

state = director.run(
    "Refactor /tmp/mod.py to extract a pure helper for date parsing.",
    cwd="/tmp",
    yes=True,                  # required for non-interactive callers
    timeout_min=10,            # overrides per-task timeout
    retries=1,
    skip_critic=False,         # keep adversarial plan critique
    skip_task_critic=False,    # keep per-task output critic
)

print(state["_run_id"])
print(state["_exit_code"])     # 0 = success
for t in state["tasks"]:
    print(t["id"], t["status"], t.get("artifact_check"), t.get("critic"))
```

`state` is the same dict that `runs/<run_id>/state.json` holds, with two additions: `_run_id` and `_exit_code`. The original `state["run_id"]` is also present.

`yes=False` raises `ValueError`. Interactive plan editing is CLI-only.

## A/B comparison

```python
result = director.ab(
    "Audit /tmp/api.py for OWASP Top 10 issues.",
    cwd="/tmp",
    yes=True,
    auto_tighten=True,         # write tightened persona on detected drift
    domain="security",         # use security threshold set
    setup_cmd="cp fixture/api.py /tmp/api.py",  # restore fixture before each arm
)

a, b = result["arms"]          # ordered: A-persona-on, B-persona-off
a_passes = sum(1 for t in a["tasks"] if t.get("critic", {}).get("verdict") == "pass")
b_passes = sum(1 for t in b["tasks"] if t.get("critic", {}).get("verdict") == "pass")
fid_a = [t.get("persona_fidelity", {}).get("score", -1) for t in a["tasks"]]
fid_a = [s for s in fid_a if s >= 0]
print(f"A: {a_passes}/{len(a['tasks'])}, fidelity {sum(fid_a)/len(fid_a):.1f}/10" if fid_a else f"A: {a_passes}/{len(a['tasks'])}")
print(f"B: {b_passes}/{len(b['tasks'])}")
```

## Recovering a stale run

```python
final = director.recover("20260503-031234-abc123", force=False, force_kill=False)
for t in final["tasks"]:
    if t.get("recovered"):
        print(t["id"], t["status"], t["artifact_check"]["reason"])
```

`force=True` re-scans even if `state["finished"]` is set. `force_kill=True` SIGTERMs any PID that still looks alive.

## Embedding the drift gates standalone

The tightener functions are callable in isolation. A downstream product that runs its own A/B harness can borrow Director's gate logic without going through `ab()`:

```python
signals = {
    "completion_drop": 0.3,           # arm A failed 30% more than arm B
    "log_inflation": 1.2,             # A's logs are 1.2x B's
    "avg_fidelity": 4.5,              # below 7.0 floor for default domain
    "absolute_completion_fail": False,
    "a_fail_count": 2,
}
result = director.tighten_persona_if_drift(
    signals=signals,
    a_state=arm_a_state,              # dict with tasks[].persona_id and persona_fidelity
    sample_log_root=Path("/tmp/arm_a_logs"),
    dry_run=False,                    # write tightened persona to disk
    domain="security",
)
print(result["fired"], result["applied"], result["target_persona"], result["reasons"])
```

`dry_run=True` (the default) reports what would be tightened without writing.

## Environment overrides

Director reads these env vars at import time. Set them before the first `import director` call to make them effective.

| Env var | Default | Effect |
|---|---|---|
| `DIRECTOR_DAILY_USD_CAP` | unset (no cap) | Pre-flight gate raises when today's spend would exceed N USD. |
| `DIRECTOR_CHILD_BACKEND` | `metis` | `metis` = DeepSeek V4 Pro via Metis. `claude` = `claude -p` (note: bypasses persona injection). |
| `DIRECTOR_POLL_SEC` | `30` | Monitor poll interval. |
| `DIRECTOR_TASK_TIMEOUT_MIN` | `30` | Default per-task timeout when the plan does not specify one. |
| `DIRECTOR_HEARTBEAT_SEC` | `300` | Worker is declared a ghost when its log is silent for this long. `0` disables. |
| `OPUS_DAILY_CAP` | `4` | Decompose calls per day allowed on Opus 4.7 high. Falls back to V4 Pro then NIM after exhaustion. |
| `OPUS_QUOTA_PATH` | `opus_quota.json` | Persistent Opus call counter. |
| `DOMAIN_DRIFT_CONFIG_PATH` | `domain_drift_config.json` | Per-domain threshold sets. |
| `DIRECTOR_VOICE_ID` | `o7NAQIWE4USENtam8Vkx` (Mythos v2) | ElevenLabs voice for the spoken event narrator. |
| `ELEVENLABS_API_KEY` | unset (TTS off) | Enables Mythos voice; falls back to `say` if unset. |

Provider keys (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `NVIDIA_API_KEY`) are read from the environment using the standard names.

## Error handling

- `RuntimeError` from `run()` / `ab()`: the underlying `cmd_run` / `cmd_ab` did not produce the expected number of run dirs. Inspect `_exit_code` for hints (decompose error = 2).
- `ValueError` from `run()` / `ab()`: `yes=False` was passed (interactive mode requested in a library context).
- Catastrophic exceptions inside the monitor loop are caught and persisted to `state["main_error"]`. Caller should still wrap `run()` in `try/except` for import-level or argparse-level failures.

## State on disk

Every call leaves a fresh `runs/<run_id>/state.json` plus a `runs/<run_id>/logs/` tree. Treat `RUNS_DIR` as part of Director's persistence contract; do not delete it between calls if you depend on `recover()` or `mnemonics_record()` audit trails.

## Cost discipline

Each `run()` triggers at least one decompose (Opus or DeepSeek) plus per-task LLM invocations from the configured child backend. Each `ab()` doubles that. Set `DIRECTOR_DAILY_USD_CAP` in the embedding service's env, not just the operator's shell, so the pre-flight gate fires before billing surprises.

For test harnesses, use `tighten_*_if_drift(..., dry_run=True)` to exercise the gates without writing personas, and use synthetic state fixtures to skip the actual LLM round trips.
