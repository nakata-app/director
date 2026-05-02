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

**Status:** **CLOSED** as of 2026-05-02 22:57.
- Persona fidelity scoring: validated (8.0/10, n=4 in baseline run; observed quirk on full-failure A arm scoring 10/10, flagged for M2 investigation)
- Drift gate suppression (no drift case): validated (baseline run, drift below threshold, tighten correctly suppressed)
- Drift gate firing (synthetic case): validated (verbose injection produced comp_drop=1.00, tighten fired)
- Tighten + backup + write: validated (1223c → 247c, sanity bounds passed, atomic disk write, timestamped backup at personas.json.bak.20260502-225718-auto-tighten)
- Mnemonics record: code present, observed timeouts in mnemonics.log, needs robustness fix in M2

**Open issues to address before or during M2:**
- Fidelity scorer should weight completion rate. A 0/2 arm should not score 10/10.
- Mnemonics ingest timeouts on local MCP server need investigation; record is currently best-effort.
- Drift signal currently relies primarily on completion_drop. Add a completion-floor rule so arms that fail completely cannot pass drift detection by accident.

## M2: Three-layer self-improvement (persona + decomposer + critic)

**Goal:** Extend auto-tighten to also rewrite the decomposer prompt and the critic prompt under drift.

**Acceptance criteria:**
- Decomposer prompt drift signals defined (e.g. brief paraphrase rate, expected_content match rate, task count variance vs baseline).
- Critic prompt drift signals defined (e.g. false-pass rate measured against fixture suite, false-fail rate, decision latency).
- Both prompts have sanity-bound rewriting paths analogous to persona auto-tighten.
- Backup and rollback semantics match persona path.
- Composite drift detection across all three (persona + decomposer + critic) does not produce thrashing or oscillation.

**Status:** not started. Pending M1 closure.

## M3: Multi-domain with ground-truth fixture suites

**Goal:** Three domains (security, refactor, design), each with hand-curated fixture suites that validate auto-tighten output against known-good behavior. Auto-rollback if a tightened persona regresses on the fixture suite.

**Acceptance criteria:**
- For each of the three domains, at least 5 hand-labeled fixtures (input + expected behavior signature).
- Fixture runner: given a persona id and a set of fixtures, produces a pass/fail report with concrete signals (not LLM-judged).
- Auto-rollback: if a tightened persona's fixture pass rate drops below the previous version, restore from backup automatically and log the rollback.
- Per-domain drift signals can be tuned independently (security may tolerate more verbose output than refactor).

**Status:** not started.

## M4: Production embedding + community

**Goal:** Director runs as the worker layer under one or more downstream products, with a working contribution flow for external developers.

**Acceptance criteria:**
- At least one production product calls Director's run or ab interface as part of a real user flow.
- A second maintainer can land a non-trivial PR (new persona, new drift signal, new domain) without the original maintainer's intervention beyond review.
- Operator playbook (`OPERATOR.md`) is sufficient for a third party to debug a stuck run, audit an evolution event, and roll back a bad tighten.
- Cost cap and budget reporting are implemented and surfaced (`DIRECTOR_DAILY_USD_CAP`).

**Status:** not started.

## Sequencing rules

- M1 closure is a hard prerequisite for M2. Don't extend the auto-tighten surface before its base case is validated.
- M2 closure is a hard prerequisite for M3. Don't multiply domains before the meta-loop (persona + decomposer + critic) is stable on one domain.
- M3 closure is a hard prerequisite for M4. Don't ship to production before regression protection is in place.

## Anti-patterns to avoid

- "We'll fix it after launch." A drift signal that fires in production without rollback is worse than no drift signal at all.
- "It worked once." A single positive run is anecdote. Acceptance is over a fixture suite.
- "Let's add another domain first." Breadth before depth degrades the trust boundary of the whole system.
