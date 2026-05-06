"""Whale Follower — Signal processing.

Standalone functions for handling whale signals from all sources,
scanning known whale positions, processing trade buffers, and
LLM-based signal quality scoring.
"""

from __future__ import annotations

import json
import re
import time

from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.enums import OrderSide

from strategies.whale_tracker_new import (
    WhaleSignal,
    SignalSource,
)
from strategies.wf_constants import (
    WHALE_BLACKLIST,
    SPORTS_WHALE_BLACKLIST,
)
from strategies.wf_sports import is_sports_market


def on_signal(
    *,
    config,
    log,
    open_positions: dict,
    pending_whales: dict,
    tracker,
    whale_tiering,
    analyzer,
    signal: WhaleSignal,
) -> None:
    """Handle a whale signal from ANY subscribed market.

    This is the central signal processing pipeline.  It performs:
        1. Tier validation (confidence + edge_score thresholds).
        2. Blacklist rejection.
        3. LLM quality scoring.
        4. Dynamic instrument registration.
        5. Kelly-sized position entry via wf_entries.enter_position().

    Args:
        config: WhaleFollowerConfig.
        log: Logger.
        open_positions: dict of inst_key -> position info.
        pending_whales: Dict keyed by client_order_id for fill metadata.
        tracker: WhaleTracker instance.
        whale_tiering: WhaleTiering instance.
        analyzer: WhaleInsiderAnalyzer instance.
        signal: The WhaleSignal to process.
    """
    from strategies.wf_entries import enter_position, ensure_instrument

    # ── Whale Tiering Integration ────────────────────────────────────
    alpha_score = getattr(signal, "alpha_score", 50.0) or 50.0
    whale_tags = getattr(signal, "tags", "[]")
    try:
        tags_list = (
            json.loads(whale_tags) if isinstance(whale_tags, str) else (whale_tags or [])
        )
    except (json.JSONDecodeError, TypeError):
        tags_list = []

    tier = whale_tiering.get_tier(alpha_score) if whale_tiering else "unknown"
    tier_config = (
        whale_tiering.get_tier_config(alpha_score) if whale_tiering else {}
    )

    # Apply tier confidence threshold (overrides base config)
    if whale_tiering and not whale_tiering.validate_confidence(
        signal.confidence, alpha_score, tags_list
    ):
        min_conf = tier_config.get("min_confidence", config.min_confidence)
        log.info(
            f"Signal below tier confidence threshold ({tier}): {signal.whale_name} "
            f"(conf {signal.confidence:.0%} < {min_conf:.0%})"
        )
        return

    # Apply tier edge_score threshold
    edge_val = getattr(signal, "edge_score", 0.0) or 0.0
    if whale_tiering and not whale_tiering.validate_edge_score(edge_val, alpha_score):
        min_edge = tier_config.get("min_edge_score", 0.15)
        log.info(
            f"Signal below tier edge_score threshold ({tier}): {signal.whale_name} "
            f"(edge {edge_val:.2f} < {min_edge:.2f})"
        )
        return

    # REJECT: blacklisted whales
    if signal.whale_name in WHALE_BLACKLIST:
        log.info(f"REJECT blacklisted whale: {signal.whale_name}")
        return
    mc = getattr(signal, "market_category", "") or ""
    if signal.whale_name in SPORTS_WHALE_BLACKLIST and mc.lower() == "sports":
        log.info(f"REJECT sports-blacklisted whale: {signal.whale_name}")
        return

    # REJECT: unknown whale signals with zero edge score (noise trades)
    if (
        edge_val == 0.0
        and (
            not signal.whale_name
            or signal.whale_name.lower() in ("", "unknown", "unknown whale", "")
        )
    ):
        wallet = getattr(signal, "whale_address", "") or ""
        wallet_info = f" wallet={wallet[:10]}..." if wallet else ""
        log.info(
            f"REJECT unknown whale zero edge: {signal.whale_name}{wallet_info} | "
            f"market={getattr(signal, 'market_title', '')[:40]} | "
            f"conf={signal.confidence:.0%}"
        )
        return

    # Apply tier-based position sizing
    if whale_tiering:
        tier_kelly = whale_tiering.apply_overrides(
            tier_config, tags_list
        ).get("kelly_multiplier", 1.0)
        signal.suggested_size_usd = round(signal.suggested_size_usd * tier_kelly, 2)

    # LLM signal quality scoring (1700 Qwen3.5-9B, ~0.3s)
    llm_score = llm_score_signal(signal=signal, log=log)
    if llm_score < 5:
        log.info(f"REJECT LLM score={llm_score}/10: {signal.whale_name}")
        return
    log.info(
        f"LLM score={llm_score}/10: {signal.whale_name} | "
        f"market={getattr(signal, 'market_title', '')[:40]}"
    )

    # Log signal with tier info
    log.info(
        f"SIGNAL [{signal.source.value}] [{tier.upper()}]: {signal.reason} | "
        f"Confidence: {signal.confidence:.0%} | "
        f"Suggested: ${signal.suggested_size_usd:,.0f}",
        color=(
            LogColor.YELLOW
            if signal.source == SignalSource.KNOWN_WHALE
            else LogColor.CYAN
        ),
    )

    if not config.auto_trade:
        log.debug("Auto-trade disabled, skipping signal execution")
        return
    if getattr(log, "_daily_loss_breached", False):
        log.warning(
            "Daily loss limit breached ($%.2f), skipping signal execution",
            getattr(log, "_daily_pnl", 0.0),
        )
        return

    # Sports-specific daily loss check
    market_category = getattr(signal, "market_category", "") or ""
    is_sports, sport_type = is_sports_market(getattr(signal, "market_title", "") or "")
    if is_sports or market_category.lower() == "sports":
        if getattr(log, "_sports_daily_loss_breached", False):
            log.warning(
                "Sports daily loss limit breached ($%.2f), skipping sports signal execution",
                getattr(log, "_sports_daily_pnl", 0.0),
            )
            return

    # Dynamic subscription: every signal is processed regardless of
    # pre-subscribed markets.
    target_inst = ensure_instrument(
        cache=None,  # TODO: pass from caller
        log=log,
        condition_id=signal.condition_id,
        token_id=signal.token_id,
        outcome=signal.outcome,
        clob_client=None,  # TODO: pass from caller
    )
    if target_inst is None:
        log.info(
            f"Could not get instrument for {getattr(signal, 'market_title', '')[:40]}, skipping"
        )
        return

    # Determine side
    side = OrderSide.BUY if signal.side == "buy" else OrderSide.SELL

    # Get whale's actual win rate for dynamic Kelly sizing
    whale_wr = None
    if config.use_dynamic_kelly and tracker:
        for w in tracker.whales.values():
            if w.name == signal.whale_name:
                whale_wr = w.win_rate
                break
        if whale_wr is None:
            log.debug(
                f"Whale '{signal.whale_name}' not found in tracker, using default Kelly"
            )

    # Delegate to wf_entries.enter_position() for execution
    enter_position(
        config=config,
        cache=None,  # TODO: pass from caller
        portfolio=None,  # TODO: pass from caller
        order_factory=None,  # TODO: pass from caller
        log=log,
        open_positions=open_positions,
        exited_positions=set(),  # TODO: pass from caller
        last_exit_time={},  # TODO: pass from caller
        whale_tiering=whale_tiering,
        clob_client=None,  # TODO: pass from caller
        side=side,
        price=signal.target_price,
        whale_amount=signal.suggested_size_usd,
        instrument_id=target_inst,
        whale_win_rate=whale_wr,
        whale_name=signal.whale_name,
        market_title=signal.market_title,
        market_category=getattr(signal, "market_category", "Unknown"),
        whale_address=getattr(signal, "whale_address", "") or "",
        edge_score=edge_val,
        confidence=signal.confidence or 0.0,
        entry_reason=signal.reason or "",
    )


