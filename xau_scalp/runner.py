"""Скальп ордер + позиция basqaru + журнал."""

from __future__ import annotations

import logging
import time
from typing import Any

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None  # type: ignore

from side import Side
from xau_scalp.adaptive import (
    log_analytics,
    log_mode_if_changed,
    resolve_invert_mode,
)
from xau_scalp.analytics import should_skip_slot
from xau_scalp.config import XauScalpSettings
from xau_scalp.journal import log_close, log_open, today_stats
from xau_scalp.news_filter import blocking_news
from xau_scalp.risk_guard import drawdown_blocks
from xau_scalp.session import in_kill_zone, session_name
from xau_scalp.state import can_trade, mark_trade
from xau_scalp.strategy import scan_scalp_signal
from xau_scalp.web_signals import get_cached_web_bias
from mt5_trade import MT5Trader

logger = logging.getLogger(__name__)

_open_meta: dict[int, dict[str, Any]] = {}
_last_stats_log: float = 0.0
_last_wait_log: float = 0.0


def _log_wait_status(trader: MT5Trader, settings: XauScalpSettings) -> None:
    """Kill zone ішінде неге сделка жоқ — 15 минут сайын."""
    global _last_wait_log
    now = time.time()
    if now - _last_wait_log < 900:
        return
    if not in_kill_zone(settings.kill_zones, use_local=settings.kill_zone_local):
        return
    _last_wait_log = now

    if mt5 is None or not trader.ensure_connected():
        logger.info("Kutu: MT5 disconnected - terminal ashik + Algo Trading")
        return
    if trader.open_positions_count(settings.symbol, settings.magic) > 0:
        return

    tf_m5 = mt5.TIMEFRAME_M5
    m5 = trader.copy_rates(settings.symbol, tf_m5, 80)
    if len(m5) < 35:
        logger.info("Күту: M5 деректері аз (%s bar)", len(m5))
        return

    point = trader.symbol_point(settings.symbol)
    from xau_scalp.liquidity import detect_sweep

    sweep = detect_sweep(
        m5,
        swing_lookback=settings.swing_lookback,
        wick_min_points=settings.sweep_wick_min_points,
        point=point,
    )
    web = get_cached_web_bias(settings)
    web_txt = web.summary if web else "—"
    if sweep is None:
        extra = ", consensus=ON" if settings.consensus_entry else ""
        logger.info(
            "Күту: likvidlik sweep жоқ (web=%s, spread=%.0f pts%s) — sweep nemese web+EMA consensus",
            web_txt,
            _spread_points(trader, settings.symbol),
            extra,
        )
        return
    logger.info("Sweep бар, фильтр/vote/confidence тексеруде…")


def _spread_points(trader: MT5Trader, symbol: str) -> float:
    point = trader.symbol_point(symbol)
    if point <= 0:
        return 9999.0
    return trader.symbol_spread(symbol) / point


def _position_profit(ticket: int) -> float:
    if mt5 is None:
        return 0.0

    deals = mt5.history_deals_get(position=ticket)
    if not deals:
        from datetime import datetime, timedelta, timezone

        since = datetime.now(timezone.utc) - timedelta(days=7)
        until = datetime.now(timezone.utc) + timedelta(minutes=1)
        all_deals = mt5.history_deals_get(since, until) or []
        deals = [d for d in all_deals if int(d.position_id) == int(ticket)]
    if not deals:
        return 0.0
    return float(sum(d.profit + d.swap + d.commission for d in deals))


def _sync_closed(trader: MT5Trader, settings: XauScalpSettings) -> None:
    if mt5 is None or not trader.ensure_connected():
        return

    open_tickets = {
        p.ticket
        for p in (mt5.positions_get(symbol=settings.symbol) or [])
        if p.magic == settings.magic
    }
    for ticket, meta in list(_open_meta.items()):
        if ticket in open_tickets:
            continue
        profit = _position_profit(ticket)
        log_close(
            settings.journal_file,
            ticket=ticket,
            side=str(meta.get("side", "")),
            symbol=settings.symbol,
            profit=profit,
            reason=str(meta.get("reason", "closed")),
            mode=str(meta.get("mode", "")),
            dow=meta.get("dow"),
            session=str(meta.get("session", "")),
            utc_hour=meta.get("utc_hour"),
        )
        logger.info(
            "SCALP CLOSE ticket=%s %s profit=%.2f",
            ticket,
            "WIN" if profit > 0 else ("LOSS" if profit < 0 else "BE"),
            profit,
        )
        del _open_meta[ticket]


