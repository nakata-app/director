#!/usr/bin/env python3
"""
Project Director — autonomous multi-session orchestrator.

Usage:
  director run "hedef metni" [--timeout MIN] [--retries N] [-y]
  director status [run-id]
  director tail <run-id>
  director cancel <run-id>

Lifecycle:
  1. Decompose goal into tasks (Opus 4.7 high → DeepSeek V4 Pro native → NVIDIA NIM fallback)
  2. Show plan to user; user can [Y]es / [n]o / [d]üzelt-and-redecompose
  3. Spawn `claude -p` background processes per task (deps gated)
  4. Poll: process status, timeout enforcement, dependency promotion
  5. Failed/timeout → 1 auto-retry, then mark fail
  6. On all done OR blocker → osascript notif + `say` voice + final report
  7. Seal own run's live-feed events (closed=true) so next run isn't polluted
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

DIRECTOR_HOME = Path(os.path.expanduser("~/.claude/director"))
RUNS_DIR = DIRECTOR_HOME / "runs"
LIVE_DIR = Path(os.environ.get("MNEMONICS_LIVE_DIR", os.path.expanduser("~/.mnemonics/live")))
NIM_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_MODEL = "deepseek-ai/deepseek-v4-pro"
# DeepSeek native API (primary; auto-fallback to NIM on 402 balance / 429 rate / 5xx)
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-pro"
POLL_INTERVAL = int(os.environ.get("DIRECTOR_POLL_SEC", "30"))
DEFAULT_TIMEOUT_MIN = int(os.environ.get("DIRECTOR_TASK_TIMEOUT_MIN", "30"))
DEFAULT_MAX_RETRIES = int(os.environ.get("DIRECTOR_MAX_RETRIES", "1"))

# --- Opus 4.7 high (used for decompose only, daily quota capped) ---
OPUS_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPUS_URL = "https://api.anthropic.com/v1/messages"
OPUS_MODEL = "claude-opus-4-7"
OPUS_DAILY_LIMIT = int(os.environ.get("DIRECTOR_OPUS_DAILY_LIMIT", "4"))
OPUS_QUOTA_PATH = DIRECTOR_HOME / "opus_quota.json"

# --- ElevenLabs TTS (Mythos v2 = TR Ultron clone) ---
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("DIRECTOR_VOICE_ID", "o7NAQIWE4USENtam8Vkx")  # Mythos v2
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
TTS_CACHE_DIR = DIRECTOR_HOME / "tts_cache"

# --- live feed write (director self-feed → joins peer awareness) ---
def _project_id(project: str) -> str:
    import hashlib
    h = hashlib.sha1(project.encode()).hexdigest()[:12]
    base = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(project)) or "root"
    return f"{base}-{h}"

def _project_root(cwd: str) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, timeout=2,
        )
        return out.decode().strip() or cwd
    except Exception:
        return cwd

MNEMONICS_LOG_PATH = DIRECTOR_HOME / "mnemonics.log"
MNEMONICS_FALLBACK_PATH = DIRECTOR_HOME / "mnemonics.fallback.jsonl"
MNEMONICS_FALLBACK_LOCK = threading.Lock()
# M2-T03: 1 initial + 3 retries with exponential backoff. Cumulative wait budget = 13s.
MNEMONICS_RETRY_BACKOFF = (1, 3, 9)
MNEMONICS_PER_ATTEMPT_TIMEOUT = 5

def _mnemonics_log(line: str) -> None:
    try:
        with open(MNEMONICS_LOG_PATH, "a") as lf:
            lf.write(line)
    except Exception:
        pass

def _mnemonics_fallback_append(text: str, ns: str) -> None:
    """Atomic append to JSONL fallback file under a process-wide lock so
    concurrent record() calls don't tear lines."""
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "ns": ns, "text": text}
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with MNEMONICS_FALLBACK_LOCK:
        try:
            with open(MNEMONICS_FALLBACK_PATH, "a") as ff:
                ff.write(line)
        except Exception as e:
            _mnemonics_log(f"[{datetime.now().isoformat(timespec='seconds')}] FALLBACK-WRITE-FAIL ns={ns}: {e}\n")

