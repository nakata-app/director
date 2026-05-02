# M3-T01: Refactor domain fixtures

**Status:** open
**Owner:** Architect → Tester → Implementer → Reviewer
**Milestone:** M3
**Estimated touch:** ~5 fixture directories + 0 director.py lines (runner already lands in T04)
**Blast radius:** low (additive, observation only until T03)

## Goal

Stand up the second domain in the fixture suite: refactor. The runner from M2-T04 already supports any domain directory; this ticket adds the 5 hand-curated refactor fixtures so M3 auto-rollback (T03) has more than one domain to defend against regressions.

A refactor fixture differs in spirit from a security fixture: where security looks for new behavior to flag, refactor looks for behavior to preserve. The disk-truth assertions therefore tend to be byte-match-exact on a transformed file rather than substring-present in a finding.

## Acceptance criteria

- New directory `fixtures/refactor/` with at least 5 fixtures.
- Each fixture has the standard layout: `goal.txt`, `metadata.yaml`, `expected.json`, optional `setup.sh`, optional `input/`.
- At least one fixture exercises each of: dead-code removal, unused-import cleanup, function rename, behavior-preserving simplification, comment normalization.
- All five fixtures pass under `./director.py fixture run refactor` in dry-run-director mode (setup.sh seeds the expected output).
- `metadata.yaml` records `ground_truth_source` for each fixture.

## Touched files

- New: `fixtures/refactor/<5-fixtures>/`.
- No `director.py` change. The runner is already domain-agnostic.

## Disciplines

- D3 disk truth wins: assertions read disk artifacts, not LLM judgement.
- D4 drift gate (M3 enablement): refactor pass rate becomes a per-domain drift signal in T04.

## Dangers

- D4 blind-spot ceiling: a fixture that asserts only "file exists" is too lax, assertions should pin actual structural change. Use `byte_match` on at least one specific transformation marker per fixture.

## Definition of done

- Five refactor fixtures exist.
- Suite runs deterministically and all five pass.
- ROADMAP M3 progress updated.

## Notes for Implementer

- Fixtures are content, not code. No director.py changes in this ticket.
- Use `setup.sh` to seed the post-refactor file for now (M2 dry-run-director convention). M3-T03 will wire the real Director invocation.

## Notes for Reviewer

- Verify each fixture covers a distinct refactor archetype (no two doing the same thing).
- Verify `byte_match` needles are specific enough that an actually wrong refactor would fail the assertion.
