# DANGERS

Four known failure modes of recursive self-improvement systems at the orchestration layer. Each one is mapped to a concrete countermeasure and a status indicator.

## D1: Persona collapse

**What it looks like:**
- The auto-tighten loop produces progressively shorter and more abstract descriptions.
- After several iterations, the persona description becomes a single sentence or a tautology ("Be a good worker.").
- Worker output regresses to baseline because the persona no longer carries actionable disciplines.

**Why it happens:**
- The drift signal rewards reduced log inflation, so the tightener naturally compresses.
- Without a content-density floor, compression continues past the point where the description still carries intent.

**Countermeasure (mapped to disciplines):**
- D1.1: Sanity bound 50-500 chars (D1 of DISCIPLINES.md, implemented).
- D1.2: Verb-to-adjective ratio floor (planned).
- D1.3: Baseline-distance ceiling with human approval (planned).
- D1.4: Per-domain fixture suite that catches regression even when description still passes sanity bounds (M3 work).

**Status:** partially mitigated. Sanity bounds prevent extreme collapse. Content density and baseline distance not yet enforced.

## D2: Self-flattering scoring

**What it looks like:**
- Fidelity scores stay high (8-10/10) across every run.
- A/B comparisons consistently favor whatever arm shares the scorer's biases.
- Drift signals never fire because the scorer cannot perceive the failure modes that the worker produces.

**Why it happens:**
- Worker LLM and scorer LLM are from the same model family. They share blind spots.
- Tightener LLM also from the same family produces descriptions that flatter that family's strengths.

**Countermeasure (mapped to disciplines):**
- D2.1: Dual-scorer fidelity (V4 Pro + V4 Flash) with consensus check (D2 of DISCIPLINES.md, implemented).
- D2.2: Compile-time check that worker family != at least one scorer family (planned).
- D2.3: Tightener family != worker family (planned).
- D2.4: External eval harness (M4 work) that uses a third-party model entirely (e.g. Gemini or NIM Llama) to spot-check fidelity quarterly.

**Status:** partially mitigated. Dual-scorer in place but family separation not enforced.

## D3: Cost runaway

**What it looks like:**
- Director runs accumulate to dozens of LLM calls per cycle (decompose + N tasks + N critics + 2 fidelity scorers + tightener).
- Daily cost exceeds budget without explicit notification.
- Quota exhaustion mid-run leaves partial state on disk.

**Why it happens:**
- No per-run or per-day cost cap.
- Fallback chain (Opus -> V4 Pro -> NIM) means a single quota event quietly switches model and changes cost characteristics.
- A/B tests double the work without doubling the visibility.

**Countermeasure:**
- D3.1: Drift gate prevents tightener from firing on every run (D4 of DISCIPLINES.md, implemented).
- D3.2: Sha1 cache for tighten prompts so repeated identical inputs hit cache (planned).
- D3.3: Per-day USD budget cap via `DIRECTOR_DAILY_USD_CAP` environment variable (planned).
- D3.4: Per-run cost estimate logged to mnemonics with explicit total surfaced at run end (partial: opus_usage.jsonl exists, V4 Pro + NIM not tracked).

**Status:** weakly mitigated. Drift gate provides natural rate limit. No hard budget cap.

## D4: Blind-spot ceiling

**What it looks like:**
- The system tightens personas competently within the model family's competence.
- Failure modes that the model family cannot perceive (e.g. specific bug classes, certain reasoning gaps) are never caught and never improved.
- The system appears to be improving but is asymptoting on a ceiling defined by the underlying model.

**Why it happens:**
- The scorer cannot see what the worker cannot see.
- The tightener cannot rewrite for failures it cannot recognize.
- LLMs improve scaffolds within their perception envelope, not beyond it.

**Countermeasure:**
- D4.1: Ground-truth fixture suites with hand-labeled correct outputs (M3 work).
- D4.2: External model spot-checks (M4 work, third-party model not in the worker/scorer chain).
- D4.3: Operator review of evolution log on a regular cadence to catch patterns the system itself cannot.
- D4.4: Explicit scope statement: this system improves verbalizable orchestration disciplines, not underlying capability. Capability ceiling is set by the model, not the loop.

**Status:** unmitigated. This is a fundamental limit, not a bug. The mitigation is honesty about the ceiling, not pretending to break it.

## Risk classification

| Danger | Severity | Probability without mitigation | Status |
|--------|----------|-------------------------------|--------|
| D1 Collapse | high | high | partially mitigated |
| D2 Self-flattering | high | high | partially mitigated |
| D3 Cost runaway | medium | medium | weakly mitigated |
| D4 Blind-spot ceiling | medium (acceptable) | certain | unmitigated by design |

The first three must reach "fully mitigated" before M3 closure. D4 cannot be mitigated and must be acknowledged in the position statement and surfaced in the operator playbook.