def _mnemonics_attempt(text: str, ns: str) -> tuple[bool, str]:
    """Single ingest attempt. Returns (ok, error_string)."""
    env = {**os.environ, "HF_HUB_DISABLE_PROGRESS_BARS": "1", "TRANSFORMERS_VERBOSITY": "error"}
    try:
        proc = subprocess.run(
            ["mnemonics", "ingest", "--ns", ns, text],
            check=False, timeout=MNEMONICS_PER_ATTEMPT_TIMEOUT, env=env,
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            return (True, "")
        return (False, f"rc={proc.returncode}: {proc.stderr[-400:]}")
    except subprocess.TimeoutExpired:
        return (False, "TIMEOUT")
    except Exception as e:
        return (False, f"ERR: {e}")

def mnemonics_record(text: str, ns: str = "director") -> None:
    """Persist a record into mnemonics DB with retry+fallback (M2-T03).

    Strategy: 1 initial attempt + up to 3 retries with exponential backoff
    (1s, 3s, 9s) on failure. Cumulative backoff budget = 13 seconds.
    If all attempts fail, write to mnemonics.fallback.jsonl instead of
    silently dropping the record. Operator runs `director mnemonics-replay`
    to retry the fallback batch later.

    D6 discipline: the fallback file is the safety net for the public
    evolution log — without it, evolution events leak through the cracks.
    """
    if not text.strip():
        return
    last_err = ""
    attempts = 1 + len(MNEMONICS_RETRY_BACKOFF)
    for i in range(attempts):
        ok, err = _mnemonics_attempt(text, ns)
        if ok:
            return
        last_err = err
        if i < len(MNEMONICS_RETRY_BACKOFF):
            time.sleep(MNEMONICS_RETRY_BACKOFF[i])
    _mnemonics_fallback_append(text, ns)
    _mnemonics_log(
        f"[{datetime.now().isoformat(timespec='seconds')}] FALLBACK ns={ns} "
        f"after {attempts} attempts ({last_err[:200]}): {text[:120]}\n"
    )

def mnemonics_replay_fallback() -> dict:
    """Read mnemonics.fallback.jsonl, retry ingest each entry, prune successful ones.
    Failed entries stay in the file for the next replay. Returns {ingested, remaining, errors}.
    Operator-triggered (CLI subcommand `mnemonics-replay`); never called implicitly.
    """
    report = {"ingested": 0, "remaining": 0, "errors": []}
    with MNEMONICS_FALLBACK_LOCK:
        if not MNEMONICS_FALLBACK_PATH.exists():
            return report
        raw = MNEMONICS_FALLBACK_PATH.read_text()
    leftover = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            leftover.append(line)  # preserve malformed lines for manual inspection
            report["errors"].append("malformed-json")
            continue
        ok, err = _mnemonics_attempt(rec.get("text", ""), rec.get("ns", "director"))
        if ok:
            report["ingested"] += 1
        else:
            leftover.append(json.dumps(rec, ensure_ascii=False))
            report["errors"].append(err[:80])
    report["remaining"] = len(leftover)
    with MNEMONICS_FALLBACK_LOCK:
        if leftover:
            MNEMONICS_FALLBACK_PATH.write_text("\n".join(leftover) + "\n")
        else:
            try:
                MNEMONICS_FALLBACK_PATH.unlink()
            except FileNotFoundError:
                pass
    return report

def director_event(run_id: str, cwd: str, kind: str, note: str, path: str = "") -> None:
    """Emit a director-level event into the live peer feed.
    Other sessions (Claude, Metis) will see what the orchestrator is doing
    in real time via their UserPromptSubmit observer hook."""
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    project = _project_root(cwd)
    feed = LIVE_DIR / f"{_project_id(project)}.jsonl"
    e = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": run_id[-8:],
        "agent": "director",
        "project": project,
        "kind": kind,         # "plan" | "spawn" | "complete" | "blocked"
        "path": path,
        "note": note,
        "director_run": run_id,
    }
    try:
        with open(feed, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    except Exception:
        pass

# --- key resolve ---
def resolve_nim_key() -> str:
    if NIM_API_KEY:
        return NIM_API_KEY
    cfg = Path.home() / ".metis" / "config.toml"
    if cfg.exists():
        try:
            import tomllib
            with open(cfg, "rb") as f:
                data = tomllib.load(f)
            return data.get("api_keys", {}).get("NVIDIA_API_KEY", "")
        except Exception:
            return ""
    return ""

# --- decomposition ---
DECOMPOSER_PROMPT_PATH = Path(os.path.expanduser("~/.claude/director")) / "decomposer_prompt.txt"

def load_decomposer_prompt() -> str:
    """Read decomposer system prompt from disk. Mutated by auto_tighten_decomposer.
    M2-T01: extracted from inline constant so the auto-tighten loop can rewrite it."""
    if not DECOMPOSER_PROMPT_PATH.exists():
        raise FileNotFoundError(f"decomposer_prompt.txt not found at {DECOMPOSER_PROMPT_PATH}")
    return DECOMPOSER_PROMPT_PATH.read_text()

DECOMP_PROMPT = load_decomposer_prompt()

COMPLEXITY_TIMEOUT = {"low": 10, "med": 30, "high": 60}

# --- persona swarm ---
PERSONAS_PATH = DIRECTOR_HOME / "personas.json"

def load_personas() -> dict:
    if not PERSONAS_PATH.exists():
        return {}
    try:
        return json.loads(PERSONAS_PATH.read_text())
    except Exception:
        return {}

def select_persona(brief: str, hint: str = "") -> tuple[str, dict]:
    """Return (persona_id, persona_dict). Hint = NIM's suggested persona id."""
    personas = load_personas()
    if not personas:
        return ("default", {"name": "Default", "description": ""})
    # Honor explicit hint if it matches a known persona
    if hint and hint in personas:
        return (hint, personas[hint])
    text = brief.lower()
    scores: dict[str, int] = {}
    for pid, p in personas.items():
        if pid == "default":
            continue
        triggers = p.get("trigger", [])
        score = sum(1 for t in triggers if t.lower() in text)
        if score > 0:
            scores[pid] = score
    if not scores:
        return ("default", personas.get("default", {"name": "Default", "description": ""}))
    best = max(scores, key=scores.get)
    return (best, personas[best])

REVISE_PROMPT = """Önceki plan'ı kullanıcı geri bildirimine göre düzenle. Aynı JSON formatında çıktı ver.

Önceki plan:
{prev_plan}

Kullanıcı feedback'i:
{feedback}

Hedef hâlâ: {goal}
"""

CRITIC_PROMPT = """Sen agresif bir plan eleştirmenisin. Önündeki decomposition planını sıkı incele.

Hedef: {goal}

Plan:
{plan}

Şu açılardan eleştir:
- Eksik task var mı? (hedef tam karşılanıyor mu)
- Fazla/gereksiz task var mı?
- Bir task çok büyük mü? (1-2 saatten uzun sürmesi muhtemel)
- Dependency'ler doğru mu? (paralelleşebilir olanlar serialize mi olmuş)
- Brief'ler net mi? (claude tek bakışta anlamalı)
- timeout_min değerleri makul mü?

Çıktı SADECE valid JSON, başka metin yok:
{
  "verdict": "ok" | "revise",
  "issues": ["sorun 1", "sorun 2", ...],
  "suggestions": "Plan'ı nasıl iyileştirmeli, 2-3 cümle"
}

Eğer plan iyiyse verdict='ok' ver, issues boş olsun. Aksi halde 'revise' ver."""

REVISE_FROM_CRITIC_PROMPT = """Plan'ı eleştirmenin geri bildirimine göre düzenle. Aynı JSON formatında çıktı ver.

Önceki plan:
{prev_plan}

Eleştirmen verdiği:
issues: {issues}
suggestions: {suggestions}

Hedef hâlâ: {goal}
"""

PERSONA_TIGHTEN_PROMPT = """Sen bir persona description sertleştirme uzmanısın. Aşağıdaki persona "verbose drift" yarattı: persona-on koşulduğunda child output'ları persona-off'a göre uzadı ve task chain incomplete kaldı (timeout/eksik).

Doğrulanmış formül (Director A/B harness verileri):
- Recall listeleri yasak: "X, Y, Z pattern'larını yansıt", "A, B, C, D terimleriyle düşün" gibi inventory'ler child'ı "her şeyi max derinlikte üret" moduna sokar.
- Format: TAVIR + SOMUT KURAL + "verbose YASAK".
- Disiplin kuralları (surgical, behavior preserve, kanıtlı bulgu) → KAL.
- Recall listesi → KALDIR veya max 2 madde.
- "KISA PoC max N satır", "tek satır fix", "sadece dosyada görünen X" gibi explicit boundary'ler EKLE.
- Toplam uzunluk hedefi: 200-280 karakter (eski sertleştirilmiş security: 258c).

[ESKİ DESCRIPTION]
{old_desc}

[ÇOCUK OUTPUT ÖRNEĞİ — verbose drift kanıtı, ilk 1500 char]
{sample}

Yeni description üret. SADECE yeni description'ı tek paragraf olarak yaz, başka hiçbir metin/açıklama yok. Markdown yok, kod fence yok.
"""

CRITIC_TIGHTEN_PROMPT = """Görev: Aşağıdaki "ESKİ KRİTİK PROMPT" bir LLM'e verilen sistem talimatıdır (task-critic). Bu critic precision veya recall drift'i yarattı: ya yanlış pass'leri (false-pass) çoğalttı (precision düştü), ya da gerçekten geçen task'ları fail etti (recall düştü). Sertleştirilmiş halini yeniden yazacaksın.

ÖNEMLİ — Çıktı kuralları:
- Çıktın bir TALIMAT METNİDİR ("Sen bir QA agent'ısın..." gibi başlar). JSON üretme. Verdict örneği yazma.
- Uzunluk: 800-1500 karakter arası.
- "pass", "fail", "artifact" veya "disk" kelimeleri MUTLAKA geçmeli (D1 anti-collapse: disk-truth criterion korunmalı).
- "byte-match", "literal" benzeri disk-truth'a referans MUTLAKA olmalı.
- JSON çıktı şeması (verdict, reason alanları) korunmalı.
- {brief}, {output}, {artifact_status} placeholder'ları MUTLAKA korunmalı (template substitution noktaları).
- Markdown code fence YASAK.

[ESKİ KRİTİK PROMPT — drift gözlendi, sertleştir]
{old_prompt}

[DRIFT KANITI — false-pass veya false-fail örnekleri]
Precision: {precision} ({n_pass_calls} PASS verildi, {true_passes} doğru)
Recall: {recall} ({n_truth_pass} truth-PASS, kaçırılan: {missed_passes})
Yanlış kararlar:
{drift_examples}

ŞİMDİ yeni TALIMAT METNINI yaz, 800-1500 karakter, placeholder'lar korunmuş, disk-truth criterion sert:
"""

DECOMPOSER_TIGHTEN_PROMPT = """Görev: Aşağıdaki "ESKİ DECOMPOSER PROMPT" bir LLM'e verilen SİSTEM TALIMATIDIR (decomposer prompt). Drift gözlendi: hedef literal'leri brief'lerde paraphrase edildi ve/veya expected_content alanı atlandı. Sen bu TALIMAT METNINI sertleştirilmiş haliyle yeniden yazacaksın.

ÖNEMLİ — Çıktı kuralları:
- Çıktın bir TALIMAT METNİDİR ("Sen bir project director'sın..." gibi başlar). JSON ÇIKARMA. Plan örneği üretme. Sadece yeni decomposer prompt metni.
- Uzunluk: 1200-1500 karakter arası.
- "expected_content", "depends_on", "brief" kelimeleri MUTLAKA geçmeli (içerik yoğunluğu kontrolü).
- "paraphrase YASAK" kuralı net belirtilmeli.
- JSON şema bloğu korunmalı: id, title, brief, cwd, depends_on, complexity, persona, timeout_min, expected_artifacts, expected_content alanları.
- Persona seçenekleri listesi (security, design, performance, research, refactor, implementer, ataturk, default) korunmalı.
- Markdown code fence YASAK.

[ESKİ DECOMPOSER PROMPT — drift gözlendi, sertleştir]
{old_prompt}

[DRIFT KANITI — bu örnek goal verildiğinde eski prompt aşağıdaki paraphrase'lı planı üretti]
Goal: {sample_goal}
Üretilen Plan: {sample_plan}

ŞİMDİ yeni TALIMAT METNINI yaz (bir LLM'in sistem prompt'u olacak), 1200-1500 karakter:
"""

PERSONA_FIDELITY_PROMPT = """Sen bir kalite değerlendiricisin. Bir alt-task'ın child output'unun, **verilen persona description'ına sadakatini** 0-10 ölçeğinde puanlayacaksın.

Persona kimliği: {persona_id}
Persona name: {persona_name}
Persona description (uyulması beklenen kurallar/tavır):
{persona_desc}

Task brief'i:
{brief}

Child output (claude/metis log'u):
{output}

Değerlendirme rubrik (her biri 0-2 puan, toplam 0-10):
- (0-2) Output persona'nın tavrını/tonunu yansıtıyor mu? (örn. implementer "pragmatik+ölçülü", security "paranoyak+kanıtlı", refactor "surgical+behavior preservation")
- (0-2) Output persona description'daki imperatif kurallara uydu mu? ("ASLA", "SADECE", "MUTLAKA" vurguları)
- (0-2) Output, default-Claude'un yapmayacağı bir karar/duruş aldı mı? (persona değeri = ayırt edici davranış)
- (0-2) Output gerekçe/yargı kalitesi persona'nın uzmanlık alanına uygun mu?
- (0-2) Output istenmeyen davranıştan kaçındı mı? (örn. implementer over-engineering yapmadı, refactor scope creep yapmadı)

Output SADECE valid JSON:
{
  "score": 0-10,
  "reasoning": "1-2 cümle, hangi rubrik kriterleri kazandırdı/kaybettirdi"
}
"""

CRITIC_PROMPT_PATH = Path(os.path.expanduser("~/.claude/director")) / "critic_prompt.txt"

def load_critic_prompt() -> str:
    """Read task-critic system prompt from disk. Mutated by auto_tighten_critic.
    M2-T02: extracted from inline constant so the auto-tighten loop can rewrite it."""
    if not CRITIC_PROMPT_PATH.exists():
        raise FileNotFoundError(f"critic_prompt.txt not found at {CRITIC_PROMPT_PATH}")
    return CRITIC_PROMPT_PATH.read_text()

TASK_CRITIC_PROMPT = load_critic_prompt()

def _post_chat(url: str, model: str, key: str, messages: list[dict]) -> str:
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": 4000,
        "temperature": 0.2,
    }
    import urllib.request, urllib.error
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()

def _is_deepseek_fallback_error(e: Exception) -> bool:
    """402 (insufficient balance), 429 (rate limit), 5xx (server) → fallback to NIM."""
    import urllib.error
    if isinstance(e, urllib.error.HTTPError):
        return e.code in (402, 429) or 500 <= e.code < 600
    return True  # network/timeout/other → also fallback

def call_v4pro(messages: list[dict]) -> str:
    """V4 Pro call: DeepSeek native first, NVIDIA NIM fallback on balance/rate/server errors."""
    last_err = None
    if DEEPSEEK_API_KEY:
        try:
            return _post_chat(DEEPSEEK_URL, DEEPSEEK_MODEL, DEEPSEEK_API_KEY, messages)
        except Exception as e:
            if _is_deepseek_fallback_error(e):
                print(f"⚠️  DeepSeek native hatası ({e}), NVIDIA NIM'e düşüyorum...", file=sys.stderr)
                last_err = e
            else:
                raise
    key = resolve_nim_key()
    if not key:
        if last_err:
            raise RuntimeError(f"DeepSeek başarısız ({last_err}) ve NVIDIA_API_KEY de yok.")
        raise RuntimeError("Ne DEEPSEEK_API_KEY ne de NVIDIA_API_KEY var.")
    return _post_chat(NIM_URL, NIM_MODEL, key, messages)

# Backward-compat alias (older call sites)
call_nim = call_v4pro

# --- Opus 4.7 high (decompose-only, daily-capped) ---
def _opus_quota_today_used() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    if not OPUS_QUOTA_PATH.exists():
        return 0
    try:
        data = json.loads(OPUS_QUOTA_PATH.read_text())
    except Exception:
        return 0
    if data.get("date") != today:
        return 0
    return int(data.get("used", 0))

def _opus_quota_increment() -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    used = _opus_quota_today_used() + 1
    try:
        OPUS_QUOTA_PATH.write_text(json.dumps({"date": today, "used": used}))
    except Exception:
        pass

def opus_quota_available() -> bool:
    return bool(OPUS_API_KEY) and _opus_quota_today_used() < OPUS_DAILY_LIMIT

def call_opus_high(system: str, user: str) -> str:
    """Opus 4.7 with adaptive thinking + high effort. Used for decompose only.
    Increments daily quota on success; raises on failure (caller fallbacks to V4 Pro)."""
    if not OPUS_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY yok; Opus çağrılamaz.")
    body = {
        "model": OPUS_MODEL,
        "max_tokens": 8000,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    import urllib.request
    req = urllib.request.Request(
        OPUS_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": OPUS_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    # Walk content blocks, return first text block
    text_parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    text = "\n".join(text_parts).strip()
    if not text:
        raise RuntimeError("Opus boş yanıt döndü")
    _opus_quota_increment()
    # Cost tracking — append usage line per call
    try:
        usage = data.get("usage", {})
        log_path = DIRECTOR_HOME / "opus_usage.jsonl"
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
        cache_read = int(usage.get("cache_read_input_tokens", 0))
        cache_write = int(usage.get("cache_creation_input_tokens", 0))
        # Pricing assumption: Opus 4 tier ($15/M in, $75/M out, $1.50/M cache_read, $18.75/M cache_write)
        cost = (in_tok * 15 + out_tok * 75 + cache_read * 1.5 + cache_write * 18.75) / 1_000_000
        with open(log_path, "a") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "model": OPUS_MODEL,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cache_read": cache_read,
                "cache_write": cache_write,
                "cost_usd_est": round(cost, 4),
            }) + "\n")
    except Exception:
        pass
    return text

