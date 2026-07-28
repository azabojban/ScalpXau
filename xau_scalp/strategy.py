"""Скальп сигнал: likvidlik sweep + kill zone + news + web + ATR SL/TP."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None  # type: ignore

from side import Side
from xau_scalp.config import XauScalpSettings
from xau_scalp.indicators import atr, ema_last, rsi
from xau_scalp.confidence import compute_confidence
from xau_scalp.dxy_filter import dxy_allows_gold_side
from xau_scalp.indicator_intel import (
    collect_raw_indicators,
    fetch_dxy_trend,
    resolve_trade_from_votes,
    try_consensus_entry,
)
from xau_scalp.liquidity import LiquiditySweep, detect_sweep, sweep_to_side
from xau_scalp.news_filter import blocking_news
from xau_scalp.risk_guard import drawdown_blocks, required_min_confidence
from xau_scalp.session import in_kill_zone
from xau_scalp.web_signals import get_cached_web_bias, web_allows_side
from mt5_trade import MT5Trader

logger = logging.getLogger(__name__)


class ScalpBias(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class ScalpSignal:
    side: ScalpBias
    entry: float
    sl: float
    tp: float
    reason: str
    spread_points: float
    confidence: int = 0
    lot: float = 0.01
    indicators: dict[str, str] | None = None
    vote_summary: str = ""


def _spread_points(trader: MT5Trader, symbol: str) -> float:
    point = trader.symbol_point(symbol)
    if point <= 0:
        return 9999.0
    return trader.symbol_spread(symbol) / point


def _rsi_filter_ok(side: Side, m1_closes: list[float], rsi_period: int) -> bool:
    rsi_now = rsi(m1_closes, rsi_period)
    if side == Side.BUY:
        return rsi_now < 55
    return rsi_now > 45


def _ema_trend_ok(side: Side, m5_closes: list[float]) -> bool:
    ema9 = ema_last(m5_closes, 9)
    ema21 = ema_last(m5_closes, 21)
    if side == Side.BUY:
        return ema9 >= ema21 * 0.999
    return ema9 <= ema21 * 1.001


def _h1_trend_ok(side: Side, h1_closes: list[float]) -> bool:
    if len(h1_closes) < 25:
        return True
    ema9 = ema_last(h1_closes, 9)
    ema21 = ema_last(h1_closes, 21)
    if side == Side.BUY:
        return ema9 >= ema21
    return ema9 <= ema21


def _news_blocks(settings: XauScalpSettings) -> tuple[bool, list[str]]:
    if not settings.use_news_filter:
        return False, []
    return blocking_news(
        block_minutes=settings.news_block_minutes,
        currencies=("USD",),
        block_high=True,
        block_medium=settings.news_block_medium,
    )


def _flip_side(side: Side) -> Side:
    return Side.BUY if side == Side.SELL else Side.SELL


def _apply_invert(
    side: Side,
    entry: float,
    sl: float,
    tp: float,
    bid: float,
    ask: float,
) -> tuple[Side, float, float, float]:
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    flipped = _flip_side(side)
    if flipped == Side.BUY:
        entry = ask
        sl = entry - risk
        tp = entry + reward
    else:
        entry = bid
        sl = entry + risk
        tp = entry - reward
    return flipped, entry, sl, tp


def _build_atr_levels(
    side: Side,
    m5: list[dict],
    settings: XauScalpSettings,
    point: float,
    bid: float,
    ask: float,
) -> tuple[float, float, float, float] | None:
    atr_val = atr(m5, settings.atr_period)
    if settings.use_atr_sltp and atr_val > 0:
        max_sl_dist = atr_val * settings.atr_sl_mult
        tp_dist = atr_val * settings.atr_tp_mult
    else:
        max_sl_dist = settings.sl_points * point
        tp_dist = settings.tp_points * point

    sl_buffer = settings.sl_buffer_points * point
    recent = m5[-12:]
    recent_low = min(b["low"] for b in recent)
    recent_high = max(b["high"] for b in recent)

    if side == Side.BUY:
        entry = ask
        sl = recent_low - sl_buffer
        if entry - sl > max_sl_dist:
            sl = entry - max_sl_dist
        tp = entry + tp_dist
    else:
        entry = bid
        sl = recent_high + sl_buffer
        if sl - entry > max_sl_dist:
            sl = entry + max_sl_dist
        tp = entry - tp_dist

    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0 or reward / risk < settings.min_rr:
        return None
    return entry, sl, tp, reward / risk


def scan_scalp_signal(
    trader: MT5Trader,
    settings: XauScalpSettings,
    *,
    invert: bool | None = None,
) -> ScalpSignal | None:
    symbol = settings.symbol

    if not in_kill_zone(settings.kill_zones):
        return None

    blocked, news_notes = _news_blocks(settings)
    if blocked:
        logger.debug("Skip: news block — %s", "; ".join(news_notes[:2]))
        return None

    spread_pts = _spread_points(trader, symbol)
    if spread_pts > settings.max_spread_points:
        return None

    if trader.open_positions_count(symbol, settings.magic) > 0:
        return None

    tf_m1 = mt5.TIMEFRAME_M1 if mt5 else 1
    tf_m5 = mt5.TIMEFRAME_M5 if mt5 else 5
    tf_m15 = mt5.TIMEFRAME_M15 if mt5 else 15
    tf_h1 = mt5.TIMEFRAME_H1 if mt5 else 60

    m1 = trader.copy_rates(symbol, tf_m1, 120)
    m5 = trader.copy_rates(symbol, tf_m5, 80)
    m15 = trader.copy_rates(symbol, tf_m15, 60)
    h1 = trader.copy_rates(symbol, tf_h1, 60)
    if len(m1) < 30 or len(m5) < 35:
        return None

    point = trader.symbol_point(symbol)
    m5_closes = [b["close"] for b in m5]
    m1_closes = [b["close"] for b in m1]
    h1_closes = [b["close"] for b in h1]

    bid = trader.tick_bid(symbol)
    ask = trader.tick_ask(symbol)
    if bid is None or ask is None:
        return None

    sweep = detect_sweep(
        m5,
        swing_lookback=settings.swing_lookback,
        wick_min_points=settings.sweep_wick_min_points,
        point=point,
    )

    web_bias = get_cached_web_bias(settings, m5=m5, m15=m15 if len(m15) >= 25 else m5)
    dxy_tr = fetch_dxy_trend(trader, settings)
    vote_summary = ""
    indicator_map: dict[str, str] = {}
    entry_kind = "sweep"
    use_invert = settings.invert_signals if invert is None else invert
    if settings.smart_indicators:
        use_invert = False

    side: Side
    ind_side: Side

    if sweep is not None:
        ind_side = sweep_to_side(sweep)
        side = ind_side

        if settings.smart_indicators:
            raw_map = collect_raw_indicators(
                sweep=sweep,
                web_bias=web_bias,
                m1_closes=m1_closes,
                m5_closes=m5_closes,
                h1_closes=h1_closes,
                rsi_period=settings.rsi_period,
                dxy_trend_val=dxy_tr,
                use_web=settings.use_web_signals,
                use_rsi=settings.use_rsi_filter,
                use_ema=settings.use_ema_filter,
                use_h1=settings.use_h1_trend,
                use_dxy=settings.use_dxy_filter,
            )
            vote = resolve_trade_from_votes(settings, raw_map)
            if vote.side is None:
                logger.debug("Skip smart vote: %s", vote.summary)
                return None
            side = vote.side
            vote_summary = vote.summary
            indicator_map = vote.raw_map
        else:
            filter_side = _flip_side(ind_side) if use_invert else ind_side

            if not web_allows_side(web_bias, ind_side, settings):
                return None

            if settings.use_rsi_filter and not _rsi_filter_ok(filter_side, m1_closes, settings.rsi_period):
                return None
            if settings.use_ema_filter and not _ema_trend_ok(filter_side, m5_closes):
                return None
            if settings.use_h1_trend and not _h1_trend_ok(filter_side, h1_closes):
                return None

            raw_map = collect_raw_indicators(
                sweep=sweep,
                web_bias=web_bias,
                m1_closes=m1_closes,
                m5_closes=m5_closes,
                h1_closes=h1_closes,
                rsi_period=settings.rsi_period,
                dxy_trend_val=dxy_tr,
                use_web=settings.use_web_signals,
                use_rsi=settings.use_rsi_filter,
                use_ema=settings.use_ema_filter,
                use_h1=settings.use_h1_trend,
                use_dxy=settings.use_dxy_filter,
            )
            indicator_map = {k: v.value for k, v in raw_map.items() if v is not None}
    else:
        consensus = try_consensus_entry(
            settings,
            web_bias=web_bias,
            m1_closes=m1_closes,
            m5_closes=m5_closes,
            h1_closes=h1_closes,
            rsi_period=settings.rsi_period,
            dxy_trend_val=dxy_tr,
        )
        if consensus is None:
            logger.debug("Skip: sweep жоқ, consensus жоқ")
            return None
        side = consensus.side
        ind_side = side
        entry_kind = "consensus"
        vote_summary = consensus.summary
        indicator_map = consensus.raw_map
        logger.info("Consensus entry: %s | %s", side.value, vote_summary)

    sl_buffer = settings.sl_buffer_points * point
    atr_val = atr(m5, settings.atr_period)
    if settings.use_atr_sltp and atr_val > 0:
        max_sl_dist = atr_val * settings.atr_sl_mult
        tp_dist = atr_val * settings.atr_tp_mult
    else:
        max_sl_dist = settings.sl_points * point
        tp_dist = settings.tp_points * point

    rr = 0.0
    if sweep is not None:
        if ind_side == Side.BUY:
            entry = ask
            sl = sweep.sweep_extreme - sl_buffer
            if entry - sl > max_sl_dist:
                sl = entry - max_sl_dist
            tp = entry + tp_dist
        else:
            entry = bid
            sl = sweep.sweep_extreme + sl_buffer
            if sl - entry > max_sl_dist:
                sl = entry + max_sl_dist
            tp = entry - tp_dist

        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk <= 0 or reward / risk < settings.min_rr:
            return None
        rr = reward / risk

        if use_invert or (settings.smart_indicators and side != ind_side):
            side, entry, sl, tp = _apply_invert(ind_side, entry, sl, tp, bid, ask)
    else:
        built = _build_atr_levels(side, m5, settings, point, bid, ask)
        if built is None:
            return None
        entry, sl, tp, rr = built

    trade_side = side
    h1_trade_ok = _h1_trend_ok(trade_side, h1_closes) if settings.use_h1_trend else True
    ema_trade_ok = _ema_trend_ok(trade_side, m5_closes) if settings.use_ema_filter else True
    rsi_trade_ok = _rsi_filter_ok(trade_side, m1_closes, settings.rsi_period) if settings.use_rsi_filter else True

    dxy_ok = True
    if settings.use_dxy_filter and not settings.smart_indicators:
        dxy_ok = dxy_allows_gold_side(trade_side, dxy_tr)
        if not dxy_ok:
            logger.debug("Skip: DXY %s gold %s", dxy_tr, trade_side.value)
            return None
    elif settings.use_dxy_filter and dxy_tr is not None:
        dxy_ok = dxy_allows_gold_side(trade_side, dxy_tr)

    conf = compute_confidence(
        settings,
        trade_side=trade_side,
        ind_side=ind_side,
        sweep=sweep,
        spread_pts=spread_pts,
        web_bias=web_bias,
        h1_ok=h1_trade_ok,
        ema_ok=ema_trade_ok,
        rsi_ok=rsi_trade_ok,
        rr=rr,
        dxy_ok=dxy_ok,
        invert_mode=use_invert,
        consensus=entry_kind == "consensus",
    )
    min_score = required_min_confidence(settings)
    if entry_kind == "consensus":
        min_score = max(min_score, settings.consensus_min_confidence)
    if conf.score < min_score:
        logger.debug("Skip confidence %s < %s (%s)", conf.score, min_score, ",".join(conf.notes))
        return None
    if conf.lot <= 0:
        return None

    bias = ScalpBias.BUY if trade_side == Side.BUY else ScalpBias.SELL
    if entry_kind == "consensus":
        reason = f"Consensus web+EMA → {bias.value}"
    else:
        reason = sweep.reason if sweep else f"Entry → {bias.value}"
    if settings.use_atr_sltp and atr_val > 0:
        reason += f" | ATR SL×{settings.atr_sl_mult} TP×{settings.atr_tp_mult}"
    if web_bias and web_bias.side is not None:
        reason = f"{reason} | web:{web_bias.summary} ({web_bias.source})"
    if use_invert:
        ind_label = "BUY" if ind_side == Side.BUY else "SELL"
        reason += f" | INVERT ind={ind_label}→{bias.value}"
    elif settings.smart_indicators and vote_summary:
        reason += f" | SMART {vote_summary}"
    if entry_kind == "consensus":
        reason += " | no-sweep"
    if dxy_tr:
        reason += f" | DXY:{dxy_tr}"
    reason += f" | conf={conf.score} lot={conf.lot}"

    return ScalpSignal(
        side=bias,
        entry=entry,
        sl=sl,
        tp=tp,
        reason=reason,
        spread_points=spread_pts,
        confidence=conf.score,
        lot=conf.lot,
        indicators=indicator_map or None,
        vote_summary=vote_summary,
    )
