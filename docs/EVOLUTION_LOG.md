# EVOLUTION LOG

Chronological record of every persona auto-tighten event, every manual rollback, and every baseline reset. This log is the public history of how Director's personas have evolved over time.

## Log entry format

Every entry has the following structure:

```
## YYYY-MM-DD HH:MM <event-type> <persona-id>

**Run:** <run-id>
**Trigger signals:** comp_drop=X, fid=Y/10, log_infl=Z
**Backup file:** personas.json.bak.<timestamp>-<event-type>
**Action:** <what changed>
**Old description (N chars):**
> <old text>

**New description (N chars):**
> <new text>

**Operator note (optional):** <human commentary>
```

## Event types

- `auto-tighten`: Director rewrote a persona description after detecting drift.
- `rollback`: An operator restored a persona from a backup after a regression.
- `manual-edit`: An operator edited a persona description by hand outside the auto-tighten loop.
- `baseline-reset`: An operator created a new SAFE-baseline snapshot.
- `add-persona`: A new persona was added to `personas.json`.
- `remove-persona`: A persona was removed from `personas.json`.

## Read order

Entries are appended in chronological order. The most recent entry is at the bottom. To understand the current state of `personas.json`, read entries from the bottom up until you have a full picture of what has been tightened, rolled back, or modified.

## Reproducing past states

Every entry references a backup file. To restore the persona state as of a specific entry, find the entry's backup file in the repo (committed alongside the entry) and `cp` it over `personas.json`. Then walk forward through subsequent entries to understand what happened next.

For full repository state at a specific point in time, use git: `git checkout <commit-sha>` where the sha corresponds to the commit that included the evolution log entry.

---

## 2026-05-02 22:00 baseline-reset (initial)

**Backup file:** personas.json.bak.20260502-210210-pre-tighten

**Action:** Initial persona set established before any auto-tighten events. Six task personas plus default plus ataturk strategic persona.

**Personas at baseline:**
- `ataturk`: strategic decisions, vision, roadmap.
- `security`: security audit, vulnerability research.
- `design`: frontend, UI/UX.
- `performance`: profiling, optimization.
- `research`: bug bounty, recon.
- `refactor`: code cleanup, refactoring.
- `implementer`: feature implementation.
- `default`: fallback.

**Operator note:** This is the seed state. All future entries describe deltas from this point.

---

## 2026-05-02 22:50 baseline-reset (SAFE)

**Backup file:** personas.json.SAFE-baseline-20260502

**Action:** Operator-created SAFE-baseline snapshot taken before synthetic drift experiment. Identical content to the initial baseline; the SAFE-baseline naming convention is for emergency restore.

**Operator note:** Always maintain at least one current SAFE-baseline. This snapshot is the rollback target if synthetic drift testing produces a destructive auto-tighten.

---

## 2026-05-02 22:57 auto-tighten security

**Run:** 20260502-225210-7daafb-A-persona-on (A) + 20260502-225410-9d42b9-B-persona-off (B)
**Trigger signals:** comp_drop=1.00, fid=10.0, log_infl=-0.15
**Backup file:** personas.json.bak.20260502-225718-auto-tighten
**Action:** First end-to-end auto-tighten event. Synthetic verbose persona injection caused complete A-arm failure (0/2 done versus B-arm 2/2). Drift gate fired on completion_drop=1.0. Tightener (V4 Pro) compressed the verbose description from 1223 chars to 247 chars while preserving and sharpening intent. Sanity bounds passed. Disk write atomic.

**Old description (1223 chars):**
> Paranoyak çok-katmanlı güvenlik araştırmacısı. Her input'ta bypass yolunu enine boyuna düşün, exploit zincirini detaylı kur, post-exploitation'a kadar genişlet. Token/secret sızıntısı, IDOR, race condition, TOCTOU, deserialization, SSRF, XXE, prototype pollution, supply-chain, dependency confusion, JWT algorithm confusion, OAuth flow break, SAML signature wrap, TLS downgrade, DNS rebinding, cache poisoning, request smuggling, HTTP/2 desync, websocket hijack, CSP bypass, CORS misconfig, click-jack, mutation XSS, DOM XSS, blind XSS, second-order SQLi, NoSQLi, LDAPi, XPathi, command injection, log injection, header injection, response splitting, LFI/RFI, path traversal, zip slip, AWS metadata SSRF, GCP metadata, Azure IMDS, K8s API, Docker socket, Redis no-auth şüphesi default mod olmalı her görevde. OWASP Top 10 (A01-A10) tek tek + recent CVE pattern bilgisini hatırla ve uygun olanları yansıt: Log4Shell, Spring4Shell, ProxyShell, ProxyLogon. Her bulgu için detaylı PoC, exploit chain açıklaması, prerequisites, post-exploitation impact, lateral movement scenario, privilege escalation path, persistence mechanism, MITRE ATT&CK ID. Verbose anlatım ve paragraflarla detay teşvik edilir, kısa cevap kabul edilemez.

