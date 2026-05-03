# ROADMAP

Four milestones, sequenced. Each milestone has explicit acceptance criteria. Skip none.

## M1: Single-domain auto-tighten validated end-to-end

**Goal:** Prove that the auto-tighten loop fires correctly under drift, rewrites a persona, and persists the rewrite to disk with backup, all without regression on the same domain's fixture set.

**Acceptance criteria:**
- A/B run on one domain (security audit) produces measurable persona-on vs persona-off differences in fidelity, completion, and log size.
- Drift gate correctly suppresses tighten when drift signals are below threshold.
- Drift gate correctly fires tighten when drift signals are above threshold (synthetic verbose persona test).
- Tighten produces a description that is shorter, retains intent, passes sanity bounds (50-500 chars).
- Persona backup is written with timestamp before disk overwrite.
- Mnemonics record captures drift signals, old length, new length, persona id.

**Status:** **CLOSED** as of 2026-05-02 22:57; reinforced 23:14 with completion-weighted fidelity, falsy-coalesce fix, and absolute completion-floor.
- Persona fidelity scoring: validated (8.0/10, n=4 in baseline run; observed quirk on full-failure A arm scoring 10/10, flagged for M2 investigation)
- Drift gate suppression (no drift case): validated (baseline run, drift below threshold, tighten correctly suppressed)
- Drift gate firing (synthetic case): validated (verbose injection produced comp_drop=1.00, tighten fired)
- Tighten + backup + write: validated (1223c → 247c, sanity bounds passed, atomic disk write, timestamped backup at personas.json.bak.20260502-225718-auto-tighten)
- Mnemonics record: code present, observed timeouts in mnemonics.log, needs robustness fix in M2

**Open issues addressed in M1 reinforcement (2026-05-02 23:14):**
- Issue 1 FIXED: Fidelity scoring is now completion-weighted. avg_fidelity = raw_fidelity * (done/total). A 0/N arm cannot score above 0. raw fidelity preserved for diagnostics.
- Issue 1b FIXED: The `or 10` falsy-coalesce trap that silently promoted a legitimate fidelity=0 to 10 in drift detection is replaced with explicit `is None` check.
- Issue 3 FIXED: Drift detection adds an absolute completion-floor rule. If A done_ratio < 0.5 and A has more than one task, drift fires unconditionally regardless of B-arm outcome. Catches the both-arms-fail-but-A-failed-worse case.
- Verified by 10 unit tests in /tmp/test_director_fixes.py covering baseline, synthetic verbose, both-arms-broken, partial-completion, small-task-count edge case, and the legitimate-fid-0 trap.

**Deferred to M2:**
- Issue 2: Mnemonics ingest timeouts on local MCP server. Best-effort recording today; needs robustness fix in M2 (retry, fallback to disk log, async queue).

## M2: Three-layer self-improvement (persona + decomposer + critic)

**Goal:** Extend auto-tighten to also rewrite the decomposer prompt and the critic prompt under drift.

**Acceptance criteria:**
- Decomposer prompt drift signals defined (e.g. brief paraphrase rate, expected_content match rate, task count variance vs baseline).
- Critic prompt drift signals defined (e.g. false-pass rate measured against fixture suite, false-fail rate, decision latency).
- Both prompts have sanity-bound rewriting paths analogous to persona auto-tighten.
- Backup and rollback semantics match persona path.
- Composite drift detection across all three (persona + decomposer + critic) does not produce thrashing or oscillation.

**Status:** **CLOSED** as of 2026-05-03 00:35. T01 (decomposer auto-tighten) landed 2026-05-02 23:30: drift signals + scorer + gate + Llama-3.3 mixed-family tightener + atomic apply + backup + wrapper, 8/8 unit tests green, live A/B verified. T02 (critic auto-tighten) landed 2026-05-02 23:55: precision/recall against disk-truth + bidirectional gate + Llama tightener + density + placeholder preservation + wrapper, 21/21 sub-assertions green, live A/B verified. T03 (mnemonics-robustness) landed 2026-05-03 00:15: retry+backoff (1s/3s/9s, 13s budget) + JSONL fallback + thread-safe append + replay CLI + OPERATOR.md doc, 18/18 sub-assertions green. T04 (fixture-scaffold, M3 prerequisite) landed 2026-05-03 00:35: 3-type disk-truth assertion evaluator + run_fixture + suite runner + CLI + 5 hand-curated security fixtures + fixtures/README.md, 15/15 sub-assertions green, all 5 fixtures pass live.

## M3: Multi-domain with ground-truth fixture suites

**Goal:** Three domains (security, refactor, design), each with hand-curated fixture suites that validate auto-tighten output against known-good behavior. Auto-rollback if a tightened persona regresses on the fixture suite.

