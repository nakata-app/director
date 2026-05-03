# M3-T04: Per-domain drift signal tuning

**Status:** closed — 2026-05-03 02:35 (41/41 in domain-config test, cumulative 217/0 across 9 suites.)
**Owner:** Architect → Implementer → Reviewer
**Milestone:** M3
**Estimated touch:** ~40-80 lines director.py + 1 test file
**Blast radius:** medium (changes drift gate threshold semantics; affects tighten frequency)

## Goal

Today, drift gate thresholds are global constants. A security task tolerating a chatty output is not the same threshold as a refactor task tolerating any chattiness at all. Per-domain tuning lets each domain specify its own drift sensitivities so verbose-acceptable domains do not over-tighten and tight-discipline domains do not under-tighten.

## Acceptance criteria

- New file `domain_drift_config.json` (`{domain: {decomposer_score_floor, decomposer_literal_floor, critic_precision_floor, critic_recall_floor, persona_completion_drop_threshold, persona_log_inflation_threshold, persona_fidelity_floor}}`).
- Default values match the current global constants (so unset domains behave exactly as today).
- New function `domain_drift_thresholds(domain) -> dict` returns the per-domain config or default.
- Three tightener wrappers (`tighten_persona_if_drift`, `tighten_decomposer_if_drift`, `tighten_critic_if_drift`) read the relevant thresholds from this map by domain instead of from module-level constants.
- The threshold lookup is logged in mnemonics records so audit can tell which threshold set fired the event.

## Touched files

- New: `domain_drift_config.json`.
- `director.py`: add `domain_drift_thresholds`, replace constant lookups with config lookups in the three tightener wrappers.
- New: `tests/test_domain_drift_config.py` with at least 4 unit tests covering:
  - Default config returns global constants.
  - Per-domain override is read correctly.
  - Missing domain key falls back to default.
  - Malformed config falls back to default with a warning logged.

## Disciplines

- D4 drift gate: this ticket is the per-domain calibration of the gate.

## Dangers

- D1 collapse: a permissive domain config can let a tightener never fire, masking real drift. Mitigation: every override must be paired with a documented rationale in the config file (human-readable comment block at the top, even though JSON does not natively support comments, emulate with a `_doc` key per domain).

## Definition of done

- All 4 unit tests pass.
- Three domains (security, refactor, design) have explicit threshold sets in the config with `_doc` rationale.
- Three tightener wrappers route through the new lookup.
- A simulated event with a domain-specific override triggers (or suppresses) the gate as expected.

## Notes for Implementer

- Default thresholds must literally equal the current global constants. No silent migration of behavior.
- Config reload is on each tightener call (not cached at startup) so an operator can edit the file mid-run without restarting Director.
