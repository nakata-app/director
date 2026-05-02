# INHERITANCE

This document is the handoff contract for any new operator who picks up Director after the original maintainer has gone silent. Read this first if you are new.

## What you are inheriting

A single-file Python orchestrator that decomposes goals, dispatches work to persona-injected workers, scores outcomes, detects drift, and rewrites under-performing personas back to disk. Every change is backed up. Every run is logged. The system is designed to be operable, debuggable, and improvable by someone who has never spoken to the original maintainer.

## What to read in what order

1. **README.md** at the repo root: a one-page overview and quickstart.
2. **VISION.md** in this directory: what the system is, what it explicitly is not, why the position is what it is.
3. **ROADMAP.md** here: the four milestones, sequenced, with acceptance criteria. The current milestone status is the source of truth for "what to work on next".
4. **DISCIPLINES.md** here: six non-negotiable rules. If you find a rule violated, the violation is a bug, not a feature.
5. **DANGERS.md** here: the four known failure modes and the countermeasures mapped to each.
6. **PRIOR_ART.md** here: the precise map of what is borrowed and what is novel. If someone challenges the novelty claim, this document is the answer.
7. **EVOLUTION_LOG.md** here: chronological record of every persona rewrite and rollback event. Read recent entries to understand the current state of `personas.json`.
8. **OPERATOR.md** here: the runbook. Day-to-day operations, debugging stuck runs, manual rollback, fixture testing.

## How to verify the system is healthy

Run this checklist before making any changes:

1. `./director.py run -y "smoke test goal that produces a known artifact"` produces the expected artifact.
2. `./director.py ab -y "..."` completes both arms without leaving a stale state.json with status=running and no pid alive.
3. `personas.json` parses as valid JSON. Backup files (`personas.json.bak.*`) parse as valid JSON.
4. The most recent mnemonics record in `ns=director` was within the last expected run window. If the system has been silent for longer than its cron cadence, investigate.
5. `git log --oneline` shows a clean history; no "broken state" or "WIP" commits at HEAD.

## How to make changes safely

1. **Never edit `personas.json` by hand without a backup.** The auto-tighten path always backs up; manual edits should do the same.
2. **Never disable the drift gate to force a tighten.** If the gate is misfiring, fix the signal definition, not the gate.
3. **Never widen the sanity bounds without a corresponding fixture suite expansion.** The bounds are tied to the regression tests.
4. **Always commit the persona backup file alongside any auto-tighten event.** The evolution log depends on the backups being in git history.
5. **Always update EVOLUTION_LOG.md when an auto-tighten or rollback event occurs.** The log is the public contract.

## How to recover from a bad state

### Stale state.json (process died mid-run)
- `cat runs/<run-id>/state.json` to see which tasks are stuck in "running".
- Check `ps` for the pid. If dead, the run is abandoned. Either resume manually or accept the partial state and start a fresh run.
- Future automation will mark these as `abandoned` automatically with a heartbeat check.

### Bad auto-tighten (regression on fixture suite)
- Locate the most recent `personas.json.bak.*-pre-tighten` backup.
- Inspect the diff between current `personas.json` and the backup to identify which persona was tightened.
- Restore: `cp personas.json.bak.<timestamp>-pre-tighten personas.json`.
- Append a rollback entry to `EVOLUTION_LOG.md` with timestamp, persona id, reason for rollback, and reference to the original tighten event.
- Commit the rollback as a normal git commit so the history records both the tighten and the rollback.

### Quota exhaustion
- `cat opus_quota.json` to see current Opus usage versus cap.
- If exhausted, decompose falls back to V4 Pro (DeepSeek). Verify by reading the run log for fallback notices.
- Adjust cap via environment if the daily budget allows.

### Cost overrun
- `cat opus_usage.jsonl` for Opus cost record.
- Once `DIRECTOR_DAILY_USD_CAP` is implemented, the system will hard stop and log to mnemonics. Until then, monitor manually.

## What to ignore

- `tts_cache/`: ElevenLabs TTS audio files, regeneratable, safe to delete.
- `__pycache__/`: Python bytecode, regeneratable.
- `runs/`: per-run state and logs. Useful for debugging recent runs, safe to archive after retention period.
- `*.bak.*-pre-opus`: pre-Opus-migration backup of `director.py`, kept as a safety reference. Do not delete unless you are certain the current code is stable.

## What to escalate

- Any case where auto-tighten produces a description that passes sanity bounds but visibly drops fixture pass rate. This indicates the fixture suite is not catching what it should. Report and expand fixtures.
- Any case where dual-scorer disagreement is consistently large (>3 points across many runs). This indicates one of the scorer families has drifted; switch the scorer or upgrade.
- Any case where the cost estimate diverges from actual billing by more than 20%. The estimator needs recalibration.

## What success looks like for an inheriting operator

You can:
- Run Director from a fresh clone within an hour.
- Read EVOLUTION_LOG.md and explain why the current `personas.json` looks the way it does.
- Reproduce any past auto-tighten event by checking out the git commit before it.
- Add a new persona with a fixture suite without breaking any existing test.
- Roll back a bad tighten and document the rollback in under 15 minutes.

If you cannot do all of these, the inheritance contract is not yet complete. Open an issue describing the gap.

## Contact

If the original maintainer is reachable: hey@nakata.app. If not, the repo, the docs, and the evolution log are the authoritative source.
