# OPERATOR PLAYBOOK

Day-to-day runbook for running, debugging, and maintaining Director. Written for an operator who has read the README, VISION.md, and INHERITANCE.md and wants the practical commands.

## Daily operations

### Starting a run
```bash
cd ~/.claude/director
./director.py run -y "the goal in plain language; reference absolute paths; reference any required output files"
```

The `-y` flag auto-confirms the decomposition plan. Without it, the system pauses and asks for approval after the decompose step.

### Running an A/B comparison
```bash
./director.py ab -y "the goal"
```
Two arms run sequentially: A with persona injection on, B with persona off. The verdict is printed at the end.

### Running A/B with auto-tighten
```bash
./director.py ab -y --auto-tighten "the goal"
```
If drift is detected after both arms complete, the system rewrites the under-performing persona description and writes the new version to `personas.json` with a timestamped backup.

### Listing recent runs
```bash
ls -lat runs/ | head -20
```

### Inspecting a specific run
```bash
./director.py tail <run-id>
```
Equivalent to walking `runs/<run-id>/state.json` and `runs/<run-id>/logs/*.log` by hand.

### Status (state.json dump)
```bash
./director.py status                # most recent 10 runs, one line each
./director.py status <run-id>       # full JSON state
```
Use this when you want the structured state machine view (task statuses, PIDs,
exit codes, artifact_check, critic, persona_fidelity), not log text.

### Cancel a running run
```bash
./director.py cancel <run-id>
```
SIGTERMs every "running" task's process group, marks them `cancelled`,
seals the run's live-feed events. Use this to stop a runaway A/B mid-flight.

### Re-attach to a detached run
```bash
./director.py attach <run-id>
```
After Ctrl+C-detaching from `run`, the children keep going. `attach` re-binds
the foreground monitor loop so you see progress without restarting the run.
Hits the same monitor as the original, exit codes, artifact verification,
critic, fidelity scoring all behave identically.

### Recover stale state (M4 silent-death recovery)
```bash
./director.py recover <run-id>                 # default: skip live PIDs
./director.py recover <run-id> --force-kill    # SIGTERM live PIDs first
./director.py recover <run-id> --force         # re-scan even if finished
```
For runs where the main director process died unexpectedly (uncaught
exception, OOM, terminal kill) and `state.json` still shows tasks as
"running". `recover` reads disk truth: PID liveness + log heartbeat
(30s) + `verify_artifacts(quick=True)` and rewrites each stale task to
`done` (artifact pass) or `failed` (artifact missing/empty/mismatch).
Sets `t["recovered"] = True` and `state["finished"]` when terminal.

If a stale task's PID is still alive *and* its log grew within 30s, the
recover skips it (the run is genuinely still working). Use `--force-kill`
to override when you're sure the worker is wedged.

If the prior crash was caught by the monitor wrapper, `state["main_error"]`
and `state["main_error_at"]` are present; `recover` prints the first line
of the captured traceback before doing anything.

### Budget check (M4-T01)
```bash
./director.py budget                # today's USD vs DIRECTOR_DAILY_USD_CAP
./director.py budget --by-model     # per-model breakdown
./director.py budget --date 2026-05-02
./director.py budget --json         # machine-readable
```
Aggregates `opus_usage.jsonl` and any V4 Pro / NIM usage records under
the model pricing table in `director.py:MODEL_PRICING`. Pre-flight
gate (`assert_under_daily_cap`) blocks new spend once cap is reached.

## Debugging

### Run is stuck
1. Find the director pid: `ps aux | grep director.py | grep -v grep`.
2. If alive but not progressing, look at the most recent task log: `tail -50 runs/<run-id>/logs/<task-id>.log`. If the worker is waiting on an LLM call, the log's last activity timestamp will be far in the past.
3. If still genuinely working, leave it. The poll interval is bounded by `POLL_INTERVAL` plus per-task `timeout_min`; the monitor will eventually time out and retry or fail the task.
4. If the worker is wedged (alive PID, log unchanged for minutes), kill it: `./director.py cancel <run-id>`. Then `./director.py recover <run-id>` to settle the state.
5. If the director main pid is gone but `state.json` still shows tasks as `running`, the main process died. Run `./director.py recover <run-id>`. If a child PID is still alive but you're sure it's wedged, add `--force-kill`.
6. Stale runs are not harmless: they leak orphaned children and pollute `status` listings. Always recover or cancel.

