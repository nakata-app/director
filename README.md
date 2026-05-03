# Director

Self-improving agent orchestration with closed-loop persona tightening.

A single-file Python CLI that decomposes a goal into parallel tasks, dispatches each task to a worker LLM with a chosen persona, runs an independent critic per task, scores persona fidelity, and (optionally) rewrites under-performing personas back to disk after detecting drift.

This is a production-grade reference implementation of one specific primitive on the path to recursive self-improvement: orchestration-layer auto-tightening. It is not an AGI/ASI system. It is the layer below.

## Why this exists

Most agent frameworks (AutoGen, CrewAI, Swarm) give you multi-persona orchestration but the personas are static text you write by hand. Most academic work on prompt optimization (DSPy, PromptBreeder, OPRO) lives in lab notebooks, not in disk-persistent production loops.

Director closes that gap with a small, opinionated, single-file system:

1. Decompose a goal into a typed DAG of tasks (Opus 4.7 high tier with quota cap, falling back to DeepSeek V4 Pro and NVIDIA NIM)
2. Assign each task a persona from `personas.json`
3. Dispatch in parallel to a worker backend (Metis CLI by default, or `claude -p`)
4. Run a critic per task with disk-truth short-circuit (artifact byte match wins)
5. Score persona fidelity with two independent scorers from different model families
6. After an A/B run (persona-on vs persona-off), detect drift signals across completion rate, fidelity, and log inflation
7. If drift suggests a persona description is too verbose or under-specified, ask a tighten-LLM to rewrite it, validate sanity bounds, write to disk with a timestamped backup

That last loop is the point. Personas in this repo are not just configuration. They are state that the system edits.

## Position statement (no hype)

What this system does:
- Recursive self-improvement at the **orchestration layer** (prompts, personas, scaffold)
- Stacked with disciplines borrowed from distributed systems (idempotent retries, mixed-family critics, disk-persistent state, automatic backups)
- Open-source evolution log: every persona rewrite is a backup file, every run is a JSON state, every learned signal is a mnemonics record. The system's history is auditable.

What this system does not do:
- Modify model weights
- Search neural architectures
- Train a new model
- Claim AGI/ASI status

Recursive self-improvement at the orchestration layer is necessary scaffolding for anyone working on the higher layers, but it is not sufficient. Anyone who tells you their prompt-rewriting loop is on the path to ASI is selling something. This is the floor of the stack, useful on its own.

## The four self-improvement layers

| Layer | What changes | This repo |
|-------|---------------|-----------|
| 1. Orchestration | Prompts, personas, decomposer, critic | **YES** (auto-tighten) |
| 2. Memory | Retrieval, context selection, summarization | partial (mnemonics ns=director) |
| 3. Skill | Tool library, capability bootstrapping | no (planned) |
| 4. Code | Runtime code rewrite | no |
| 5. Weights | Model parameters | open research |
| 6. Architecture | Model topology | open research |

## Quick start

```bash
# Decompose + execute one goal with persona injection
./director.py run -y "audit /tmp/api.py for SQL injection, write findings to /tmp/review.md"

# A/B compare persona-on vs persona-off
./director.py ab -y "audit /tmp/api.py for SQL injection, write findings to /tmp/review.md"

# Same A/B but apply auto-tighten if drift is detected
./director.py ab -y --auto-tighten "..."
```

## Required environment

```bash
export ANTHROPIC_API_KEY=...     # decompose Opus 4.7 high (quota-capped)
export DEEPSEEK_API_KEY=...      # persona/critic/revise primary, decompose fallback
export NVIDIA_API_KEY=...        # final fallback (NIM Llama 3.3)
export DIRECTOR_CHILD_BACKEND=metis  # default; alternative: claude
```

## Disciplines (non-negotiable)

These are the rules that keep self-improvement from collapsing into either degeneration or self-flattery:

1. **Anti-collapse guards.** Every tightened description goes through length bounds (50 to 500 chars), content-density checks, and a baseline-distance ceiling. Beyond that ceiling, human approval is required.
2. **Mixed-family scoring.** The worker LLM and the critic LLM must come from different model families (e.g. DeepSeek worker, NIM Llama or Gemini Flash critic). A model is not allowed to score its own family unsupervised.
3. **Disk truth wins.** If the artifact on disk byte-matches the brief's `expected_content`, the LLM critic is short-circuited. Real evidence beats LLM opinion.
4. **Drift gate.** Auto-tighten only fires when at least one drift signal (completion drop, fidelity drop, log inflation) crosses threshold. No drift = no rewrite.
5. **Backup before write.** Every persona rewrite produces a timestamped backup of `personas.json`. Rollback is a `cp` away. Future automation will rollback automatically if a fixture suite regresses.
6. **Cost cap.** Per-day USD budget cap on Director runs (planned: `DIRECTOR_DAILY_USD_CAP`).
7. **Public evolution log.** All persona rewrites and their drift signals are committed to `EVOLUTION_LOG.md` (in progress).

## Status (M1 to M4 roadmap)

- **M1**: Single-domain auto-tighten validated end-to-end with drift signals firing correctly. **Closed.**
- **M2**: Decomposer and critic prompts also subject to auto-tighten. Three layers of self-improvement at once. **Closed.**
- **M3**: Three domains (security, refactor, design) with per-domain personas and ground-truth fixture suites for regression testing. **Closed 2026-05-03.**
- **M4**: Director embedded as worker layer under one or more downstream products. **In progress** (T01 daily USD cap closed; T02 operator playbook closed; T03 programmatic API + T04 contributor flow next).

## Documentation

- [`docs/VISION.md`](docs/VISION.md), what the system is and is not.
- [`docs/ROADMAP.md`](docs/ROADMAP.md), milestone closure log.
- [`docs/OPERATOR.md`](docs/OPERATOR.md), day-to-day commands, debugging, recovery, audit.
- [`docs/EMBEDDING.md`](docs/EMBEDDING.md), Python API for downstream products that want to call `run` / `ab` / `tighten_*_if_drift` without spawning a subprocess.
- [`docs/INHERITANCE.md`](docs/INHERITANCE.md), what a new maintainer needs to read first.
- [`docs/EVOLUTION_LOG.md`](docs/EVOLUTION_LOG.md), every persona tighten and rollback, with reasons.
- [`docs/DISCIPLINES.md`](docs/DISCIPLINES.md), the design rules every change must respect.

## License

TBD. Likely MIT.

## Contact

hey@nakata.app

## Inheritance note

This README and the `personas.json` evolution backups together form the inheritance contract. If the original maintainer goes silent, a new operator should be able to clone the repo, read the README, walk the persona backup history, read `EVOLUTION_LOG.md`, and continue without losing context.

That is the design intent: a system that survives its operator.
