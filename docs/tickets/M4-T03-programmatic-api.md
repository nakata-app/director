# M4-T03: Programmatic Director API

**Status:** closed, 2026-05-03 (`run`, `ab`, `recover` wrappers in director.py; `__all__` exports `tighten_*_if_drift` + disk-truth helpers; `docs/EMBEDDING.md` covers install path, public surface, examples, env reference, error contract.)
**Owner:** Architect → Implementer → Reviewer
**Milestone:** M4
**Blast radius:** low (additive API surface, no behavior change)

## Goal

Downstream products can call `director.run()`, `director.ab()`, `director.recover()` as a library without spawning a subprocess.

## Acceptance criteria

- `run`, `ab`, `recover` importable from `director`.
- `__all__` declares public surface.
- `docs/EMBEDDING.md` documents install path, env vars, error contract, and usage examples.
- No existing CLI behavior changed.