def _manage_open(trader: MT5Trader, settings: XauScalpSettings) -> None:
    if mt5 is None or not trader.ensure_connected():
        return

    for pos in mt5.positions_get(symbol=settings.symbol) or []:
        if pos.magic != settings.magic:
            continue

        point = trader.symbol_point(settings.symbol)
        risk = abs(pos.price_open - pos.sl) if pos.sl else settings.sl_points * point
        if risk <= 0:
            risk = settings.sl_points * point

        if pos.type == mt5.POSITION_TYPE_BUY:
            profit_dist = pos.price_current - pos.price_open
            be_sl = pos.price_open + 2 * point
        else:
            profit_dist = pos.price_open - pos.price_current
            be_sl = pos.price_open - 2 * point

        pos_time = pos.time
        if hasattr(pos_time, "timestamp"):
            opened = pos_time.timestamp()
        else:
            opened = float(pos_time)
        age_min = (time.time() - opened) / 60.0
        if age_min >= settings.max_hold_min:
            meta = _open_meta.get(pos.ticket, {})
            meta["reason"] = f"max_hold_{settings.max_hold_min}m"
            _open_meta[pos.ticket] = meta
            trader.close_all_trading(settings.symbol, settings.magic)
            logger.info("Max hold %s мин — позиция жабылды", settings.max_hold_min)
            continue

        if settings.breakeven_r > 0 and profit_dist >= settings.breakeven_r * risk:
            if pos.type == mt5.POSITION_TYPE_BUY and pos.sl < be_sl - point:
                trader.set_stop_loss(settings.symbol, be_sl, magic=settings.magic)
                logger.info("Breakeven BUY SL→%.2f", be_sl)
            elif pos.type == mt5.POSITION_TYPE_SELL and (pos.sl == 0 or pos.sl > be_sl + point):
                trader.set_stop_loss(settings.symbol, be_sl, magic=settings.magic)
                logger.info("Breakeven SELL SL→%.2f", be_sl)

        if settings.trail_start_r > 0 and profit_dist >= settings.trail_start_r * risk:
            trail = settings.trail_points * point
            if pos.type == mt5.POSITION_TYPE_BUY:
                new_sl = pos.price_current - trail
                if new_sl > pos.sl + point:
                    trader.set_stop_loss(settings.symbol, new_sl, magic=settings.magic)
            else:
                new_sl = pos.price_current + trail
                if pos.sl == 0 or new_sl < pos.sl - point:
                    trader.set_stop_loss(settings.symbol, new_sl, magic=settings.magic)

        meta = _open_meta.get(pos.ticket, {})
        if (
            settings.use_partial_tp
            and not meta.get("partial_done")
            and profit_dist >= settings.partial_tp_r * risk
        ):
            ok, closed_vol, remain = trader.partial_close_percent(
                settings.symbol,
                settings.partial_tp_pct,
                magic=settings.magic,
            )
            if ok:
                meta["partial_done"] = True
                _open_meta[pos.ticket] = meta
                logger.info(
                    "Partial TP %s%% @ %.1fR — %.2f lot жабылды, %.2f қалды",
                    settings.partial_tp_pct,
                    settings.partial_tp_r,
                    closed_vol,
                    remain,
                )
                if pos.type == mt5.POSITION_TYPE_BUY and pos.sl < be_sl - point:
                    trader.set_stop_loss(settings.symbol, be_sl, magic=settings.magic)
                elif pos.type == mt5.POSITION_TYPE_SELL and (pos.sl == 0 or pos.sl > be_sl + point):
                    trader.set_stop_loss(settings.symbol, be_sl, magic=settings.magic)