def parse_plan(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    plan = json.loads(text)
    if "tasks" not in plan or not isinstance(plan["tasks"], list):
        raise RuntimeError(f"Plan invalid: {plan}")
    return plan

def normalize_plan(plan: dict, cwd: str, default_timeout: int) -> dict:
    for i, t in enumerate(plan["tasks"]):
        t.setdefault("id", f"task-{i+1}")
        t.setdefault("cwd", cwd)
        t.setdefault("depends_on", [])
        t.setdefault("complexity", "med")
        t.setdefault("expected_artifacts", [])
        if not isinstance(t["expected_artifacts"], list):
            t["expected_artifacts"] = []
        t.setdefault("expected_content", {})
        if not isinstance(t["expected_content"], dict):
            t["expected_content"] = {}
        # Complexity → timeout mapping (only if NIM didn't specify timeout_min)
        if "timeout_min" not in t:
            t["timeout_min"] = COMPLEXITY_TIMEOUT.get(t["complexity"], default_timeout)
        if not t.get("cwd"):
            t["cwd"] = cwd
    return plan

def decompose(goal: str, cwd: str, default_timeout: int) -> dict:
    system_msg = "Türkçe çalışan, JSON-only çıktı veren orchestration uzmanısın."
    user_msg = DECOMP_PROMPT + f"\n\nHedef:\n{goal}\n\n(working_dir: {cwd})"
    # Try Opus 4.7 high first (daily-capped); fallback to NIM on quota/error
    if opus_quota_available():
        try:
            text = call_opus_high(system_msg, user_msg)
            return normalize_plan(parse_plan(text), cwd, default_timeout)
        except Exception as e:
            print(f"⚠️  Opus decompose hatası, V4 Pro'ya düşüyorum: {e}", file=sys.stderr)
            speak("Opus başarısız, Diepsiik V4 Pro'ya düşüyorum.")
    elif OPUS_API_KEY:
        # Quota dolu — sessiz fallback yerine sesli haber ver
        speak("Opus günlük quota tükendi, V4 Pro'ya geçiyorum.")
    text = call_v4pro([
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ])
    return normalize_plan(parse_plan(text), cwd, default_timeout)

def revise(prev_plan: dict, feedback: str, goal: str, cwd: str, default_timeout: int) -> dict:
    prompt = (REVISE_PROMPT
              .replace("{prev_plan}", json.dumps(prev_plan, ensure_ascii=False, indent=2))
              .replace("{feedback}", feedback)
              .replace("{goal}", goal))
    text = call_nim([
        {"role": "system", "content": "Türkçe çalışan, JSON-only çıktı veren orchestration uzmanısın."},
        {"role": "user", "content": prompt},
    ])
    return normalize_plan(parse_plan(text), cwd, default_timeout)

def critique_plan(plan: dict, goal: str) -> dict:
    """Adversarial critic — different agent reviews the plan.
    Returns {'verdict': 'ok'|'revise', 'issues': [...], 'suggestions': '...'}"""
    prompt = (CRITIC_PROMPT
              .replace("{goal}", goal)
              .replace("{plan}", json.dumps(plan, ensure_ascii=False, indent=2)))
    text = call_nim([
        {"role": "system", "content": "Sen JSON-only çıktı veren agresif plan eleştirmenisin. Açıklama, markdown yok."},
        {"role": "user", "content": prompt},
    ])
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        result = json.loads(text)
    except Exception:
        return {"verdict": "ok", "issues": [], "suggestions": ""}
    result.setdefault("verdict", "ok")
    result.setdefault("issues", [])
    result.setdefault("suggestions", "")
    return result

def verify_artifacts(task: dict) -> dict:
    """Disk-based artifact verification with short retry window for fs-flush race.
    Independent of LLM critic. Returns {'verdict':'pass'|'fail','reason':'...'}.
    Pass if no artifacts declared. Enforces expected_content literal match if declared."""
    artifacts = task.get("expected_artifacts") or []
    expected_content = task.get("expected_content") or {}
    if not artifacts:
        return {"verdict": "pass", "reason": "no artifacts declared"}
    # Short retry: child may have exited before fs flush completed
    last_state = None
    for attempt in range(5):  # 0, 0.4, 0.8, 1.2, 1.6s = 4s total
        missing, empty, mismatch = [], [], []
        for path in artifacts:
            p = Path(path)
            if not p.exists():
                missing.append(path); continue
            try:
                if p.is_file() and p.stat().st_size == 0:
                    empty.append(path); continue
            except Exception:
                missing.append(path); continue
            if path in expected_content:
                try:
                    actual = p.read_text().rstrip("\n")
                    expected = str(expected_content[path]).rstrip("\n")
                    if actual != expected:
                        mismatch.append(f"{path} (got {actual!r} != want {expected!r})")
                except Exception as e:
                    mismatch.append(f"{path} (read err: {e})")
        last_state = (missing, empty, mismatch)
        if not (missing or empty or mismatch):
            return {"verdict": "pass", "reason": f"{len(artifacts)} artifact(s) ok"}
        if attempt < 4:
            time.sleep(0.4)
    missing, empty, mismatch = last_state
    parts = []
    if missing: parts.append(f"missing: {missing}")
    if empty: parts.append(f"empty: {empty}")
    if mismatch: parts.append(f"content_mismatch: {mismatch}")
    return {"verdict": "fail", "reason": "; ".join(parts)[:300]}

def _single_fidelity_score(messages: list[dict], use_flash: bool = False) -> tuple[int, str]:
    """One scorer call. Returns (score, reasoning). Score=-1 on error."""
    try:
        if use_flash and DEEPSEEK_API_KEY:
            # Independent second-opinion via DeepSeek V4 Flash (lighter, biases differently)
            text = _post_chat(DEEPSEEK_URL, "deepseek-v4-flash", DEEPSEEK_API_KEY, messages)
        else:
            text = call_v4pro(messages)
    except Exception as e:
        return (-1, f"call err: {e}")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        result = json.loads(text)
    except Exception:
        return (-1, "json parse err")
    score = max(-1, min(10, int(result.get("score", -1))))
    return (score, str(result.get("reasoning", ""))[:200])

def auto_tighten_persona(persona_id: str, sample_output: str) -> dict | None:
    """Generate a tightened persona description via V4 Pro.
    Returns {'old': str, 'new': str} or None on failure."""
    personas = load_personas()
    if persona_id not in personas:
        return None
    old_desc = personas[persona_id].get("description", "")
    if not old_desc:
        return None
    snippet = sample_output[-1500:] if len(sample_output) > 1500 else sample_output
    prompt = (PERSONA_TIGHTEN_PROMPT
              .replace("{old_desc}", old_desc)
              .replace("{sample}", snippet))
    try:
        new_desc = call_v4pro([
            {"role": "system", "content": "Sen JSON-FREE plain text output veren persona-tightener uzmanısın. Sadece yeni description'ı yaz."},
            {"role": "user", "content": prompt},
        ]).strip()
    except Exception as e:
        return None
    new_desc = re.sub(r"^```.*?\n|```$", "", new_desc, flags=re.DOTALL).strip()
    new_desc = new_desc.strip('"\'`')
    if not new_desc or len(new_desc) > 500 or len(new_desc) < 50:
        return None  # Sanity bounds
    if new_desc == old_desc:
        return None
    return {"old": old_desc, "new": new_desc}

def apply_tightened_persona(persona_id: str, new_desc: str) -> bool:
    """Atomically update personas.json with backup."""
    try:
        personas_path = PERSONAS_PATH
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = personas_path.with_suffix(f".json.bak.{ts}-auto-tighten")
        backup.write_text(personas_path.read_text())
        data = json.loads(personas_path.read_text())
        if persona_id not in data:
            return False
        data[persona_id]["description"] = new_desc
        tmp = personas_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(personas_path)
        return True
    except Exception as e:
        print(f"⚠️  apply tighten failed: {e}", file=sys.stderr)
        return False

# --- M2-T01: decomposer drift signals + auto-tighten ---
DECOMPOSER_STOP_WORDS = {"the", "and", "için", "olan", "sonra", "veya", "with", "from",
                         "into", "this", "that", "şunu", "bunu", "olarak"}

def _decomposer_literals(text: str) -> tuple[set[str], set[str]]:
    """Return (exact_paths_and_quoted, 4char_word_stems).
    Stem proxy handles Turkish suffixes (api.py'yi vs api.py, incele vs incelenecek).
    Note: only catches verbatim drift; semantic paraphrase is M3's deeper net.
    """
    paths = set(re.findall(r"/[A-Za-z0-9._/-]+", text))
    quoted = set(re.findall(r'"([^"]+)"', text))
    words = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü_][A-Za-zÇĞİÖŞÜçğıöşü0-9_]{3,}", text)
    stems = {w[:4].lower() for w in words if w.lower() not in DECOMPOSER_STOP_WORDS}
    return (paths | quoted), stems

def goal_literal_preservation(goal: str, plan: dict) -> float:
    g_exact, g_stems = _decomposer_literals(goal)
    g_total = g_exact | g_stems
    if not g_total:
        return 1.0
    briefs = " ".join(t.get("brief", "") for t in plan.get("tasks", []))
    b_exact, b_stems = _decomposer_literals(briefs)
    overlap = (g_exact & b_exact) | (g_stems & b_stems)
    return len(overlap) / len(g_total)

def expected_content_presence(plan: dict) -> float:
    """Of tasks whose brief mentions a target file path, how many declare expected_content?"""
    tasks = plan.get("tasks", [])
    candidates = [t for t in tasks
                  if re.search(r"/[A-Za-z0-9._/-]+\.[a-z]{1,4}\b", t.get("brief", ""))]
    if not candidates:
        return 1.0
    declared = sum(1 for t in candidates if t.get("expected_content"))
    return declared / len(candidates)

def task_count_deviation(plan: dict, baseline_avg: float) -> float:
    """Absolute deviation from rolling baseline of task counts for similar goals."""
    n = len(plan.get("tasks", []))
    if baseline_avg <= 0:
        return 0.0
    return abs(n - baseline_avg) / baseline_avg

def score_decomposer_fidelity(goal: str, plan: dict, baseline_avg: float = 4.0) -> dict:
    """Composite 0-10 scorer on three signals:
    - goal-literal preservation (verbatim from goal carried into briefs)
    - expected_content presence ratio
    - task-count stability vs baseline
    Returns dict with score + per-signal breakdown for diagnostics.
    """
    lit = goal_literal_preservation(goal, plan)
    exp = expected_content_presence(plan)
    dev = task_count_deviation(plan, baseline_avg)
    s_lit = lit * 3.33
    s_exp = exp * 3.33
    s_dev = max(0.0, 3.34 - min(dev, 1.0) * 3.34)
    score = round(s_lit + s_exp + s_dev, 2)
    return {"score": score, "literal": round(lit, 2),
            "expected": round(exp, 2), "deviation": round(dev, 2)}

def decomposer_drift_fires(score: float, literal: float) -> bool:
    """Drift gate: composite < 7 OR literal preservation < 0.7."""
    return score < 7.0 or literal < 0.7

def _decomposer_content_density_ok(prompt: str) -> bool:
    """D1 anti-collapse: tightened prompt must still contain structural keywords."""
    required = ["expected_content", "depends_on", "brief"]
    return all(k in prompt for k in required)

DECOMPOSER_TIGHTENER_MODEL = "meta/llama-3.3-70b-instruct"  # Meta family != DeepSeek worker

def call_nim_llama(messages: list[dict]) -> str:
    """Mixed-family tightener call: routes through NIM with Llama 3.3 to avoid same-family bias.
    D2 discipline: tightener must use a different LLM family than the worker (DeepSeek)."""
    key = resolve_nim_key()
    if not key:
        raise RuntimeError("NIM API key yok — Llama tightener çağrılamadı")
    return _post_chat(NIM_URL, DECOMPOSER_TIGHTENER_MODEL, key, messages)

