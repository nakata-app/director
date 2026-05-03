# Director

Director is a multi-agent orchestration system. It decomposes goals into tasks,
executes them with configurable personas, and applies auto-tightening to improve
output quality over time.

## Architecture

Director uses a decomposer-critic-executor loop. The decomposer breaks down goals
into atomic tasks. The critic validates the plan. Each task runs as a child process.

## Configuration

Set `DIRECTOR_DAILY_USD_CAP` to limit daily spend. Personas live in `personas.json`.
