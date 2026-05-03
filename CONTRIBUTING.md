# Contributing to Director

Director is small enough that a fresh contributor should be able to land their first PR in about half an hour. This file describes the path; the deeper "how do I author X" details live under `docs/AUTHORING.md`.

## What kinds of PR are welcome

- A new persona (text-only addition to `personas.json` plus at least one fixture).
- A new domain (new entry in `domain_drift_config.json`, a fixture suite under `fixtures/<domain>/`, optionally a domain-specific persona).
- A new drift signal (new field on the signals dict consumed by `tighten_*_if_drift`).
- A bug fix with a regression test under `tests/`.
- Documentation improvements.

PRs that introduce new external dependencies, change the on-disk state schema, or alter the cost-cap or rollback semantics need an issue first to align on the design. Director's `docs/DISCIPLINES.md` is binding for all changes; read it once before authoring.

## Local setup

```bash
git clone <repo-url> director
cd director
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt   # if a requirements file lands in the repo
```

Smoke check that the CLI loads:

```bash
./director.py --help
python3 -m pytest tests/ -q
```

The full suite is fast (single-digit seconds). Always keep it green on your branch.

## Workflow

1. **Pick or open an issue.** For non-trivial work, agree on the shape with a maintainer before writing code.
2. **Branch from master.** Use the convention `<kind>/<short-slug>`: `persona/auditor`, `domain/docs`, `fix/heartbeat-zero-disable`.
3. **Read the relevant authoring guide.** `docs/AUTHORING.md` covers persona, domain, and signal additions step by step. `docs/SAMPLE_CONTRIBUTION.md` walks through an end-to-end domain addition.
4. **Write the change + the test together.** Director's design rule: a new persona ships with at least one fixture. A new domain ships with at least 5 fixtures and a threshold set. A bug fix ships with a regression test. No exceptions; the auto-rollback wiring assumes fixtures exist.
5. **Run the suite + a smoke run.**
   ```bash
   python3 -m pytest tests/ -q
   ./director.py fixture run <your-domain>   # if you added or touched a domain
   ```
6. **Commit per logical change.** One commit per ticket / per bug fix. Commit messages follow `<scope>: <imperative summary>` (see `git log` for examples).
7. **Open the PR.** Fill in the template; the reviewer checks the same boxes.

## Code style

- Single-file core (`director.py`) is intentional. Until M5, keep it that way; do not split into a package.
- Match the surrounding style. No new code formatters, no aggressive linting commits unrelated to your change.
- Type hints on new function signatures (`from __future__ import annotations` is in effect).
- No new external imports without explicit approval. Standard library + the existing provider SDKs (`requests`, `urllib`) are the baseline.
- Write comments only when the *why* is non-obvious. Names should explain the *what*.
- Persona descriptions and prompt files: terse, declarative, no recall lists or "encourage maximum thoroughness" verbose drift. The auto-tighten loop will slap that out anyway.

## Tests

- New persona: at least one fixture under `fixtures/<domain>/<id>/`. Fixture layout described in `fixtures/README.md`.
- New domain: 5 fixtures minimum, plus a `domain_drift_config.json` entry, plus an entry in `tests/test_domain_drift_config.py` confirming the threshold set parses.
- New signal: extend the signals dict in `tighten_persona_if_drift` callers, add a unit test that exercises the gate firing for the new signal in isolation.
- Bug fix: a `tests/test_<area>.py` case that fails on master and passes on your branch.

`python3 -m pytest tests/ -q` must be green before you push.

## Documentation

If your PR adds CLI flags, env vars, or new public-API symbols, update the matching doc in the same commit:

- New CLI flag → `docs/OPERATOR.md` section that covers the command.
- New env var → `docs/EMBEDDING.md` env table (and `docs/OPERATOR.md` if operator-relevant).
- New `__all__` export → `docs/EMBEDDING.md` public surface table.
- New persona → no doc edit needed; the file itself is the doc.
- New domain → `docs/EVOLUTION_LOG.md` entry on the domain landing.

## Review

A maintainer review checks:
- Tests added and green.
- DISCIPLINES respected (D3 disk-truth, D5 backup before write, D6 public log, no silent state).
- Cost cap not bypassed.
- No new top-level files unless explicitly justified.
- Style matches surrounding code.

If the reviewer asks for changes, push to the same branch; do not open a new PR.

## License

By contributing you agree your contribution is licensed under the same terms as the repository (see `README.md` § License). When the license is finalized, this section will name it explicitly.
