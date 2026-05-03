# Sample Contribution: Adding a "docs" Domain End to End

This walkthrough shows the full path a fresh contributor takes to land a new domain. The example domain is `docs` (documentation-writing tasks). Replace with your own domain name where appropriate.

The whole walkthrough takes a focused contributor about an hour, not counting the smoke-run wait time.

## Why this example

Documentation tasks are different enough from security or refactor work that they justify their own threshold set: long-form writing varies in length (so log inflation is noisy), but persona fidelity matters more (the worker must match the doc's voice). Existing personas (`implementer`, `default`) under-serve doc work. So: new domain.

## Step 1: Open an issue (skip-able for this walkthrough)

Title: `domain: add docs`. Body: scope (one paragraph), why existing domains don't cover it, proposed threshold rationale. A maintainer either agrees with the design or pushes back before any code is written.

## Step 2: Branch

```bash
git checkout master && git pull
git checkout -b domain/docs
```

## Step 3: Define the scope

```bash
mkdir -p docs/domains
cat > docs/domains/docs.md <<'EOF'
# Domain: docs

Scope: documentation tasks. Writing or revising user-facing docs (README,
guides, API reference, runbooks). Not changelog entries or commit messages.

Target user: a developer who needs to ship a doc page that a peer can
follow without asking questions.

Success looks like: the doc compiles to the right format, contains the
required sections, links to canonical sources, and is short enough to
read end to end in under five minutes.
EOF
```

The scope file is two-thirds of the design work. Without it, fixture authors will write 5 fixtures that test 5 different ideas of what `docs` means.

## Step 4: Add the persona

```bash
cp personas.json personas.json.bak.$(date +%Y%m%d-%H%M%S)-pre-add
```

Edit `personas.json`, add the new entry next to `default`:

```json
"docs": {
  "name": "Documentation Writer",
  "trigger": ["doc", "docs", "documentation", "readme", "guide", "runbook", "tutorial", "docstring"],
  "description": "Reader-first. Write the doc the next contributor needs, not the doc you wish you had. Each section answers one question. Cite canonical sources by URL or file:line. No marketing voice, no 'in conclusion'. If you can't compress a paragraph to half its length without losing meaning, the source is the problem, not the prose."
}
```

Validate:

```bash
python3 -c "import json; json.load(open('personas.json'))"
```

## Step 5: Add the threshold set

Edit `domain_drift_config.json`, append a `docs` block:

```json
"docs": {
  "_doc": "Doc tasks tolerate completion drop (long-form writing varies). Persona fidelity floor matters more than log inflation; loose on log size, strict on persona fidelity.",
  "decomposer_score_floor": 6.5,
  "decomposer_literal_floor": 0.55,
  "persona_completion_drop_threshold": 0.30,
  "persona_log_inflation_threshold": 0.60,
  "persona_fidelity_floor": 7.5
}
```

Validate:

```bash
python3 -c "import json; json.load(open('domain_drift_config.json'))"
```

## Step 6: Author 5 fixtures

```bash
mkdir -p fixtures/docs/{f01_readme_skeleton,f02_runbook_section,f03_api_reference_table,f04_glossary_entry,f05_changelog_compression}/input
```

For each fixture, write four files: `goal.txt`, `metadata.yaml`, `expected.json`, optionally `setup.sh` and `input/`. Layout reference: `fixtures/README.md`.

Example for `f01_readme_skeleton`:

`fixtures/docs/f01_readme_skeleton/goal.txt`:
```
Write a README skeleton at /tmp/docs_f01/README.md for the project
described in /tmp/docs_f01/input/PROJECT_BRIEF.md. Required sections,
in order: Title, One-line description, Quick start, Documentation
links, License. Keep total length under 60 lines.
```

`fixtures/docs/f01_readme_skeleton/metadata.yaml`:
```yaml
domain: docs
persona: docs
difficulty: easy
ground_truth_source: hand-labeled by maintainer 2026-05-03
```

`fixtures/docs/f01_readme_skeleton/expected.json`:
```json
{
  "assertions": [
    {"type": "file_present", "path": "/tmp/docs_f01/README.md"},
    {"type": "byte_match",   "path": "/tmp/docs_f01/README.md", "needle": "## Quick start"},
    {"type": "byte_match",   "path": "/tmp/docs_f01/README.md", "needle": "## License"}
  ]
}
```

`fixtures/docs/f01_readme_skeleton/setup.sh`:
```bash
#!/bin/bash
mkdir -p /tmp/docs_f01/input
cat > /tmp/docs_f01/input/PROJECT_BRIEF.md <<'EOF'
Project: nano-router
A 200-line HTTP router with no dependencies. Target audience: developers
embedding a router into a sidecar binary where size matters.
EOF
```

Repeat the pattern for the other four fixtures, picking five distinct doc tasks (runbook section, API table, glossary, changelog compression). Diverse fixtures, not five variations of the same one.

## Step 7: Add the threshold parser test

Edit `tests/test_domain_drift_config.py`, append:

```python
def test_docs_domain_thresholds():
    t = domain_drift_thresholds("docs")
    assert t["persona_fidelity_floor"] == 7.5
    assert t["persona_completion_drop_threshold"] == 0.30
    assert t["decomposer_score_floor"] == 6.5
```

Run the suite:

```bash
python3 -m pytest tests/ -q
```

Green required.

## Step 8: Smoke the fixture suite

```bash
./director.py fixture run docs
```

All 5 fixtures must pass. If they don't, your `expected.json` assertions are wrong (the runner is correct by definition; D3). Tighten or relax the assertions until ground truth lines up.

## Step 9: One A/B run to baseline

```bash
DIRECTOR_DAILY_USD_CAP=5 ./director.py ab -y --domain docs "Write a runbook section at /tmp/docs_smoke/runbook.md describing how an operator restarts the foo daemon. Required: precondition check, restart command, post-condition check. Keep it under 30 lines."
```

Inspect: are persona ids `docs` for the persona-on arm? Persona fidelity at least 7/10? If yes, you have a working baseline. If not, the description in `personas.json` needs a rewrite. Iterate until baseline holds.

## Step 10: EVOLUTION_LOG entry

Append to `docs/EVOLUTION_LOG.md`:

```md
## new domain: docs (2026-05-03)
Scope: doc-writing tasks (README, runbook, API ref, glossary, changelog).
Persona: docs.
Threshold rationale: doc length is noisy, so completion drop and log inflation are loose; persona fidelity floor is strict (7.5) because doc voice is the main thing personas affect.
Initial fixture pass rate: 5/5 on baseline.
Initial A/B verdict: A persona-on fidelity 8.2/10, B persona-off fidelity 6.4/10. Persona kazanç on the smoke run.
```

## Step 11: Commit

```bash
git add docs/domains/docs.md domain_drift_config.json fixtures/docs/ \
        docs/EVOLUTION_LOG.md tests/test_domain_drift_config.py \
        personas.json personas.json.bak.*-pre-add
git commit -m "domain: add docs"
```

One commit covers the whole domain addition. Splitting into "add persona" + "add fixtures" + "add threshold" leaves master in inconsistent intermediate states (e.g. persona without fixtures, or domain without threshold parser test).

## Step 12: Open the PR

Push and open the PR. Fill in the `.github/PULL_REQUEST_TEMPLATE.md` checklist:

- Kind: New domain.
- Tests: pytest green; fixture run docs 5/5; smoke A/B verdict pasted.
- Documentation: EVOLUTION_LOG entry added; CLI flags unchanged so OPERATOR.md unchanged; no new env vars.
- Discipline: backups present (`personas.json.bak.*-pre-add`); EVOLUTION_LOG entry; no LLM-only assertions in fixtures.

In the smoke-run paste, include the verdict line and one or two task lines, not the whole log. Reviewers want signal, not noise.

## What the maintainer reviews

- The scope file (`docs/domains/docs.md`) is concrete, not aspirational.
- The five fixtures cover five distinct cases.
- Each fixture has a provenance line in `metadata.yaml`.
- `domain_drift_config.json` overrides only what differs from defaults.
- The persona description is terse and follows the AUTHORING rules (no recall lists, no verbose drift teaching).
- The smoke A/B verdict shows the persona is at least neutral, ideally a kazanç. If it shows a regression, the description needs another pass before merge.

If everything checks out, the PR merges. The first cron tick that runs `--auto-tighten --domain docs` will then start exercising the auto-tighten + auto-rollback loop on your domain. Welcome to the maintainer set.

## After merge

Within the first week, watch for:
- Fixture pass rate on `docs` drops below 4/5: investigate (was the merge tested under a different env?).
- Tighten event lands in `EVOLUTION_LOG.md` for `docs`: read the diff. Did the description get sharper or just shorter?
- Auto-rollback fires: the description regressed. Expected; the wiring works. Tune the threshold or the description.

If a maintainer reaches out about your domain a month after merge, that's the system working as designed. Domain health is the contributor's responsibility for the first 30 days, then it folds into shared maintenance.
