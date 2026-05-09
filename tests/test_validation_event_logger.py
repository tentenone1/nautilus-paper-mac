import gzip
import importlib.util
import json
import sys
import uuid
from datetime import date
from pathlib import Path

# Load event_logger module directly from file path (bypass components/__init__.py)
_event_logger_path = Path(__file__).resolve().parent.parent / "components" / "validation" / "event_logger.py"
_spec = importlib.util.spec_from_file_location("event_logger", _event_logger_path)
event_logger = importlib.util.module_from_spec(_spec)
sys.modules["event_logger"] = event_logger
_spec.loader.exec_module(event_logger)

# Import needed items
EventType = event_logger.EventType
GENESIS_CHECKSUM = event_logger.GENESIS_CHECKSUM
GZIP_SUFFIX = event_logger.GZIP_SUFFIX
log_event = event_logger.log_event
get_events = event_logger.get_events
rotate_previous_day_file = event_logger.rotate_previous_day_file


def test_log_event_appends_jsonl_with_checksum_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(event_logger, "LOGS_DIR", tmp_path)

    first_id = log_event(
        event_type=EventType.WHALE_TRADE_DETECTED,
        run_id="run-1",
        mode="paper",
        strategy_id="whale-follower",
        correlation_id="corr-1",
        payload={"market": "m1", "price": 0.42},
    )
    second_id = log_event(
        event_type=EventType.SIGNAL_GENERATED,
        run_id="run-1",
        mode="paper",
        strategy_id="whale-follower",
        correlation_id="corr-1",
        payload={"signal": "buy"},
    )

    uuid.UUID(first_id)
    uuid.UUID(second_id)
    # Use UTC date since log_event uses datetime.now(timezone.utc).date()
    utc_today = event_logger.datetime.now(event_logger.timezone.utc).date()
    event_file = tmp_path / f"{utc_today.isoformat()}.validation.jsonl"
    rows = [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()]

    assert rows[0]["prev_checksum"] == GENESIS_CHECKSUM
    assert rows[1]["prev_checksum"] == rows[0]["checksum"]
    assert rows[0]["checksum"] != rows[1]["checksum"]


def test_get_events_filters_by_date_and_type(tmp_path, monkeypatch):
    monkeypatch.setattr(event_logger, "LOGS_DIR", tmp_path)
    log_event(
        event_type=EventType.API_ERROR,
        run_id="run-1",
        mode="live",
        strategy_id="whale-follower",
        correlation_id="corr-2",
        payload={"status": 500},
    )
    log_event(
        event_type=EventType.RETRY_ATTEMPT,
        run_id="run-1",
        mode="live",
        strategy_id="whale-follower",
        correlation_id="corr-2",
        payload={"attempt": 1},
    )

    # Use UTC date
    utc_today = event_logger.datetime.now(event_logger.timezone.utc).date()
    events = get_events(utc_today, EventType.API_ERROR)

    assert len(events) == 1
    assert events[0].event_type == EventType.API_ERROR.value
    assert events[0].payload == {"status": 500}


def test_rotate_previous_day_file_compresses_and_removes_source(tmp_path, monkeypatch):
    monkeypatch.setattr(event_logger, "LOGS_DIR", tmp_path)
    previous_day = date(2026, 5, 9)
    source = tmp_path / "2026-05-09.validation.jsonl"
    source.write_text('{"event_id":"example"}\n', encoding="utf-8")

    rotated = rotate_previous_day_file(now_utc=date(2026, 5, 10))

    assert rotated == source.with_suffix(source.suffix + GZIP_SUFFIX)
    assert not source.exists()
    with gzip.open(rotated, "rt", encoding="utf-8") as handle:
        assert handle.read() == '{"event_id":"example"}\n'