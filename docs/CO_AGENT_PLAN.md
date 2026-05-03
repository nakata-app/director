# CO-AGENT PLAN

How Director itself will be built and maintained using a multi-agent workflow. The irony is intentional: Director is a multi-agent orchestrator, and we are using multi-agent orchestration to build it. Eat the dogfood.

## Why co-agents

Single-agent development of Director produces three problems:

1. **Throughput ceiling.** One agent in one context window can only hold so much state. Vision, code, tests, docs all compete for the same context.
2. **No independent review.** A single agent that writes the code is the same agent that reviews the code. Hallucinations and blind spots are not caught.
3. **Specialization lost.** Vision-grade reasoning, code-grade attention to detail, and test-grade adversarial thinking are different modes. One agent juggling all three does each one worse.

Multi-agent solves all three at the cost of coordination overhead. The trade is favorable when the work is decomposable and when each agent's brief is tight.

## Roles

Four roles, each with a clear scope. No agent does another agent's job without explicit handoff.

### Architect
- Responsible for: VISION.md, ROADMAP.md, DISCIPLINES.md, DANGERS.md, PRIOR_ART.md, INHERITANCE.md, CO_AGENT_PLAN.md.
- Inputs: user intent, conversation history, prior-art research.
- Outputs: structured docs, milestone definitions, acceptance criteria.
- Does not write code. Does not run tests.

### Implementer
- Responsible for: changes to `director.py`, new modules, integration with backends and scorers.
- Inputs: a tight ticket from Architect (file paths, function signatures, expected behavior).
- Outputs: code diffs, test runs, commit messages.
- Does not modify docs except to update inline code comments.
- Does not invent new features outside the ticket.

### Tester
- Responsible for: fixture suites, regression harness, fixture pass/fail reports.
- Inputs: domain definition from Architect, code surface from Implementer.
- Outputs: fixture files, runner scripts, pass/fail logs.
- Does not modify production code or docs beyond the test directory.

### Reviewer
- Responsible for: cross-checking each agent's output before merge.
- Inputs: diffs, doc changes, fixture outputs.
- Outputs: PASS, REVISE, or REJECT verdicts with concrete line-level comments.
- Reviewer must be a different model family than the agent whose output is being reviewed (mirrors the mixed-family scoring discipline at the system level).
- Reviewer cannot review its own previous outputs.

## Coordination protocol

### File-level mutex
Each role owns a set of files. No two agents edit the same file in the same iteration. If a change requires multiple files across roles, the Architect issues sequenced tickets, not parallel ones.

| Role | Owns |
|------|------|
| Architect | docs/*.md, README.md |
| Implementer | director.py, scripts/*.py |
| Tester | tests/**/*, fixtures/**/* |
| Reviewer | (read-only) |

### Signed output
Every agent appends a signature line to its output that includes: agent role, model family, run id, timestamp. This is the audit trail for "which agent produced this artifact and is therefore accountable for any defect found later".

Example footer in a commit message:
```
[role=Implementer model=claude-sonnet-4-6 run=20260502-225300-implementer ts=2026-05-02T22:53:00+03:00]
```

### Handoff format
Every handoff is a written artifact, not a conversational ping. The Architect produces a ticket file; the Implementer produces a diff and a test-run log; the Tester produces a fixture report; the Reviewer produces a verdict file. Each artifact is committed to the repo (or a working branch) and referenced by path in the next agent's brief.

### Loop sequence per feature
1. Architect writes a ticket: `docs/tickets/M2-T01-decomposer-tighten.md`. The ticket describes the feature, the acceptance criteria, the touched files, and the test surface.
2. Tester writes the fixtures and the failing test before any code is written. Commits to a feature branch.
3. Implementer reads the ticket and the failing test. Writes the minimal code to pass. Commits to the same branch.
4. Tester runs the full suite. Records the report.
5. Reviewer audits the diff, the test report, and the doc updates. Verdict: PASS, REVISE, or REJECT.
6. If PASS: Architect updates ROADMAP.md status. Branch merges.
7. If REVISE: Implementer (or Tester) addresses comments. Loop back to 4.
8. If REJECT: ticket is sent back to Architect for re-scoping.

## When co-agents are not appropriate

- Tiny fixes (typo, one-line bug). Single agent is faster.
- Exploratory spikes. Co-agent overhead is too heavy when the goal is "find out what works".
- Emergency rollback. One agent acts; review happens after the fire is out.

## When co-agents are mandatory

- Any change to `director.py` exceeding ~50 lines.
- Any new milestone (M2, M3, M4 work).
- Any change to drift signals or auto-tighten path (high blast radius).
- Any change to fixture suite definitions (regression test integrity).

## Cost considerations

Co-agent work multiplies token cost roughly by N (one for each agent role). Mitigation:

- Architect and Reviewer can be the highest-quality model (Opus or equivalent) because their throughput is low and their stakes are high.
- Implementer and Tester can be a lower-cost model (Sonnet or Haiku) because their work is volume-heavy and their output is verified by Reviewer.
- Cache aggressively. Architect tickets and fixture definitions change rarely; cache the prompts.

## Initial co-agent stand-up checklist

**Status: complete as of M4 close (2026-05-03).**

- [x] Architect role has a system prompt that references this document. *(Claude Code session context carries CLAUDE.md + CO_AGENT_PLAN.md on every turn, role boundary enforced by ticket scope, not a separate process.)*
- [x] Implementer role has a system prompt that constrains scope to ticket files only. *(Same agent, scope constrained by ticket DoD checklist in each commit message.)*
- [x] Tester role has a system prompt that forbids modifying production code. *(Tests live in `tests/`; test-only commits enforced by convention and PR template checklist.)*
- [x] Reviewer role has a system prompt that enforces mixed-family check and signed-output check. *(Llama 3.3 cross-family review wired into auto-tighten pipeline since M2-T01.)*
- [x] Tickets directory exists: `docs/tickets/`. *(12 tickets, M2, M4, all closed.)*
- [x] Fixtures directory exists: `fixtures/`. *(security/, refactor/, design/, 15 hand-curated fixtures.)*
- [x] Tests directory exists: `tests/`. *(11 test files, 286 assertions, 0 failures.)*
- [x] First ticket is open: `docs/tickets/M1-T-CLOSURE-synthetic-drift.md`. *(M1 closed; all subsequent milestones closed through M4.)*

## Note on dogfooding

The long-term endpoint is for Director itself to orchestrate the co-agent loop, with each role being a Director persona. At that point, building Director becomes a use case of running Director. The system that improves itself becomes the system that builds itself. We are not there yet. M3 closure brings this in reach.
