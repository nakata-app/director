"""Quick verification of the 3 director.py fixes.

We don't import director directly (it has heavy imports). Instead we replicate
the verdict-block logic with the new behavior and check the math against
hand-computed expected values.
"""

# Edit 1 + 2: completion-weighted fidelity + no falsy-coalesce trap
def computed_fidelity(raw_fid_scores, done, total):
    fidelity_scores = [s for s in raw_fid_scores if s >= 0]
    raw_avg = sum(fidelity_scores) / len(fidelity_scores) if fidelity_scores else None
    done_ratio = done / max(1, total)
    return raw_avg * done_ratio if raw_avg is not None else None

def drift_check(a_done, a_total, b_done, b_total, a_avg_log, b_avg_log, a_fail, a_fidelity):
    a_done_ratio = a_done / max(1, a_total)
    b_done_ratio = b_done / max(1, b_total)
    completion_drop = b_done_ratio - a_done_ratio
    log_inflation = (a_avg_log - b_avg_log) / max(1, b_avg_log)
    avg_fid = a_fidelity if a_fidelity is not None else 10  # NO falsy coalesce
    absolute_completion_fail = a_done_ratio < 0.5 and a_total > 1
    return (
        (completion_drop > 0.15)
        or (avg_fid < 7)
        or (log_inflation > 0.30 and a_fail > 0)
        or absolute_completion_fail,
        {
            "completion_drop": completion_drop,
            "log_inflation": log_inflation,
            "avg_fid": avg_fid,
            "absolute_completion_fail": absolute_completion_fail,
        }
    )

cases = [
    # (label, raw_fid, done, total, expected_completion_weighted)
    ("baseline 5/5 done with 8/10 fid", [8,8,8,8], 5, 5, 8.0),
    ("synthetic 0/2 done with 10/10 fid (was the bug)", [10,10], 0, 2, 0.0),
    ("partial 2/5 done with 8/10 fid", [8,8,8,8,8], 2, 5, 3.2),
    ("no fidelity scores", [], 5, 5, None),
]
print("=" * 60)
print("Edit 1+2: completion-weighted fidelity")
print("=" * 60)
for label, raw, done, total, expected in cases:
    got = computed_fidelity(raw, done, total)
    ok = (got is None and expected is None) or (got is not None and abs(got - expected) < 0.01)
    print(f"  [{'OK' if ok else 'FAIL'}] {label}: expected={expected}, got={got}")

print()
print("=" * 60)
print("Edit 3: drift detection with new floor")
print("=" * 60)
drift_cases = [
    # (label, a_done, a_total, b_done, b_total, a_avg_log, b_avg_log, a_fail, a_fidelity, expected_drift, expected_reason)
    ("baseline equal arms", 4, 5, 4, 5, 2000, 2000, 0, 8.0, False, "no signal"),
    ("synthetic verbose A=0/2 B=2/2", 0, 2, 2, 2, 1953, 2294, 1, 0.0, True, "comp_drop + fid + abs_floor"),
    ("both arms broken A=0/5 B=0/5", 0, 5, 0, 5, 0, 0, 5, 0.0, True, "abs_floor + fid (NEW catch)"),
    ("partial A=2/5 B=4/5 with high fid", 2, 5, 4, 5, 2000, 2000, 3, 8.0, True, "comp_drop + abs_floor"),
    ("small A=1/2 B=2/2 abs floor inactive", 1, 2, 2, 2, 2000, 2000, 1, 8.0, True, "comp_drop"),
    ("legitimate fid=0 must trigger (was bugged)", 5, 5, 5, 5, 2000, 2000, 0, 0.0, True, "fid (was masked by `or 10`)"),
]
print(f"{'label':<55} {'drift?':<7} {'reason'}")
for label, ad, at, bd, bt, al, bl, af, afid, exp_drift, exp_reason in drift_cases:
    got_drift, signals = drift_check(ad, at, bd, bt, al, bl, af, afid)
    ok = got_drift == exp_drift
    mark = "OK" if ok else "FAIL"
    print(f"  [{mark}] {label:<50} drift={got_drift} (expected {exp_drift}) signals={signals}")
