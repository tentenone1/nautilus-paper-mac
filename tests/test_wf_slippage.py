"""
Unit tests for strategies.wf_slippage — slippage and spread modeling.
"""
import sys
sys.path.insert(0, ".")

import pytest
from strategies.wf_slippage import (
    compute_slippage,
    compute_spread_adjustment,
    compute_fill_price,
)


class TestComputeSlippage:
    def test_no_slippage_below_threshold(self):
        """Orders below size threshold get no slippage"""
        result = compute_slippage(
            side="BUY",
            price=0.5,
            order_size_usd=10.0,  # tiny relative to volume
            visible_volume_usd=100000.0,  # huge volume
            model="bounded_adverse",
            size_threshold_bps=100,
            max_slippage_bps=20,
        )
        assert result == 0.5

    def test_adverse_slippage_for_large_buy(self):
        """Large BUY order gets adverse slippage (higher price)"""
        result = compute_slippage(
            side="BUY",
            price=0.5,
            order_size_usd=1000.0,
            visible_volume_usd=5000.0,  # 20% of volume
            model="bounded_adverse",
            size_threshold_bps=100,
            max_slippage_bps=20,
        )
        assert result > 0.5  # must be higher (adverse)

    def test_adverse_slippage_for_large_sell(self):
        """Large SELL order gets adverse slippage (lower price)"""
        result = compute_slippage(
            side="SELL",
            price=0.5,
            order_size_usd=1000.0,
            visible_volume_usd=5000.0,
            model="bounded_adverse",
            size_threshold_bps=100,
            max_slippage_bps=20,
        )
        assert result < 0.5  # must be lower (adverse)

    def test_fixed_bps_slippage(self):
        """Fixed bps model applies constant slippage"""
        result = compute_slippage(
            side="BUY",
            price=0.5,
            order_size_usd=100.0,
            visible_volume_usd=100.0,
            model="fixed_bps",
        )
        slip = 0.5 * 5 / 10000  # 5 bps
        expected = 0.5 + slip
        assert abs(result - expected) < 0.0001

    def test_no_slippage_disabled(self):
        """When model=none, no slippage applied"""
        result = compute_slippage(
            side="BUY",
            price=0.5,
            order_size_usd=1000000.0,
            visible_volume_usd=1.0,
            model="none",
        )
        assert result == 0.5


class TestComputeSpreadAdjustment:
    def test_buy_hits_ask_higher(self):
        """BUY should be priced above mid (hits ask)"""
        result = compute_spread_adjustment("BUY", 0.5)
        assert result > 0.5

    def test_sell_hits_bid_lower(self):
        """SELL should be priced below mid (hits bid)"""
        result = compute_spread_adjustment("SELL", 0.5)
        assert result < 0.5


class TestComputeFillPrice:
    def test_fill_price_in_binary_range(self):
        """Fill price must stay in [0.001, 0.999]"""
        result = compute_fill_price(
            side="BUY",
            mid_price=0.001,
            order_size_usd=10000.0,
            visible_volume_usd=100.0,
        )
        assert 0.001 <= result <= 0.999

    def test_zero_volume_uses_fallback(self):
        """Zero visible volume should not crash"""
        result = compute_fill_price(
            side="BUY",
            mid_price=0.5,
            order_size_usd=100.0,
            visible_volume_usd=0.0,
        )
        assert 0.0 < result < 1.0
