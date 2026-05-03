# M2-T04: Fixture suite scaffold (M3 prerequisite)

**Status:** closed — 2026-05-03 00:35 (15/15 sub-assertions green, 5 security fixtures pass live.)
**Owner:** Architect → Tester → Implementer → Reviewer
**Milestone:** M2 (soft-start of M3 prerequisite)
**Estimated touch:** ~100 lines director.py + new fixtures directory
**Blast radius:** low until M3 work activates auto-rollback

## Goal

Stand up the fixture-suite directory structure and the fixture runner needed for M3 auto-rollback. M2 does not yet enable auto-rollback; this ticket only builds the scaffold so M3 work can plug in.

A fixture is a hand-curated triple: input goal, expected artifact specification, behavior signature. Behavior signature is structural (e.g. "the output JSON has at least 3 vulnerability entries with severity field set"), not LLM-judged. The runner should be deterministic: same fixture + same persona + same model = same pass/fail decision.

## Acceptance criteria

- New directory `fixtures/security/` with at least 5 fixtures for the security domain. Each fixture is a directory containing:
  - `goal.txt`: the goal text passed to Director.
  - `setup.sh`: optional, prepares any input files needed.
  - `expected.json`: structural assertions about output artifacts (file path, file presence, JSON schema, byte-match patterns where appropriate).
  - `metadata.yaml`: domain, persona id, ground-truth source, expected difficulty.
- A new function `run_fixture(fixture_dir, persona_id) -> FixtureResult` that:
  - Cleans the fixture's working directory.
  - Runs `setup.sh` if present.
  - Invokes Director's `run` path with the fixture's goal.
  - Asserts the expected.json structure against produced artifacts.
  - Returns pass/fail with concrete failure reasons.
- A new function `run_fixture_suite(domain) -> SuiteReport` that runs all fixtures in a domain and reports per-fixture and aggregate pass rates.
- A new operator command `./director.py fixture run <domain> [--persona <id>]` that runs the suite and prints the report.
- Fixture runs do NOT auto-tighten or auto-rollback in M2. They are observation only. M3 wires the auto-rollback to fixture pass-rate regression.

## Touched files

- New: `fixtures/security/<5-fixtures>/` directories.
- New: `fixtures/README.md` describing fixture format and contribution guidelines.
- `director.py`: add fixture runner, suite runner, subcommand.
- New: `tests/test_fixture_runner.py` with at least 6 unit tests covering:
  - expected.json structural assertions (file presence, JSON schema, byte match).
  - Fixture pass/fail decision is deterministic given mocked Director output.
  - Suite report aggregates per-fixture results correctly.
  - Setup script execution failure is reported as fixture-setup-failure, not test failure.
- `docs/OPERATOR.md`: add a section on running fixtures.
- `docs/ROADMAP.md`: M3 prerequisite progress checkmark.

## Disciplines (DISCIPLINES.md)

- D3 disk truth wins: fixture assertions read disk artifacts, not LLM critic output.
- D4 drift gate: in M3, fixture pass rate becomes a drift signal that overrides LLM-based signals when in conflict.

## Dangers (DANGERS.md)

- D4 blind-spot ceiling: fixtures are the first defense against blind-spot drift. Every domain needs at least 5 hand-labeled fixtures before auto-tighten on that domain can be trusted in production.

## Definition of done

- All 6 unit tests pass.
- Five security fixtures exist and the runner produces a deterministic pass/fail report on each.
- Suite runner aggregates and prints a clear table (fixture-id, status, reason, latency).
- OPERATOR.md updated.
- ROADMAP M3 prerequisite checkmark added.

## Notes for Implementer

- Fixtures should be small and fast. Aim for under 30 seconds per fixture so the full suite runs in under 5 minutes.
- Use existing `/tmp/dir-sec/api.py` style targets where appropriate, but copy them into `fixtures/security/<id>/input/` so fixtures are hermetic.
- Do not invoke auto-tighten from the fixture runner. M2 scope is observation only.

## Notes for Reviewer

- Verify fixture hermetic isolation: a fixture must not depend on files outside its own directory.
- Verify deterministic results: run the same fixture twice, results identical.
- Verify the report format is parseable (used by M3 auto-rollback decision).