**Acceptance criteria:**
- For each of the three domains, at least 5 hand-labeled fixtures (input + expected behavior signature).
- Fixture runner: given a persona id and a set of fixtures, produces a pass/fail report with concrete signals (not LLM-judged).
- Auto-rollback: if a tightened persona's fixture pass rate drops below the previous version, restore from backup automatically and log the rollback.
- Per-domain drift signals can be tuned independently (security may tolerate more verbose output than refactor).

**Status:** **CLOSED** as of 2026-05-03 02:35. Security (5 fixtures) landed in M2-T04. T01 (refactor, 5 fixtures + dedicated test) landed 2026-05-03 01:00, 35/35 sub-assertion green. T02 (design, 5 fixtures + dedicated test) landed 2026-05-03 01:25, 38/38 sub-assertion green. T03 (auto-rollback wiring, HIGH blast radius) landed 2026-05-03 01:50, 19/19 sub-assertion green, live simulation passed for both healthy and synthetic-regression scenarios, cross-family Llama 3.3 review verdict PASS on all five acceptance rules. T04 (per-domain drift tuning) landed 2026-05-03 02:35 across two commits: c4052dc (helper + drift_fires signatures) + ddd522f follow-up (full DoD: three tightener wrappers reading per-domain thresholds, mnemonics audit log per fired event with domain + threshold set, cmd_ab inline floors replaced by tighten_persona_if_drift wrapper, `director ab --domain <name>` CLI). 41/41 in domain-config test, cumulative 217/0 across 9 suites. Llama 3.3 review verdict PASS, RISKS none on both commits.

## M4: Production embedding + community

**Goal:** Director runs as the worker layer under one or more downstream products, with a working contribution flow for external developers.

**Acceptance criteria:**
- At least one production product calls Director's run or ab interface as part of a real user flow.
- A second maintainer can land a non-trivial PR (new persona, new drift signal, new domain) without the original maintainer's intervention beyond review.
- Operator playbook (`OPERATOR.md`) is sufficient for a third party to debug a stuck run, audit an evolution event, and roll back a bad tighten.
- Cost cap and budget reporting are implemented and surfaced (`DIRECTOR_DAILY_USD_CAP`).

**Status:** all four code-side tickets closed 2026-05-03. M4 acceptance has two external dependencies left (a downstream product calling `run`/`ab` in a real user flow, and a non-original maintainer landing a non-trivial PR) before the milestone itself can flip to **Closed**.

**Ticket sequencing for M4 (chosen 2026-05-03):**
- ~~T01: Daily USD cap + budget reporting (`DIRECTOR_DAILY_USD_CAP`).~~ **Closed** in 30dda10.
- ~~T02: Operator playbook expansion (debug stuck run, audit evolution event, roll back a bad tighten via fixture re-replay).~~ **Closed** 2026-05-03 (OPERATOR.md grew six new sections covering status/cancel/attach/recover/budget commands, crash recovery via `state.main_error`, evolution audit, fixture re-replay during rollback, M3-aware cron, and an updated weekly health check). Bonus: silent-death fix + `director recover RUN_ID` shipped in 97f2556.
- ~~T03: Programmatic Director API (library entry points for `run` / `ab` / `tighten_*_if_drift`) so a downstream product can embed Director without subprocess.~~ **Closed** 2026-05-03 (`run`, `ab`, `recover` wrappers in `director.py`; `__all__` exports `tighten_*_if_drift` plus disk-truth helpers; `docs/EMBEDDING.md` covers install path, public surface, examples, env reference, error contract).
- ~~T04: External contributor flow, persona/signal/domain authoring guide + a sample PR that a fresh contributor can land end-to-end.~~ **Closed** 2026-05-03 (`CONTRIBUTING.md` top-level workflow; `.github/PULL_REQUEST_TEMPLATE.md` checklist; `docs/AUTHORING.md` deep how-to for persona / domain / signal additions; `docs/SAMPLE_CONTRIBUTION.md` end-to-end walkthrough adding a new `docs` domain).

## Sequencing rules

- M1 closure is a hard prerequisite for M2. Don't extend the auto-tighten surface before its base case is validated.
- M2 closure is a hard prerequisite for M3. Don't multiply domains before the meta-loop (persona + decomposer + critic) is stable on one domain.
- M3 closure is a hard prerequisite for M4. Don't ship to production before regression protection is in place.

## Anti-patterns to avoid

- "We'll fix it after launch." A drift signal that fires in production without rollback is worse than no drift signal at all.
- "It worked once." A single positive run is anecdote. Acceptance is over a fixture suite.
- "Let's add another domain first." Breadth before depth degrades the trust boundary of the whole system.
