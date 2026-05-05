"""Tests for whale_tiering module — dual-axis classification."""
import json
import sys
import os
import tempfile

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.whale_tiering import WhaleTiering

# Dual-axis config matching production whale_tiers.json
DUAL_CONFIG = {
    "capital_tiers": {
        "A": {"min_volume": 200000, "max_volume": 999999999, "label": "Whale ($200K+)", "kelly_multiplier": 0.8},
        "B": {"min_volume": 50000, "max_volume": 199999, "label": "Dolphin ($50K+)", "kelly_multiplier": 1.0},
        "C": {"min_volume": 10000, "max_volume": 49999, "label": "Shark ($10K+)", "kelly_multiplier": 1.0},
        "D": {"min_volume": 1000, "max_volume": 9999, "label": "Fish ($1K+)", "kelly_multiplier": 0.75},
        "E": {"min_volume": 0, "max_volume": 999, "label": "Minnow (<$1K)", "kelly_multiplier": 0.5},
    },
    "precision_tiers": {
        "HIGH": {"min_wr": 0.65, "label": "High Precision"},
        "MEDIUM": {"min_wr": 0.45, "label": "Medium Precision"},
        "LOW": {"min_wr": 0, "label": "Low Precision"},
    },
    "tier_matrix": {
        "A+HIGH": {"kelly_multiplier": 1.0, "max_position_usd": 2000, "max_concurrent": 8, "min_confidence": 0.2},
        "A+MEDIUM": {"kelly_multiplier": 0.6, "max_position_usd": 1000, "max_concurrent": 5, "min_confidence": 0.3},
        "A+LOW": {"kelly_multiplier": 0.3, "max_position_usd": 500, "max_concurrent": 3, "min_confidence": 0.4},
        "B+HIGH": {"kelly_multiplier": 1.0, "max_position_usd": 1500, "max_concurrent": 8, "min_confidence": 0.2},
        "B+MEDIUM": {"kelly_multiplier": 0.75, "max_position_usd": 750, "max_concurrent": 6, "min_confidence": 0.3},
        "B+LOW": {"kelly_multiplier": 0.4, "max_position_usd": 400, "max_concurrent": 3, "min_confidence": 0.4},
        "C+HIGH": {"kelly_multiplier": 1.0, "max_position_usd": 1000, "max_concurrent": 6, "min_confidence": 0.25},
        "C+MEDIUM": {"kelly_multiplier": 0.75, "max_position_usd": 500, "max_concurrent": 5, "min_confidence": 0.35},
        "C+LOW": {"kelly_multiplier": 0.5, "max_position_usd": 250, "max_concurrent": 3, "min_confidence": 0.45},
        "D+HIGH": {"kelly_multiplier": 1.0, "max_position_usd": 500, "max_concurrent": 5, "min_confidence": 0.3},
        "D+MEDIUM": {"kelly_multiplier": 0.75, "max_position_usd": 300, "max_concurrent": 4, "min_confidence": 0.4},
        "D+LOW": {"kelly_multiplier": 0.5, "max_position_usd": 150, "max_concurrent": 2, "min_confidence": 0.5},
        "E+HIGH": {"kelly_multiplier": 0.8, "max_position_usd": 250, "max_concurrent": 4, "min_confidence": 0.35},
        "E+MEDIUM": {"kelly_multiplier": 0.6, "max_position_usd": 150, "max_concurrent": 3, "min_confidence": 0.45},
        "E+LOW": {"kelly_multiplier": 0.3, "max_position_usd": 75, "max_concurrent": 2, "min_confidence": 0.55},
    },
    "backwards_compat": {
        "description": "Dual-axis system replaces old single-axis tiers.",
        "tiers": {
            "elite": {"alpha_min": 90, "alpha_max": 100, "kelly_multiplier": 0.5, "max_position_usd": 1000, "max_concurrent_positions": 5, "min_confidence": 0.25, "min_edge_score": 0.4},
            "established": {"alpha_min": 70, "alpha_max": 89, "kelly_multiplier": 0.375, "max_position_usd": 500, "max_concurrent_positions": 8, "min_confidence": 0.3, "min_edge_score": 0.3},
            "emerging": {"alpha_min": 50, "alpha_max": 69, "kelly_multiplier": 0.25, "max_position_usd": 250, "max_concurrent_positions": 10, "min_confidence": 0.4, "min_edge_score": 0.2},
            "speculative": {"alpha_min": 0, "alpha_max": 49, "kelly_multiplier": 0.125, "max_position_usd": 100, "max_concurrent_positions": 5, "min_confidence": 0.5, "min_edge_score": 0.1},
        },
        "defaults": {"kelly_multiplier": 0.25, "max_position_usd": 250, "max_concurrent_positions": 5, "min_confidence": 0.3, "min_edge_score": 0.15},
        "overrides": {"tag_based": {}},
        "edge_kelly_mapping": {
            "ranges": [
                {"min": 0.0, "max": 0.3, "kelly_fraction": 0.015},
                {"min": 0.3, "max": 0.4, "kelly_fraction": 0.04},
                {"min": 0.4, "max": 0.5, "kelly_fraction": 0.09},
                {"min": 0.5, "max": 0.6, "kelly_fraction": 0.11},
                {"min": 0.6, "max": 0.7, "kelly_fraction": 0.10},
                {"min": 0.7, "max": 0.8, "kelly_fraction": 0.125},
                {"min": 0.8, "max": 0.9, "kelly_fraction": 0.15},
                {"min": 0.9, "max": 1.01, "kelly_fraction": 0.10},
            ],
            "default_kelly_fraction": 0.05,
        },
        "kelly_sanity_checks": {
            "max_position_pct": 0.125, "min_position_pct": 0.005, "enabled": True,
        },
    },
}


