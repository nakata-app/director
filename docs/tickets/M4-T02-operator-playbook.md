# M4-T02: Operator playbook expansion

**Status:** closed, 2026-05-03 (OPERATOR.md +6 sections: status/cancel/attach/recover/budget commands, crash recovery via state.main_error, evolution audit, fixture re-replay during rollback, M3-aware cron, weekly health check. Bonus: silent-death fix + `director recover RUN_ID` in 97f2556.)
**Owner:** Architect → Implementer → Reviewer
**Milestone:** M4
**Blast radius:** low (docs-only + recover command)

## Goal

Ensure a third party can use OPERATOR.md alone to: debug a stuck run, audit an evolution event, and roll back a bad tighten.

## Acceptance criteria

- `director status`, `director cancel`, `director attach`, `director recover` documented end-to-end.
- Crash recovery path (`state.main_error`) documented.
- Evolution audit (EVOLUTION_LOG.md) walkthrough present.
- Fixture re-replay during rollback documented.
- Weekly health-check cron example present.
