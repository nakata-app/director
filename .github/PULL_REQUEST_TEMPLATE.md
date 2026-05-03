<!--
PR title: <scope>: <imperative summary>
e.g. "persona: add auditor", "domain: add docs", "fix: heartbeat zero disable"
-->

## Summary

<!-- One paragraph: what changes and why. Link the issue if there is one. -->

## Kind of change

- [ ] New persona
- [ ] New domain
- [ ] New drift signal
- [ ] Bug fix
- [ ] Documentation
- [ ] Other (explain)

## Tests

- [ ] `python3 -m pytest tests/ -q` is green on this branch.
- [ ] Persona PRs: at least one new fixture under `fixtures/<domain>/<id>/`.
- [ ] Domain PRs: 5+ fixtures, `domain_drift_config.json` entry, parser test.
- [ ] Signal PRs: unit test exercising the gate firing for the new signal.
- [ ] Bug fix PRs: regression test that fails on master, passes here.

## Documentation

- [ ] New CLI flag → `docs/OPERATOR.md` updated.
- [ ] New env var → `docs/EMBEDDING.md` env table updated.
- [ ] New `__all__` export → `docs/EMBEDDING.md` public surface table updated.
- [ ] New domain → `docs/EVOLUTION_LOG.md` entry added.

## Discipline checklist (`docs/DISCIPLINES.md`)

- [ ] D3 (disk-truth wins): no LLM-only short-circuit on artifact verification.
- [ ] D5 (backup before write): any disk mutation has a timestamped `.bak.*` next to it.
- [ ] D6 (public log): every persona / decomposer / critic mutation appends to `docs/EVOLUTION_LOG.md`.
- [ ] No silent state changes; new failure modes are visible in `state.json` or stderr.
- [ ] No new external dependencies (or: justified inline).
- [ ] Cost cap (`DIRECTOR_DAILY_USD_CAP`) path not bypassed for new LLM call sites.

## Smoke run (paste output)

<!--
For domain or signal PRs, paste the relevant run output:

  ./director.py fixture run <your-domain>

For persona PRs:

  ./director.py ab -y --domain <domain> "<one canonical goal>"

Trim to the verdict + per-task line; full logs not needed.
-->

```
<paste here>
```

## Notes for the reviewer

<!-- Anything subtle: tradeoffs you considered, edge cases you couldn't reach, follow-up work that should be a separate PR. -->
