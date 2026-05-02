# DISCIPLINES

Six non-negotiable disciplines. Each one closes a specific failure mode that recursive self-improvement systems are known to fall into. Skip any one and the whole loop loses its credibility.

## 1. Anti-collapse guards

**Failure mode this prevents:** A self-rewriting prompt collapses into a degenerate description that maximizes the metric while abandoning the intent. Example: if "shorter is better" becomes the implicit reward, the system rewrites the persona description to one word, then to nothing.

**Implemented (current):**
- Sanity bounds on tightened description length: 50 chars minimum, 500 chars maximum. Outside this range, tighten is rejected.
- Tighten is rejected if the new description equals the old.

**Planned:**
- Content-density check: count action verbs (yaz, kontrol et, ölç, denetle) versus adjectives in the new description. If the verb-to-adjective ratio falls below a threshold, reject.
- Baseline-distance ceiling: if Levenshtein distance between the new description and the original baseline (snapshot at repo init or a marked baseline) exceeds N percent of the baseline length, require explicit human approval before disk write.

## 2. Mixed-family scoring

**Failure mode this prevents:** Self-flattering scores. If the worker LLM and the scoring LLM come from the same model family (e.g. both DeepSeek), the scorer can systematically underestimate failure modes that the worker is also blind to. Output looks great by the scorer's lights and is in fact mediocre.

**Implemented (current):**
- Dual-scorer fidelity: V4 Pro and V4 Flash both produce independent scores. If the gap exceeds 2 points, mark consensus=False and surface the disagreement.

**Planned:**
- Compile-time check: at startup, validate that the worker backend's model family is different from at least one scorer's model family. Refuse to start otherwise.
- Tighten LLM family must differ from worker LLM family. A model is not allowed to rewrite a persona that targets its own family unsupervised.

## 3. Disk truth wins

**Failure mode this prevents:** LLM critic hallucinates a failure or success. Real artifacts on disk should override critic opinion when they unambiguously prove or disprove the brief.

**Implemented (current):**
- If the brief includes `expected_content: {path: literal_str}` and the file at that path byte-matches the literal (rstrip newline), the LLM critic is short-circuited to PASS. No ambiguity.

**Planned:**
- Extend disk-truth to FAIL: if expected_content is set and the file does not exist or does not match, short-circuit to FAIL even if the LLM critic claims success.

## 4. Drift gate

**Failure mode this prevents:** Auto-tighten thrashing. Without a drift signal, the system rewrites personas every run, even when behavior is fine. This burns tokens and introduces churn.

**Implemented (current):**
- Tighten only fires when at least one of the three drift signals crosses threshold:
  - completion drop (A vs B completion rate delta)
  - fidelity drop (A fidelity below floor)
  - log inflation (A log size grows above threshold relative to B)

**Planned:**
- Cost-of-tighten gate: if the projected cost of a tighten cycle exceeds a per-run budget, defer to next run.
- Persona-stability decay: if a specific persona has been tightened twice in 24 hours, lock it for human review.

## 5. Backup before write

**Failure mode this prevents:** A tighten produces a worse description than the original, and there is no way back.

**Implemented (current):**
- Every tighten produces a timestamped `personas.json.bak.YYYYMMDD-HHMMSS-pre-tighten` backup before disk write.
- Atomic write via `tmp + rename` to avoid partial corruption.

**Planned:**
- Auto-rollback: after a tightened persona is in use, if a fixture suite or production metric regresses beyond threshold, automatically restore from the most recent pre-tighten backup and log the rollback as an evolution event.
- Backup retention policy: keep the last N backups indexed in the evolution log, prune older ones with summary metadata preserved.

## 6. Public evolution log

**Failure mode this prevents:** The system's behavioral history is opaque, making it impossible for a new operator to understand why a persona looks the way it does today.

**Implemented (current):**
- Backup files exist on disk, ordered by timestamp.
- Mnemonics records every run end and every A/B verdict in `ns=director`.

**Planned:**
- `EVOLUTION_LOG.md` auto-generated from the backup history and mnemonics records, committed to the repo on each tighten event.
- Each evolution log entry includes: timestamp, persona id, drift signals, old description, new description, scorer disagreement (if any), rollback status.

## Discipline test

A new operator cloning this repo should be able to read these six disciplines and immediately understand the safety boundaries of the auto-tighten loop. If any of these can be skipped without consequence, the discipline is wrong, not the system. Tighten the discipline, do not relax it.
