# M2-T01: Decomposer auto-tighten

**Status:** open
**Owner:** Architect (this ticket) → Tester (writes failing test) → Implementer (writes code) → Reviewer (verdict)
**Milestone:** M2
**Estimated touch:** ~80-150 lines director.py + 1 new test file
**Blast radius:** medium (decomposer prompt is the entry point of every run)

## Goal

Extend the auto-tighten loop to also rewrite the decomposer prompt under drift. Today, only persona descriptions are tightened. The decomposer's system prompt is hand-written and never improves.

The decomposer is responsible for converting a goal into a typed DAG of tasks with briefs, dependencies, and `expected_content` artifacts. Decomposer drift looks like: tasks that paraphrase the goal instead of preserving its literals, task counts that vary wildly between runs of the same goal, briefs that omit the `expected_content` field, briefs that decompose into too few or too many tasks for the actual scope.

## Acceptance criteria

- A new function `score_decomposer_fidelity(goal, plan, baseline_metrics)` returns a 0-10 score on at least three signals:
  - Goal-literal preservation: count of distinct literal tokens from `goal` that appear in any task `brief` divided by total distinct literal tokens in goal.
  - `expected_content` field presence: ratio of tasks that include the field where the goal mentions a target file path.
  - Task-count stability: deviation from a rolling baseline of task counts for similar goals.
- Decomposer drift gate fires when the dual-scorer fidelity drops below 7 OR goal-literal preservation drops below 0.7.
- A new function `auto_tighten_decomposer(sample_goals_and_plans)` calls a mixed-family LLM (Llama 3.3 via NIM, since worker is DeepSeek) to rewrite the decomposer system prompt. Sanity bounds: **800-2400 chars** (revised from initial 200-1500, the existing baseline `decomposer_prompt.txt` is ~2285c, so 1500 ceiling was unrealistic; floor raised to 800 to prevent stub collapse).
- Decomposer prompt rewrites are persisted to a new file `decomposer_prompt.txt` (extracted from director.py constant), with timestamped backups in the same backup convention as personas.
- Each decomposer-tighten event is mnemonics-recorded with `event=decomposer-tighten`.
- Each event is appended to `EVOLUTION_LOG.md`.

## Touched files

- `director.py`: extract DECOMPOSE_PROMPT to file load, add scorer + tightener + drift gate.
- New: `decomposer_prompt.txt` (initial content from current DECOMPOSE_PROMPT constant).
- New: `tests/test_decomposer_drift.py` with at least 8 unit tests covering:
  - Goal-literal preservation math (full match, partial match, no match).
  - Task count deviation calculation.
  - Drift gate firing and suppression.
  - Tighten produces output within sanity bounds.
- `docs/EVOLUTION_LOG.md`: append entry on first real tighten.
- `docs/ROADMAP.md`: M2 acceptance progress check.

## Disciplines (DISCIPLINES.md)

- D1 anti-collapse: sanity bounds 800-2400 chars on tightened prompt; do not allow rewrites that drop below the floor.
- D2 mixed-family: tightener must use a model family different from the worker LLM family. If the project's worker is DeepSeek, tightener must be NIM Llama or Gemini Flash.
- D5 backup before write: timestamped backup of `decomposer_prompt.txt` before each rewrite.
- D6 public evolution log: append to `EVOLUTION_LOG.md` on every event.

## Dangers (DANGERS.md)

- D1 collapse: prompt could shrink toward "decompose this goal into tasks" with no structure. Sanity bounds + content-density check (must include the words `expected_content`, `depends_on`, `brief`).
- D4 blind-spot ceiling: the goal-literal preservation metric only catches verbatim drift, not semantic paraphrase. M3's fixture suite is the deeper net. Document this limitation in the metric's docstring.

## Definition of done

- All 8 unit tests pass.
- A live A/B run with deliberately paraphrasing-prone goal triggers the decomposer drift gate.
- Tightener produces a valid replacement prompt that passes JSON-schema parse on a test goal.
- Backup file exists, atomic write succeeds.
- Mnemonics record present.
- EVOLUTION_LOG entry appended.
- ROADMAP M2 status updated.

## Notes for Implementer

- Do not edit `personas.json` or persona logic in this ticket. Out of scope.
- Do not change the existing persona auto-tighten path. Only add the decomposer parallel path.
- Reuse the same backup-and-rollback convention. Same mnemonics recording style.
- The decomposer prompt is currently a Python constant; extracting it to a file is a prerequisite, not an optional refactor.

## Notes for Reviewer

- Verify that the existing persona auto-tighten path is unchanged (regression check).
- Verify that drift detection now looks at both persona AND decomposer signals; one should not mask the other.
- Verify that the mixed-family check is enforced at runtime (refuse to tighten if worker family == tightener family).