def make_tiering(config=None):
    """Create WhaleTiering instance with test config."""
    if config is None:
        config = DUAL_CONFIG
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(config, tmp)
    tmp.close()
    t = WhaleTiering(tmp.name)
    t._config = config
    return t


# ═══════════════════════════════════════════════════════════════════
# Dual-Axis Capital Tier Tests
# ═══════════════════════════════════════════════════════════════════

def test_capital_tier_a():
    """Volume >= $200K → Tier A."""
    t = make_tiering()
    assert t.classify_capital(200000) == "A", f"Expected A, got {t.classify_capital(200000)}"
    assert t.classify_capital(500000) == "A", f"Expected A, got {t.classify_capital(500000)}"
    assert t.classify_capital(999999999) == "A", f"Expected A, got {t.classify_capital(999999999)}"
    print("✅ test_capital_tier_a PASSED")


def test_capital_tier_b():
    """$50K <= volume < $200K → Tier B."""
    t = make_tiering()
    assert t.classify_capital(50000) == "B", f"Expected B, got {t.classify_capital(50000)}"
    assert t.classify_capital(100000) == "B", f"Expected B, got {t.classify_capital(100000)}"
    assert t.classify_capital(199999) == "B", f"Expected B, got {t.classify_capital(199999)}"
    print("✅ test_capital_tier_b PASSED")


def test_capital_tier_c():
    """$10K <= volume < $50K → Tier C."""
    t = make_tiering()
    assert t.classify_capital(10000) == "C", f"Expected C, got {t.classify_capital(10000)}"
    assert t.classify_capital(25000) == "C", f"Expected C, got {t.classify_capital(25000)}"
    assert t.classify_capital(49999) == "C", f"Expected C, got {t.classify_capital(49999)}"
    print("✅ test_capital_tier_c PASSED")


def test_capital_tier_d():
    """$1K <= volume < $10K → Tier D."""
    t = make_tiering()
    assert t.classify_capital(1000) == "D", f"Expected D, got {t.classify_capital(1000)}"
    assert t.classify_capital(5000) == "D", f"Expected D, got {t.classify_capital(5000)}"
    assert t.classify_capital(9999) == "D", f"Expected D, got {t.classify_capital(9999)}"
    print("✅ test_capital_tier_d PASSED")


def test_capital_tier_e():
    """Volume < $1K → Tier E."""
    t = make_tiering()
    assert t.classify_capital(0) == "E", f"Expected E, got {t.classify_capital(0)}"
    assert t.classify_capital(500) == "E", f"Expected E, got {t.classify_capital(500)}"
    assert t.classify_capital(999) == "E", f"Expected E, got {t.classify_capital(999)}"
    print("✅ test_capital_tier_e PASSED")


