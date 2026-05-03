# M3-T02: Design domain fixtures

**Status:** closed — 2026-05-03 01:25 (38/38 sub-assertions green.)
**Owner:** Architect → Tester → Implementer → Reviewer
**Milestone:** M3
**Estimated touch:** ~5 fixture directories
**Blast radius:** low (additive, observation only until T03)

## Goal

Stand up the third domain in the fixture suite: design (UI/UX/CSS/accessibility). The runner already supports any domain; this ticket adds 5 hand-curated design fixtures so M3 auto-rollback has three domains to defend against regression in three different reasoning modes.

Design fixtures are usually about output structure (HTML element tree, CSS rule presence, accessibility attribute set), not about behavior preservation or vulnerability finding. They lend themselves naturally to `json_schema` and `byte_match` assertions over generated markup.

## Acceptance criteria

- New directory `fixtures/design/` with at least 5 fixtures covering distinct design archetypes:
  - Component scaffold (HTML+CSS for a typed component).
  - WCAG accessibility attribute set on an interactive element.
  - Dark-mode CSS variable definition.
  - Responsive layout breakpoint structure.
  - Keyboard navigation focus order.
- Each fixture follows the standard layout: `goal.txt`, `metadata.yaml`, `expected.json`, optional `setup.sh`, optional `input/`.
- All 5 fixtures pass under `./director.py fixture run design` in dry-run-director mode.
- `metadata.yaml` records `ground_truth_source` (likely WCAG 2.2 AA or a hand-labeled UX spec).

## Touched files

- New: `fixtures/design/<5-fixtures>/`.
- No `director.py` change.

## Disciplines

- D3 disk truth wins.
- D4 drift gate (M3 enablement).

## Dangers

- D4 blind-spot ceiling: design assertions can be too forgiving (any HTML passes). Pin specific structural markers, `aria-label` presence, named CSS variable, element nesting depth, etc.
- A design fixture that asserts only on aesthetics is not a fixture, it's a wish.

## Definition of done

- Five design fixtures exist and pass deterministically.
- ROADMAP M3 progress updated.
