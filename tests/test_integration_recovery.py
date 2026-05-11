"""
Integration tests: Restart Recovery — Position Persistence.
"""
import pytest
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from strategies.wf_position_persistence import save_open_positions, load_open_positions


class TestPositionPersistence:
    """Tests for save/load open positions survival across restart."""

    def test_save_and_load_positions_survive_restart(self, tmp_path):
        """Save positions → simulate restart → load positions → verify state."""
        positions_file = tmp_path / "open_positions.json"

        # Pre-populate with 3 mock positions
        open_positions = {
            "A": {
                "side": "BUY",
                "entry_price": 0.45,
                "size": 20.0,
                "market_category": "sports",
                "whale_name": "TestWhale1",
                "market_title": "Game 1",
                "condition_id": "condA",
                "inst_key": "condA-tokenA.POLYMARKET",
                "entry_time": 1000.0,
                "trade_id": "T-A",
            },
            "B": {
                "side": "SELL",
                "entry_price": 0.70,
                "size": 15.0,
                "market_category": "politics",
                "whale_name": "TestWhale2",
                "market_title": "Election",
                "condition_id": "condB",
                "inst_key": "condB-tokenB.POLYMARKET",
                "entry_time": 1001.0,
                "trade_id": "T-B",
            },
            "C": {
                "side": "BUY",
                "entry_price": 0.12,
                "size": 10.0,
                "market_category": "crypto",
                "whale_name": "TestWhale3",
                "market_title": "Bitcoin",
                "condition_id": "condC",
                "inst_key": "condC-tokenC.POLYMARKET",
                "entry_time": 1002.0,
                "trade_id": "T-C",
            },
        }

        # Patch the POSITIONS_FILE path temporarily
        import strategies.wf_position_persistence as persistence
        original_file = persistence.POSITIONS_FILE
        persistence.POSITIONS_FILE = positions_file

        try:
            # Save
            save_open_positions(open_positions)
            assert positions_file.exists(), "Positions file should exist after save"

            # Load (simulate restart)
            loaded = load_open_positions()

            # Verify all positions preserved
            assert len(loaded) == 3, f"Expected 3 positions, got {len(loaded)}"
            for key in ["A", "B", "C"]:
                assert key in loaded, f"Position {key} missing after load"
                assert loaded[key]["side"] == open_positions[key]["side"]
                assert loaded[key]["entry_price"] == open_positions[key]["entry_price"]
                assert loaded[key]["size"] == open_positions[key]["size"]
                assert loaded[key]["market_category"] == open_positions[key]["market_category"]
                assert loaded[key]["whale_name"] == open_positions[key]["whale_name"]
        finally:
            persistence.POSITIONS_FILE = original_file

    def test_corrupted_json_returns_empty_dict(self, tmp_path):
        """Corrupted JSON file → recovery returns empty dict, no crash."""
        bad_file = tmp_path / "corrupt_positions.json"
        bad_file.write_text('{"broken": json')

        import strategies.wf_position_persistence as persistence
        original_file = persistence.POSITIONS_FILE
        persistence.POSITIONS_FILE = bad_file

        try:
            result = load_open_positions()
            assert result == {}, f"Expected empty dict for corrupt JSON, got {result}"
        finally:
            persistence.POSITIONS_FILE = original_file

    def test_missing_file_returns_empty_dict(self, tmp_path):
        """Missing positions file → returns empty dict (first startup)."""
        nonexistent = tmp_path / "does_not_exist.json"

        import strategies.wf_position_persistence as persistence
        original_file = persistence.POSITIONS_FILE
        persistence.POSITIONS_FILE = nonexistent

        try:
            result = load_open_positions()
            assert result == {}, f"Expected empty dict for missing file, got {result}"
        finally:
            persistence.POSITIONS_FILE = original_file
