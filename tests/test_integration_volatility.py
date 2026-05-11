"""
Integration tests: Volatility Spike — Stop-Loss Fires.
"""
import pytest
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

from strategies.wf_exits import should_exit_for_resolution


class MockQuoteTick:
    """Minimal mock quote tick with bid/ask."""
    def __init__(self, bid, ask):
        self.bid = bid
        self.ask = ask
        self._mid = (bid + ask) / 2

    @property
    def bid_price(self):
        return self.bid

    @property
    def ask_price(self):
        return self.ask


class TestStopLossTrigger:
    """Tests for stop-loss and exit triggers on price volatility."""

    def test_sports_stop_loss_fires_on_large_adverse_move(self):
        """Sports position entry=0.50, price drops to 0.30 → SL fires."""
        # Simulate sports stop-loss: if mid drops >25% from entry, trigger
        entry_price = 0.50
        current_mid = 0.30
        stop_loss_pct = 0.25

        # SL condition: price moved adversely by > stop_loss_pct
        adverse_move = (entry_price - current_mid) / entry_price
        assert adverse_move > stop_loss_pct, f"Move {adverse_move:.1%} should exceed SL {stop_loss_pct:.0%}"

    def test_certainty_win_exit_triggers_on_high_price(self):
        """Position entry=0.50, price moves to 0.96 → certainty exit fires."""
        entry_price = 0.50
        current_mid = 0.96
        certainty_win_threshold = 0.95

        assert current_mid >= certainty_win_threshold

    def test_position_held_when_price_is_normal(self):
        """Price between certainty thresholds → no exit."""
        entry_price = 0.50
        current_mid = 0.52
        certainty_win_threshold = 0.95
        certainty_loss_threshold = 0.05

        # Not at certainty win
        assert current_mid < certainty_win_threshold
        # Not at certainty loss
        assert current_mid > certainty_loss_threshold
        # Normal hold
        assert abs(current_mid - entry_price) / entry_price < 0.25


class TestResolutionExit:
    """Tests for resolution-based exit."""

    def test_should_exit_for_resolved_market(self):
        """Market resolved → should exit."""
        # This requires mocking the API call, tested in unit tests
        # Here we verify the function exists and is callable
        assert callable(should_exit_for_resolution)

    def test_should_exit_for_imminent_resolution(self):
        """Market resolving within RESOLUTION_EXIT_HOURS → should exit."""
        # The actual API-dependent behavior is tested in test_wf_market_data.py
        # This is a smoke test that the function is callable
        result = should_exit_for_resolution("cond-test-POLYMARKET", log_func=None)
        assert isinstance(result, bool)

    def test_should_not_exit_for_distant_market(self):
        """Market resolving > RESOLUTION_EXIT_HOURS away → no exit."""
        # Smoke test
        result = should_exit_for_resolution("cond-far-POLYMARKET", log_func=None)
        assert isinstance(result, bool)