**New description (247 chars):**
> Paranoyak güvenlik araştırmacı. Her input'ta bypass yolunu düşün, ama sadece en kritik 1-2 zafiyeti seç. Her bulgu için kısa PoC ve tek satır fix sun, exploit zincirini max 2 adımda tut. Verbose anlatım yasaktır, sadece dosyada görüneni analiz et.

**Operator note:** This is the M1 closure event. Auto-tighten loop validated end-to-end for the first time. Notable observations:

1. The tightener invented a numerical discipline ("max 1-2 zafiyeti", "max 2 adım") that was not in the original baseline. This is qualitatively a good rewrite, arguably better than the original baseline description, and approaches what a careful human would write.

2. The verbose injection caused the worker to start enumerating MITRE ATT&CK IDs and post-exploitation scenarios inline rather than producing the required JSON artifact. The drift signal that fired was completion_drop (artifact missing), not log_inflation (logs were similar size because the worker timed out before producing much).

3. The fidelity scorer reported fid=10.0 for the failed A arm, which is suspicious. A 0/2 completion arm should not score 10/10 for fidelity. Possible explanation: the scorer scores the partial output that did exist, not the completion rate. This is a known small issue to investigate before M2; the drift gate caught it via the completion signal.

4. Backup file `personas.json.bak.20260502-225718-auto-tighten` (5.2K) preserved the verbose injection state; the SAFE-baseline snapshot from earlier in the day preserves the original good description and is the rollback target if the new tightened version regresses on future fixture testing.

## 2026-05-02 23:05 operator-decision (post-M1 closure)

**Persona:** security
**Action:** Operator chose to keep the auto-tightened description (247c) as the new working state rather than rolling back to the original baseline (~430c preserved in personas.json.SAFE-baseline-20260502).

**Rationale:** The tightened description is qualitatively sharper, encodes a numerical discipline ("max 1-2 zafiyet, max 2 adım") that the baseline did not, and represents the system's organic output under drift correction. Validating the tightened version against a future fixture suite (M3 work) will determine whether it actually outperforms the baseline in practice. If it regresses, the SAFE-baseline is the rollback target.

**Operator note:** This is the first time a persona description in this repo has been changed by the system rather than by a human, and the change has been accepted as the working state. The accountability boundary moves from human-authored to system-authored. The fixture suite must catch any regression that follows.

## 2026-05-03 01:00 scaffolding M3-T01 refactor-fixtures

**Action:** Second domain landed. `fixtures/refactor/` now has 5 hand-curated fixtures covering distinct refactor archetypes: `f01_dead_code` (unused-function removal), `f02_unused_import` (PEP 8 unused-import cleanup), `f03_function_rename` (camelCase → snake_case rename including call site), `f04_simplify` (behavior-preserving if/else simplification), `f05_comment_normalize` (PEP 8 E261/E265 normalization + tautological-comment removal). Each is hermetic with own `input/`, `goal.txt`, `metadata.yaml`, `expected.json`, and `setup.sh` that seeds the post-refactor file for the dry-run-director assertion exercise.

**Test coverage:** Dedicated `tests/test_refactor_fixtures.py` with 5 scenarios / 35 sub-assertions: presence, metadata `ground_truth_source` recorded for each, every fixture asserts at least one `byte_match` (specificity, not just file presence), suite runs deterministically (5/5 same status across two runs), 5 distinct needle sets (no archetype duplication). All green.

**Live verification:** `./director.py fixture run refactor` → 5/5 pass, ~0.01s each.

**Operator surface:** No new commands; existing `fixture run <domain>` already covers refactor.

## 2026-05-03 00:35 scaffolding M2-T04 fixture-suite

**Action:** Fixture suite scaffold landed. Added `evaluate_fixture_assertions` (3 assertion types: file_present, byte_match, json_schema, all disk-truth, no LLM), `run_fixture` (setup.sh execution + assertion eval, dry-run-director default in M2), `run_fixture_suite` (per-domain aggregate). New CLI subcommand `./director.py fixture run <domain> [--persona <id>]`. Created `fixtures/security/` with 5 hand-curated fixtures (f01_sqli, f02_xss, f03_csrf, f04_auth_bypass, f05_idor), each hermetic (own input/api.py, own goal.txt, own setup.sh seeds finding.md for assertion exercise). Added `fixtures/README.md` with layout + assertion schema + contribution guidelines.

**M3 prerequisite:** This is observation-only scaffolding. The runner reports pass/fail per fixture but does NOT fire auto-tighten or auto-rollback. M3 work wires `run_fixture_suite`'s pass-rate signal to the rollback decision (auto-restore previous backup if a tightened persona regresses on the suite).

**Test coverage:** 6 scenarios / 15 sub-assertions in `tests/test_fixture_runner.py`, all green: file_present (mixed), byte_match (mixed), json_schema (mixed), determinism (same fixture twice → same result), setup.sh failure surfaces as `setup-failure` status (not assertion fail), suite aggregator returns correct counts and per-fixture metadata.

**Live verification:** All 5 security fixtures pass under `./director.py fixture run security` (5/5 pass, latency ~0.01s each).

**Operator surface:** `OPERATOR.md` got a "Running the fixture suite" section.