def auto_tighten_decomposer(sample_goal: str, sample_plan: dict) -> dict | None:
    """Generate a tightened decomposer system prompt via Llama 3.3 (mixed-family from DeepSeek worker).
    Sanity: 200-1500 chars + structural keywords present (D1 anti-collapse).
    Returns {'old': str, 'new': str} or None on failure.
    """
    try:
        old_prompt = load_decomposer_prompt()
    except Exception:
        return None
    plan_json = json.dumps(plan_compact_for_tighten(sample_plan), ensure_ascii=False)[:1500]
    prompt = (DECOMPOSER_TIGHTEN_PROMPT
              .replace("{old_prompt}", old_prompt)
              .replace("{sample_goal}", sample_goal[:500])
              .replace("{sample_plan}", plan_json))
    try:
        new_prompt = call_nim_llama([
            {"role": "system", "content": "Sen JSON-FREE plain text output veren prompt-tightener uzmanısın. Sadece yeni decomposer prompt'unu yaz."},
            {"role": "user", "content": prompt},
        ]).strip()
    except Exception:
        return None
    new_prompt = re.sub(r"^```.*?\n|```$", "", new_prompt, flags=re.DOTALL).strip()
    new_prompt = new_prompt.strip('"\'`')
    # Sanity bounds: baseline DECOMP_PROMPT is ~2285c, so ceiling raised to 2400 (vs ticket's 1500).
    # Floor 800 prevents collapse to "decompose this goal into tasks" stub. Ticket updated 2026-05-02.
    if not (800 <= len(new_prompt) <= 2400):
        return None
    if not _decomposer_content_density_ok(new_prompt):
        return None
    if new_prompt == old_prompt:
        return None
    return {"old": old_prompt, "new": new_prompt}

def plan_compact_for_tighten(plan: dict) -> dict:
    """Strip plan to drift-relevant fields for prompting."""
    return {
        "summary": plan.get("summary", ""),
        "tasks": [{"id": t.get("id"), "brief": t.get("brief", "")[:200],
                   "expected_artifacts": t.get("expected_artifacts", []),
                   "expected_content": t.get("expected_content", {})}
                  for t in plan.get("tasks", [])],
    }

def apply_tightened_decomposer(new_prompt: str) -> bool:
    """Atomically update decomposer_prompt.txt with timestamped backup."""
    try:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = DECOMPOSER_PROMPT_PATH
        backup = path.with_suffix(f".txt.bak.{ts}-auto-tighten")
        backup.write_text(path.read_text())
        tmp = path.with_suffix(".txt.tmp")
        tmp.write_text(new_prompt)
        tmp.replace(path)
        return True
    except Exception as e:
        print(f"⚠️  apply decomposer tighten failed: {e}", file=sys.stderr)
        return False

# --- M2-T02: critic drift signals + auto-tighten ---
# Worker family for the task-critic = DeepSeek (call_v4pro). Tightener must differ.
CRITIC_TIGHTENER_MODEL = "meta/llama-3.3-70b-instruct"  # Meta family
CRITIC_WORKER_FAMILY = "DeepSeek"
CRITIC_TIGHTENER_FAMILY = "Llama"

def critic_precision(decisions: list[dict]) -> float:
    """Of all critic-PASS calls, how many actually had disk-truth pass?
    decisions: [{'task_id', 'critic_verdict' ('pass'|'fail'), 'disk_truth_pass' (bool)}]
    Vacuous 1.0 when no PASS calls (no false positives possible).
    """
    pass_calls = [d for d in decisions if d.get("critic_verdict") == "pass"]
    if not pass_calls:
        return 1.0
    correct = sum(1 for d in pass_calls if d.get("disk_truth_pass"))
    return correct / len(pass_calls)

def critic_recall(decisions: list[dict]) -> float:
    """Of all disk-truth-PASS tasks, how many did the critic mark PASS?
    Vacuous 1.0 when no ground-truth PASS exists (D4 blind-spot ceiling).
    """
    truth_pass = [d for d in decisions if d.get("disk_truth_pass")]
    if not truth_pass:
        return 1.0
    found = sum(1 for d in truth_pass if d.get("critic_verdict") == "pass")
    return found / len(truth_pass)

def score_critic_quality(decisions: list[dict]) -> dict:
    """Returns precision + recall against disk-truth ground truth + diagnostics."""
    p = critic_precision(decisions)
    r = critic_recall(decisions)
    return {
        "precision": round(p, 3), "recall": round(r, 3),
        "n_decisions": len(decisions),
        "n_pass_calls": sum(1 for d in decisions if d.get("critic_verdict") == "pass"),
        "n_truth_pass": sum(1 for d in decisions if d.get("disk_truth_pass")),
    }

def critic_drift_fires(precision: float, recall: float) -> bool:
    """Bidirectional drift gate: precision < 0.8 (too lenient) OR recall < 0.8 (too strict).
    D2 self-flattering defense: gate is two-sided so a one-sided model can't game it.
    """
    return precision < 0.8 or recall < 0.8

def _critic_density_ok(prompt: str) -> bool:
    """D1 anti-collapse: disk-truth criterion + pass/fail decision schema must remain.
    Refuse rewrites that strip the explicit decision criteria."""
    p = prompt.lower()
    has_disk = any(k in p for k in ["disk", "artifact", "byte", "literal"])
    has_decision = "pass" in p and "fail" in p
    return has_disk and has_decision

def _mixed_family_ok(worker_family: str, tightener_family: str) -> bool:
    """D2: tightener family must differ from worker family."""
    return worker_family.lower() != tightener_family.lower()

def _summarize_drift_examples(decisions: list[dict], max_n: int = 6) -> str:
    """Pull up to max_n misclassified decisions for the tightener prompt."""
    wrong = [d for d in decisions
             if (d.get("critic_verdict") == "pass") != bool(d.get("disk_truth_pass"))]
    out = []
    for d in wrong[:max_n]:
        kind = "FALSE-PASS" if d.get("critic_verdict") == "pass" else "FALSE-FAIL"
        out.append(f"- [{kind}] task={d.get('task_id','?')} reason={d.get('reason','-')[:80]}")
    return "\n".join(out) if out else "(no misclassified decisions captured)"

def auto_tighten_critic(decisions: list[dict]) -> dict | None:
    """Generate a tightened critic prompt via Llama 3.3 (mixed-family from DeepSeek worker).
    Sanity: 800-1500 chars + density (disk + pass/fail) + placeholder preservation.
    Returns {'old': str, 'new': str, 'precision': float, 'recall': float} or None.
    """
    if not _mixed_family_ok(CRITIC_WORKER_FAMILY, CRITIC_TIGHTENER_FAMILY):
        return None
    try:
        old_prompt = load_critic_prompt()
    except Exception:
        return None
    quality = score_critic_quality(decisions)
    pass_calls = [d for d in decisions if d.get("critic_verdict") == "pass"]
    true_passes = sum(1 for d in pass_calls if d.get("disk_truth_pass"))
    truth_pass = [d for d in decisions if d.get("disk_truth_pass")]
    missed = sum(1 for d in truth_pass if d.get("critic_verdict") != "pass")
    drift_examples = _summarize_drift_examples(decisions)
    prompt = (CRITIC_TIGHTEN_PROMPT
              .replace("{old_prompt}", old_prompt)
              .replace("{precision}", f"{quality['precision']}")
              .replace("{recall}", f"{quality['recall']}")
              .replace("{n_pass_calls}", str(quality["n_pass_calls"]))
              .replace("{true_passes}", str(true_passes))
              .replace("{n_truth_pass}", str(quality["n_truth_pass"]))
              .replace("{missed_passes}", str(missed))
              .replace("{drift_examples}", drift_examples))
    try:
        new_prompt = call_nim_llama([
            {"role": "system", "content": "Sen JSON-FREE plain text output veren prompt-tightener uzmanısın. Sadece yeni critic prompt'unu yaz."},
            {"role": "user", "content": prompt},
        ]).strip()
    except Exception:
        return None
    new_prompt = re.sub(r"^```.*?\n|```$", "", new_prompt, flags=re.DOTALL).strip()
    new_prompt = new_prompt.strip('"\'`')
    # Sanity bounds: 800-1500c (revised from ticket's 150-1000 since baseline is ~1244c).
    if not (800 <= len(new_prompt) <= 1500):
        return None
    if not _critic_density_ok(new_prompt):
        return None
    # Placeholders must survive — without {brief}/{output}/{artifact_status}, runtime breaks.
    for ph in ("{brief}", "{output}", "{artifact_status}"):
        if ph not in new_prompt:
            return None
    if new_prompt == old_prompt:
        return None
    return {"old": old_prompt, "new": new_prompt,
            "precision": quality["precision"], "recall": quality["recall"]}

def apply_tightened_critic(new_prompt: str) -> bool:
    """Atomically update critic_prompt.txt with timestamped backup."""
    try:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = CRITIC_PROMPT_PATH
        backup = path.with_suffix(f".txt.bak.{ts}-auto-tighten")
        backup.write_text(path.read_text())
        tmp = path.with_suffix(".txt.tmp")
        tmp.write_text(new_prompt)
        tmp.replace(path)
        return True
    except Exception as e:
        print(f"⚠️  apply critic tighten failed: {e}", file=sys.stderr)
        return False

def tighten_critic_if_drift(decisions: list[dict], dry_run: bool = True) -> dict:
    """Drift gate + tighten + (optional) apply + log + mnemonics, atomic.
    Returns {'fired', 'applied', 'old_len', 'new_len', 'quality'}.
    """
    quality = score_critic_quality(decisions)
    fires = critic_drift_fires(quality["precision"], quality["recall"])
    out = {"fired": fires, "applied": False, "old_len": 0, "new_len": 0, "quality": quality}
    if not fires:
        return out
    proposal = auto_tighten_critic(decisions)
    if not proposal:
        return out
    out["old_len"] = len(proposal["old"])
    out["new_len"] = len(proposal["new"])
    if dry_run:
        return out
    if apply_tightened_critic(proposal["new"]):
        out["applied"] = True
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        mnemonics_record(
            f"[{ts}] director critic-tighten: precision={quality['precision']}, "
            f"recall={quality['recall']}, n={quality['n_decisions']} | "
            f"old={out['old_len']}c -> new={out['new_len']}c",
            ns="director",
        )
        append_evolution_log("critic-tighten", {
            "precision": quality["precision"], "recall": quality["recall"],
            "n": quality["n_decisions"],
            "old": out["old_len"], "new": out["new_len"],
        })
    return out

def tighten_decomposer_if_drift(goal: str, plan: dict, baseline_avg: float = 4.0,
                                 dry_run: bool = True) -> dict:
    """Drift gate + tighten + (optional) apply + log + mnemonics, atomic.
    Returns {'fired': bool, 'applied': bool, 'old_len': int, 'new_len': int, 'signals': dict}.
    Caller decides dry_run; CLI surface adds it later.
    """
    fid = score_decomposer_fidelity(goal, plan, baseline_avg)
    fires = decomposer_drift_fires(fid["score"], fid["literal"])
    out = {"fired": fires, "applied": False, "old_len": 0, "new_len": 0, "signals": fid}
    if not fires:
        return out
    proposal = auto_tighten_decomposer(goal, plan)
    if not proposal:
        return out
    out["old_len"] = len(proposal["old"])
    out["new_len"] = len(proposal["new"])
    if dry_run:
        return out
    if apply_tightened_decomposer(proposal["new"]):
        out["applied"] = True
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        mnemonics_record(
            f"[{ts}] director decomposer-tighten: signals="
            f"(score={fid['score']}, literal={fid['literal']}, exp={fid['expected']}, dev={fid['deviation']}) | "
            f"old={out['old_len']}c -> new={out['new_len']}c",
            ns="director",
        )
        append_evolution_log("decomposer-tighten", {
            "score": fid["score"], "literal": fid["literal"],
            "old": out["old_len"], "new": out["new_len"],
        })
    return out

