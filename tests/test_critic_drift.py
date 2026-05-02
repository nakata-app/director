"""M2-T02: Critic auto-tighten drift signals — pure-math tests.

Same convention as test_decomposer_drift.py: replicate the math without
importing director (heavy module). When implementation lands, formulas
in director.py must match exactly.

Run: python3 tests/test_critic_drift.py
"""

# --- Precision / Recall against disk-truth ground truth ---
# decisions: list of dicts {'task_id', 'critic_verdict' ('pass'|'fail'), 'disk_truth_pass' (bool)}
#   disk_truth_pass = artifact exists AND (no expected_content OR byte-matches)
# precision = correct PASS / total critic-PASS
# recall = correct PASS / total disk-truth-PASS

def critic_precision(decisions: list[dict]) -> float:
    pass_calls = [d for d in decisions if d["critic_verdict"] == "pass"]
    if not pass_calls:
        return 1.0  # no PASS calls → no false positives possible (vacuous)
    correct = sum(1 for d in pass_calls if d["disk_truth_pass"])
    return correct / len(pass_calls)

def critic_recall(decisions: list[dict]) -> float:
    truth_pass = [d for d in decisions if d["disk_truth_pass"]]
    if not truth_pass:
        return 1.0  # no ground truth → vacuous (D4 blind-spot ceiling)
    found = sum(1 for d in truth_pass if d["critic_verdict"] == "pass")
    return found / len(truth_pass)

def score_critic_quality(decisions: list[dict]) -> dict:
    p = critic_precision(decisions)
    r = critic_recall(decisions)
    return {"precision": round(p, 3), "recall": round(r, 3),
            "n_decisions": len(decisions),
            "n_pass_calls": sum(1 for d in decisions if d["critic_verdict"] == "pass"),
            "n_truth_pass": sum(1 for d in decisions if d["disk_truth_pass"])}

# --- Bidirectional drift gate ---
# precision < 0.8 (too lenient) OR recall < 0.8 (too strict)
def critic_drift_fires(precision: float, recall: float) -> bool:
    return precision < 0.8 or recall < 0.8

# --- Sanity bounds for tightened critic prompt ---
# 150-1000 chars per ticket
def critic_within_sanity(prompt: str) -> bool:
    return 150 <= len(prompt) <= 1000

# --- D1 anti-collapse: tightened prompt must keep disk-truth criterion ---
def critic_density_ok(prompt: str) -> bool:
    p = prompt.lower()
    # Must mention disk/artifact verification AND pass/fail decision schema
    has_disk = any(k in p for k in ["disk", "artifact", "byte", "literal"])
    has_decision = "pass" in p and "fail" in p
    return has_disk and has_decision

# --- D2 mixed-family enforcement ---
# tightener family must differ from worker family AND scorer family
def mixed_family_ok(worker_family: str, tightener_family: str) -> bool:
    return worker_family.lower() != tightener_family.lower()

# --- Tests ---
def _ok(label, cond):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")
    return cond

print("=" * 60)
print("Test 1: precision math")
print("=" * 60)
# 4 PASS decisions, 3 actually correct → precision = 0.75
case_a = [
    {"task_id": "t1", "critic_verdict": "pass", "disk_truth_pass": True},
    {"task_id": "t2", "critic_verdict": "pass", "disk_truth_pass": True},
    {"task_id": "t3", "critic_verdict": "pass", "disk_truth_pass": True},
    {"task_id": "t4", "critic_verdict": "pass", "disk_truth_pass": False},  # false-pass
    {"task_id": "t5", "critic_verdict": "fail", "disk_truth_pass": False},
]
_ok(f"4 PASS, 3 correct → 0.75 (got {critic_precision(case_a):.2f})", abs(critic_precision(case_a) - 0.75) < 0.01)
# all PASS correct → 1.0
case_perfect = [
    {"task_id": "t1", "critic_verdict": "pass", "disk_truth_pass": True},
    {"task_id": "t2", "critic_verdict": "pass", "disk_truth_pass": True},
]
_ok(f"all-correct PASS → 1.0", critic_precision(case_perfect) == 1.0)
# no PASS calls → 1.0 vacuous
_ok(f"no PASS calls → 1.0 vacuous", critic_precision([{"task_id":"t1","critic_verdict":"fail","disk_truth_pass":False}]) == 1.0)