def _log_daily_stats(settings: XauScalpSettings) -> None:
    global _last_stats_log
    now = time.time()
    if now - _last_stats_log < 1800:
        return
    _last_stats_log = now
    stats = today_stats(settings.journal_file)
    if stats["opens"] or stats["closed"]:
        logger.info(
            "Күн stats: opens=%s closed=%s wins=%s losses=%s profit=%.2f win_rate=%.1f%%",
            stats["opens"],
            stats["closed"],
            stats["wins"],
            stats["losses"],
            stats["profit"],
            stats["win_rate"],
        )
    if settings.adaptive_mode or settings.smart_indicators:
        log_analytics(settings)


def try_open_scalp(
    trader: MT5Trader,
    settings: XauScalpSettings,
) -> bool:
    if not in_kill_zone(settings.kill_zones, use_local=settings.kill_zone_local):
        return False

    if settings.use_news_filter:
        blocked, notes = blocking_news(
            block_minutes=settings.news_block_minutes,
            currencies=("USD",),
            block_high=True,
            block_medium=settings.news_block_medium,
        )
        if blocked:
            logger.debug("Skip: news — %s", "; ".join(notes[:2]))
            return False

    blocked, dd_reason = drawdown_blocks(settings)
    if blocked:
        logger.info("Drawdown guard: %s", dd_reason)
        return False

    ok, reason = can_trade(
        settings.state_file,
        settings.max_trades_day,
        settings.cooldown_sec,
    )
    if not ok:
        logger.debug("Skip: %s", reason)
        return False

    skip, skip_reason = should_skip_slot(settings)
    if skip:
        logger.info("Skip slot: %s", skip_reason)
        return False

    invert = False
    if settings.smart_indicators:
        logger.debug("Smart indicators: vote-based direction")
    else:
        invert, mode_reason = resolve_invert_mode(settings)
        log_mode_if_changed(settings, invert, mode_reason)

    signal = scan_scalp_signal(trader, settings, invert=invert)
    if signal is None:
        return False

    side = Side.BUY if signal.side.value == "BUY" else Side.SELL
    trade_lot = signal.lot if signal.lot > 0 else settings.lot
    ticket = trader.open_market(
        settings.symbol,
        side,
        trade_lot,
        sl=signal.sl,
        tp=signal.tp,
        comment="xau_scalp",
        magic=settings.magic,
    )
    if ticket is None:
        return False

    mark_trade(settings.state_file)
    if settings.smart_indicators:
        trade_mode = "smart"
    else:
        trade_mode = "invert" if invert else "normal"
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    utc_hour = now.hour
    dow = now.weekday()
    sess = session_name(utc_hour, settings.kill_zones)
    _open_meta[ticket] = {
        "side": signal.side.value,
        "reason": signal.reason,
        "mode": trade_mode,
        "utc_hour": utc_hour,
        "dow": dow,
        "session": sess,
        "partial_done": False,
        "confidence": signal.confidence,
        "indicators": signal.indicators or {},
    }
    log_open(
        settings.journal_file,
        ticket=ticket,
        side=signal.side.value,
        symbol=settings.symbol,
        lot=trade_lot,
        entry=signal.entry,
        sl=signal.sl,
        tp=signal.tp,
        reason=signal.reason,
        spread_points=signal.spread_points,
        mode=trade_mode,
        utc_hour=utc_hour,
        dow=dow,
        session=sess,
        indicators=signal.indicators,
    )
    logger.info(
        "SCALP %s %s lot=%s conf=%s entry=%.2f SL=%.2f TP=%.2f | %s",
        signal.side.value,
        settings.symbol,
        trade_lot,
        signal.confidence,
        signal.entry,
        signal.sl,
        signal.tp,
        signal.reason,
    )
    return True


def run_cycle(trader: MT5Trader, settings: XauScalpSettings) -> None:
    if settings.use_web_signals:
        get_cached_web_bias(settings)
    if not trader.ensure_connected():
        logger.warning("MT5 joq — цикл skip (terminal ашық па?)")
        return
    _sync_closed(trader, settings)
    _manage_open(trader, settings)
    _log_wait_status(trader, settings)
    try_open_scalp(trader, settings)
    _log_daily_stats(settings)
