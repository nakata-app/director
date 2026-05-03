# M4-T01: Daily USD cap + budget reporting

**Status:** closed — 2026-05-03 (commit 30dda10. Budget gate + reporting live.)
**Owner:** Architect → Implementer → Reviewer
**Milestone:** M4
**Estimated touch:** ~120-200 lines director.py + 1 test file + 1 CLI subcommand
**Blast radius:** medium-high (gates every paid LLM call; misconfigured cap can stall production runs)

## Goal

Today, only Opus 4.7 is metered, and only by call-count (`OPUS_DAILY_LIMIT`), not USD. V4 Pro, NIM Llama 3.3, and DeepSeek calls are completely untracked. M4 anti-pattern explicitly forbids shipping Director to a downstream product without a hard USD ceiling: a runaway tightener loop or a verbose persona could burn unbounded spend without a guard. This ticket installs a USD-denominated daily cap that all paid model paths route through, plus a per-model usage tracker and an operator-facing `director budget` CLI.

## Acceptance criteria

- New env var `DIRECTOR_DAILY_USD_CAP` (default 5.0) defines the ceiling. `0` or negative disables the gate.
- New module-level `MODEL_PRICING` dict covers at minimum: `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`, `deepseek-ai/deepseek-v4-pro`, `meta/llama-3.3-70b-instruct`, `deepseek-chat`. Each entry exposes per-million-token cost for input, output, cache_read, cache_write (cache fields default 0 if the API does not surface them).
- New helper `cost_for_call(model, input_tokens, output_tokens, cache_read=0, cache_write=0) -> float` computes USD using the pricing table; unknown models return 0.0 and emit a warning to stderr (no crash).
- New helper `record_usage(model, usage_dict)` appends one JSONL line to `director_usage.jsonl` (project-wide, replaces Opus-only `opus_usage.jsonl` going forward; legacy file kept readable for backward compat). Each line carries `ts`, `model`, token counts, `cost_usd_est`.
- New helper `daily_usd_spent(date=None) -> float` aggregates JSONL lines for a UTC date (default today). Reads both `director_usage.jsonl` and `opus_usage.jsonl` so historical Opus data is not lost.
- New helper `daily_usd_cap_blocks(estimate_usd=0.0) -> bool` returns True iff `daily_usd_spent() + estimate_usd >= DIRECTOR_DAILY_USD_CAP` (and cap > 0).
- All paid call sites (`call_opus_high`, `call_v4pro`, `call_nim_llama`, `call_deepseek_*`, the `_post_chat` shim if applicable) gain:
  - A pre-flight cap check that raises `RuntimeError("USD cap reached: $X spent / $Y cap")` when blocked. Estimate uses prior call's cost as a heuristic, falling back to 0 on cold start.
  - A post-flight `record_usage` call (best-effort: parse failures or disk errors must not break the request).
- `opus_quota_available()` extended: returns False if `daily_usd_cap_blocks()` (in addition to existing call-count check).
- New CLI subcommand `director budget [--date YYYY-MM-DD] [--by-model] [--json]`. Default surface: today's total + cap utilization (`$X.XX / $Y.YY (Z%)`); `--by-model` adds a per-model breakdown; `--json` returns machine-readable.
- Mnemonics record fired on first cap-block of the day (so an operator session sees it), gated by a per-day flag in `opus_quota.json` to avoid spamming.

## Touched files

- `director.py`: pricing table, three cost helpers, two cap helpers, call-site wiring, new `cmd_budget`, argparse subparser, `opus_quota_available` extension.
- New: `tests/test_daily_usd_cap.py` with at least 8 sub-assertions covering:
  - `cost_for_call` arithmetic for known model.
  - Unknown model returns 0.0 with stderr warning.
  - `daily_usd_spent` aggregates across both legacy and new JSONL files.
  - `daily_usd_cap_blocks` respects env var override.
  - Cap of 0 disables the gate even with non-zero spend.
  - Pre-flight gate raises before LLM call (mocked).
  - `record_usage` writes JSONL line and is idempotent on disk error.
  - `cmd_budget --json` returns expected schema for today.

## Disciplines

- D2 (mixed-family / cost discipline): cost ceiling forces the operator to notice runaway loops before the bill arrives.
- D6 (audit log): every paid call leaves a JSONL trail with cost estimate, regardless of model family.

## Dangers

- D1 collapse: a too-strict cap can stall every paid path mid-run, leaving Director unable to recover. Mitigation: cap value is an env var (operator-tunable), default is conservative ($5/day), and the block raises a clear RuntimeError that the caller can choose to handle.
- D3 leaky abstraction: pricing table will drift as Anthropic / NVIDIA / DeepSeek change rates. Mitigation: pricing block sits at module top with a `# Last verified: <date>` comment and a structured `note` field per entry; out-of-band review is part of the operator playbook (M4-T02).
- D4 drift: a buggy pricing entry can silently understate spend by orders of magnitude. Mitigation: `tests/test_daily_usd_cap.py` includes one fixed-arithmetic assertion per pricing-table entry so a numeric error is caught at test time.

## Definition of done

- All test sub-assertions pass.
- Cumulative test sweep stays green; no regression in any prior suite.
- Live `director budget --json` returns a non-error response on the local environment with at least Opus historical usage aggregated correctly.
- Llama 3.3 mixed-family review verdict PASS with RISKS = none (matching the M3 wrapping convention).
- ROADMAP.md M4 status updated with the T01 closure line.
- Mnemonics record (ns=director) summarizes the cap value, models covered, JSONL paths.

## Notes for Implementer

- Keep `cost_for_call` pure (no I/O, no side-effects) so it is unit-testable in isolation.
- Prefer raising `RuntimeError` over silent skip when the cap blocks; the auto-tightener loop should observe the failure and abort, not silently swallow it.
- Pricing assumption used in the existing Opus block (`$15/M in, $75/M out, $1.50/M cache_read, $18.75/M cache_write`) is the source of truth for the Opus row. Other rows must be verified against the corresponding provider docs at implementation time, with the exact numbers committed in `MODEL_PRICING` and a `# Last verified: 2026-05-03` comment.
- `director_usage.jsonl` is append-only; never rewrite or rotate. Operator playbook (T02) will document log rotation if it ever becomes an issue.
- Cap defaults to 5.0 USD because that is the same ceiling Atakan uses for ad-hoc Sonnet/Opus testing. Production deployments are expected to override per-environment.
