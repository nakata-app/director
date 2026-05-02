"""M3-T04: per-domain drift threshold tuning tests.

Verifies that domain_drift_thresholds() correctly returns per-domain overrides
when defined, defaults otherwise, and survives malformed config files without
crashing.

Run: python3 tests/test_domain_drift_config.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import director


def _ok(label, cond):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")
    return cond


# Defaults must literally equal the global constants from M2.
DEFAULTS = {
    "decomposer_score_floor": 7.0,
    "decomposer_literal_floor": 0.7,
    "critic_precision_floor": 0.8,
    "critic_recall_floor": 0.8,
    "persona_completion_drop_threshold": 0.15,
    "persona_log_inflation_threshold": 0.30,
    "persona_fidelity_floor": 7.0,
}


# ----- Test 1: default config returns global constants -----
print("=" * 60)
print("Test 1: unset domain → defaults match global constants")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    saved = director.DOMAIN_DRIFT_CONFIG_PATH
    director.DOMAIN_DRIFT_CONFIG_PATH = Path(td) / "no_such_file.json"
    try:
        thresholds = director.domain_drift_thresholds("nonexistent")
        for k, v in DEFAULTS.items():
            _ok(f"{k} = {v} (got {thresholds.get(k)})", thresholds.get(k) == v)
    finally:
        director.DOMAIN_DRIFT_CONFIG_PATH = saved


# ----- Test 2: per-domain override is read correctly -----
print()
print("=" * 60)
print("Test 2: per-domain override returned over default")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    cfg = {
        "security": {
            "_doc": "security tolerates verbose output, lower thresholds",
            "decomposer_score_floor": 6.0,
            "critic_precision_floor": 0.7,
        },
        "refactor": {
            "_doc": "refactor needs strict discipline, raise thresholds",
            "decomposer_score_floor": 8.0,
            "persona_completion_drop_threshold": 0.10,
        },
    }
    cfg_path = Path(td) / "domain_drift_config.json"
    cfg_path.write_text(json.dumps(cfg))
    saved = director.DOMAIN_DRIFT_CONFIG_PATH
    director.DOMAIN_DRIFT_CONFIG_PATH = cfg_path
    try:
        sec = director.domain_drift_thresholds("security")
        _ok(f"security score_floor overridden 7.0→6.0 (got {sec['decomposer_score_floor']})",
            sec["decomposer_score_floor"] == 6.0)
        _ok(f"security precision_floor overridden 0.8→0.7 (got {sec['critic_precision_floor']})",
            sec["critic_precision_floor"] == 0.7)
        _ok(f"security recall_floor unchanged at 0.8 (got {sec['critic_recall_floor']})",
            sec["critic_recall_floor"] == 0.8)
        ref = director.domain_drift_thresholds("refactor")
        _ok(f"refactor score_floor overridden 7.0→8.0 (got {ref['decomposer_score_floor']})",
            ref["decomposer_score_floor"] == 8.0)
        _ok(f"refactor completion_drop overridden 0.15→0.10 (got {ref['persona_completion_drop_threshold']})",
            ref["persona_completion_drop_threshold"] == 0.10)
    finally:
        director.DOMAIN_DRIFT_CONFIG_PATH = saved


# ----- Test 3: missing domain key falls back to default -----
print()
print("=" * 60)
print("Test 3: missing domain key → defaults")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    cfg = {"security": {"decomposer_score_floor": 6.0}}
    cfg_path = Path(td) / "domain_drift_config.json"
    cfg_path.write_text(json.dumps(cfg))
    saved = director.DOMAIN_DRIFT_CONFIG_PATH
    director.DOMAIN_DRIFT_CONFIG_PATH = cfg_path
    try:
        # design is not in the config — should get all defaults
        des = director.domain_drift_thresholds("design")
        for k, v in DEFAULTS.items():
            _ok(f"design {k} = default {v} (got {des.get(k)})", des.get(k) == v)
    finally:
        director.DOMAIN_DRIFT_CONFIG_PATH = saved


# ----- Test 4: malformed config file falls back to defaults with warning -----
print()
print("=" * 60)
print("Test 4: malformed JSON → defaults, no crash")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    cfg_path = Path(td) / "broken.json"
    cfg_path.write_text("{not valid json")
    saved = director.DOMAIN_DRIFT_CONFIG_PATH
    director.DOMAIN_DRIFT_CONFIG_PATH = cfg_path
    try:
        result = director.domain_drift_thresholds("security")
        _ok("malformed → returns defaults (no exception)",
            result.get("decomposer_score_floor") == 7.0)
        _ok("malformed → all default keys present",
            all(k in result for k in DEFAULTS))
    finally:
        director.DOMAIN_DRIFT_CONFIG_PATH = saved


# ----- Test 5: drift gates honor domain-specific thresholds -----
print()
print("=" * 60)
print("Test 5: drift gate uses domain-specific threshold when given")
print("=" * 60)
with tempfile.TemporaryDirectory() as td:
    cfg = {
        "permissive_dom": {
            "decomposer_score_floor": 5.0,  # very permissive
            "decomposer_literal_floor": 0.3,
        },
        "strict_dom": {
            "decomposer_score_floor": 9.0,  # very strict
            "decomposer_literal_floor": 0.95,
        },
    }
    cfg_path = Path(td) / "domain_drift_config.json"
    cfg_path.write_text(json.dumps(cfg))
    saved = director.DOMAIN_DRIFT_CONFIG_PATH
    director.DOMAIN_DRIFT_CONFIG_PATH = cfg_path
    try:
        # Score 6.0 / literal 0.5 — fires under default (7.0/0.7),
        # suppressed under permissive_dom (5.0/0.3), fires under strict_dom (9.0/0.95).
        _ok("default gate fires (6.0<7.0)",
            director.decomposer_drift_fires(6.0, 0.5, domain="default") is True)
        _ok("permissive_dom gate suppresses (6.0>5.0 and 0.5>0.3)",
            director.decomposer_drift_fires(6.0, 0.5, domain="permissive_dom") is False)
        _ok("strict_dom gate fires (6.0<9.0)",
            director.decomposer_drift_fires(6.0, 0.5, domain="strict_dom") is True)
        # Critic side
        _ok("default critic gate fires (0.7<0.8)",
            director.critic_drift_fires(0.7, 0.9, domain="default") is True)
    finally:
        director.DOMAIN_DRIFT_CONFIG_PATH = saved


# ----- Test 6: tighten_persona_if_drift gate honors per-domain thresholds -----
print()
print("=" * 60)
print("Test 6: persona drift wrapper uses per-domain thresholds (no LLM)")
print("=" * 60)
# Suppress LLM tightener so the wrapper short-circuits cleanly: target_pid
# is found, but auto_tighten_persona returns None, so applied=False, no API call.
saved_tightener = director.auto_tighten_persona
director.auto_tighten_persona = lambda pid, log: None
try:
    with tempfile.TemporaryDirectory() as td:
        cfg = {
            "loose": {
                "persona_completion_drop_threshold": 0.50,
                "persona_log_inflation_threshold": 0.99,
                "persona_fidelity_floor": 1.0,
            },
            "tight": {
                "persona_completion_drop_threshold": 0.05,
                "persona_log_inflation_threshold": 0.05,
                "persona_fidelity_floor": 9.5,
            },
        }
        cfg_path = Path(td) / "domain_drift_config.json"
        cfg_path.write_text(json.dumps(cfg))
        saved = director.DOMAIN_DRIFT_CONFIG_PATH
        director.DOMAIN_DRIFT_CONFIG_PATH = cfg_path
        try:
            # Borderline signals: comp_drop 0.20 (>0.15 default, <0.50 loose, >0.05 tight),
            # log_infl 0.40 (>0.30 default, <0.99 loose, >0.05 tight),
            # avg_fid 8.0 (≥7.0 default → no fid trigger; ≥1.0 loose; <9.5 tight → fires).
            sig = {
                "completion_drop": 0.20,
                "log_inflation": 0.40,
                "avg_fidelity": 8.0,
                "absolute_completion_fail": False,
                "a_fail_count": 1,
            }
            a_state = {
                "tasks": [
                    {"id": "t1", "persona_id": "writer", "status": "failed",
                     "persona_fidelity": {"score": 6}},
                    {"id": "t2", "persona_id": "writer", "status": "done",
                     "persona_fidelity": {"score": 7}},
                ]
            }
            res_default = director.tighten_persona_if_drift(sig, a_state, dry_run=True, domain="default")
            _ok("default fires (comp_drop 0.20>0.15, log_infl 0.40>0.30)",
                res_default["fired"] is True)
            _ok("default thresholds in audit", res_default["thresholds"]["persona_completion_drop_threshold"] == 0.15)
            _ok("default target=writer", res_default["target_persona"] == "writer")

            res_loose = director.tighten_persona_if_drift(sig, a_state, dry_run=True, domain="loose")
            _ok("loose suppresses (all signals under loose floors)",
                res_loose["fired"] is False)
            _ok("loose thresholds in audit", res_loose["thresholds"]["persona_completion_drop_threshold"] == 0.50)

            res_tight = director.tighten_persona_if_drift(sig, a_state, dry_run=True, domain="tight")
            _ok("tight fires (fid 8.0<9.5 plus comp_drop and log_infl over)",
                res_tight["fired"] is True)
            _ok("tight reasons include fid", any("fid=" in r for r in res_tight["reasons"]))
            _ok("tight reasons include comp_drop", any("comp_drop=" in r for r in res_tight["reasons"]))
        finally:
            director.DOMAIN_DRIFT_CONFIG_PATH = saved
finally:
    director.auto_tighten_persona = saved_tightener


# ----- Test 7: wrapper return shape carries domain + thresholds (audit) -----
print()
print("=" * 60)
print("Test 7: tighten_*_if_drift wrappers return domain + thresholds (audit)")
print("=" * 60)
# Suppress real scoring + tightener calls (no API, no I/O).
saved_score_critic = director.score_critic_quality
saved_score_decomp = director.score_decomposer_fidelity
saved_auto_critic = director.auto_tighten_critic
saved_auto_decomp = director.auto_tighten_decomposer
director.score_critic_quality = lambda decisions: {
    "precision": 1.0, "recall": 1.0, "n_decisions": 0,
    "n_pass_calls": 0, "n_truth_pass": 0,
}
director.score_decomposer_fidelity = lambda goal, plan, baseline_avg=4.0: {
    "score": 10.0, "literal": 1.0, "expected": 1.0, "deviation": 0.0,
}
director.auto_tighten_critic = lambda d: None
director.auto_tighten_decomposer = lambda g, p: None
try:
    with tempfile.TemporaryDirectory() as td:
        cfg = {"audit_dom": {"_doc": "audit shape test", "decomposer_score_floor": 6.0}}
        cfg_path = Path(td) / "domain_drift_config.json"
        cfg_path.write_text(json.dumps(cfg))
        saved = director.DOMAIN_DRIFT_CONFIG_PATH
        director.DOMAIN_DRIFT_CONFIG_PATH = cfg_path
        try:
            r_crit = director.tighten_critic_if_drift([], dry_run=True, domain="audit_dom")
            _ok("critic wrapper carries domain", r_crit["domain"] == "audit_dom")
            _ok("critic wrapper carries thresholds dict",
                isinstance(r_crit.get("thresholds"), dict)
                and "critic_precision_floor" in r_crit["thresholds"])

            r_dec = director.tighten_decomposer_if_drift("g", {"tasks": []}, dry_run=True, domain="audit_dom")
            _ok("decomposer wrapper carries domain", r_dec["domain"] == "audit_dom")
            _ok("decomposer wrapper threshold respects override",
                r_dec["thresholds"]["decomposer_score_floor"] == 6.0)
        finally:
            director.DOMAIN_DRIFT_CONFIG_PATH = saved
finally:
    director.score_critic_quality = saved_score_critic
    director.score_decomposer_fidelity = saved_score_decomp
    director.auto_tighten_critic = saved_auto_critic
    director.auto_tighten_decomposer = saved_auto_decomp


# ----- Test 8: persona drift wrapper, no impacted persona → no target -----
print()
print("=" * 60)
print("Test 8: persona drift fires but no eligible persona on A-arm")
print("=" * 60)
saved_tightener = director.auto_tighten_persona
director.auto_tighten_persona = lambda pid, log: None
try:
    sig = {
        "completion_drop": 0.99, "log_inflation": 0.99,
        "avg_fidelity": 0.0, "absolute_completion_fail": True,
        "a_fail_count": 5,
    }
    # All tasks pinned to default persona → impacted dict empty.
    a_state = {"tasks": [
        {"id": "t1", "persona_id": "default", "status": "failed"},
        {"id": "t2", "persona_id": None, "status": "failed"},
    ]}
    res = director.tighten_persona_if_drift(sig, a_state, dry_run=True, domain="default")
    _ok("gate fires", res["fired"] is True)
    _ok("target_persona None when no eligible", res["target_persona"] is None)
    _ok("applied False", res["applied"] is False)
    _ok("reasons populated", len(res["reasons"]) >= 3)
finally:
    director.auto_tighten_persona = saved_tightener