def scan_whale_positions(
    *,
    config,
    log,
    tracker,
    on_signal_fn,
) -> None:
    """Poll known whale positions with rate limiting.

    Args:
        config: WhaleFollowerConfig.
        log: Logger.
        tracker: WhaleTracker instance.
        on_signal_fn: Callable to handle each detected signal
            (typically on_signal() from this module).
    """
    if not tracker or not config.auto_trade:
        log.warning(
            "Whale scan skipped: tracker=%s auto_trade=%s",
            bool(tracker),
            config.auto_trade,
        )
        return

    # Reset per-scan trade counter (caller manages)
    trades_this_scan = 0

    # Clear expired dedup entries (TTL-based re-scan)
    now = time.time()
    ttl = config.seen_position_ttl
    if tracker.seen_positions:
        expired = [
            k for k, v in tracker.seen_positions.items() if now - v > ttl
        ]
        if expired:
            for k in expired:
                del tracker.seen_positions[k]
            log.info(f"Cleared {len(expired)} expired dedup entries (TTL={ttl/3600:.0f}h)")

    try:
        signals = tracker.scan_known_whales()

        if signals:
            log.info(
                f"Whale scan complete: {len(signals)} new signals detected "
                f"from {len(tracker.whales)} tracked whales"
            )

        for signal in signals:
            if trades_this_scan >= config.max_trades_per_scan:
                log.info(
                    f"Scan trade limit reached ({config.max_trades_per_scan}), "
                    f"skipping {len(signals) - trades_this_scan} remaining signals"
                )
                break
            on_signal_fn(signal)
            trades_this_scan += 1
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.error(f"Whale scan error: {e}\n{tb}")


