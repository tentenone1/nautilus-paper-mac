"""
Unit tests for strategies.wf_market_data — market data fetching and resolution checks.
"""
import pytest
import sys
sys.path.insert(0, ".")

from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from strategies.wf_market_data import should_exit_for_resolution


class TestShouldExitForResolution:
    """Tests for should_exit_for_resolution() — resolution-based exit timing."""

    def _mock_response(self, end_date_iso, status_code=200):
        """Create a mock API response."""
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = {"end_date_iso": end_date_iso}
        return mock_resp

    def test_market_resolving_soon_exits(self):
        """Market resolving within RESOLUTION_EXIT_HOURS (6h) → exit."""
        # Market ends in 3 hours
        future = datetime.now(timezone.utc) + timedelta(hours=3)
        end_date = future.isoformat()

        with patch("strategies.wf_market_data.requests.get") as mock_get:
            mock_get.return_value = self._mock_response(end_date)

            result = should_exit_for_resolution(
                instrument_id_str="cond-token-abc123.POLYMARKET",
                log_func=MagicMock(),
            )

        assert result is True

    def test_market_resolved_already_exits(self):
        """Market that already ended → exit immediately."""
        # Market ended 1 hour ago
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        end_date = past.isoformat()

        with patch("strategies.wf_market_data.requests.get") as mock_get:
            mock_get.return_value = self._mock_response(end_date)
            mock_log = MagicMock()

            result = should_exit_for_resolution(
                instrument_id_str="cond-token-abc123.POLYMARKET",
                log_func=mock_log,
            )

        assert result is True
        mock_log.assert_called()

    def test_market_far_in_future_does_not_exit(self):
        """Market resolving in >6 hours → hold, no exit."""
        # Market ends in 48 hours
        future = datetime.now(timezone.utc) + timedelta(hours=48)
        end_date = future.isoformat()

        with patch("strategies.wf_market_data.requests.get") as mock_get:
            mock_get.return_value = self._mock_response(end_date)

            result = should_exit_for_resolution(
                instrument_id_str="cond-token-abc123.POLYMARKET",
                log_func=MagicMock(),
            )

        assert result is False

    def test_api_failure_does_not_exit(self):
        """API failure → don't exit on error (fail safe)."""
        with patch("strategies.wf_market_data.requests.get") as mock_get:
            mock_get.side_effect = Exception("Network error")

            result = should_exit_for_resolution(
                instrument_id_str="cond-token-abc123.POLYMARKET",
                log_func=MagicMock(),
            )

        assert result is False

    def test_api_returns_non_200_does_not_exit(self):
        """API returns non-200 → don't exit."""
        with patch("strategies.wf_market_data.requests.get") as mock_get:
            mock_get.return_value = self._mock_response(None, status_code=404)

            result = should_exit_for_resolution(
                instrument_id_str="cond-token-abc123.POLYMARKET",
                log_func=MagicMock(),
            )

        assert result is False

    def test_market_exactly_at_threshold_does_not_exit(self):
        """Market resolving in exactly 6 hours → do NOT exit (only < 6h triggers)."""
        # Use a known fixed reference time: 2026-05-11 12:00 UTC
        # Market ends 6 hours later: 2026-05-11 18:00 UTC
        fixed_now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        end_time = datetime(2026, 5, 11, 18, 0, 0, tzinfo=timezone.utc)

        with patch("strategies.wf_market_data.requests.get") as mock_get:
            mock_get.return_value = self._mock_response(end_time.isoformat())

            with patch("strategies.wf_market_data.datetime") as mock_dt:
                mock_dt.now.return_value = fixed_now
                mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                mock_dt.timezone = timezone

                result = should_exit_for_resolution(
                    instrument_id_str="cond-token-abc123.POLYMARKET",
                    log_func=MagicMock(),
                )

        # Exactly 6 hours should NOT exit (only strictly less than 6)
        assert result is False