print()
print("=" * 60)
print("Test 2: recall math")
print("=" * 60)
# 3 truth-PASS, 2 found → recall = 0.667
case_strict = [
    {"task_id": "t1", "critic_verdict": "pass", "disk_truth_pass": True},
    {"task_id": "t2", "critic_verdict": "pass", "disk_truth_pass": True},
    {"task_id": "t3", "critic_verdict": "fail", "disk_truth_pass": True},  # false-fail
    {"task_id": "t4", "critic_verdict": "fail", "disk_truth_pass": False},
]
_ok(f"3 truth-PASS, 2 found → 0.667 (got {critic_recall(case_strict):.3f})", abs(critic_recall(case_strict) - 0.667) < 0.01)
# all truth-PASS found → 1.0
_ok("all truth-PASS found → 1.0", critic_recall(case_perfect) == 1.0)
# no ground truth → 1.0 vacuous
_ok("no truth-PASS → 1.0 vacuous (blind-spot ceiling)",
    critic_recall([{"task_id":"t1","critic_verdict":"fail","disk_truth_pass":False}]) == 1.0)

print()
print("=" * 60)
print("Test 3: drift gate — too lenient (low precision)")
print("=" * 60)
# Lots of false-passes → precision drops
lenient = [
    {"task_id": f"t{i}", "critic_verdict": "pass", "disk_truth_pass": (i < 2)}
    for i in range(5)
]  # 2 of 5 PASS calls actually correct → precision 0.4
p = critic_precision(lenient); r = critic_recall(lenient)
_ok(f"lenient critic (precision={p:.2f}) → drift fires", critic_drift_fires(p, r) is True)

print()
print("=" * 60)
print("Test 4: drift gate — too strict (low recall)")
print("=" * 60)
# Many false-fails → recall drops
strict = [
    {"task_id": f"t{i}", "critic_verdict": ("fail" if i < 4 else "pass"), "disk_truth_pass": True}
    for i in range(5)
]  # 5 truth-PASS, 1 found → recall 0.2
p = critic_precision(strict); r = critic_recall(strict)
_ok(f"strict critic (recall={r:.2f}) → drift fires", critic_drift_fires(p, r) is True)

print()
print("=" * 60)
print("Test 5: drift gate — healthy critic (no fire)")
print("=" * 60)
healthy = [
    {"task_id": "t1", "critic_verdict": "pass", "disk_truth_pass": True},
    {"task_id": "t2", "critic_verdict": "pass", "disk_truth_pass": True},
    {"task_id": "t3", "critic_verdict": "pass", "disk_truth_pass": True},
    {"task_id": "t4", "critic_verdict": "pass", "disk_truth_pass": True},
    {"task_id": "t5", "critic_verdict": "fail", "disk_truth_pass": False},
]
p = critic_precision(healthy); r = critic_recall(healthy)
_ok(f"healthy critic (p={p:.2f} r={r:.2f}) → no drift", critic_drift_fires(p, r) is False)

print()
print("=" * 60)
print("Test 6: sanity bounds (150-1000c on tightened critic)")
print("=" * 60)
_ok("100c → reject (below floor)", critic_within_sanity("x"*100) is False)
_ok("200c → accept", critic_within_sanity("x"*200) is True)
_ok("900c → accept (within ceiling)", critic_within_sanity("x"*900) is True)
_ok("1100c → reject (above ceiling)", critic_within_sanity("x"*1100) is False)

print()
print("=" * 60)
print("Test 7: density anti-collapse (D1)")
print("=" * 60)
# Must keep disk/artifact AND pass/fail
good = "Sen QA agent'ısın. Disk artifact byte-match yaptı. Pass/fail kararı ver."
bad_no_disk = "Sen QA'sın. PASS veya FAIL döndür."
bad_no_decision = "Sen QA'sın. Disk artifact byte-match kontrolü yap."
collapsed = "QA agent. Karar ver."
_ok("good prompt → density ok", critic_density_ok(good) is True)
_ok("missing disk-truth → density fail", critic_density_ok(bad_no_disk) is False)
_ok("missing decision schema → density fail", critic_density_ok(bad_no_decision) is False)
_ok("fully collapsed → density fail", critic_density_ok(collapsed) is False)

print()
print("=" * 60)
print("Test 8: mixed-family enforcement (D2)")
print("=" * 60)
_ok("DeepSeek worker / Llama tightener → ok", mixed_family_ok("DeepSeek", "Llama") is True)
_ok("DeepSeek worker / DeepSeek tightener → reject", mixed_family_ok("DeepSeek", "DeepSeek") is False)
_ok("Llama worker / Gemini tightener → ok", mixed_family_ok("Llama", "Gemini") is True)
_ok("case-insensitive: deepseek / DEEPSEEK → reject",
    mixed_family_ok("deepseek", "DEEPSEEK") is False)
