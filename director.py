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

def mnemonics_record(text: str, ns: str = "director") -> None:
    """Persist a run summary or learning into mnemonics DB (cross-session memory).
    Errors logged to ~/.claude/director/mnemonics.log instead of swallowed silently."""
    if not text.strip():
        return
    env = {**os.environ, "HF_HUB_DISABLE_PROGRESS_BARS": "1", "TRANSFORMERS_VERBOSITY": "error"}
    try:
        proc = subprocess.run(
            ["mnemonics", "ingest", "--ns", ns, text],
            check=False, timeout=15, env=env,
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            with open(MNEMONICS_LOG_PATH, "a") as lf:
                lf.write(f"[{datetime.now().isoformat(timespec='seconds')}] FAIL ns={ns} rc={proc.returncode}: {proc.stderr[-500:]}\n")
    except subprocess.TimeoutExpired:
        with open(MNEMONICS_LOG_PATH, "a") as lf:
            lf.write(f"[{datetime.now().isoformat(timespec='seconds')}] TIMEOUT ns={ns}: {text[:120]}\n")
    except Exception as e:
        try:
            with open(MNEMONICS_LOG_PATH, "a") as lf:
                lf.write(f"[{datetime.now().isoformat(timespec='seconds')}] ERR ns={ns}: {e}\n")
        except Exception:
            pass

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
DECOMP_PROMPT = """Sen bir project director'sın. Kullanıcının verdiği hedefi paralel çalışılabilecek alt-task'lara böl.

Kurallar:
- 2-6 task arası (daha az = paralelleşme yok, daha çok = chaos)
- Bir task BAŞKA bir task'ın çıktısını/sonucunu kullanıyorsa depends_on ile belirt
- Her task tek bir Claude session'ında bitebilecek BÜYÜKLÜKTE olmalı
- Hedef hâlihazırda atomik ise (tek bir küçük iş) → 1 task döndür
- complexity: 'low' (basit dosya/komut, ~5-10dk), 'med' (refactor/modifikasyon, ~20-30dk), 'high' (mimari/araştırma, ~45-60dk)
- complexity'ye göre timeout_min otomatik atanır (low=10, med=30, high=60); özel durumda override et
- expected_artifacts: Eğer task somut bir dosya/dosyalar üretmeli ise mutlak yolları liste olarak ver (örn. ["/tmp/foo/L1.txt"]). Hedefte geçen dosya adı veya yolu BIRAKMADAN aynen aktar — paraphrase etme. Üretilecek dosya yoksa boş liste []
- expected_content: Hedefte üretilecek içerik LITERAL string olarak (boşluk, satır, büyük/küçük harf dahil) belirtildiyse, dosya yolunu key, beklenen string'i value yaparak object döndür (örn. {"/tmp/foo/L1.txt": "ALPHA BETA"}). Hedefte literal içerik yoksa veya tartışmalıysa boş object {}. Bu alan child'a quote içinde verilecek, paraphrase yasak.
- persona: task'ın domain'ine göre seç. Seçenekler:
  - 'security' (auth, vuln, exploit, token, audit)
  - 'design' (UI, UX, frontend, CSS, accessibility)
  - 'performance' (perf, latency, memory, profiling)
  - 'research' (bug bounty, recon, OSINT, CVE)
  - 'refactor' (cleanup, dead code, teknik borç)
  - 'implementer' (yeni feature, endpoint, generic kod)
  - 'ataturk' (stratejik, mimari karar, plan)
  - 'default' (hiçbiri uymuyorsa)

Çıktı SADECE valid JSON, başka hiçbir metin ekleme. Markdown code fence yok.

{
  "summary": "Hedefin 1 cümle yorumu",
  "tasks": [
    {
      "id": "task-1",
      "title": "kısa başlık",
      "brief": "Claude'a verilecek 2-3 cümlelik talimat",
      "cwd": "/abs/path veya '' (boşsa parent cwd kullanılır)",
      "depends_on": [],
      "complexity": "low|med|high",
      "persona": "security|design|performance|research|refactor|implementer|ataturk|default",
      "timeout_min": 30,
      "expected_artifacts": ["/abs/path/file.ext"],
      "expected_content": {"/abs/path/file.ext": "literal beklenen icerik"}
    }
  ]
}
"""

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

TASK_CRITIC_PROMPT = """Sen bir QA agent'ısın. Bir alt-task'ın output'unu inceleyip BAŞARILI olup olmadığına karar vereceksin.

ÖNEMLİ — Disk doğrulama zaten geçti:
{artifact_status}
Yani task'ın somut çıktısı diskte mevcut ve (varsa) içerik literal olarak eşleşiyor. Senin işin sadece açık başarısızlık sinyali yakalamak.

Task brief'i: {brief}

Task output (claude -p log'u):
{output}

SADECE şu açık başarısızlık sinyallerinde 'fail' ver:
- Log'da explicit refuse: "yapamam", "yapamadım", "izin verilmedi", "bunu yapmayacağım"
- Log'da explicit error/exception: traceback, "Error:", "Exception:", non-zero exit hint
- Log'da "kullanıcıya soracağım", "açıklığa kavuştur" gibi tamamlanmamışlık ifadesi
- Brief tamamen farklı bir iş yapılmış (örn. silmesi gerekirken eklemiş)

Aşağıdaki durumlar 'fail' DEĞİLDİR (default 'pass'):
- Log kısa veya minimal — claude bazen sadece dosya yazıp çıkar, açıklama yapmaz
- "Verified", "doğruladı" gibi explicit kanıt eksikliği — disk zaten doğrulandı
- Output stiliyle ilgili şikayetler — sadece içerik ve task tamamlanması önemli

Çıktı SADECE valid JSON, başka metin yok:
{
  "verdict": "pass" | "fail",
  "reason": "1 cümle gerekçe (max 100 char)"
}

Default 'pass'. Sadece yukarıdaki açık fail sinyallerinden BIRI varsa 'fail'.
"""

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
        fidelity_scores = [t.get("persona_fidelity", {}).get("score", -1) for t in state["tasks"]]
        fidelity_scores = [s for s in fidelity_scores if s >= 0]
        avg_fidelity = sum(fidelity_scores) / len(fidelity_scores) if fidelity_scores else None
        summary.append({
            "arm": arm_label, "pass": passes, "fail": fails, "no_critic": no_critic,
            "done": done, "avg_log": avg_size, "duration": duration,
            "fidelity": avg_fidelity,
        })
        print(f"\n  ARM {arm_label}  ({state['run_id']}):")
        print(f"    critic pass={passes}  fail={fails}  uncategorized={no_critic}")
        print(f"    completed={done}/{len(state['tasks'])}")
        print(f"    avg log size: {avg_size:.0f}B")
        print(f"    duration: {duration:.0f}s")
        if avg_fidelity is not None:
            print(f"    🎭 avg persona-fidelity: {avg_fidelity:.1f}/10  (n={len(fidelity_scores)})")

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
        avg_fid = a.get("fidelity") or 10
        drift_suspected = (completion_drop > 0.15) or (avg_fid < 7) or (log_inflation > 0.30 and a["fail"] > 0)
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
                    print(f"\n🔁 Auto-tightener: drift suspected (completion_drop={completion_drop:.2f}, fid={avg_fid:.1f}, log_inflation={log_inflation:.2f})")
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

    args = p.parse_args()
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
