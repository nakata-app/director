# M2-T02: Critic auto-tighten

**Status:** open
**Owner:** Architect → Tester → Implementer → Reviewer
**Milestone:** M2
**Estimated touch:** ~80-150 lines director.py + 1 new test file
**Blast radius:** high (critic decides pass/fail, drives every downstream signal)

## Goal

Extend the auto-tighten loop to also rewrite the critic prompt under drift. The critic decides whether each task's output passes or fails. If the critic is too lenient, drift signals are starved of failure cases. If the critic is too strict, every run reports failure and the auto-tighten loop never converges.

Critic drift looks like: false-pass rate above threshold (when measured against fixture suite or disk-truth artifacts), false-fail rate above threshold, decision latency growing without information gain.

## Acceptance criteria

- A new function `score_critic_quality(critic_decisions, ground_truth_artifacts)` returns precision and recall against disk-truth.
  - Precision = correct PASS decisions / total PASS decisions.
  - Recall = correct PASS decisions / total tasks that actually produced the expected artifact byte-match.
- Critic drift gate fires when:
  - precision falls below 0.8 (too lenient: passing things that didn't deliver), OR
  - recall falls below 0.8 (too strict: failing things that did deliver).
- A new function `auto_tighten_critic(sample_critic_decisions, ground_truth)` calls the tightener with examples of false-pass and false-fail cases attached to the prompt rewrite request.
- Critic prompt is extracted to `critic_prompt.txt` (currently a Python constant in director.py).
- Sanity bounds: **800-1500 chars** on tightened critic prompt (revised from initial 150-1000, baseline `critic_prompt.txt` is ~1244c with mandatory `{brief}`/`{output}`/`{artifact_status}` placeholders, so 1000 ceiling was unrealistic).
- Each event mnemonics-recorded with `event=critic-tighten` and includes the precision/recall measurements that triggered it.
- Each event appended to `EVOLUTION_LOG.md`.

## Touched files

- `director.py`: extract CRITIC_PROMPT to file load, add scorer + tightener + drift gate.
- New: `critic_prompt.txt` (initial content from current constant).
- New: `tests/test_critic_drift.py` with at least 8 unit tests covering:
  - Precision/recall math on synthetic decision sets.
  - Drift gate firing on too-lenient and too-strict scenarios separately.
  - Tighten output passes sanity bounds.
  - Mixed-family enforcement at tightener call site.
- `docs/EVOLUTION_LOG.md`: append on first real tighten.
- `docs/ROADMAP.md`: M2 status update.

## Disciplines (DISCIPLINES.md)

- D1 anti-collapse: sanity bounds 800-1500 chars; refuse rewrites that drop the explicit decision criteria ("PASS only if artifact byte-matches" must remain) AND lose `{brief}`/`{output}`/`{artifact_status}` placeholders.
- D2 mixed-family: tightener family different from worker AND scorer families if practical.
- D3 disk truth wins: precision/recall ground-truth comes from disk artifact match, not from another LLM's opinion.
- D5 backup before write.
- D6 evolution log entry.

## Dangers (DANGERS.md)

- D1 collapse: critic could converge to "PASS" or "FAIL" defaults if the drift signal is one-sided. Bidirectional drift gate (precision AND recall) is the structural defense.
- D2 self-flattering: if the same model writes the critic prompt and is the critic, scoring its own decisions, the loop is corrupted. Enforce family separation strictly.
- D4 blind-spot ceiling: ground-truth requires `expected_content` artifacts to compare against. Tasks without expected_content cannot contribute to precision/recall. Document this in the metric.

## Definition of done

- All 8 unit tests pass.
- A live A/B run with a deliberately misconfigured critic (e.g. always-PASS injected for the test) triggers the precision drift gate.
- Tightener produces a valid replacement prompt that includes the disk-truth criterion.
- Backup file exists, atomic write succeeds.
- Mnemonics record present with precision/recall numbers.
- EVOLUTION_LOG entry appended.
- ROADMAP M2 status updated.

## Notes for Implementer

- Do not change the critic short-circuit logic (disk-truth bypasses critic for byte-match cases). Out of scope.
- Do not modify the critic prompt content directly; modify only via the auto-tighten path.
- Reuse the persona auto-tighten file structure as a template.

## Notes for Reviewer

- Verify that critic short-circuit (disk-truth) still fires when expected_content matches.
- Verify that drift detection now considers persona, decomposer (T01), and critic signals together. Ordering and priority must be deterministic.
- Verify that critic auto-tighten cannot be triggered by itself (tighten requires a baseline from a prior run, otherwise the precision/recall numerator is zero).
