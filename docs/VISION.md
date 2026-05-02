# VISION

## What

Director is a single-file agent orchestrator with a closed self-improvement loop at the orchestration layer. It decomposes a goal, dispatches work to persona-injected workers, runs critics, scores persona fidelity with two independent models, detects drift across runs, and rewrites under-performing personas back to disk. Every change is backed up. Every run is logged. The system's behavioral history is fully auditable.

## Why this matters

Agent frameworks today (AutoGen, CrewAI, Swarm) give multi-persona orchestration but the personas are static text written by hand. Prompt-optimization research (DSPy, PromptBreeder, OPRO) demonstrates self-improving prompts but lives in lab notebooks, not in disk-persistent production systems.

Director closes that gap with a small, opinionated, single-file system that ships actual closed-loop persona tightening in production.

## The position (precise, no hype)

There are six theoretical layers of self-improvement in agent and AI systems:

| Layer | What changes | Status in this repo |
|-------|---------------|---------------------|
| 1. Orchestration | Prompts, personas, decomposer, critic | **YES** (auto-tighten loop) |
| 2. Memory | Retrieval, context selection | partial (mnemonics ns=director) |
| 3. Skill | Tool library, capability bootstrapping | not yet |
| 4. Code | Runtime code rewrite | not yet |
| 5. Weights | Model parameters | open research, out of scope |
| 6. Architecture | Model topology | open research, out of scope |

Director operates at **layer 1**, with the long-term aim to expand to layers 2 through 4. Layers 5 and 6 are the hard threshold for AGI/ASI claims and are explicitly out of scope.

### What we explicitly do not claim

- This is not AGI or ASI.
- This system does not modify model weights.
- This system does not search neural architectures.
- This system does not train new models.
- Stacking layer 1+2+3+4 produces a powerful agent system, not a superintelligence.

### What we do claim

- This is a production-grade reference implementation of one specific recursive self-improvement primitive.
- The combination of disk-persistent persona rewrites + drift signals + dual-family scoring + open evolution log is, to our current knowledge, not shipped anywhere else in this configuration.
- Layer 1 self-improvement is necessary scaffolding for any higher-layer self-improvement work that may come later. Building the floor right matters.

## Differentiation from prior art

| System | Self-modifying? | Persona-level? | Disk-persistent? | Drift signals? | Open evolution log? |
|--------|------------------|------------------|--------------------|------------------|----------------------|
| AutoGen, CrewAI, Swarm | no | yes (static) | no | no | no |
| DSPy | yes (compile-time) | no (few-shot) | partial | metric-based | no |
| PromptBreeder | yes (in-memory) | no (prompt-level) | no | no | no |
| Reflexion | yes (memory) | no | yes (memory) | implicit | no |
| Voyager | yes (skill lib) | no | yes | no | partial |
| **Director** | **yes** | **yes** | **yes** | **multi-signal** | **yes** |

The novel cell here is the entire bottom row, specifically the combination. Each individual capability has a precedent. The combination, shipped in production with a public evolution log, is the contribution.

## North-star outcome

A system where:

- A new operator can clone the repo and run it in under one hour.
- Director can run unattended for a month with cron, detecting drift and rolling back regressions, with no human in the loop.
- Persona descriptions evolve organically across thousands of runs and end up better than what any single human would have written by hand.
- The full evolution history is public, auditable, and contributes to the broader research conversation about recursive self-improvement at the orchestration layer.

## Inheritance

This system is designed to outlive its original maintainer. The README, the docs in this directory, the persona backups, the evolution log, and the mnemonics history together form the inheritance contract. If the original maintainer goes silent, a new operator should be able to walk these artifacts and continue without losing context.
