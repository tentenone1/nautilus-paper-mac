"""Tests for snapshot_store module."""

import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Load snapshot_store module directly
_snapshot_path = Path(__file__).resolve().parent.parent / "components" / "validation" / "snapshot_store.py"
_spec = __import__("importlib.util").util.spec_from_file_location("snapshot_store", _snapshot_path)
snapshot_store = __import__("importlib.util").util.module_from_spec(_spec)
sys.modules["snapshot_store"] = snapshot_store
_spec.loader.exec_module(snapshot_store)

freeze_snapshot = snapshot_store.freeze_snapshot
load_snapshot = snapshot_store.load_snapshot
verify_snapshot = snapshot_store.verify_snapshot
list_snapshots = snapshot_store.list_snapshots
get_snapshot_path = snapshot_store.get_snapshot_path
SignalSnapshot = snapshot_store.SignalSnapshot
SNAPSHOTS_DIR = snapshot_store.SNAPSHOTS_DIR


def test_freeze_snapshot_creates_file(monkeypatch, tmp_path):
    """Test that freeze_snapshot creates compressed file."""
    monkeypatch.setattr(snapshot_store, "SNAPSHOTS_DIR", tmp_path)
    
    snapshot = freeze_snapshot(
        signal_id="sig-1",
        market_state={"price": 0.50, "liquidity": 10000},
        orderbook={"bids": [0.49, 0.48], "asks": [0.51, 0.52]},
        whale_metrics={"position": 5000, "confidence": 0.85},
        classification="skilled_human",
        confidence=0.85,
        market_regime="neutral",
        strategy_version="v1.0",
    )
    
    assert snapshot.snapshot_id is not None
    assert len(snapshot.snapshot_id) == 36  # UUID format
    assert snapshot.signal_id == "sig-1"
    assert snapshot.checksum is not None
    assert len(snapshot.checksum) == 64  # SHA256 hex
    
    # Check file exists
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot_file = tmp_path / today / f"{snapshot.snapshot_id}.json.gz"
    assert snapshot_file.exists()


def test_load_snapshot_retrieves_data(monkeypatch, tmp_path):
    """Test that load_snapshot retrieves stored data."""
    monkeypatch.setattr(snapshot_store, "SNAPSHOTS_DIR", tmp_path)
    
    # Create snapshot
    original = freeze_snapshot(
        signal_id="sig-2",
        market_state={"price": 0.42, "liquidity": 5000},
        orderbook={"bids": [], "asks": []},
        whale_metrics={"classification": "degenerate_human"},
        classification="degenerate_human",
        confidence=0.60,
    )
    
    # Load it back
    loaded = load_snapshot(original.snapshot_id)
    
    assert loaded is not None
    assert loaded.snapshot_id == original.snapshot_id
    assert loaded.signal_id == "sig-2"
    assert loaded.market_state["price"] == 0.42
    assert loaded.checksum == original.checksum


def test_verify_snapshot_checksum(monkeypatch, tmp_path):
    """Test that verify_snapshot validates checksum."""
    monkeypatch.setattr(snapshot_store, "SNAPSHOTS_DIR", tmp_path)
    
    snapshot = freeze_snapshot(
        signal_id="sig-3",
        market_state={"price": 0.50},
        orderbook={},
        whale_metrics={},
        classification="sacrificial_account",
        confidence=0.75,
    )
    
    # Should verify correctly
    assert verify_snapshot(snapshot) is True
    
    # Corrupt checksum
    corrupted_dict = {
        "snapshot_id": snapshot.snapshot_id,
        "timestamp": snapshot.timestamp,
        "ts_mono_ns": snapshot.ts_mono_ns,
        "signal_id": snapshot.signal_id,
        "market_state": snapshot.market_state,
        "orderbook": snapshot.orderbook,
        "whale_metrics": snapshot.whale_metrics,
        "classification": snapshot.classification,
        "confidence": snapshot.confidence,
        "market_regime": snapshot.market_regime,
        "strategy_version": snapshot.strategy_version,
        "checksum": "corrupted_checksum",
    }
    corrupted = SignalSnapshot(**corrupted_dict)
    
    assert verify_snapshot(corrupted) is False


def test_list_snapshots(monkeypatch, tmp_path):
    """Test listing all snapshots."""
    monkeypatch.setattr(snapshot_store, "SNAPSHOTS_DIR", tmp_path)
    
    # Create multiple snapshots
    snap1 = freeze_snapshot(
        signal_id="sig-a",
        market_state={},
        orderbook={},
        whale_metrics={},
        classification="skilled_human",
        confidence=0.80,
    )
    
    snap2 = freeze_snapshot(
        signal_id="sig-b",
        market_state={},
        orderbook={},
        whale_metrics={},
        classification="sacrificial_account",
        confidence=0.70,
    )
    
    # List them
    snapshot_ids = list_snapshots()
    
    assert snap1.snapshot_id in snapshot_ids
    assert snap2.snapshot_id in snapshot_ids
    assert len(snapshot_ids) >= 2


def test_load_nonexistent_snapshot_returns_none(monkeypatch, tmp_path):
    """Test that loading nonexistent snapshot returns None."""
    monkeypatch.setattr(snapshot_store, "SNAPSHOTS_DIR", tmp_path)
    
    loaded = load_snapshot("nonexistent-uuid")
    
    assert loaded is None


def test_get_snapshot_path(monkeypatch, tmp_path):
    """Test getting snapshot file path."""
    monkeypatch.setattr(snapshot_store, "SNAPSHOTS_DIR", tmp_path)
    
    snapshot = freeze_snapshot(
        signal_id="sig-4",
        market_state={},
        orderbook={},
        whale_metrics={},
        classification="skilled_human",
        confidence=0.90,
    )
    
    path = get_snapshot_path(snapshot.snapshot_id)
    
    assert path is not None
    assert path.exists()
    assert path.name.endswith(".json.gz")