def append_evolution_log(event_kind: str, details: dict) -> None:
    """Append a single-line entry to docs/EVOLUTION_LOG.md.
    D6 discipline: every tighten event is publicly logged."""
    log_path = DIRECTOR_HOME / "docs" / "EVOLUTION_LOG.md"
    if not log_path.parent.exists():
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"- [{ts}] event={event_kind} " + " ".join(f"{k}={v}" for k, v in details.items())
    try:
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def score_persona_fidelity(persona_id: str, persona: dict, task: dict, output: str) -> dict:
    """Dual-scorer fidelity (V4 Pro primary + V4 Flash second-opinion). Reduces single-model bias.
    Returns {'score': avg, 'reasoning': str, 'pro_score', 'flash_score', 'consensus': bool}."""
    if not output.strip() or not persona:
        return {"score": -1, "reasoning": "boş output veya persona yok"}
    snippet = output[-2500:] if len(output) > 2500 else output
    prompt = (PERSONA_FIDELITY_PROMPT
              .replace("{persona_id}", persona_id)
              .replace("{persona_name}", persona.get("name", persona_id))
              .replace("{persona_desc}", persona.get("description", ""))
              .replace("{brief}", task.get("brief", ""))
              .replace("{output}", snippet))
    msgs = [
        {"role": "system", "content": "Sen JSON-only çıktı veren kalitatif değerlendiricisin."},
        {"role": "user", "content": prompt},
    ]
    pro_score, pro_reason = _single_fidelity_score(msgs, use_flash=False)
    flash_score, flash_reason = _single_fidelity_score(msgs, use_flash=True)
    valid = [s for s in (pro_score, flash_score) if s >= 0]
    if not valid:
        return {"score": -1, "reasoning": f"both scorers failed: pro={pro_reason}, flash={flash_reason}"}
    avg = sum(valid) / len(valid)
    consensus = (len(valid) == 2 and abs(pro_score - flash_score) <= 2)
    reasoning = pro_reason if pro_score >= 0 else flash_reason
    if pro_score >= 0 and flash_score >= 0:
        reasoning = f"pro={pro_score} flash={flash_score}; {pro_reason}"
    return {
        "score": int(round(avg)), "reasoning": reasoning,
        "pro_score": pro_score, "flash_score": flash_score,
        "consensus": consensus,
    }

def _structural_failure_signal(task: dict, output: str) -> str | None:
    """Cheap regex pre-check for explicit failure markers BEFORE LLM critic.
    Returns reason string if structural fail detected, else None.
    Only flags markers that appear at line-start or after refusal-context phrases —
    avoids false positives from reasoning text like 'I can't verify X'."""
    # Lines that START with refusal/error markers (not embedded in reasoning)
    line_start_markers = [
        r"^(?:yapamam|yapamadım)\b",
        r"^(?:izin verilmiyor|izin verilmedi)\b",
        r"^(?:i can't|i cannot|i'm sorry)\b",
        r"^traceback \(most recent call last\)",
        r"^uncaughtexception",
        r"^permission denied",
        r"^command not found",
    ]
    for line in output.splitlines():
        ls = line.strip().lower()
        for pat in line_start_markers:
            if re.match(pat, ls):
                return f"refusal/error at line start: {ls[:60]!r}"
    # Tantivy/MCP "fatal" patterns (LLM never recovered)
    fatal_substrings = [
        "fatal error: lockfile",  # tantivy hard-fail
        "killed by signal",
        "out of memory",
    ]
    low = output.lower()
    for m in fatal_substrings:
        if m in low:
            return f"fatal: {m!r}"
    # Suspiciously tiny output for a non-trivial brief
    brief_len = len(task.get("brief", ""))
    if brief_len > 100 and len(output.strip()) < 30:
        return f"output too short ({len(output.strip())}B) for brief ({brief_len}B)"
    return None

def critique_task_output(task: dict, output: str, artifact_status: str = "") -> dict:
    """Validate a task's claude -p output. Returns {'verdict':'pass'|'fail','reason':'...'}"""
    if not output.strip():
        return {"verdict": "fail", "reason": "boş output"}
    # Structural pre-check — catches obvious failures before LLM softens them
    structural_fail = _structural_failure_signal(task, output)
    if structural_fail:
        return {"verdict": "fail", "reason": f"structural: {structural_fail}"}
    snippet = output[-2500:] if len(output) > 2500 else output
    prompt = (TASK_CRITIC_PROMPT
              .replace("{artifact_status}", artifact_status or "(artifact bilgisi yok)")
              .replace("{brief}", task.get("brief", ""))
              .replace("{output}", snippet))
    try:
        text = call_nim([
            {"role": "system", "content": "Sen JSON-only çıktı veren agresif QA agent'ısın."},
            {"role": "user", "content": prompt},
        ])
    except Exception as e:
        return {"verdict": "fail", "reason": f"critic call failed: {e}"}
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        result = json.loads(text)
    except Exception:
        return {"verdict": "fail", "reason": "critic json bozuk"}
    result.setdefault("verdict", "fail")
    result.setdefault("reason", "")
    return result

def revise_from_critic(prev_plan: dict, critique: dict, goal: str, cwd: str, default_timeout: int) -> dict:
    prompt = (REVISE_FROM_CRITIC_PROMPT
              .replace("{prev_plan}", json.dumps(prev_plan, ensure_ascii=False, indent=2))
              .replace("{issues}", json.dumps(critique.get("issues", []), ensure_ascii=False))
              .replace("{suggestions}", critique.get("suggestions", ""))
              .replace("{goal}", goal))
    text = call_nim([
        {"role": "system", "content": "Türkçe çalışan, JSON-only çıktı veren orchestration uzmanısın."},
        {"role": "user", "content": prompt},
    ])
    return normalize_plan(parse_plan(text), cwd, default_timeout)

# --- run state ---
def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

def run_dir(run_id: str) -> Path:
    p = RUNS_DIR / run_id
    p.mkdir(parents=True, exist_ok=True)
    (p / "logs").mkdir(exist_ok=True)
    return p

def write_state(rd: Path, state: dict) -> None:
    (rd / "state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False))

def load_state(rd: Path) -> dict:
    return json.loads((rd / "state.json").read_text())

# --- spawn ---
_PROCS: dict[str, subprocess.Popen] = {}

def build_brief(rd: Path, task: dict, state: dict) -> str:
    """Compose final brief: persona prepend + original brief + dependency handoff."""
    brief = task["brief"]

    # Persona prepend (skipped when state.persona_off=True for A/B testing)
    if state.get("persona_off"):
        persona_block = ""
        task["persona_id"] = "(off)"
    else:
        pid, persona = select_persona(brief, hint=task.get("persona", ""))
        task["persona_id"] = pid
        persona_block = (
            f"[Persona: {persona.get('name', pid)}]\n"
            f"{persona.get('description', '')}\n"
            f"---\n\n"
        )

    # Expected artifacts — explicit file path contract for the child
    artifacts = task.get("expected_artifacts") or []
    expected_content = task.get("expected_content") or {}
    artifact_block = ""
    if artifacts:
        artifact_lines = "\n".join(f"  - {p}" for p in artifacts)
        artifact_block = (
            f"\n\nBu task'ın TAMAMLANMIŞ sayılması için ŞU dosyalar mutlak yol olarak diskte oluşmuş ve boş olmamalı:\n"
            f"{artifact_lines}\n"
            f"Yolları aynen kullan, başka isim üretme. Diğer dizinlere yazma."
        )
        if expected_content:
            content_lines = []
            for path, literal in expected_content.items():
                literal_str = str(literal)
                content_lines.append(
                    f'  - {path} icerigi TAM OLARAK su olmali (tirnak haric, byte byte ayni — bosluk/satir/buyuk-kucuk harf dahil):\n    """{literal_str}"""'
                )
            artifact_block += (
                "\n\nDosya icerigi spesifikasyonu (paraphrase YASAK, literal kopyala):\n"
                + "\n".join(content_lines)
            )

    # Dependency handoff
    deps = task.get("depends_on", [])
    handoff = ""
    if deps:
        sections = []
        for dep_id in deps:
            log_path = rd / "logs" / f"{dep_id}.log"
            if not log_path.exists():
                continue
            try:
                log_text = log_path.read_text()
            except Exception:
                continue
            snippet = log_text[-1500:] if len(log_text) > 1500 else log_text
            sections.append(f"--- Bağımlı task '{dep_id}' çıktısı (son 1500 char) ---\n{snippet}")
        if sections:
            handoff = "\n\n" + "\n\n".join(sections)

    return persona_block + brief + artifact_block + handoff

def spawn_task(rd: Path, task: dict, state: dict, run_id: str, attempt: int) -> int:
    log = rd / "logs" / f"{task['id']}.log"
    # Append on retry, fresh on first attempt
    mode = "a" if attempt > 0 else "w"
    full_brief = build_brief(rd, task, state)
    # Default backend = metis (DeepSeek V4 Pro). claude -p bypasses persona blocks,
    # so for fair persona injection we use metis. Override with DIRECTOR_CHILD_BACKEND=claude.
    backend = os.environ.get("DIRECTOR_CHILD_BACKEND", "metis").lower()
    if backend == "claude":
        cmd = ["claude", "-p", full_brief]
    else:
        cmd = ["metis", "--yes", full_brief]
    cwd = task.get("cwd") or str(Path.home())
    env = {**os.environ, "DIRECTOR_RUN_ID": run_id, "DIRECTOR_TASK_ID": task["id"]}
    lf = open(log, mode)
    if attempt > 0:
        lf.write(f"\n\n=== RETRY #{attempt} @ {datetime.now().isoformat()} ===\n\n")
        lf.flush()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=lf,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    _PROCS[task["id"]] = proc
    return proc.pid

def task_status(task: dict) -> tuple[str, int | None]:
    proc = _PROCS.get(task["id"])
    if proc is None:
        if not task.get("pid"):
            return ("failed", None)
        try:
            os.kill(task["pid"], 0)
            return ("running", None)
        except (ProcessLookupError, PermissionError):
            return ("done", None)
    rc = proc.poll()
    if rc is None:
        return ("running", None)
    return ("done" if rc == 0 else "failed", rc)

# --- notification ---
def _tts_mythos(msg: str) -> str | None:
    """Generate MP3 via ElevenLabs Mythos v2 voice; cache by content hash. Return mp3 path or None on failure."""
    if not ELEVENLABS_API_KEY:
        return None
    import hashlib, urllib.request
    TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1((ELEVENLABS_VOICE_ID + "|" + msg).encode("utf-8")).hexdigest()[:16]
    mp3 = TTS_CACHE_DIR / f"{h}.mp3"
    if mp3.exists() and mp3.stat().st_size > 1000:
        return str(mp3)
    body = {
        "text": msg,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.3, "use_speaker_boost": True},
    }
    url = ELEVENLABS_TTS_URL.format(voice_id=ELEVENLABS_VOICE_ID)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            mp3.write_bytes(resp.read())
        return str(mp3) if mp3.stat().st_size > 1000 else None
    except Exception as e:
        print(f"⚠️  ElevenLabs TTS hatası: {e}", file=sys.stderr)
        return None

