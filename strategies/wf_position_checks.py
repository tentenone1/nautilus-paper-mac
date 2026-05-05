"""Whale Follower — Position checking and daily loss limit.

Standalone functions extracted from wf_exits.py for modularity.
All state passed as explicit parameters.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from nautilus_trader.model.identifiers import InstrumentId

from strategies.wf_constants import (
    CERTAINTY_LOSS_THRESHOLD,
    CERTAINTY_WIN_THRESHOLD,
    MAX_SANE_RETURN,
)
from strategies.wf_exits import (
    _resolve_exit_price_with_deps,
    exit_all_positions,
    exit_position,
)


def check_all_positions(
    *,
    config,
    cache,
    log,
    open_positions: dict,
    exited_positions: set,
    last_exit_time: dict,
    resolution_poller=None,
    clob_client=None,
) -> None:
    """Check exit conditions for ALL open positions.

    Phase 1: Duration-based exit — close positions held past max_hold_hours.
    Phase 2: Certainty exits — exit when price > 0.95 or < 0.05.

    Args:
        config: WhaleFollowerConfig.
        cache: Nautilus Cache.
        log: Logger.
        open_positions: dict of inst_key -> position info (mutated).
        exited_positions: set of exited inst_keys (mutated).
        last_exit_time: dict of inst_key -> timestamp (mutated).
        resolution_poller: Optional ResolutionPoller.
        clob_client: Optional ClobClient.
    """
    now = time.time()

    # Phase 1: Duration-based exit
    max_hold = config.max_hold_hours
    expired = [
        k
        for k, v in open_positions.items()
        if now - v.get("entry_time", 0) > max_hold * 3600
    ]
    for inst_key in expired:
        try:
            inst_id = InstrumentId.from_str(inst_key)
            exit_position(
                config=config,
                cache=cache,
                log=log,
                open_positions=open_positions,
                exited_positions=exited_positions,
                last_exit_time=last_exit_time,
                resolution_poller=resolution_poller,
                clob_client=clob_client,
                instrument_id=inst_id,
                exit_reason="max_hold",
            )
        except Exception as e:
            log.error(f"Error exiting expired position {inst_key[:50]}...: {e}")
            if inst_key in open_positions:
                del open_positions[inst_key]

    # Phase 2: Check ALL open positions for certainty exits
    for inst_key in list(open_positions.keys()):
        try:
            try:
                inst_id = InstrumentId.from_str(inst_key)
            except Exception as parse_err:
                log.error(f"Failed to parse instrument ID '{inst_key[:50]}...': {parse_err}")
                continue

            open_pos_list = cache.positions_open(instrument_id=inst_id)
            if not open_pos_list or open_pos_list[0].quantity.as_double() == 0:
                continue

            pos = open_pos_list[0]
            raw_entry = pos.avg_px_open
            entry = (
                raw_entry.as_double()
                if hasattr(raw_entry, "as_double")
                else float(raw_entry)
            )
            if entry <= 0:
                continue

            pos_info = open_positions.get(inst_key, {})
            quote = cache.quote_tick(inst_id)
            if quote is None:
                if pos_info:
                    mid = _resolve_exit_price_with_deps(
                        pos_info=pos_info,
                        instrument_id_str=inst_key,
                        resolution_poller=resolution_poller,
                        clob_client=clob_client,
                        log=log,
                    )
                    log.info(f"SIMULATED PRICE for {inst_id}: {mid:.4f} (no quote ticks)")
                else:
                    continue
            else:
                mid = (
                    quote.bid_price.as_double() + quote.ask_price.as_double()
                ) / 2

            position_edge = pos_info.get("edge_score", 0.0) or 0.0
            side = pos_info.get("side", "BUY")

            if side == "BUY":
                is_certain_win = mid > CERTAINTY_WIN_THRESHOLD
                is_certain_loss = mid < CERTAINTY_LOSS_THRESHOLD
            else:
                is_certain_win = mid < CERTAINTY_LOSS_THRESHOLD
                is_certain_loss = mid > CERTAINTY_WIN_THRESHOLD

            if is_certain_win:
                log.info(
                    f"CERTAINTY EXIT (WIN) {inst_id}: mid={mid:.4f}, "
                    f"entry={entry:.4f}, edge={position_edge:.2f}, "
                    f"condition_id={pos_info.get('condition_id', '?')[:20]}..."
                )
                exit_position(
                    config=config,
                    cache=cache,
                    log=log,
                    open_positions=open_positions,
                    exited_positions=exited_positions,
                    last_exit_time=last_exit_time,
                    resolution_poller=resolution_poller,
                    clob_client=clob_client,
                    instrument_id=inst_id,
                    exit_reason="certainty_win",
                )
                continue
            elif is_certain_loss:
                log.info(
                    f"CERTAINTY LOSS BLOCKED (Phase A): {inst_id}: mid={mid:.4f}, "
                    f"entry={entry:.4f}, edge={position_edge:.2f}, "
                    f"condition_id={pos_info.get('condition_id', '?')[:20]}... "
                    f"holding to resolution instead"
                )
                continue
            else:
                log.info(
                    f"HOLDING {inst_id}: entry={entry:.4f}, mid={mid:.4f}, "
                    f"edge={position_edge:.2f} - holding to resolution"
                )
                continue

        except Exception as pos_error:
            log.error(
                f"Error checking position {inst_key[:50]}...: {pos_error} | "
                f"continuing to next position"
            )
            continue


def check_daily_loss_limit(
    *,
    config,
    log,
    daily_pnl: float,
    daily_pnl_date: str,
    daily_loss_breached: bool,
    open_positions: dict,
    exited_positions: set,
    last_exit_time: dict,
    resolution_poller=None,
    clob_client=None,
    cache=None,
) -> tuple[float, str, bool]:
    """Check if daily loss limit has been breached.

    Args:
        config: WhaleFollowerConfig (for daily_loss_limit).
        log: Logger.
        daily_pnl: Current daily P&L accumulator.
        daily_pnl_date: Date string of current daily tracking.
        daily_loss_breached: Whether limit was already breached today.
        open_positions: dict of inst_key -> position info.
        exited_positions: set of exited inst_keys.
        last_exit_time: dict of inst_key -> timestamp.
        resolution_poller: Optional ResolutionPoller.
        clob_client: Optional ClobClient.
        cache: Nautilus Cache (for exit_all_positions).

    Returns:
        Tuple of (new_daily_pnl, new_daily_pnl_date, new_daily_loss_breached).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != daily_pnl_date:
        return 0.0, today, False

    if daily_loss_breached:
        return daily_pnl, daily_pnl_date, True

    if daily_pnl <= -config.daily_loss_limit:
        log.error(
            f"DAILY LOSS LIMIT BREACHED: ${daily_pnl:,.2f} / "
            f"-${config.daily_loss_limit:,.2f}. "
            f"Closing all positions and stopping auto-trade."
        )
        if cache is not None:
            exit_all_positions(
                config=config,
                cache=cache,
                log=log,
                open_positions=open_positions,
                exited_positions=exited_positions,
                last_exit_time=last_exit_time,
                resolution_poller=resolution_poller,
                clob_client=clob_client,
            )
        return daily_pnl, daily_pnl_date, True

    return daily_pnl, daily_pnl_date, False