### Director main process crashed (state.main_error)
The monitor loop catches catastrophic exceptions, writes the traceback to `state["main_error"]` + `state["main_error_at"]`, prints a `💥 Director main loop crashed` banner to stderr with a recover hint, and re-raises.

1. Identify the run: `./director.py status` (most-recent-10 listing). Crashed runs typically show `running` (no `finished`).
2. Read the captured error: `python3 -c "import json; print(json.load(open('runs/<run-id>/state.json'))['main_error'])"`. The first line is the exception type and message; the rest is the full traceback.
3. Recover state: `./director.py recover <run-id>`. This does not retry, it settles each stale task to `done` (artifact passed) or `failed` (artifact missing) so the run can be archived.
4. Diagnose the root cause (the traceback). If it's a Director bug, file it. If it's a transient (network, LLM provider 5xx), retry the original goal as a new run.
5. The `main_error` field stays in the run forever as audit trail; do not delete it from `state.json`.

### Tighten produced a bad description
1. Identify the most recent backup: `ls -lat personas.json.bak.*-pre-tighten | head -1`. Backups are timestamped and the suffix tells you which auto-tightener fired (`pre-tighten`, `pre-add`, `auto-tighten`).
2. Restore: `cp personas.json.bak.<timestamp>-pre-tighten personas.json`. Validate JSON immediately: `python3 -c "import json; json.load(open('personas.json'))"`.
3. Re-replay the fixture suite for the affected domain to confirm the rollback restores baseline behavior: `./director.py fixture run <domain>`. The pass-rate should match or beat the pre-tighten baseline. If it does not, you restored to the wrong backup, walk further back: `ls -lat personas.json.bak.*-pre-tighten | head -5`.
4. Append a rollback entry to `docs/EVOLUTION_LOG.md` with timestamp, persona id, what was tightened, why it was bad, what was restored, and the fixture pass-rate before/after.
5. Commit the rollback: `git add personas.json docs/EVOLUTION_LOG.md && git commit -m "rollback: persona <id> tighten regressed on <signal>"`.
6. If the M3-T03 auto-rollback wiring should have caught this and didn't, file it as a drift-threshold bug: the threshold for the offending domain is too lax. Tune via `domain_drift_config.json`.

### Audit an evolution event (who tightened what, when, why)
Every auto-tighten or auto-rollback emits three artifacts: a `personas.json.bak.*-{pre-tighten,auto-tighten}` file, an entry in `docs/EVOLUTION_LOG.md`, and a mnemonics record under `ns=director`.

1. Timeline reconstruction: `ls -lat personas.json.bak.* | head -20` shows every persona mutation in chronological order, with the suffix telling you the trigger.
2. What changed: `diff personas.json.bak.<earlier>-pre-tighten personas.json.bak.<later>-auto-tighten` shows the exact description rewrite.
3. Why it fired: `mnemonics retrieve --ns director "drift fired persona=<id>"` returns the run's drift signals (`comp_drop`, `fid`, `log_infl`) plus the threshold set used (per `domain_drift_config.json`).
4. Cross-check `docs/EVOLUTION_LOG.md` for the human-written reasoning if an operator added one.
5. If a tighten event is missing from `EVOLUTION_LOG.md` but a `*-auto-tighten` backup exists, that's a documentation gap. Backfill the log entry from the mnemonics record.

### Auto-tighten did not fire when it should have
1. Check the run's verdict output for the drift signal values: `comp_drop`, `fid`, `log_infl`.
2. Compare against the threshold in `director.py` (search for `drift` and `worst_score`).
3. If signals are below threshold but you believe they should fire, the threshold is wrong. Adjust in code with a corresponding test fixture that reproduces the case.
4. Never disable the gate to force a tighten. Fix the signal definition.