def process_trade_buffer(
    *,
    tracker,
    trade_buffer: list[dict],
    on_signal_fn,
    log,
) -> None:
    """Process buffered large trades into signals.

    Args:
        tracker: WhaleTracker instance.
        trade_buffer: List of trade dicts with size, price, side, timestamp.
        on_signal_fn: Callable to handle each detected signal.
        log: Logger.
    """
    if not tracker or not trade_buffer:
        log.debug("Trade buffer processing skipped: no tracker or buffer empty")
        return

    try:
        signals = tracker.detect_large_trades(trade_buffer)
        trade_buffer.clear()
        for signal in signals:
            on_signal_fn(signal)
    except Exception as e:
        log.error(f"Trade processing error: {e}")


def llm_score_signal(
    *,
    signal: WhaleSignal,
    log,
) -> int:
    """Score a whale signal using a local LLM (Qwen3.5-9B).

    Sends a short prompt to the local LLM endpoint and extracts
    a numeric score 1-10.

    Args:
        signal: The WhaleSignal to score.
        log: Logger.

    Returns:
        Integer score 1-10. Returns 5 on failure.
    """
    import urllib.request as ureq

    market = getattr(signal, "market_title", "") or ""
    whale = signal.whale_name or "unknown"
    side = getattr(signal, "side", "?") or "?"
    price = getattr(signal, "target_price", 0.5) or 0.5
    category = getattr(signal, "market_category", "") or ""
    prompt = (
        "Score this Polymarket signal 1-10. "
        f"Market: {market[:80]}. Whale: {whale[:30]}. "
        f"Side: {side} at {price:.3f}. Category: {category}."
    )
    if whale in ("unknown", "unknown whale", ""):
        prompt += " Unknown whale, be skeptical."

    payload = json.dumps(
        {
            "model": "Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Score betting signals 1-10. Known losing whales get 1-3. "
                        "Good signals get 7-10. Reply ONLY a number 1-10."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 10,
            "temperature": 0.01,
        }
    ).encode()

    try:
        req = ureq.Request(
            "http://127.0.0.1:8080/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with ureq.urlopen(req, timeout=10) as resp:
            data2 = json.loads(resp.read())
        content = data2["choices"][0]["message"].get("content", "").strip()
        nums = re.findall(
            r"\d+", content.replace("<think>", "").replace("</think>", "")
        )
        score = int(nums[0]) if nums else 5
        return max(1, min(10, score))
    except Exception as e:
        log.warning(f"LLM score failed: {e}")
        return 5
