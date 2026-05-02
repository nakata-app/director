# Fixtures

Hand-curated test inputs that validate Director's behavior on known-good cases. Each fixture is hermetic: it brings its own input files in `input/`, declares its expected output in `expected.json`, and reports pass/fail without invoking any LLM judge.

M2 scope (this directory) is **observation only**: the runner reports per-fixture results, but does not auto-tighten or auto-rollback. M3 work wires the suite's pass-rate signal to the rollback decision.

## Layout

```
fixtures/
  <domain>/
    <fixture_id>/
      goal.txt           # the goal text Director receives (required)
      metadata.yaml      # domain, persona id, difficulty, ground-truth source (required)
      expected.json      # structural assertions (required)
      setup.sh           # optional, runs before the fixture (e.g. seed input files)
      input/             # optional, hermetic input data referenced by goal.txt
```

## expected.json schema

```json
{
  "assertions": [
    {"type": "file_present", "path": "/abs/path"},
    {"type": "byte_match",   "path": "/abs/path", "needle": "literal substring"},
    {"type": "json_schema",  "path": "/abs/path", "required_keys": ["field1", "field2"]}
  ]
}
```

Every assertion runs against disk artifacts. No LLM is consulted (D3 disk-truth wins). All assertions must pass for the fixture to pass.

## Running

```bash
./director.py fixture run security
./director.py fixture run security --persona security
```

The runner prints a per-fixture table (id, status, latency, reason) plus aggregate totals, and exits non-zero if any fixture failed.

## Adding a new fixture

1. Pick a unique id (`f06_<short_topic>`) under the appropriate domain directory.
2. Write `goal.txt` referencing absolute paths inside the fixture directory only.
3. Write `metadata.yaml` with at least `domain`, `persona`, `difficulty` keys.
4. Write `expected.json` with assertions that hold deterministically given the goal.
5. If the fixture needs preexisting input (e.g. an `api.py` to audit), put it in `input/` and copy it into place from `setup.sh`.
6. Run `./director.py fixture run <domain>` and confirm pass/fail matches the labeled ground truth.

## Determinism rule

The same fixture run twice on the same code must produce the same status. If a fixture flakes, fix the assertion, not the runner.

## Ground-truth source

Every fixture's `metadata.yaml` should record where the ground-truth came from: hand-labeled by an operator, lifted from a CVE entry, distilled from a past incident postmortem, etc. Without provenance the fixture has no audit trail.