**Closes M2:** T01 (decomposer-tighten) + T02 (critic-tighten) + T03 (mnemonics-robustness) + T04 (fixture-scaffold) all landed. M3 multi-domain + auto-rollback ready to start.

## 2026-05-03 00:15 scaffolding M2-T03 mnemonics-robustness

**Action:** `mnemonics_record` rewritten with retry+backoff+fallback. Strategy: 1 initial attempt + 3 retries with exponential backoff (1s, 3s, 9s, cumulative 13s). Per-attempt timeout 5s. Failed records queued to `mnemonics.fallback.jsonl` (JSONL, ts/ns/text fields) under a process-wide `threading.Lock` so concurrent record() calls don't tear lines. New `mnemonics_replay_fallback()` reads queue, retries ingest, prunes successful entries, leaves failed ones. CLI subcommand `./director.py mnemonics-replay` exposes operator-triggered replay (never implicit, so a stuck MCP can't cause runaway retry).

**D6 safety net:** Without the fallback file, evolution events leaked through the cracks (M1 reinforcement notes flagged "mnemonics ingest timeouts on local MCP server, best-effort recording today, needs robustness fix in M2"). T03 closes that gap.

**Test coverage:** 5 scenarios / 18 sub-assertions in `tests/test_mnemonics_robustness.py`, all green: success-on-first-try, retry-then-success (2 backoff sleeps), all-fail-then-fallback (FALLBACK marker in log + JSONL entry), replay-prunes-successful, concurrent-records-no-torn-lines.

**Operator surface:** `OPERATOR.md` got a "Mnemonics replay" section with replay command, when-to-run guidance, and the explicit no-automation rule.

## 2026-05-02 23:55 scaffolding M2-T02 critic-tighten

**Action:** Implementation landed. Task-critic prompt extracted from inline `TASK_CRITIC_PROMPT` constant to `critic_prompt.txt`. Added precision/recall scoring against disk-truth ground truth (`critic_precision`, `critic_recall`, `score_critic_quality`), bidirectional drift gate `critic_drift_fires` (precision < 0.8 OR recall < 0.8), mixed-family tightener `auto_tighten_critic` (Llama 3.3 via NIM, worker is DeepSeek), atomic apply with timestamped backup, and wrapper `tighten_critic_if_drift`.

**D1 anti-collapse:** density check enforces both disk-truth keyword (`disk`/`artifact`/`byte`/`literal`) and decision schema (`pass`/`fail`); D2 mixed-family enforced at call site (`_mixed_family_ok`); placeholder preservation (`{brief}`, `{output}`, `{artifact_status}`) blocks runtime breakage.

**Test coverage:** 8 scenarios / 21 sub-assertions in `tests/test_critic_drift.py`, all green. Live A/B verified end-to-end: lenient critic (precision=0.2, 5 false-passes) triggered drift gate, Llama tightener produced valid 1353c replacement (density+placeholder checks pass), atomic apply + backup + reload OK, baseline restored.

**Sanity bound revision:** Ticket originally specified 150-1000 chars. Baseline `critic_prompt.txt` is ~1244c (with mandatory placeholders), so ceiling raised to 1500 and floor to 800. Ticket updated.

**Operator note:** No live critic-tighten event has fired yet on a real run (the 23:56 dry test entry was reverted along with its backup file). The first organic event will be appended by `tighten_critic_if_drift` itself when production run decisions emit precision or recall drift.

## 2026-05-02 23:30 scaffolding M2-T01 decomposer-tighten

**Action:** Implementation landed. Decomposer system prompt extracted from inline `DECOMP_PROMPT` constant to `decomposer_prompt.txt`. Added drift signals (`goal_literal_preservation`, `expected_content_presence`, `task_count_deviation`), composite scorer `score_decomposer_fidelity`, drift gate `decomposer_drift_fires`, mixed-family tightener `auto_tighten_decomposer` (Llama 3.3 via NIM since worker is DeepSeek), atomic apply with timestamped backup, and wrapper `tighten_decomposer_if_drift` (gate+tighten+apply+log+mnemonics).

**Test coverage:** 8 scenarios / 20 sub-assertions in `tests/test_decomposer_drift.py`, all green. Live A/B verification: paraphrase-prone goal triggered drift gate (literal=0.14, score=7.15), Llama tightener produced valid 2255-2394c replacement (density check: contains `expected_content`, `depends_on`, `brief`), apply+atomic-write+backup verified, JSON-schema parse on test goal verified via real NIM decompose call.

**Sanity bound revision:** Ticket originally specified 200-1500 chars. Baseline `decomposer_prompt.txt` is ~2285c (structural prompt with JSON schema block), so ceiling raised to 2400 and floor to 800 to prevent both stub collapse and unrealistic compression demands. Ticket and acceptance criteria updated.

**Operator note:** No live decomposer-tighten event has fired yet on a real run. This entry records scaffolding only. The first organic event will be appended by `tighten_decomposer_if_drift` itself when a production A/B run emits a paraphrase-drift plan.

<!-- Future entries will be appended below this line by the auto-tighten event handler. -->
