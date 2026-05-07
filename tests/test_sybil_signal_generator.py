"""
Unit tests for sybil signal generator.

Tests:
1. NO-Bias Fade triggers at >$50K NO across ≥5 wallets
2. Concentrated Conviction Follow triggers at >$150K YES with ≥2 wallets, YES ratio >15%
3. Sentiment-Manipulation Fade triggers at ≥10 wallets avg <$400
4. Graceful handling of missing/empty input
5. Signal queue format matches whale_follower.py expectations
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from sybil_signal_generator import generate_signals, compute_no_size


class TestComputeNoSize(unittest.TestCase):
    def test_binary_yes_no_market(self):
        market = {"total_size_usd": 100_000, "yes_size_usd": 30_000}
        self.assertEqual(compute_no_size(market), 70_000)

    def test_all_yes(self):
        market = {"total_size_usd": 50_000, "yes_size_usd": 50_000}
        self.assertEqual(compute_no_size(market), 0)

    def test_all_no(self):
        market = {"total_size_usd": 80_000, "yes_size_usd": 0}
        self.assertEqual(compute_no_size(market), 80_000)

    def test_empty_market(self):
        self.assertEqual(compute_no_size({}), 0)


class TestNoBiasFade(unittest.TestCase):
    def test_triggers_on_large_no_exposure(self):
        data = {"groups": {"sybil_group_1": {"markets": [{
            "condition_id": "0xtest", "market_title": "Test", "market_slug": "test",
            "total_size_usd": 100_000, "yes_size_usd": 10_000,
            "wallets": [{"label": f"w{i}"} for i in range(6)],
        }]}}}
        signals = generate_signals(data)
        fade = [s for s in signals if s["signal_type"] == "no_bias_fade"]
        self.assertEqual(len(fade), 1)
        self.assertEqual(fade[0]["side"], "BUY YES")

    def test_does_not_trigger_below_threshold(self):
        data = {"groups": {"sybil_group_1": {"markets": [{
            "condition_id": "0xtest", "market_title": "Test", "market_slug": "test",
            "total_size_usd": 40_000, "yes_size_usd": 5_000,
            "wallets": [{"label": f"w{i}"} for i in range(6)],
        }]}}}
        signals = generate_signals(data)
        fade = [s for s in signals if s["signal_type"] == "no_bias_fade"]
        self.assertEqual(len(fade), 0)

    def test_does_not_trigger_below_wallet_count(self):
        data = {"groups": {"sybil_group_1": {"markets": [{
            "condition_id": "0xtest", "market_title": "Test", "market_slug": "test",
            "total_size_usd": 100_000, "yes_size_usd": 10_000,
            "wallets": [{"label": "w1"}, {"label": "w2"}, {"label": "w3"}],
        }]}}}
        signals = generate_signals(data)
        fade = [s for s in signals if s["signal_type"] == "no_bias_fade"]
        self.assertEqual(len(fade), 0)


class TestConcentratedFollow(unittest.TestCase):
    def test_triggers_on_large_yes_conviction(self):
        data = {"groups": {"sybil_group_1": {"markets": [{
            "condition_id": "0xtest", "market_title": "Test", "market_slug": "test",
            "total_size_usd": 200_000, "yes_size_usd": 180_000,
            "wallets": [{"label": "w1"}, {"label": "w2"}],
        }]}}}
        signals = generate_signals(data)
        follow = [s for s in signals if s["signal_type"] == "concentrated_follow"]
        self.assertEqual(len(follow), 1)
        self.assertEqual(follow[0]["side"], "BUY YES")

    def test_does_not_trigger_below_yes_threshold(self):
        data = {"groups": {"sybil_group_1": {"markets": [{
            "condition_id": "0xtest", "market_title": "Test", "market_slug": "test",
            "total_size_usd": 100_000, "yes_size_usd": 90_000,
            "wallets": [{"label": "w1"}, {"label": "w2"}],
        }]}}}
        signals = generate_signals(data)
        follow = [s for s in signals if s["signal_type"] == "concentrated_follow"]
        self.assertEqual(len(follow), 0)

    def test_does_not_trigger_below_yes_ratio(self):
        data = {"groups": {"sybil_group_1": {"markets": [{
            "condition_id": "0xtest", "market_title": "Test", "market_slug": "test",
            "total_size_usd": 200_000, "yes_size_usd": 20_000,
            "wallets": [{"label": "w1"}, {"label": "w2"}],
        }]}}}
        signals = generate_signals(data)
        follow = [s for s in signals if s["signal_type"] == "concentrated_follow"]
        self.assertEqual(len(follow), 0)


class TestManipulationFade(unittest.TestCase):
    def test_triggers_on_many_small_bets(self):
        data = {"groups": {"sybil_group_1": {"markets": [{
            "condition_id": "0xtest", "market_title": "Test", "market_slug": "test",
            "total_size_usd": 3_000, "yes_size_usd": 1_500,
            "wallets": [{"label": f"w{i}"} for i in range(10)],
        }]}}}
        signals = generate_signals(data)
        manip = [s for s in signals if s["signal_type"] == "manipulation_fade"]
        self.assertEqual(len(manip), 1)
        self.assertEqual(manip[0]["side"], "BUY NO")

    def test_does_not_trigger_above_avg_threshold(self):
        data = {"groups": {"sybil_group_1": {"markets": [{
            "condition_id": "0xtest", "market_title": "Test", "market_slug": "test",
            "total_size_usd": 5_000, "yes_size_usd": 2_500,
            "wallets": [{"label": f"w{i}"} for i in range(10)],
        }]}}}
        signals = generate_signals(data)
        manip = [s for s in signals if s["signal_type"] == "manipulation_fade"]
        self.assertEqual(len(manip), 0)

    def test_does_not_trigger_below_wallet_count(self):
        data = {"groups": {"sybil_group_1": {"markets": [{
            "condition_id": "0xtest", "market_title": "Test", "market_slug": "test",
            "total_size_usd": 3_000, "yes_size_usd": 1_500,
            "wallets": [{"label": f"w{i}"} for i in range(5)],
        }]}}}
        signals = generate_signals(data)
        manip = [s for s in signals if s["signal_type"] == "manipulation_fade"]
        self.assertEqual(len(manip), 0)


class TestEdgeCases(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(len(generate_signals({"groups": {}})), 0)

    def test_missing_groups_key(self):
        self.assertEqual(len(generate_signals({})), 0)

    def test_empty_markets_list(self):
        self.assertEqual(len(generate_signals({"groups": {"g1": {"markets": []}}})), 0)

    def test_signal_has_required_fields(self):
        data = {"groups": {"sybil_group_1": {"markets": [{
            "condition_id": "0xtest", "market_title": "Test", "market_slug": "test",
            "total_size_usd": 200_000, "yes_size_usd": 180_000,
            "wallets": [{"label": "w1"}, {"label": "w2"}],
        }]}}}
        signals = generate_signals(data)
        self.assertGreater(len(signals), 0)
        for s in signals:
            self.assertIn("signal_type", s)
            self.assertIn("group_id", s)
            self.assertIn("market_title", s)
            self.assertIn("condition_id", s)
            self.assertIn("side", s)
            self.assertIn("confidence", s)
            self.assertIn("reason", s)
            self.assertIn("total_exposure_usd", s)
            self.assertIn("wallet_count", s)
            self.assertIn("generated_at", s)


if __name__ == "__main__":
    unittest.main()