### Quota exhausted
1. `cat opus_quota.json` to confirm.
2. Decompose will fall back to V4 Pro (DeepSeek). Verify by checking the run log for the fallback notice.
3. To raise the cap (if billing allows): `OPUS_DAILY_CAP=8 ./director.py ab ...`.
4. Reset on next calendar day automatically.

### Cost runaway
1. `./director.py budget` for today's USD spend vs `DIRECTOR_DAILY_USD_CAP`. Add `--by-model` for the per-model breakdown (Opus / Sonnet / V4 Pro / NIM Llama).
2. The pre-flight gate (`assert_under_daily_cap`) hard-stops new spend once cap is reached and emits a record. So a true runaway means either the cap is too high or a model is missing from `MODEL_PRICING`.
3. Cap is unset by default. Set it: `export DIRECTOR_DAILY_USD_CAP=10` (USD). Persist in your shell rc.
4. Historical raw data: `cat opus_usage.jsonl | tail -20`. The `record_usage()` writer is best-effort; if disk is full or read-only, costs are not lost (the call still succeeds and is logged to stderr) but `budget` will under-report. Check disk health.
5. To dispute the budget output, dump `./director.py budget --json` and grep `opus_usage.jsonl` for the cited window.

## Adding a new persona

1. Decide the persona id (lowercase, no spaces).
2. Take a backup of `personas.json` first: `cp personas.json personas.json.bak.$(date +%Y%m%d-%H%M%S)-pre-add`.
3. Edit `personas.json` to add the new entry. Required fields: `name`, `trigger` (list of keyword strings), `description` (50-500 chars).
4. Validate JSON: `python3 -c "import json; json.load(open('personas.json'))"`.
5. Commit: `git add personas.json && git commit -m "add persona: <id>"`.
6. If you have fixture suite: add at least 5 fixtures for this persona before relying on auto-tighten for it. Without fixtures, regression detection is impossible.

## Adding a new domain

A domain in Director's vocabulary is a topic area that has its own persona, its own fixture suite, and (eventually) its own drift thresholds.

1. Define the domain in `docs/domains/<name>.md`: scope, target user, what success looks like.
2. Create or assign personas: in most cases, one persona per domain plus shared general-purpose personas.
3. Create fixture directory: `fixtures/<domain>/`. Each fixture is a goal + expected artifact + expected behavior signature.
4. Run smoke test: a single `run` of the domain's primary use case.
5. Run A/B without auto-tighten to establish baseline metrics.
6. After 3-5 runs of stable behavior, enable auto-tighten with explicit drift thresholds tuned to the domain.

## Cron and unattended operation

M3 closed 2026-05-03 (auto-rollback wired against fixture pass-rate, per-domain drift thresholds). M4-T01 closed (`DIRECTOR_DAILY_USD_CAP` enforced). Unattended operation is now safe **per domain that has a fixture suite**.

Cron pattern:
```cron
# every 6 hours, run A/B against the canonical fixture suite for security
0 */6 * * * cd /Users/macmini/.claude/director && DIRECTOR_DAILY_USD_CAP=10 ./director.py ab -y --auto-tighten --domain security "$(cat fixtures/security/canonical.goal)" >> /var/log/director-cron.log 2>&1
```

Per-domain rules:
- A domain may run unattended only if `fixtures/<domain>/` has at least 5 fixtures and `domain_drift_config.json` defines its threshold set.
- `DIRECTOR_DAILY_USD_CAP` must be set in the cron environment, not just the operator's shell.
- Use `--domain <name>` so the right threshold set fires; without it, defaults apply and may miss domain-specific drift.
- Pipe stdout+stderr to a log file; the monitor's `💥` banner only helps if it lands somewhere.

Health-check the cron weekly: `grep -E "rollback|crash|cap" /var/log/director-cron.log | tail`.

## Health checks

Run weekly:

