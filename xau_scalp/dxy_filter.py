"""DXY / USDX — gold inverse correlation filter."""

from __future__ import annotations

import logging

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None  # type: ignore

from side import Side
from xau_scalp.indicators import ema_last
from mt5_trade import MT5Trader

logger = logging.getLogger(__name__)


def _resolve_dxy_symbol(trader: MT5Trader, candidates: tuple[str, ...]) -> str | None:
    if mt5 is None or not trader._connected:
        return None
    for sym in candidates:
        if not sym:
            continue
        if mt5.symbol_select(sym, True):
            return sym
    return None


def dxy_trend(trader: MT5Trader, symbol: str | None, candidates: tuple[str, ...]) -> str | None:
    """bull / bear / None."""
    if mt5 is None or not trader._connected:
        return None
    sym = symbol or _resolve_dxy_symbol(trader, candidates)
    if not sym:
        return None

    tf = mt5.TIMEFRAME_M15
    bars = trader.copy_rates(sym, tf, 40)
    if len(bars) < 25:
        return None

    closes = [float(b["close"]) for b in bars]
    ema9 = ema_last(closes, 9)
    ema21 = ema_last(closes, 21)
    if ema9 > ema21 * 1.0005:
        return "bull"
    if ema9 < ema21 * 0.9995:
        return "bear"
    return "flat"


def dxy_allows_gold_side(trade_side: Side, trend: str | None, *, block_flat: bool = False) -> bool:
    """
    Gold BUY ↔ DXY bear (USD әлсіз)
    Gold SELL ↔ DXY bull (USD күшті)
    """
    if trend is None:
        return True
    if trend == "flat":
        return not block_flat
    if trade_side == Side.BUY:
        return trend == "bear"
    return trend == "bull"