def speak(msg: str) -> None:
    """Voice-only event (no banner). Best-effort, never blocks director flow."""
    if not msg.strip():
        return
    try:
        mp3 = _tts_mythos(msg)
        if mp3:
            subprocess.run(["afplay", mp3], check=False, timeout=20)
        else:
            subprocess.run(["say", "-v", "Yelda", msg], check=False, timeout=8)
    except Exception:
        pass

def notify(title: str, msg: str, voice: bool = True) -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{msg.replace(chr(34), chr(39))}" with title "{title}"'],
            check=False, timeout=3,
        )
    except Exception:
        pass
    if voice:
        mp3 = _tts_mythos(msg)
        if mp3:
            try:
                subprocess.run(["afplay", mp3], check=False, timeout=30)
            except Exception:
                pass
        else:
            # Fallback: Yelda built-in
            try:
                subprocess.run(["say", "-v", "Yelda", msg], check=False, timeout=10)
            except Exception:
                try:
                    subprocess.run(["say", msg], check=False, timeout=10)
                except Exception:
                    pass
    print(f"\n📢 {title}: {msg}\n", flush=True)

# --- main flow ---
def cmd_run(args) -> int:
    cwd = os.getcwd()
    default_timeout = args.timeout or DEFAULT_TIMEOUT_MIN
    print(f"🎯 Hedef: {args.goal}")
    print(f"📂 Working dir: {cwd}  (timeout default: {default_timeout}min, retries: {args.retries})")
    speak("Direktör başladı, hedef bölünüyor.")

    plan = None
    while True:
        try:
            if plan is None:
                opus_left = max(0, OPUS_DAILY_LIMIT - _opus_quota_today_used())
                if opus_quota_available():
                    label = f"Opus 4.7 high ({opus_left}/{OPUS_DAILY_LIMIT} kaldi)"
                else:
                    label = "DeepSeek V4 Pro [native→NIM fallback] (opus quota dolu)"
                print(f"⏳ Decomposition ({label})...")
                plan = decompose(args.goal, cwd, default_timeout)
                # Adversarial critic loop (1 pass)
                if not args.skip_critic:
                    print("🔍 Critic plan'ı eleştiriyor...")
                    critique = critique_plan(plan, args.goal)
                    if critique["verdict"] == "revise" and critique["issues"]:
                        print(f"   Critic: {', '.join(critique['issues'][:3])}")
                        print("   ↻ plan revize ediliyor...")
                        speak("Critic plan'a itiraz etti, revize ediyorum.")
                        plan = revise_from_critic(plan, critique, args.goal, cwd, default_timeout)
                    else:
                        print("   ✓ critic OK")
            else:
                feedback = input("Plan'da neyi değiştirelim? (boş = vazgeç): ").strip()
                if not feedback:
                    print("Vazgeçildi.")
                    return 1
                print("⏳ Plan revize ediliyor...")
                plan = revise(plan, feedback, args.goal, cwd, default_timeout)
        except Exception as e:
            print(f"❌ Decompose hatası: {e}", file=sys.stderr)
            return 2

        # CLI-level timeout override applies to ALL tasks if user gave -t
        if args.timeout:
            for t in plan["tasks"]:
                t["timeout_min"] = args.timeout

        print(f"\n📋 Plan: {plan.get('summary','')}")
        print(f"   Toplam task: {len(plan['tasks'])}\n")
        for t in plan["tasks"]:
            deps = f" ⤴{','.join(t['depends_on'])}" if t["depends_on"] else ""
            cx = f" [{t.get('complexity','med')}]"
            # Resolve persona for preview (NIM hint OR keyword fallback)
            pid, _ = select_persona(t["brief"], hint=t.get("persona", ""))
            ps = f" 🎭{pid}" if pid != "default" else ""
            print(f"  [{t['id']}] {t['title']}  ({t['timeout_min']}min){cx}{ps}{deps}")
            print(f"          {t['brief'][:120]}")
        print()

        if args.yes:
            break
        speak(f"Plan hazır, {len(plan['tasks'])} görev. Onayını bekliyorum.")
        ans = input("Onay? [Y]es / [d]üzelt / [n]o: ").strip().lower()
        if ans in ("", "y", "yes", "evet", "e"):
            break
        if ans in ("d", "duzelt", "düzelt"):
            continue
        print("İptal.")
        return 1

    run_id = new_run_id()
    rd = run_dir(run_id)
    state = {
        "run_id": run_id,
        "goal": args.goal,
        "summary": plan.get("summary", ""),
        "started": datetime.now(timezone.utc).isoformat(),
        "cwd": cwd,
        "max_retries": args.retries,
        "skip_task_critic": args.skip_task_critic,
        "tasks": [],
    }
    for t in plan["tasks"]:
        state["tasks"].append({
            **t,
            "status": "pending",
            "pid": None,
            "started": None,
            "ended": None,
            "attempts": 0,
            "exit_code": None,
        })
    write_state(rd, state)
    print(f"\n🚀 Run ID: {run_id}")
    print(f"   Logs: {rd}/logs/\n")

    director_event(run_id, cwd, "plan",
                   f"plan onaylandı: {len(state['tasks'])} task — {state['summary'][:80]}")
    spawn_ready_tasks(rd, state, run_id)
    write_state(rd, state)

    print(f"⏱  Polling every {POLL_INTERVAL}s. Ctrl+C → cancel; süreçler kalır.")
    try:
        monitor_loop(rd, state, run_id)
    except KeyboardInterrupt:
        print(f"\n⏸  Detached. `director status {run_id}` ile dön, `director cancel {run_id}` ile durdur.")
        return 0
    return 0

def spawn_ready_tasks(rd: Path, state: dict, run_id: str) -> int:
    spawned = 0
    done_ids = {t["id"] for t in state["tasks"] if t["status"] == "done"}
    for t in state["tasks"]:
        if t["status"] != "pending":
            continue
        if t["depends_on"] and not set(t["depends_on"]).issubset(done_ids):
            continue
        attempt = t.get("attempts", 0)
        pid = spawn_task(rd, t, state, run_id, attempt)
        t["pid"] = pid
        t["status"] = "running"
        t["started"] = datetime.now(timezone.utc).isoformat()
        t["attempts"] = attempt + 1
        spawned += 1
        retry_tag = f" (retry #{attempt})" if attempt > 0 else ""
        print(f"▶  spawned [{t['id']}] PID={pid}{retry_tag} → logs/{t['id']}.log")
        director_event(run_id, state["cwd"], "spawn",
                       f"{t['id']} başladı{retry_tag}: {t['title']}", t.get("cwd", ""))
    return spawned

def monitor_loop(rd: Path, state: dict, run_id: str) -> None:
    max_retries = state.get("max_retries", DEFAULT_MAX_RETRIES)
    while True:
        any_running = False
        any_change = False
        for t in state["tasks"]:
            if t["status"] != "running":
                continue
            status, rc = task_status(t)
            if status == "running":
                any_running = True
                started = datetime.fromisoformat(t["started"])
                age_min = (datetime.now(timezone.utc) - started).total_seconds() / 60
                if age_min > t["timeout_min"]:
                    try:
                        os.killpg(os.getpgid(t["pid"]), signal.SIGTERM)
                    except Exception:
                        pass
                    t["status"] = "timeout"
                    t["ended"] = datetime.now(timezone.utc).isoformat()
                    any_change = True
                    print(f"⏰ [{t['id']}] timeout ({t['timeout_min']}min), killed")
                    # Promote to retry pending if budget left
                    if t["attempts"] <= max_retries:
                        print(f"   ↻ retry budget left, requeuing")
                        t["status"] = "pending"
                continue
            # Process exited
            log_path = rd / "logs" / f"{t['id']}.log"
            log_size = log_path.stat().st_size if log_path.exists() else 0
            log_text = log_path.read_text() if log_path.exists() else ""

            # Artifact verification — disk-based, runs first, LLM-independent
            artifact_result = None
            if status == "done":
                artifact_result = verify_artifacts(t)
                t["artifact_check"] = artifact_result
                if artifact_result["verdict"] == "fail":
                    status = "failed"
                    print(f"   ⚠ artifact FAIL: {artifact_result['reason']}")
                else:
                    print(f"   ✓ artifact ok ({artifact_result['reason']})")

            # Per-task critic — short-circuit when disk truth is conclusive.
            # If artifact + literal content_match passed, disk is ground truth → skip LLM critic.
            critic_result = None
            if status == "done" and not state.get("skip_task_critic"):
                expected_content = t.get("expected_content") or {}
                disk_conclusive = (
                    artifact_result and artifact_result.get("verdict") == "pass"
                    and bool(expected_content)
                    and all(p in expected_content for p in (t.get("expected_artifacts") or []))
                )
                if disk_conclusive:
                    critic_result = {"verdict": "pass", "reason": "disk truth (artifact + content match)"}
                    t["critic"] = critic_result
                    print(f"   ✓ critic skip (disk truth)")
                else:
                    print(f"   🔍 [{t['id']}] critic check...")
                    artifact_status = (
                        f"verdict={artifact_result.get('verdict')}, reason={artifact_result.get('reason')}"
                        if artifact_result else "(disk doğrulama yok)"
                    )
                    critic_result = critique_task_output(t, log_text, artifact_status=artifact_status)
                    t["critic"] = critic_result
                    if critic_result["verdict"] == "fail":
                        status = "failed"
                        print(f"   ⚠ critic FAIL: {critic_result['reason']}")
                    else:
                        print(f"   ✓ critic pass")

            # Persona fidelity scoring — only when A/B test arm is persona-on (extra LLM call, $)
            if (status == "done" and state.get("ab_arm") and not state.get("persona_off")
                    and not state.get("skip_task_critic")):
                pid = t.get("persona_id", "")
                personas = load_personas()
                if pid and pid in personas:
                    fidelity = score_persona_fidelity(pid, personas[pid], t, log_text)
                    t["persona_fidelity"] = fidelity
                    if fidelity["score"] >= 0:
                        print(f"   🎭 persona fidelity: {fidelity['score']}/10 — {fidelity['reasoning'][:80]}")

            if status == "failed" and t["attempts"] <= max_retries:
                print(f"❌ [{t['id']}] failed (exit={rc}), retry...")
                t["status"] = "pending"
                t["pid"] = None
                any_change = True
            else:
                t["status"] = status
                t["exit_code"] = rc
                t["ended"] = datetime.now(timezone.utc).isoformat()
                any_change = True
                icon = "✅" if status == "done" else "❌"
                print(f"{icon} [{t['id']}] {status} (exit={rc}, log {log_size}B)")
                if status == "failed":
                    speak(f"{t.get('title', t['id'])} başarısız oldu.")

        if any_change:
            new_spawns = spawn_ready_tasks(rd, state, run_id)
            if new_spawns:
                any_running = True
            write_state(rd, state)

        if not any_running:
            break
        time.sleep(POLL_INTERVAL)

    finalize(rd, state, run_id)

