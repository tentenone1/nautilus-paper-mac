"""Tests for whale_tiering module."""
import json
import sys
import os
import tempfile

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.whale_tiering import WhaleTiering

# Create temp config for tests
TEST_CONFIG = {
    "tiers": {
        "elite": {"alpha_min": 90, "alpha_max": 100, "kelly_multiplier": 1.0, "max_position_usd": 1000, "max_concurrent_positions": 5, "min_confidence": 0.25},
        "established": {"alpha_min": 70, "alpha_max": 89, "kelly_multiplier": 0.75, "max_position_usd": 500, "max_concurrent_positions": 8, "min_confidence": 0.30},
        "emerging": {"alpha_min": 50, "alpha_max": 69, "kelly_multiplier": 0.50, "max_position_usd": 250, "max_concurrent_positions": 10, "min_confidence": 0.40},
        "speculative": {"alpha_min": 0, "alpha_max": 49, "kelly_multiplier": 0.25, "max_position_usd": 100, "max_concurrent_positions": 5, "min_confidence": 0.50},
    },
    "defaults": {"kelly_multiplier": 0.5, "max_position_usd": 250, "max_concurrent_positions": 5, "min_confidence": 0.30},
    "overrides": {
        "tag_based": {
            "top_performer": {"tier_boost": 1},
            "high_efficiency": {"min_confidence_reduction": 0.05},
        }
    },
}

def make_tiering(config=None):
    """Create WhaleTiering instance with test config."""
    if config is None:
        config = TEST_CONFIG
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(config, tmp)
    tmp.close()
    t = WhaleTiering(tmp.name)
    t._config = config
    return t


def test_tier_boundaries():
    t = make_tiering()
    assert t.get_tier(95) == "elite", f"Expected elite, got {t.get_tier(95)}"
    assert t.get_tier(90) == "elite", f"Expected elite, got {t.get_tier(90)}"
    assert t.get_tier(80) == "established", f"Expected established, got {t.get_tier(80)}"
    assert t.get_tier(70) == "established", f"Expected established, got {t.get_tier(70)}"
    assert t.get_tier(60) == "emerging", f"Expected emerging, got {t.get_tier(60)}"
    assert t.get_tier(50) == "emerging", f"Expected emerging, got {t.get_tier(50)}"
    assert t.get_tier(30) == "speculative", f"Expected speculative, got {t.get_tier(30)}"
    assert t.get_tier(0) == "speculative", f"Expected speculative, got {t.get_tier(0)}"
    print("✅ test_tier_boundaries PASSED")


def test_tier_config():
    t = make_tiering()
    cfg = t.get_tier_config(95)
    assert cfg["kelly_multiplier"] == 1.0
    assert cfg["max_position_usd"] == 1000
    assert cfg["min_confidence"] == 0.25
    cfg = t.get_tier_config(30)
    assert cfg["kelly_multiplier"] == 0.25
    assert cfg["max_position_usd"] == 100
    print("✅ test_tier_config PASSED")


def test_confidence_validation():
    t = make_tiering()
    # Elite: min conf 0.25
    assert t.validate_confidence(0.30, 95) == True
    assert t.validate_confidence(0.20, 95) == False
    # Speculative: min conf 0.50
    assert t.validate_confidence(0.60, 30) == True
    assert t.validate_confidence(0.40, 30) == False
    print("✅ test_confidence_validation PASSED")


def test_tag_overrides():
    t = make_tiering()
    # High efficiency reduces min confidence by 0.05
    tags = ["high_efficiency"]
    cfg = t.get_tier_config(95)  # elite: min_conf 0.25
    overridden = t.apply_overrides(cfg, tags)
    assert overridden["min_confidence"] == 0.20, f"Expected 0.20, got {overridden['min_confidence']}"
    # Validate with tags
    assert t.validate_confidence(0.22, 95, tags) == True
    assert t.validate_confidence(0.22, 95) == False  # without tags, still fails
    print("✅ test_tag_overrides PASSED")


def test_kelly_sizing():
    t = make_tiering()
    # Elite: 1.0x multiplier
    assert t.kelly_sized_position(100, 95) == 100.0
    # Speculative: 0.25x multiplier
    assert t.kelly_sized_position(100, 30) == 25.0
    # Emerging: 0.50x
    assert t.kelly_sized_position(100, 55) == 50.0
    print("✅ test_kelly_sizing PASSED")


def test_fallback_on_missing_config():
    """WhaleTiering should handle missing or empty config gracefully."""
    # Test with missing file
    t = WhaleTiering("/nonexistent/path/config.json")
    assert t.get_tier(50) == "speculative"
    cfg = t.get_tier_config(50)
    assert cfg.get("kelly_multiplier", 0) > 0
    
    # Test with empty config (file exists but empty)
    empty_config = {"tiers": {}, "defaults": {"kelly_multiplier": 0.5, "max_position_usd": 250, "max_concurrent_positions": 5, "min_confidence": 0.30}, "overrides": {"tag_based": {}}}
    t2 = make_tiering(empty_config)
    assert t2.get_tier(50) == "speculative"
    cfg2 = t2.get_tier_config(50)
    assert cfg2.get("kelly_multiplier", 0) > 0
    print("✅ test_fallback_on_missing_config PASSED")


def test_alpha_out_of_range():
    t = make_tiering()
    assert t.get_tier(200) == "speculative"  # above max
    assert t.get_tier(-10) == "speculative"  # below min
    print("✅ test_alpha_out_of_range PASSED")


def test_get_all_tier_summary():
    t = make_tiering()
    summary = t.get_all_tier_summary()
    assert "elite" in summary
    assert "speculative" in summary
    assert len(summary) == 4
    print("✅ test_get_all_tier_summary PASSED")


if __name__ == "__main__":
    test_tier_boundaries()
    test_tier_config()
    test_confidence_validation()
    test_tag_overrides()
    test_kelly_sizing()
    test_fallback_on_missing_config()
    test_alpha_out_of_range()
    test_get_all_tier_summary()
    print("\n🎉 All tests passed!")
