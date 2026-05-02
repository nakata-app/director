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