```bash
# 1. Runs are completing
ls -lat runs/ | head -5

# 2. State files are not stale (no "running" with dead pid). Recover any hits.
for r in $(ls -t runs/ | head -10); do
  python3 -c "
import json, os
s = json.load(open('runs/$r/state.json'))
running = [t for t in s.get('tasks',[]) if t.get('status')=='running']
if running:
    pid_status = []
    for t in running:
        pid = t.get('pid')
        if pid:
            try: os.kill(pid, 0); alive = 'alive'
            except (ProcessLookupError, PermissionError): alive = 'dead'
        else:
            alive = 'no-pid'
        pid_status.append(f\"{t['id']}/pid={pid}/{alive}\")
    print('STALE:', '$r', pid_status)
"
done
# For any STALE row with all 'dead' PIDs: ./director.py recover <run-id>

# 3. Tighten events are documented in EVOLUTION_LOG.md
diff <(ls personas.json.bak.*-auto-tighten 2>/dev/null | wc -l) <(grep -c '^## auto-tighten' docs/EVOLUTION_LOG.md 2>/dev/null || echo 0)

# 4. Mnemonics is recording
mnemonics retrieve --ns director "recent run" | head

# 5. Cost is within bounds (M4-T01)
./director.py budget --by-model

# 6. No silent crashes pending recovery
for r in $(ls -t runs/ | head -10); do
  python3 -c "
import json
s = json.load(open('runs/$r/state.json'))
if s.get('main_error') and not s.get('recovered_at'):
    print('UNRECOVERED CRASH:', '$r', s['main_error'].splitlines()[0])
"
done
```

If any check fails, investigate before the next run.

## Running the fixture suite (M2-T04)

Fixtures live under `fixtures/<domain>/<fixture_id>/` (see `fixtures/README.md` for layout). Each fixture brings its own `goal.txt`, `expected.json` (structural assertions), `metadata.yaml`, optional `setup.sh`, and optional `input/` directory. The runner reads disk artifacts and applies the assertions; no LLM is consulted (D3 disk-truth wins).

```bash
./director.py fixture run security
./director.py fixture run security --persona security
```

Output is a per-fixture table (id, status, latency, reason) plus aggregate totals. Exit code is `0` when every fixture passes, `1` otherwise.

As of M3-T03 the wiring is in place: when an `ab --auto-tighten` run produces a tightened persona that subsequently regresses fixture pass-rate past the per-domain threshold (`domain_drift_config.json`), the tighten is automatically reverted, a `*-auto-rollback` backup is written, and an `auto-rollback` entry lands in both `docs/EVOLUTION_LOG.md` and the mnemonics record (ns=director). The standalone `./director.py fixture run <domain>` command itself is observation only: it reports pass/fail and exits, no tighten or rollback fires from a direct fixture invocation.

## Mnemonics replay (M2-T03)

Mnemonics ingest is best-effort. If the local MCP server is slow or down, the record is retried up to 3 times with exponential backoff (1s, 3s, 9s). All four attempts failing means the record gets queued in `mnemonics.fallback.jsonl` instead of being silently dropped, and a `FALLBACK` line is written to `mnemonics.log`.

To replay queued fallback records once the server is healthy again:

```bash
./director.py mnemonics-replay
```

The command reads every line in `mnemonics.fallback.jsonl`, retries ingest, and prunes successful entries. Failed entries stay in the file for the next replay attempt. Exit code is `0` when everything ingests, `1` when records remain queued.

Run this whenever:
- `mnemonics.log` shows recent `FALLBACK` lines.
- `mnemonics.fallback.jsonl` exists at top of the director repo.
- A weekly health check shows missing run summaries in mnemonics retrieval.

Do not run automatically on every cron tick, replay is operator-triggered so a stuck mnemonics server can't cause a runaway retry loop in the background.

## Emergency stop

If Director is doing something destructive (e.g. tightening every persona on every run, runaway cost):

```bash
# Kill all director processes
pkill -f "director.py"

# Restore personas from a known-good backup
ls personas.json.bak.*-SAFE-* 2>/dev/null
# pick a baseline and restore
cp personas.json.bak.SAFE-baseline-<date> personas.json

# Disable auto-tighten by removing the flag from any cron entries
crontab -e

# Document in EVOLUTION_LOG.md and mnemonics
```

The SAFE-baseline backups are operator-created snapshots taken before any major experiment. Maintain at least one current SAFE-baseline at all times.