def seal_run_events(run_id: str) -> int:
    """Mark this run's events as closed in all live feeds.
    Subsequent reader/observer hooks will skip them."""
    if not LIVE_DIR.exists():
        return 0
    sealed = 0
    for fp in LIVE_DIR.glob("*.jsonl"):
        try:
            lines = fp.read_text().splitlines()
        except Exception:
            continue
        new_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                new_lines.append(line)
                continue
            if e.get("director_run") == run_id and not e.get("closed"):
                e["closed"] = True
                sealed += 1
            new_lines.append(json.dumps(e, ensure_ascii=False))
        if sealed:
            fp.write_text("\n".join(new_lines) + "\n")
    return sealed

def finalize(rd: Path, state: dict, run_id: str) -> None:
    state["finished"] = datetime.now(timezone.utc).isoformat()
    counts = {"done": 0, "failed": 0, "timeout": 0, "pending": 0}
    for t in state["tasks"]:
        counts[t.get("status", "pending")] = counts.get(t.get("status", "pending"), 0) + 1
    write_state(rd, state)

    msg = f"{counts['done']}/{len(state['tasks'])} done"
    if counts["failed"] or counts["timeout"]:
        msg += f", {counts['failed']} failed, {counts['timeout']} timeout"
    director_event(run_id, state["cwd"], "complete", f"run bitti: {msg}")
    sealed = seal_run_events(run_id)
    notify("Director run bitti", msg)

    # Persist run summary to mnemonics for cross-session recall
    goal_short = (state.get("goal", "") or "")[:120]
    arm_tag = f" [{state['ab_arm']}]" if state.get("ab_arm") else ""
    fid_scores = [t.get("persona_fidelity", {}).get("score", -1) for t in state["tasks"]]
    fid_scores = [s for s in fid_scores if s >= 0]
    fid_tag = f", fidelity={sum(fid_scores)/len(fid_scores):.1f}/10" if fid_scores else ""
    summary_text = (
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] director{arm_tag}: "
        f"goal=\"{goal_short}\" — {msg}{fid_tag} (run_id={run_id})"
    )
    mnemonics_record(summary_text, ns="director")

    print("\n" + "=" * 50)
    print(f"📊 Run {run_id} bitti")
    print("=" * 50)
    for t in state["tasks"]:
        icon = {"done": "✅", "failed": "❌", "timeout": "⏰", "pending": "⏸"}.get(t["status"], "?")
        attempts = f" [{t['attempts']} attempt{'s' if t['attempts']!=1 else ''}]" if t.get("attempts", 0) > 1 else ""
        print(f"  {icon} [{t['id']}] {t['title']}  ({t['status']}){attempts}")
    if sealed:
        print(f"\n  🔒 {sealed} live-feed event sealed (cross-run noise temizlendi)")
    print(f"  Logs: {rd}/logs/")
    print(f"  Detay: director tail {run_id}\n")

def cmd_status(args) -> int:
    if args.run_id:
        rd = RUNS_DIR / args.run_id
        if not (rd / "state.json").exists():
            print(f"Run bulunamadı: {args.run_id}", file=sys.stderr)
            return 1
        state = load_state(rd)
        print(json.dumps(state, indent=2, ensure_ascii=False))
        return 0
    runs = sorted(RUNS_DIR.glob("*/state.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
    if not runs:
        print("Hiç run yok.")
        return 0
    print(f"{'RUN ID':30s}  {'STATUS':10s}  GOAL")
    for sp in runs:
        s = json.loads(sp.read_text())
        finished = "finished" if s.get("finished") else "running"
        goal = (s.get("goal", "") or "")[:50]
        print(f"{s['run_id']:30s}  {finished:10s}  {goal}")
    return 0

def cmd_tail(args) -> int:
    rd = RUNS_DIR / args.run_id
    if not rd.exists():
        print(f"Run bulunamadı: {args.run_id}", file=sys.stderr)
        return 1
    state = load_state(rd)
    for t in state["tasks"]:
        log = rd / "logs" / f"{t['id']}.log"
        print(f"\n=== [{t['id']}] {t['title']} ({t['status']}) ===")
        if log.exists():
            text = log.read_text()
            print(text[-2000:] if len(text) > 2000 else text)
        else:
            print("(log yok)")
    return 0

def cmd_ab(args) -> int:
    """A/B test: run the same plan once with personas, once without.
    Compares per-task critic verdicts to determine if persona injection
    actually helps or is placebo."""
    cwd = os.getcwd()
    print(f"🧪 A/B test: {args.goal}")
    print(f"📂 Working dir: {cwd}\n")
    speak("A B testi başlıyor, plan üretiliyor.")
    print("⏳ Decomposition (tek plan, iki arm aynısını kullanacak)...")
    try:
        plan = decompose(args.goal, cwd, DEFAULT_TIMEOUT_MIN)
        if not args.skip_critic:
            print("🔍 Critic plan'ı eleştiriyor...")
            critique = critique_plan(plan, args.goal)
            if critique["verdict"] == "revise" and critique["issues"]:
                print(f"   Critic: {', '.join(critique['issues'][:3])}")
                plan = revise_from_critic(plan, critique, args.goal, cwd, DEFAULT_TIMEOUT_MIN)
    except Exception as e:
        print(f"❌ Decompose hatası: {e}", file=sys.stderr)
        return 2

    print(f"\n📋 {len(plan['tasks'])} task — iki arm sequential olarak çalışacak (~2x token).")
    for t in plan["tasks"]:
        print(f"  [{t['id']}] {t['title']}")
    if not args.yes:
        ans = input("Devam? [Y/n]: ").strip().lower()
        if ans not in ("", "y", "yes", "evet", "e"):
            print("İptal.")
            return 1

    arms = []
    setup_cmd = getattr(args, "setup_cmd", None)
    for arm_label, persona_off in [("A-persona-on", False), ("B-persona-off", True)]:
        print(f"\n{'='*60}\n🧪 ARM: {arm_label}\n{'='*60}")
        speak(f"Kol {arm_label[0]} başlıyor, persona {'açık' if not persona_off else 'kapalı'}.")
        if setup_cmd:
            print(f"🔧 Fixture setup: {setup_cmd}")
            try:
                subprocess.run(setup_cmd, shell=True, cwd=cwd, check=True, timeout=60)
            except subprocess.CalledProcessError as e:
                print(f"❌ Setup başarısız (exit {e.returncode}), arm atlanıyor")
                continue
            except subprocess.TimeoutExpired:
                print(f"❌ Setup timeout (60s), arm atlanıyor")
                continue
        run_id = new_run_id() + f"-{arm_label}"
        rd = run_dir(run_id)
        state = {
            "run_id": run_id,
            "goal": args.goal,
            "summary": plan.get("summary", ""),
            "started": datetime.now(timezone.utc).isoformat(),
            "cwd": cwd,
            "max_retries": 0,  # retry compares unfair, disable
            "skip_task_critic": False,  # critic mandatory for measurement
            "persona_off": persona_off,
            "ab_arm": arm_label,
            "tasks": [],
        }
        for t in plan["tasks"]:
            state["tasks"].append({
                **t, "status": "pending", "pid": None, "started": None,
                "ended": None, "attempts": 0, "exit_code": None,
            })
        write_state(rd, state)
        director_event(run_id, cwd, "plan", f"AB arm {arm_label}: {len(state['tasks'])} task")
        spawn_ready_tasks(rd, state, run_id)
        write_state(rd, state)
        try:
            monitor_loop(rd, state, run_id)
        except KeyboardInterrupt:
            print(f"\n⏸ ARM {arm_label} iptal. Sonuçlar kısmi.")
            return 1
        arms.append((arm_label, rd, state))
        # Clear in-memory popen handles between arms (prevents stale lookups)
        _PROCS.clear()

    # Compare
    print("\n" + "=" * 60)
    print("📊 A/B Test Karşılaştırma")
    print("=" * 60)
    summary = []
    for arm_label, rd, state in arms:
        passes = sum(1 for t in state["tasks"] if t.get("critic", {}).get("verdict") == "pass")
        fails = sum(1 for t in state["tasks"] if t.get("critic", {}).get("verdict") == "fail")
        no_critic = sum(1 for t in state["tasks"] if not t.get("critic"))
        done = sum(1 for t in state["tasks"] if t["status"] == "done")
        log_sizes = []
        for t in state["tasks"]:
            log = rd / "logs" / f"{t['id']}.log"
            if log.exists():
                log_sizes.append(log.stat().st_size)
        avg_size = sum(log_sizes) / len(log_sizes) if log_sizes else 0
        duration = 0
        try:
            end = state.get("finished") or datetime.now(timezone.utc).isoformat()
            duration = (datetime.fromisoformat(end) - datetime.fromisoformat(state["started"])).total_seconds()
        except Exception:
            pass
        # Persona fidelity (sadece persona-on arm için kayıtlı)
        # Completion-weighted: a 0/N done arm cannot legitimately score 10/10 fidelity
        # because the worker did not actually deliver the artifact. Multiply by done_ratio
        # to anchor fidelity to outcome, not just stylistic adherence on partial output.
        fidelity_scores = [t.get("persona_fidelity", {}).get("score", -1) for t in state["tasks"]]
        fidelity_scores = [s for s in fidelity_scores if s >= 0]
        raw_avg_fidelity = sum(fidelity_scores) / len(fidelity_scores) if fidelity_scores else None
        done_ratio_for_fid = done / max(1, len(state["tasks"]))
        avg_fidelity = raw_avg_fidelity * done_ratio_for_fid if raw_avg_fidelity is not None else None
        summary.append({
            "arm": arm_label, "pass": passes, "fail": fails, "no_critic": no_critic,
            "done": done, "avg_log": avg_size, "duration": duration,
            "fidelity": avg_fidelity,
            "fidelity_raw": raw_avg_fidelity,
        })
        print(f"\n  ARM {arm_label}  ({state['run_id']}):")
        print(f"    critic pass={passes}  fail={fails}  uncategorized={no_critic}")
        print(f"    completed={done}/{len(state['tasks'])}")
        print(f"    avg log size: {avg_size:.0f}B")
        print(f"    duration: {duration:.0f}s")
        if avg_fidelity is not None:
            raw_tag = ""
            if raw_avg_fidelity is not None and abs(raw_avg_fidelity - avg_fidelity) > 0.5:
                raw_tag = f"  (raw {raw_avg_fidelity:.1f} × completion {done_ratio_for_fid:.2f})"
            print(f"    🎭 avg persona-fidelity: {avg_fidelity:.1f}/10  (n={len(fidelity_scores)}){raw_tag}")

    # Verdict
    if len(summary) == 2:
        a, b = summary
        delta_pass = a["pass"] - b["pass"]
        delta_log = a["avg_log"] - b["avg_log"]
        print(f"\n🏁 Verdict:")
        if delta_pass > 0:
            print(f"   Persona ON kazandı: {delta_pass} daha fazla critic-pass")
        elif delta_pass < 0:
            print(f"   Persona OFF kazandı: {-delta_pass} daha fazla critic-pass (persona ZARAR verdi?)")
        else:
            print(f"   Eşit critic-pass: {a['pass']} = {b['pass']}")
            if abs(delta_log) > 100:
                better = "ON" if delta_log > 0 else "OFF"
                print(f"   Log size farkı: persona {better} {abs(delta_log):.0f}B daha fazla output")
            else:
                print(f"   Critic-pass açısından eşit görünüyor.")
        # Persona fidelity verdict (qualitative — measures stylistic adherence)
        fid_label = ""
        if a.get("fidelity") is not None:
            f = a["fidelity"]
            if f >= 7:
                fid_label = f"GÜÇLÜ ({f:.1f}/10)"
                print(f"   🎭 Persona fidelity GÜÇLÜ ({f:.1f}/10): persona description child output'ta net görünüyor.")
                speak(f"A B testi bitti. Persona fidelity güçlü, on üzerinden {f:.0f} puan.")
            elif f >= 4:
                fid_label = f"ORTA ({f:.1f}/10)"
                print(f"   🎭 Persona fidelity ORTA ({f:.1f}/10): kısmi etki, prompt sertleştirilebilir.")
                speak(f"A B testi bitti. Persona fidelity orta, on üzerinden {f:.0f}.")
            else:
                fid_label = f"ZAYIF ({f:.1f}/10)"
                print(f"   🎭 Persona fidelity ZAYIF ({f:.1f}/10): description child'a ulaşıyor ama davranışı şekillendirmiyor.")
                speak(f"A B testi bitti. Persona fidelity zayıf, on üzerinden {f:.0f}.")
        # Persist A/B verdict as a learning (separate from per-run summary)
        verdict_text = (
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] director ab-verdict: "
            f"goal=\"{(args.goal or '')[:100]}\" — "
            f"A(on) pass={a['pass']}/{a['done']} log={a['avg_log']:.0f}B; "
            f"B(off) pass={b['pass']}/{b['done']} log={b['avg_log']:.0f}B; "
            f"persona-fidelity={fid_label or 'N/A'}"
        )
        mnemonics_record(verdict_text, ns="director")

        # --- Auto-tightener loop (closed-loop self-improvement primitive) ---
        # Trigger: persona-on incomplete vs persona-off OR low fidelity OR verbose drift
        a_state = arms[0][2]  # A persona-on state
        a_done_ratio = a["done"] / max(1, len(a_state["tasks"]))
        b_done_ratio = b["done"] / max(1, len(arms[1][2]["tasks"]))
        completion_drop = b_done_ratio - a_done_ratio  # positive = persona-on lost ground
        log_inflation = (a["avg_log"] - b["avg_log"]) / max(1, b["avg_log"])  # positive = persona-on verbose
        # Use fidelity if known, else neutral 10. Use explicit `is None` to avoid
        # the `0 or 10` Python falsy-coalesce trap (a legitimate 0 fidelity must NOT
        # be silently promoted to 10).
        avg_fid = a.get("fidelity") if a.get("fidelity") is not None else 10
        # Absolute completion-floor: if persona-on cannot finish at least half its
        # tasks, fire drift unconditionally regardless of B-arm outcome. Catches the
        # pathological case where both arms fail but A failed catastrophically.
        absolute_completion_fail = a_done_ratio < 0.5 and len(a_state["tasks"]) > 1
        drift_suspected = (
            (completion_drop > 0.15)
            or (avg_fid < 7)
            or (log_inflation > 0.30 and a["fail"] > 0)
            or absolute_completion_fail
        )
        if drift_suspected:
            # Find most-impacted persona from A arm tasks
            impacted = {}
            for t in a_state["tasks"]:
                pid = t.get("persona_id")
                if not pid or pid == "default":
                    continue
                impacted.setdefault(pid, []).append(t)
            if impacted:
                # Pick persona with worst fidelity or any failed task
                target_pid = None
                worst_score = 11
                for pid, tasks in impacted.items():
                    fids = [t.get("persona_fidelity", {}).get("score", 10) for t in tasks if t.get("persona_fidelity")]
                    avg_pid_fid = sum(fids) / len(fids) if fids else 10
                    has_fail = any(t["status"] == "failed" for t in tasks)
                    if has_fail or avg_pid_fid < worst_score:
                        target_pid = pid
                        worst_score = avg_pid_fid
                if target_pid:
                    sample_task = next((t for t in a_state["tasks"] if t.get("persona_id") == target_pid), None)
                    sample_log = ""
                    if sample_task:
                        log_path = arms[0][1] / "logs" / f"{sample_task['id']}.log"
                        if log_path.exists():
                            sample_log = log_path.read_text()[:3000]
                    drift_reasons = []
                    if completion_drop > 0.15: drift_reasons.append(f"comp_drop={completion_drop:.2f}")
                    if avg_fid < 7: drift_reasons.append(f"fid={avg_fid:.1f}")
                    if log_inflation > 0.30 and a["fail"] > 0: drift_reasons.append(f"log_infl={log_inflation:.2f}")
                    if absolute_completion_fail: drift_reasons.append(f"a_done_ratio={a_done_ratio:.2f}<0.5")
                    print(f"\n🔁 Auto-tightener: drift suspected ({', '.join(drift_reasons)})")
                    print(f"   Hedef persona: {target_pid} (worst fidelity={worst_score:.1f})")
                    speak(f"Verbose drift tespit edildi, persona {target_pid} sertleştiriliyor.")
                    proposal = auto_tighten_persona(target_pid, sample_log)
                    if proposal:
                        print(f"\n   --- ESKİ ({len(proposal['old'])}c) ---")
                        print(f"   {proposal['old']}")
                        print(f"\n   --- ÖNERİLEN ({len(proposal['new'])}c) ---")
                        print(f"   {proposal['new']}")
                        if getattr(args, "auto_tighten", False):
                            ok = apply_tightened_persona(target_pid, proposal["new"])
                            if ok:
                                print(f"\n   ✅ persona '{target_pid}' güncellendi (yedek alındı)")
                                speak(f"Persona {target_pid} otomatik sertleştirildi.")
                                mnemonics_record(
                                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] director auto-tighten: "
                                    f"persona={target_pid} | drift_signals=(comp_drop={completion_drop:.2f}, "
                                    f"fid={avg_fid:.1f}, log_infl={log_inflation:.2f}) | "
                                    f"old={len(proposal['old'])}c → new={len(proposal['new'])}c",
                                    ns="director",
                                )
                            else:
                                print(f"\n   ❌ apply başarısız")
                        else:
                            print(f"\n   💡 Uygulamak için: --auto-tighten flag'i ile çalıştır")
                    else:
                        print(f"   ⚠ tightener öneri üretemedi (uzun/kısa/aynı/parse fail)")
        else:
            print(f"\n   ✓ drift sinyali yok (comp_drop={completion_drop:.2f}, fid={avg_fid:.1f}, log_infl={log_inflation:.2f})")
    return 0

