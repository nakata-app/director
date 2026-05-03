# M2-T03: Mnemonics ingest robustness

**Status:** closed — 2026-05-03 00:15 (18/18 sub-assertions green, JSONL fallback + replay CLI shipped.)
**Owner:** Architect → Implementer → Reviewer
**Milestone:** M2
**Estimated touch:** ~50 lines director.py + 1 small test
**Blast radius:** low (mnemonics is best-effort, but losing records means losing the public evolution log's source of truth)

## Goal

Make `mnemonics_record(text, ns="director")` resilient to local MCP server slowness or unavailability. Today, observed `mnemonics.log` shows TIMEOUT entries for several records. Records are silently dropped if the MCP server does not respond within the implicit timeout. The evolution log loses entries it should preserve.

This is the open issue Issue 2 deferred from M1 closure.

## Acceptance criteria

- `mnemonics_record` retries up to 3 times with exponential backoff (1s, 3s, 9s) on timeout or 5xx response.
- If all retries fail, the record is appended to a local fallback file `~/.claude/director/mnemonics.fallback.jsonl` with timestamp, namespace, and text. Operator can replay later.
- A new function `mnemonics_replay_fallback()` reads the fallback file, attempts ingest, removes successfully ingested entries from the fallback file. Called explicitly by an operator command, not implicitly.
- A new operator command `./director.py mnemonics-replay` runs the replay and prints a summary.
- Mnemonics ingest never blocks the main run thread for more than the cumulative retry budget (~13 seconds total). After that, fallback-and-continue.
- Fallback events are themselves logged to `mnemonics.log` with `FALLBACK` prefix for visibility.

## Touched files

- `director.py`: modify `mnemonics_record`, add `mnemonics_replay_fallback`, add `cmd_mnemonics_replay`, register subcommand.
- New: `tests/test_mnemonics_robustness.py` with 5+ unit tests covering:
  - Successful ingest on first try.
  - Retry with backoff on transient failure.
  - Fallback file write after exhausted retries.
  - Replay reads fallback, ingests, prunes.
  - Concurrent record calls do not corrupt the fallback file (file lock or atomic append).
- `docs/OPERATOR.md`: add a section on `mnemonics-replay` command and when to run it.

## Disciplines (DISCIPLINES.md)

- D6 public evolution log: the fallback file is the safety net for D6. Without it, evolution events leak through the cracks.

## Dangers (DANGERS.md)

- D3 cost runaway: retries cost time, not money (mnemonics is local), but a runaway retry loop wastes wall-clock. Hard cap at 3 retries.

## Definition of done

- All 5 unit tests pass.
- A simulated MCP timeout (e.g. via mocked transport) produces a fallback file entry, not a silent drop.
- `mnemonics-replay` command works against a fallback file with at least 3 entries.
- OPERATOR.md updated with the new command.

## Notes for Implementer

- Do not block the main loop on retries. Use a thread or async dispatch with a hard timeout.
- Do not modify the mnemonics MCP server itself. Only modify the client side in director.py.
- The fallback file is JSONL (one record per line) for ease of grep + replay.

## Notes for Reviewer

- Verify that no record is silently dropped (every record either ingests or appears in fallback).
- Verify that fallback file growth is bounded (replay command is documented as the prune mechanism).
- Verify that the existing mnemonics.log keeps its current format (operators may have grep workflows on it).
