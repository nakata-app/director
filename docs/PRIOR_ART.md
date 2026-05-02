# PRIOR ART

A precise map of what already exists in the literature and in production tools that overlaps with Director, and what specifically is novel about this combination.

## Academic precedents

### DSPy (Khattab et al., Stanford, 2023-2024)
- Framework for "compiling" LLM prompts via metric feedback.
- Optimizers: BootstrapFewShot, MIPRO, COPRO.
- Operates primarily at the few-shot example level and instruction level.
- Library, not a runtime daemon.
- Closest established research analogue to Director's auto-tighten loop.

### PromptBreeder (Fernando et al., DeepMind, 2023)
- Self-referential evolutionary algorithm for LLM prompt optimization.
- LLM mutates its own prompts and the prompts that mutate it.
- In-memory evolution, not disk-persistent.
- Lab demonstration, not a deployed system.

### OPRO, Optimization by PROmpting (Yang et al., DeepMind, 2023)
- LLM as optimizer for natural-language objectives, including prompts for other LLMs.
- Single-objective, no multi-signal drift detection.
- Notebook-scale evaluation.

### APE, Automatic Prompt Engineer (Zhou et al., 2022)
- LLM generates and scores candidate prompts for a target task.
- Single-shot generation, not a continuous loop.

### EvoPrompt (Guo et al., 2023)
- Evolutionary algorithm over LLM-written prompts.
- Like PromptBreeder, in-memory and lab-scoped.

### Reflexion (Shinn et al., 2023)
- Agent reflects on failure and updates a memory store.
- Updates memory, not the prompt or persona.
- Closest analogue at the memory layer (Director's planned layer 2).

### Voyager (Wang et al., NVIDIA, 2023)
- Skill library that grows over time in a Minecraft environment.
- Closest analogue at the skill layer (Director's planned layer 3).

### STOP, Self-Taught Optimizer (Zelikman et al., 2023)
- Recursively self-improving code optimization.
- Operates at code layer (Director's planned layer 4).

## Production framework precedents

### AutoGen (Microsoft)
- Multi-agent orchestration with conversational handoffs.
- Personas defined statically.
- No self-modification.

### CrewAI
- Multi-agent task orchestration with role definitions.
- Personas defined statically.
- No self-modification.

### Swarm (OpenAI, 2024)
- Lightweight multi-agent orchestration with handoff primitive.
- Personas (instructions) defined statically.
- No self-modification.
- Closest single-file analogue to Director's structural shape.

### MetaGPT
- Multi-agent SOPs with role-based personas.
- Personas defined statically.
- No self-modification.

## Capability comparison

| Capability | DSPy | PromptBreeder | Swarm | Reflexion | Director |
|------------|------|----------------|-------|-----------|----------|
| Multi-persona orchestration | no | no | yes | no | yes |
| Self-modifying prompts | yes (compile) | yes (in-memory) | no | partial (memory) | yes (disk) |
| Persona-level edits | no | no | no | no | yes |
| Multi-signal drift detection | partial (single metric) | no | no | implicit | yes |
| Disk persistence | partial | no | no | yes (memory) | yes |
| Backup and rollback | no | no | no | no | yes (backup), planned (rollback) |
| Mixed-family scoring | no | no | no | no | yes (dual-scorer) |
| Public evolution log | no | no | no | no | yes (planned, in progress) |
| Single-file production | no (lib) | no (research) | yes | no (lib) | yes |
| Open source ready | yes | no | yes | yes | yes (planned public) |

## What is genuinely novel in Director

The novelty is not in any single capability. Each capability has a precedent. The contribution is the **specific combination** plus three production-grade disciplines that are systematically absent from prior work:

1. **Persona-level disk-persistent self-edit with timestamped backup.** Prior work either edits prompts in memory, or edits memory rather than prompts, or does not persist evolution. Director keeps every persona version on disk forever, addressable by timestamp, restorable by `cp`.

2. **Mixed-family scoring as a structural discipline.** Prior work uses a single scorer (often the same model family as the worker). Director makes dual-family scoring a non-negotiable design requirement, not a nice-to-have.

3. **Public evolution log as part of the artifact.** Prior work treats evolution as internal state. Director treats it as part of the published repository. The system's behavioral history is open-source the way the code is open-source.

This combination is the contribution. It is small, opinionated, and not a paper. It is a single-file system that ships, runs, and remembers.

## What Director borrows

- Decomposer-worker-critic shape: classical agent orchestration, traceable to ReAct (Yao et al. 2022) and AutoGen.
- Persona injection: standard in CrewAI, AutoGen, Swarm.
- Evolutionary self-rewriting: PromptBreeder, EvoPrompt.
- Metric-driven prompt optimization: DSPy, OPRO.
- Reflection on failure: Reflexion.
- Skill library mindset (planned layer 3): Voyager.

## What Director does not attempt

- Weight-level self-improvement (out of scope, layer 5).
- Architecture-level self-improvement (out of scope, layer 6).
- General-purpose agent framework competing with AutoGen or CrewAI on breadth (Director is opinionated and narrow).
- Distributed execution or multi-host coordination (single-machine by design for now).

If anyone reading this is aware of a system that ships the specific combination above (persona-level + disk-persistent + multi-signal drift + mixed-family scoring + public evolution log), please open an issue. The position statement here will be updated.