def cmd_attach(args) -> int:
    """Re-attach to a detached run. Popen handles are lost, so we fall back to
    PID-based liveness check (kill 0). Exit code is unknown but log content is
    handed to the per-task critic to validate completion."""
    rd = RUNS_DIR / args.run_id
    if not (rd / "state.json").exists():
        print(f"Run bulunamadı: {args.run_id}", file=sys.stderr)
        return 1
    state = load_state(rd)
    if state.get("finished"):
        print(f"Run zaten bitmiş ({state['finished']}). Çıkıyorum.")
        return 0
    print(f"🔌 Attached: {state['run_id']}  ({state.get('summary','')})")
    print(f"   Polling every {POLL_INTERVAL}s. Ctrl+C → tekrar detach.")
    try:
        monitor_loop(rd, state, state["run_id"])
    except KeyboardInterrupt:
        print("\n⏸ Re-detached.")
        return 0
    return 0

def cmd_cancel(args) -> int:
    rd = RUNS_DIR / args.run_id
    if not (rd / "state.json").exists():
        print(f"Run bulunamadı: {args.run_id}", file=sys.stderr)
        return 1
    state = load_state(rd)
    killed = 0
    for t in state["tasks"]:
        if t.get("status") == "running" and t.get("pid"):
            try:
                os.killpg(os.getpgid(t["pid"]), signal.SIGTERM)
                t["status"] = "cancelled"
                t["ended"] = datetime.now(timezone.utc).isoformat()
                killed += 1
            except Exception:
                pass
    state["cancelled"] = datetime.now(timezone.utc).isoformat()
    write_state(rd, state)
    sealed = seal_run_events(args.run_id)
    print(f"Cancelled {killed} running task(s); sealed {sealed} live-feed events.")
    return 0

def cmd_mnemonics_replay(args) -> int:
    """Replay fallback mnemonics records. Operator-triggered (never implicit)."""
    if not MNEMONICS_FALLBACK_PATH.exists():
        print(f"✓ Fallback dosyası yok ({MNEMONICS_FALLBACK_PATH}), replay'e gerek yok.")
        return 0
    print(f"⏳ Replay başlıyor: {MNEMONICS_FALLBACK_PATH}")
    report = mnemonics_replay_fallback()
    print(f"✓ ingested={report['ingested']}, remaining={report['remaining']}")
    if report["errors"]:
        print(f"⚠️  {len(report['errors'])} hata, ilk 3:")
        for e in report["errors"][:3]:
            print(f"   - {e}")
    return 0 if report["remaining"] == 0 else 1

def main() -> int:
    p = argparse.ArgumentParser(prog="director")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Yeni run başlat")
    p_run.add_argument("goal", help="Hedef metni")
    p_run.add_argument("-y", "--yes", action="store_true", help="Plan onayı atla")
    p_run.add_argument("-t", "--timeout", type=int, default=0,
                       help="Tüm task'lar için timeout (dk). NIM'in verdiği timeout_min'i ezer.")
    p_run.add_argument("-r", "--retries", type=int, default=DEFAULT_MAX_RETRIES,
                       help=f"Max retry/task (default {DEFAULT_MAX_RETRIES})")
    p_run.add_argument("--skip-critic", action="store_true",
                       help="Plan critique loop'u atla (basit hedeflerde hız için)")
    p_run.add_argument("--skip-task-critic", action="store_true",
                       help="Per-task output validation'ı atla")
    p_run.set_defaults(func=cmd_run)

    p_st = sub.add_parser("status", help="Run durumunu göster")
    p_st.add_argument("run_id", nargs="?")
    p_st.set_defaults(func=cmd_status)

    p_tl = sub.add_parser("tail", help="Run loglarını göster")
    p_tl.add_argument("run_id")
    p_tl.set_defaults(func=cmd_tail)

    p_cn = sub.add_parser("cancel", help="Running task'ları öldür")
    p_cn.add_argument("run_id")
    p_cn.set_defaults(func=cmd_cancel)

    p_at = sub.add_parser("attach", help="Detach edilmiş run'a geri bağlan")
    p_at.add_argument("run_id")
    p_at.set_defaults(func=cmd_attach)

    p_ab = sub.add_parser("ab", help="A/B test: persona on vs off, kalite kıyas")
    p_ab.add_argument("goal")
    p_ab.add_argument("-y", "--yes", action="store_true")
    p_ab.add_argument("--skip-critic", action="store_true")
    p_ab.add_argument("--setup-cmd", default=None,
                      help="Shell command her arm öncesi koşturulur (fixture izolasyonu). Örn: 'rm -rf /tmp/x && cp -R fixture/ /tmp/x'")
    p_ab.add_argument("--auto-tighten", action="store_true",
                      help="Drift tespitinde persona description'ı V4 Pro ile sertleştir ve personas.json'a yaz (yedek alınır). Closed-loop self-improvement.")
    p_ab.set_defaults(func=cmd_ab)

    p_mr = sub.add_parser("mnemonics-replay",
                          help="Fallback dosyasındaki mnemonics kayıtlarını yeniden ingest dene (M2-T03)")
    p_mr.set_defaults(func=cmd_mnemonics_replay)

    args = p.parse_args()
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
