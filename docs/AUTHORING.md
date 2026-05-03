# Authoring Guide: Personas, Domains, and Drift Signals

This is the deep how-to that `CONTRIBUTING.md` points at. Read `docs/DISCIPLINES.md` first; everything below assumes those rules.

## Authoring a new persona

A persona is a triple: an identifier, a list of trigger keywords, and a description that gets prepended to a worker task's brief whenever the brief matches a trigger.

### Step by step

1. **Pick the id.** Lowercase, no spaces, short. Existing ids: `ataturk`, `security`, `design`, `performance`, `research`, `refactor`, `implementer`, `default`. Avoid synonyms; if your domain overlaps with an existing persona, reuse it.

2. **Back up `personas.json`.**
   ```bash
   cp personas.json personas.json.bak.$(date +%Y%m%d-%H%M%S)-pre-add
   ```
   Director's auto-tighten loop expects every mutation to leave a backup. Manual additions must follow the same rule.

3. **Add the entry.** Required fields: `name` (display string), `trigger` (list of keyword strings), `description` (50 to 500 chars). Example:
   ```json
   "auditor": {
     "name": "Quality Auditor",
     "trigger": ["audit", "compliance", "checklist", "iso", "soc2"],
     "description": "Audit-first stance. Verify each claim against the source before writing it. Cite the file:line you read. No assumptions, no extrapolation. If the source is missing, say so explicitly."
   }
   ```

4. **Description rules (hard-won from the A/B harness).**
   - **Disciplines win, recall lists lose.** "Surgical changes only", "no assumptions", "verify against source" outperforms "OWASP Top 10 + CVE pattern recall" by a wide margin.
   - **No verbose teaching.** The worker LLM does not need a primer on the domain. Tell it the *constraint*, not the syllabus.
   - **No "encourage maximum thoroughness".** The auto-tightener will rewrite this anyway after one A/B run shows it caused completion drop.
   - **Turkish or English, but commit to one.** Mixed-language descriptions confuse the persona-fidelity scorer.

5. **Validate JSON.**
   ```bash
   python3 -c "import json; json.load(open('personas.json'))"
   ```

6. **Add at least one fixture** under the relevant domain directory (see "Authoring a new domain" for fixture layout). Without a fixture, regression detection is impossible and the auto-rollback wiring cannot protect this persona.

7. **Smoke run.**
   ```bash
   ./director.py ab -y --domain <domain> "<one canonical goal that should trigger this persona>"
   ```
   Inspect the output: did the worker actually pick this persona? `state["tasks"][i]["persona_id"]` should match. Persona fidelity score should be at least 7/10 on the persona-on arm.

8. **Commit.**
   ```bash
   git add personas.json fixtures/<domain>/<new_fixture_id>/
   git commit -m "persona: add <id>"
   ```

### When the auto-tightener will rewrite your persona

After three A/B runs against the persona's domain fixtures, the tightener compares completion rate, log size, and persona fidelity between persona-on and persona-off arms. If your description reduces completion or inflates logs, it gets rewritten on the next A/B with `--auto-tighten`. The original is preserved in `personas.json.bak.<ts>-pre-tighten` and an entry lands in `docs/EVOLUTION_LOG.md`. If the rewrite regresses fixture pass rate, M3-T03 auto-rollback restores the backup.

This is normal and expected. Persona descriptions are state, not configuration. If you have strong preferences about the wording, capture them as fixtures, not as comments in `personas.json`.

## Authoring a new domain

A domain is a topic area with its own fixture suite and (usually) its own persona. Domains let Director apply different drift thresholds: security tolerates verbose output (false negatives are catastrophic), refactor punishes scope creep (every dropped task is a defect).

### Step by step

1. **Define scope.** Open `docs/domains/<name>.md` (create the directory if it does not exist):
   ```md
   # Domain: <name>
   Scope: ...
   Target user: ...
   Success looks like: ...
   ```
   Without scope written down, fixture authors will drift. The first three sentences are the contract.

2. **Persona.** Either reuse an existing one (set the `persona` field in your fixture metadata to point at it) or add a new one (see above).

3. **Drift threshold set.** Edit `domain_drift_config.json`:
   ```json
   "docs": {
     "_doc": "Documentation tasks tolerate large completion drops (long-form writing varies in length). Tighten on persona fidelity floor; loose on log inflation.",
     "decomposer_score_floor": 6.5,
     "persona_completion_drop_threshold": 0.30,
     "persona_log_inflation_threshold": 0.60,
     "persona_fidelity_floor": 7.0
   }
   ```
   Any key you omit falls back to `DRIFT_DEFAULTS` in `director.py`. Override only what differs from default; do not copy the defaults verbatim.

