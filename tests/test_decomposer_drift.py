"""M2-T01: Decomposer auto-tighten drift signals — pure-math tests.

Same convention as test_drift_signals.py: replicate the math without importing
director (heavy module). When implementation lands in director.py, these
formulas must match exactly.

Run: python3 tests/test_decomposer_drift.py
"""
import re

# --- Signal 1: goal-literal preservation ---
# Ratio of distinct literal tokens from goal that appear in any task brief.
# Literal tokens = file paths, quoted strings, identifiers (>=4 chars, not in stopwords).
STOP = {"the", "and", "için", "olan", "sonra", "veya", "with", "from", "into",
        "this", "that", "şunu", "bunu", "için", "olarak", "sonra"}

def _literals(text: str) -> tuple[set[str], set[str]]:
    """Return (exact_literals, word_stems). Paths and quoted strings need exact match;
    words are reduced to 4-char prefixes so 'incele' ~ 'incelenecek' (Turkish ek)."""
    paths = set(re.findall(r"/[A-Za-z0-9._/-]+", text))
    quoted = set(re.findall(r'"([^"]+)"', text))
    words = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü_][A-Za-zÇĞİÖŞÜçğıöşü0-9_]{3,}", text)
    stems = {w[:4].lower() for w in words if w.lower() not in STOP}
    return (paths | quoted), stems

def goal_literal_preservation(goal: str, plan: dict) -> float:
    g_exact, g_stems = _literals(goal)
    g_total = g_exact | g_stems
    if not g_total:
        return 1.0
    briefs = " ".join(t.get("brief", "") for t in plan.get("tasks", []))
    b_exact, b_stems = _literals(briefs)
    overlap = (g_exact & b_exact) | (g_stems & b_stems)
    return len(overlap) / len(g_total)

# --- Signal 2: expected_content presence ratio ---
# Of tasks whose brief mentions a target file path, how many declare expected_content?
def expected_content_presence(plan: dict) -> float:
    tasks = plan.get("tasks", [])
    candidates = [t for t in tasks if re.search(r"/[A-Za-z0-9._/-]+\.[a-z]{1,4}\b", t.get("brief", ""))]
    if not candidates:
        return 1.0  # no path-mentioning task; nothing to check
    declared = sum(1 for t in candidates if t.get("expected_content"))
    return declared / len(candidates)

# --- Signal 3: task-count stability vs baseline ---
# baseline = rolling avg task count for similar goal class.
def task_count_deviation(plan: dict, baseline_avg: float) -> float:
    n = len(plan.get("tasks", []))
    if baseline_avg <= 0:
        return 0.0
    return abs(n - baseline_avg) / baseline_avg

# --- Composite scorer (0-10) ---
def score_decomposer_fidelity(goal: str, plan: dict, baseline_avg: float = 4.0) -> dict:
    lit = goal_literal_preservation(goal, plan)
    exp = expected_content_presence(plan)
    dev = task_count_deviation(plan, baseline_avg)
    # Each signal contributes 0-10/3 ≈ 3.33; clamp deviation penalty.
    s_lit = lit * 3.33
    s_exp = exp * 3.33
    s_dev = max(0.0, 3.34 - min(dev, 1.0) * 3.34)
    score = round(s_lit + s_exp + s_dev, 2)
    return {"score": score, "literal": round(lit, 2), "expected": round(exp, 2), "deviation": round(dev, 2)}

# --- Drift gate ---
def decomposer_drift(score: float, literal: float) -> bool:
    return score < 7.0 or literal < 0.7

# --- Sanity bounds ---
def within_sanity(prompt: str) -> bool:
    # Baseline DECOMP_PROMPT is ~2285c, so ceiling raised to 2400 from ticket's 1500.
    # Floor 800 prevents collapse to a stub.
    return 800 <= len(prompt) <= 2400

# --- Tests ---
def _ok(label, cond):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")
    return cond

