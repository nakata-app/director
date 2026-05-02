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

## Debugging

### Run is stuck
1. Find the pid: `ps aux | grep director.py | grep -v grep`.
2. If a pid is alive but not progressing, check the most recent task log: `tail -50 runs/<run-id>/logs/task-N.log`.
3. If the worker is waiting on an LLM call, the log will show the last activity timestamp far in the past.
4. If the pid is dead and `state.json` still shows tasks as "running", the process died silently. The state is stale.
5. Mark the run as abandoned manually by editing `state.json` (or simply ignore and start fresh; the abandoned run is harmless).

### Tighten produced a bad description
1. Identify the most recent backup: `ls -lat personas.json.bak.*-pre-tighten | head -1`.
2. Restore: `cp personas.json.bak.<timestamp>-pre-tighten personas.json`.
3. Append a rollback entry to `docs/EVOLUTION_LOG.md` with timestamp, persona id, what was tightened, why it was bad, what was restored.
4. Commit the rollback: `git add personas.json docs/EVOLUTION_LOG.md && git commit -m "rollback: persona <id> tighten regressed on <signal>"`.

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
1. `cat opus_usage.jsonl | tail -20` for recent Opus runs.
2. V4 Pro and NIM costs are not yet tracked. Estimate from the provider dashboard.
3. Once `DIRECTOR_DAILY_USD_CAP` is implemented, the system will hard stop and log the event. Until then, monitor manually.

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

Once M3 is closed and fixture-based auto-rollback is in place, the system can run unattended. Until then, all auto-tighten runs should be observed by an operator.

Planned cron pattern (post-M3):
```cron
# every 6 hours, run A/B against the canonical fixture suite for each domain
0 */6 * * * cd /Users/macmini/.claude/director && ./director.py ab -y --auto-tighten "$(cat fixtures/security/canonical.goal)" >> /var/log/director-cron.log 2>&1
```

Do not enable this cron until:
- Fixture suites exist for each domain that runs.
- Auto-rollback on fixture regression is implemented.
- `DIRECTOR_DAILY_USD_CAP` is implemented.
- Mnemonics record verifies that previous runs were healthy.

## Health checks

Run weekly until M4:

```bash
# 1. Runs are completing
ls -lat runs/ | head -5

# 2. State files are not stale (no "running" with dead pid)
for r in $(ls -t runs/ | head -10); do
  python3 -c "
import json
s = json.load(open('runs/$r/state.json'))
running = [t['id'] for t in s.get('tasks',[]) if t.get('status')=='running']
if running:
    print('STALE:', '$r', running)
"
done

# 3. Tighten events are documented in EVOLUTION_LOG.md
diff <(ls personas.json.bak.*-auto-tighten 2>/dev/null | wc -l) <(grep -c '^## auto-tighten' docs/EVOLUTION_LOG.md 2>/dev/null || echo 0)

# 4. Mnemonics is recording
mnemonics retrieve --ns director "recent run" | head

# 5. Cost is within bounds
cat opus_usage.jsonl | python3 -c "
import json, sys, datetime
today = datetime.date.today().isoformat()
total = sum(float(json.loads(l)['cost_usd_est']) for l in sys.stdin if today in l)
print(f'Today Opus est: \${total:.2f}')
"
```

If any check fails, investigate before the next run.

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
