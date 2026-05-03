## `director.run(goal, *, yes=True, timeout_min=0)`

Runs a multi-step goal using the Director orchestration engine.
Decomposes the goal into tasks, executes them in parallel where possible,
and returns the final state dict.