4. **Fixture suite.** Create `fixtures/<domain>/` with at least 5 fixtures. Each fixture follows the layout in `fixtures/README.md`. Hard rules:
   - Hermetic: `goal.txt` references absolute paths inside the fixture only. No external network, no system-wide state.
   - Deterministic: same fixture twice = same pass/fail. If it flakes, fix the assertion.
   - Provenance: `metadata.yaml` records where ground truth came from (CVE id, postmortem doc, hand-label by operator).
   - Diverse: 5 fixtures covering 5 distinct cases of the domain. Five variations of the same case is one fixture, not five.

5. **Threshold parser test.** Add a case to `tests/test_domain_drift_config.py` that confirms your domain block parses and yields the expected threshold values. Without this test, a typo in the JSON ships silently.

6. **Smoke run.**
   ```bash
   ./director.py fixture run <domain>
   ```
   Every fixture must pass. If they don't, your fixtures are wrong (not Director's runner). Tighten the assertions or fix the goal text.

7. **EVOLUTION_LOG entry.**
   ```md
   ## new domain: <name> (YYYY-MM-DD)
   Scope: ...
   Persona: <id>
   Threshold rationale: ...
   Initial fixture pass rate: 5/5 on baseline.
   ```
   `docs/EVOLUTION_LOG.md` is the audit trail. Skipping this entry breaks discipline D6 and downstream auditors will not know your domain exists.

8. **Commit (single ticket, four artifacts).**
   ```bash
   git add docs/domains/<name>.md domain_drift_config.json fixtures/<domain>/ docs/EVOLUTION_LOG.md tests/test_domain_drift_config.py
   git commit -m "domain: add <name>"
   ```

### When a domain becomes safe to run unattended

Per `OPERATOR.md` Cron section: only after 5+ fixtures land, the threshold set is in `domain_drift_config.json`, and `DIRECTOR_DAILY_USD_CAP` is set in the cron environment. Until then keep new domains operator-observed.

## Authoring a new drift signal

A drift signal is a numeric or boolean field on the `signals` dict consumed by `tighten_persona_if_drift` (and its sibling tighteners for decomposer and critic). Existing signals: `completion_drop`, `log_inflation`, `avg_fidelity`, `absolute_completion_fail`, `a_fail_count`.

### Step by step

1. **Justify it.** Open an issue first. New signals enlarge the gate's surface area; the tightener becomes harder to reason about with each additional dimension. Be ready to defend why an existing signal cannot capture the case.

2. **Implement the measurement.** Whatever computes the signal goes near the existing computation in `cmd_ab` (search for `signals = {`). Keep it side-effect free: read state, return a number.

3. **Add a threshold floor.** New signals get a default floor in `DRIFT_DEFAULTS` (search `director.py`) and an optional override slot in `domain_drift_config.json`'s schema. Default floors should be conservative (gate fires only on clear regression).

4. **Wire it into the tightener.** Inside `tighten_persona_if_drift`, append a reason line:
   ```python
   if my_signal > thresholds["my_signal_floor"]:
       reasons.append(f"my_signal={my_signal:.2f}>{thresholds['my_signal_floor']}")
   ```

5. **Unit test the gate firing.** `tests/test_persona_drift_gate.py` (or create) needs a case where only your new signal is over its floor and `tighten_persona_if_drift` returns `fired=True` with your reason in `reasons[]`.

6. **Document.**
   - `docs/EMBEDDING.md` signals dict reference (the `tighten_persona_if_drift` example).
   - `docs/AUTHORING.md` (this file): add the signal to the "Existing signals" list above.
   - `docs/DISCIPLINES.md`: if the signal touches discipline boundaries (e.g. introduces a new dimension of "what counts as drift"), add a paragraph.

7. **Smoke**: re-run an A/B that historically did not trigger `fired`; with your new signal computing nonzero, confirm the verdict changes.

8. **Commit.**
   ```bash
   git commit -m "signal: add <name> to persona drift gate"
   ```

### Why the unit test is non-negotiable

The drift gate is the single most opinionated piece of Director. A new signal that fires on a false positive starts cascading auto-tightens, which trip auto-rollbacks, which pollute `EVOLUTION_LOG.md` with churn. The unit test is the cheapest way to catch a sign error or off-by-one before it hits production state.

## See also

- `fixtures/README.md`, fixture layout, assertion schema, determinism rule.
- `docs/SAMPLE_CONTRIBUTION.md`, end-to-end walkthrough of adding a new domain.
- `docs/DISCIPLINES.md`, D1 to D6 design rules every contribution respects.
- `docs/OPERATOR.md`, what the operator does after your PR lands.