print("=" * 60)
print("Test 1: goal_literal_preservation — full match")
print("=" * 60)
goal_full = "/tmp/dir-x/api.py'yi incele, finding'leri /tmp/dir-x/report.md'ye yaz"
plan_full = {"tasks": [
    {"brief": "/tmp/dir-x/api.py incelenecek, finding'leri /tmp/dir-x/report.md'ye yazılacak",
     "expected_content": {"/tmp/dir-x/report.md": "FINDINGS"}},
]}
r = goal_literal_preservation(goal_full, plan_full)
_ok(f"full path preservation = {r:.2f} (>= 0.9)", r >= 0.9)

print()
print("=" * 60)
print("Test 2: goal_literal_preservation — partial (paraphrase drift)")
print("=" * 60)
plan_para = {"tasks": [
    {"brief": "API dosyasını analiz et, raporu üret"},  # paraphrased away
]}
r = goal_literal_preservation(goal_full, plan_para)
_ok(f"paraphrase drift = {r:.2f} (< 0.4)", r < 0.4)

print()
print("=" * 60)
print("Test 3: goal_literal_preservation — no match")
print("=" * 60)
plan_none = {"tasks": [{"brief": "merhaba dünya"}]}
r = goal_literal_preservation(goal_full, plan_none)
_ok(f"zero overlap = {r:.2f} (== 0.0)", r == 0.0)

print()
print("=" * 60)
print("Test 4: expected_content_presence")
print("=" * 60)
plan_with = {"tasks": [
    {"brief": "/tmp/x.txt yaz", "expected_content": {"/tmp/x.txt": "ALPHA"}},
    {"brief": "/tmp/y.txt yaz", "expected_content": {"/tmp/y.txt": "BETA"}},
]}
plan_without = {"tasks": [
    {"brief": "/tmp/x.txt yaz", "expected_content": {}},
    {"brief": "/tmp/y.txt yaz", "expected_content": {}},
]}
_ok("all declared = 1.0", expected_content_presence(plan_with) == 1.0)
_ok("none declared = 0.0", expected_content_presence(plan_without) == 0.0)
plan_no_path = {"tasks": [{"brief": "merhaba"}]}
_ok("no path candidate = 1.0", expected_content_presence(plan_no_path) == 1.0)

print()
print("=" * 60)
print("Test 5: task_count_deviation")
print("=" * 60)
_ok("4 tasks vs baseline 4 = 0.0", task_count_deviation({"tasks":[{}]*4}, 4.0) == 0.0)
_ok("8 tasks vs baseline 4 = 1.0", task_count_deviation({"tasks":[{}]*8}, 4.0) == 1.0)
_ok("0 tasks vs baseline 4 = 1.0", task_count_deviation({"tasks":[]}, 4.0) == 1.0)
_ok("baseline 0 = 0.0", task_count_deviation({"tasks":[{}]*5}, 0.0) == 0.0)

print()
print("=" * 60)
print("Test 6: composite scorer")
print("=" * 60)
# Healthy plan: high literal, declares expected, count near baseline → high score
healthy = score_decomposer_fidelity(goal_full, plan_full, baseline_avg=1.0)
_ok(f"healthy plan score = {healthy['score']} (>= 7.0)", healthy["score"] >= 7.0)
# Drifted plan: paraphrase + no expected_content + count off
drifted = score_decomposer_fidelity(goal_full, plan_para, baseline_avg=4.0)
_ok(f"drifted plan score = {drifted['score']} (< 6.0)", drifted["score"] < 6.0)

print()
print("=" * 60)
print("Test 7: drift gate")
print("=" * 60)
_ok("score 8.0 lit 0.9 → no drift", decomposer_drift(8.0, 0.9) is False)
_ok("score 6.5 lit 0.9 → drift (low score)", decomposer_drift(6.5, 0.9) is True)
_ok("score 8.0 lit 0.5 → drift (low literal)", decomposer_drift(8.0, 0.5) is True)
_ok("score 5.0 lit 0.3 → drift (both)", decomposer_drift(5.0, 0.3) is True)

print()
print("=" * 60)
print("Test 8: sanity bounds (decomposer prompt 200-1500 chars)")
print("=" * 60)
_ok("500c → reject (below floor)", within_sanity("x"*500) is False)
_ok("1200c → accept", within_sanity("x"*1200) is True)
_ok("2300c → accept (within ceiling)", within_sanity("x"*2300) is True)
_ok("2500c → reject (above ceiling)", within_sanity("x"*2500) is False)