# ═══════════════════════════════════════════════════════════════════
# Dual-Axis Precision Tier Tests
# ═══════════════════════════════════════════════════════════════════

def test_precision_high():
    """Win rate >= 0.65 → HIGH precision."""
    t = make_tiering()
    assert t.classify_precision(0.65) == "HIGH"
    assert t.classify_precision(0.80) == "HIGH"
    assert t.classify_precision(1.00) == "HIGH"
    print("✅ test_precision_high PASSED")


def test_precision_medium():
    """0.45 <= win rate < 0.65 → MEDIUM precision."""
    t = make_tiering()
    assert t.classify_precision(0.45) == "MEDIUM"
    assert t.classify_precision(0.55) == "MEDIUM"
    assert t.classify_precision(0.64) == "MEDIUM"
    print("✅ test_precision_medium PASSED")


def test_precision_low():
    """Win rate < 0.45 → LOW precision."""
    t = make_tiering()
    assert t.classify_precision(0.00) == "LOW"
    assert t.classify_precision(0.30) == "LOW"
    assert t.classify_precision(0.44) == "LOW"
    print("✅ test_precision_low PASSED")


# ═══════════════════════════════════════════════════════════════════
# Combined Tier Tests
# ═══════════════════════════════════════════════════════════════════

def test_dual_tier_key():
    """get_dual_tier produces correct combined key."""
    t = make_tiering()
    assert t.get_dual_tier("A", "HIGH") == "A+HIGH"
    assert t.get_dual_tier("B", "MEDIUM") == "B+MEDIUM"
    assert t.get_dual_tier("E", "LOW") == "E+LOW"
    print("✅ test_dual_tier_key PASSED")


def test_dual_tier_config_lookup():
    """get_dual_tier_config returns correct matrix values."""
    t = make_tiering()
    # A+HIGH: the best tier
    cfg = t.get_dual_tier_config("A", "HIGH")
    assert cfg["kelly_multiplier"] == 1.0
    assert cfg["max_position_usd"] == 2000
    assert cfg["max_concurrent"] == 8
    assert cfg["min_confidence"] == 0.2

    # E+LOW: the worst tier
    cfg = t.get_dual_tier_config("E", "LOW")
    assert cfg["kelly_multiplier"] == 0.3
    assert cfg["max_position_usd"] == 75
    assert cfg["max_concurrent"] == 2
    assert cfg["min_confidence"] == 0.55

    print("✅ test_dual_tier_config_lookup PASSED")


def test_dual_tier_config_fallback():
    """Unknown combination falls back to defaults."""
    t = make_tiering()
    cfg = t.get_dual_tier_config("A", "ULTRA")  # precision_tier doesn't exist
    assert cfg["kelly_multiplier"] == 0.25
    assert cfg["max_position_usd"] == 100
    assert cfg["min_confidence"] == 0.5
    print("✅ test_dual_tier_config_fallback PASSED")


# ═══════════════════════════════════════════════════════════════════
# Caching Tests
# ═══════════════════════════════════════════════════════════════════

def test_cache_whale():
    """cache_whale stores classification and get_cached_tier retrieves config."""
    t = make_tiering()
    t.cache_whale("big_whale", total_volume_usd=300000, win_rate=0.80)
    cfg = t.get_cached_tier("big_whale")
    assert cfg["kelly_multiplier"] == 1.0  # A+HIGH
    assert cfg["max_position_usd"] == 2000
    print("✅ test_cache_whale PASSED")


def test_cache_whale_small():
    """Small whale with low win rate gets conservative config."""
    t = make_tiering()
    t.cache_whale("small_fry", total_volume_usd=500, win_rate=0.30)
    cfg = t.get_cached_tier("small_fry")
    assert cfg["kelly_multiplier"] == 0.3  # E+LOW
    assert cfg["max_position_usd"] == 75
    print("✅ test_cache_whale_small PASSED")


def test_cache_miss():
    """Uncached whale returns fallback defaults."""
    t = make_tiering()
    cfg = t.get_cached_tier("unknown_whale")
    assert cfg["kelly_multiplier"] == 0.25
    assert cfg["max_position_usd"] == 100
    print("✅ test_cache_miss PASSED")


