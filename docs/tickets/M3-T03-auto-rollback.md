# M3-T03: Auto-rollback on fixture pass-rate regression

**Status:** open
**Owner:** Architect → Tester → Implementer → Reviewer
**Milestone:** M3
**Estimated touch:** ~80-130 lines director.py + 1 test file
**Blast radius:** **HIGH** (this is the closed-loop self-correction). Reviewer must be a different model family than Implementer.

## Goal

Wire the fixture suite's pass-rate signal to an automatic rollback decision. Today, M2-T04 lands the runner but it is observation only. A tightener can produce a regression and there is no automatic protection beyond manual operator review of EVOLUTION_LOG.

After T03, the rule is: when a tightener runs, the runner re-evaluates the affected domain's fixture suite before and after. If post-tighten pass rate drops below the pre-tighten pass rate, the system auto-rollbacks to the prior backup file and records the rollback as an `auto-rollback` event.

This is the M3 closure event. It is also the most dangerous single piece of code in the project so far, because a buggy auto-rollback either (1) prevents legitimate improvements from sticking or (2) accepts regressions silently.

## Acceptance criteria

- New function `fixture_baseline(domain) -> {fixture_id: status}` runs the suite once, stores the result keyed by current `personas.json` content hash, and returns the per-fixture status snapshot. Result cached on disk under `fixtures/<domain>/.baseline.json` so repeated calls without a tighten event are free.
- New function `fixture_compare(before, after) -> {improved, regressed, total, regression_signal}` computes per-fixture deltas and returns a structured comparison. `regression_signal` is `True` iff at least one fixture flipped from PASS to FAIL.
- New function `auto_rollback_persona(persona_id, backup_path)` atomically restores the persona description from the named backup, writes a backup of the regressed version (so the regressed state is auditable), records a mnemonics + EVOLUTION_LOG entry of kind `auto-rollback`.
- Wire `tighten_persona_if_drift` (the existing wrapper from M1) to:
  1. Snapshot fixture baseline for the affected domain before tightening.
  2. Apply the tightened description.
  3. Re-run the fixture suite for that domain.
  4. If `regression_signal` fires → auto-rollback + log.
  5. If no regression → keep tightened version + log.
- Same wiring applies to `tighten_decomposer_if_drift` and `tighten_critic_if_drift`. Each prompt has its own affected-domain set (decomposer affects all domains; critic affects all; persona affects its specific domain mapping).
- The auto-rollback path must be reentrancy-safe: a rollback that itself causes a regression must not loop. Hard cap: at most one rollback per tightener event.
- Each `auto-rollback` event mnemonics-recorded with the regressed-fixture ids and the restored backup file name.

## Touched files

- `director.py`: add baseline + compare + auto_rollback functions, modify three tightener wrappers, register the regression-signal short-circuit.
- New: `tests/test_auto_rollback.py` with at least 6 unit tests covering:
  - `fixture_compare` math on synthetic before/after maps (no regression, single regression, multiple regressions, mixed improvement+regression).
  - `auto_rollback_persona` atomicity: backup of regressed state is written before restoring prior backup.
  - Reentrancy cap: rollback that triggers another regression does not chain.
  - End-to-end: synthetic tighten event + regressed fixture set → auto-rollback fires + EVOLUTION_LOG entry.
  - Healthy tighten event (no regression) → no rollback.
- `docs/EVOLUTION_LOG.md`: append on first real auto-rollback.
- `docs/ROADMAP.md`: M3 closure update.

## Disciplines

- D1 anti-collapse: rollback decision is on disk-truth pass rate, not LLM judgement.
- D4 drift gate: this ticket is the wiring of the drift gate's most consequential branch.
- D5 backup before write: regressed state is itself backed up before restore.
- D6 evolution log: every auto-rollback is publicly logged.

## Dangers

- D1 collapse: rollback could mask legitimate improvements that happen to flake on a single fixture. Mitigation: regression signal requires at least one PASS→FAIL flip, not a metric drop. Flips are unambiguous; metric drops can be noise.
- D2 self-flattering: the same model that produces the tighten cannot produce the rollback decision. Rollback is purely structural (disk truth), no LLM consulted.
- D3 cost runaway: re-running the fixture suite per tighten event roughly doubles tighten cost. Mitigation: dry-run-director means assertion-only re-run, no LLM calls per fixture.

## Definition of done

- All 6 unit tests pass.
- A simulated tighten event with one regressed fixture triggers auto-rollback and writes the EVOLUTION_LOG entry.
- A simulated tighten event with no regression keeps the tightened version.
- `docs/ROADMAP.md` marks M3 closed.
- A reviewer in a different model family confirms the wiring is structurally correct and reentrancy-safe.

## Notes for Implementer

- Do not introduce a new global lock; reuse `MNEMONICS_FALLBACK_LOCK` if any cross-thread coordination is needed (highly unlikely, tighten events are sequential per the existing design).
- Do not change the existing tightener function signatures; add wiring as decorator or sequential pre/post hook within `tighten_*_if_drift`.
- Cache `fixture_baseline` result on disk so the cost of "no tighten happened, rerun the fixture suite" is zero.

## Notes for Reviewer

- Verify the regression signal is `at-least-one PASS→FAIL flip`, not a numeric pass-rate drop.
- Verify auto-rollback writes both: backup of regressed state, AND restoration of prior backup.
- Verify mixed-family rule still applies: tightener uses Llama, rollback decision uses no LLM, and the reviewer for this ticket should be a different family from whatever generated the tighten path code.