# ═══════════════════════════════════════════════════════════════════
# Kelly Sizing Tests
# ═══════════════════════════════════════════════════════════════════

def test_dual_kelly_sizing():
    """dual_kelly_sized_position uses cached tier multiplier."""
    t = make_tiering()
    t.cache_whale("elite_whale", total_volume_usd=300000, win_rate=0.80)
    sized = t.dual_kelly_sized_position(1000, "elite_whale")
    assert sized == 1000.0  # A+HIGH = 1.0 multiplier

    t.cache_whale("weak_whale", total_volume_usd=500, win_rate=0.30)
    sized = t.dual_kelly_sized_position(1000, "weak_whale")
    assert sized == 300.0  # E+LOW = 0.3 multiplier
    print("✅ test_dual_kelly_sizing PASSED")


def test_dual_sizing_limits():
    """get_dual_sizing_limits returns correct caps."""
    t = make_tiering()
    t.cache_whale("top", total_volume_usd=300000, win_rate=0.80)
    limits = t.get_dual_sizing_limits("top")
    assert limits["max_position_usd"] == 2000
    assert limits["max_concurrent"] == 8
    assert limits["min_confidence"] == 0.2

    t.cache_whale("bottom", total_volume_usd=500, win_rate=0.30)
    limits = t.get_dual_sizing_limits("bottom")
    assert limits["max_position_usd"] == 75
    assert limits["max_concurrent"] == 2
    assert limits["min_confidence"] == 0.55
    print("✅ test_dual_sizing_limits PASSED")


# ═══════════════════════════════════════════════════════════════════
# Tier Summary Tests
# ═══════════════════════════════════════════════════════════════════

def test_get_all_tier_summary():
    """get_all_tier_summary returns complete matrix."""
    t = make_tiering()
    summary = t.get_all_tier_summary()
    assert "capital_tiers" in summary
    assert "precision_tiers" in summary
    assert "tier_matrix" in summary
    assert "A" in summary["capital_tiers"]
    assert "HIGH" in summary["precision_tiers"]
    assert "A+HIGH" in summary["tier_matrix"]
    assert len(summary["tier_matrix"]) == 15  # 5 × 3 combination
    print("✅ test_get_all_tier_summary PASSED")


# ═══════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════

def test_negative_volume():
    """Negative volume should return tier E (lowest)."""
    t = make_tiering()
    assert t.classify_capital(-100) == "E"
    print("✅ test_negative_volume PASSED")


def test_huge_volume():
    """Extremely large volume stays in tier A."""
    t = make_tiering()
    assert t.classify_capital(999_999_999) == "A"
    assert t.classify_capital(1_000_000_000) == "E"  # Falls outside config range → E
    print("✅ test_huge_volume PASSED")


def test_zero_win_rate():
    """Zero win rate returns LOW precision."""
    t = make_tiering()
    assert t.classify_precision(0.0) == "LOW"
    print("✅ test_zero_win_rate PASSED")


def test_perfect_win_rate():
    """Perfect win rate returns HIGH precision."""
    t = make_tiering()
    assert t.classify_precision(1.0) == "HIGH"
    print("✅ test_perfect_win_rate PASSED")


if __name__ == "__main__":
    # Dual-axis capital tier tests
    test_capital_tier_a()
    test_capital_tier_b()
    test_capital_tier_c()
    test_capital_tier_d()
    test_capital_tier_e()

    # Dual-axis precision tier tests
    test_precision_high()
    test_precision_medium()
    test_precision_low()

    # Combined tier tests
    test_dual_tier_key()
    test_dual_tier_config_lookup()
    test_dual_tier_config_fallback()

    # Caching tests
    test_cache_whale()
    test_cache_whale_small()
    test_cache_miss()

    # Kelly sizing tests
    test_dual_kelly_sizing()
    test_dual_sizing_limits()

    # Tier summary tests
    test_get_all_tier_summary()

    # Edge cases
    test_negative_volume()
    test_huge_volume()
    test_zero_win_rate()
    test_perfect_win_rate()

    print("\n🎉 All dual-axis classification tests passed!")
