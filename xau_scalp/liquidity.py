"""Likvidlik sweep: swing high/low + false break."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from side import Side


class SweepKind(str, Enum):
    BULLISH = "BULLISH"  # asta likvidlik alindi → BUY
    BEARISH = "BEARISH"  # ustte likvidlik alindi → SELL


@dataclass
class LiquiditySweep:
    kind: SweepKind
    level: float
    sweep_extreme: float
    reason: str


def _recent_swing_high(highs: list[float], end_idx: int, lookback: int = 3) -> float | None:
    """end_idx-тен burin swing high."""
    start = max(lookback, end_idx - 25)
    best: float | None = None
    for i in range(start, end_idx - lookback):
        h = highs[i]
        left = highs[i - lookback : i]
        right = highs[i + 1 : i + lookback + 1]
        if not left or not right:
            continue
        if h >= max(left) and h >= max(right):
            if best is None or h > best:
                best = h
    return best


def _recent_swing_low(lows: list[float], end_idx: int, lookback: int = 3) -> float | None:
    start = max(lookback, end_idx - 25)
    best: float | None = None
    for i in range(start, end_idx - lookback):
        lo = lows[i]
        left = lows[i - lookback : i]
        right = lows[i + 1 : i + lookback + 1]
        if not left or not right:
            continue
        if lo <= min(left) and lo <= min(right):
            if best is None or lo < best:
                best = lo
    return best


def _equal_highs(highs: list[float], tol: float) -> float | None:
    """Zhoqari likvidlik — bir deyngdegi swing high."""
    if len(highs) < 10:
        return None
    window = highs[-30:-3]
    if not window:
        return None
    top = max(window)
    hits = [h for h in window if abs(h - top) <= tol]
    if len(hits) >= 2:
        return top
    return None


def _equal_lows(lows: list[float], tol: float) -> float | None:
    if len(lows) < 10:
        return None
    window = lows[-30:-3]
    if not window:
        return None
    bot = min(window)
    hits = [lo for lo in window if abs(lo - bot) <= tol]
    if len(hits) >= 2:
        return bot
    return None


def detect_sweep(
    bars: list[dict],
    *,
    swing_lookback: int = 3,
    wick_min_points: float,
    point: float,
) -> LiquiditySweep | None:
    """
    Bearish sweep: high > swing_high, close < swing_high → SELL
    Bullish sweep: low < swing_low, close > swing_low → BUY
    """
    if len(bars) < 35:
        return None

    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    tol = wick_min_points * point * 0.3

    end = len(bars) - 1
    cur = bars[end]
    prev = bars[end - 1]

    eq_high = _equal_highs(highs, tol)
    eq_low = _equal_lows(lows, tol)
    swing_high = _recent_swing_high(highs, end, swing_lookback)
    swing_low = _recent_swing_low(lows, end, swing_lookback)

    levels_high = [x for x in (swing_high, eq_high) if x is not None]
    levels_low = [x for x in (swing_low, eq_low) if x is not None]
    liq_high = max(levels_high) if levels_high else None
    liq_low = min(levels_low) if levels_low else None

    wick_min = wick_min_points * point

    # Bearish: ustte stop alindi, kerei tusti
    if liq_high is not None:
        level = liq_high
        swept = cur["high"] > level + wick_min * 0.2 or prev["high"] > level + wick_min * 0.2
        rejected = cur["close"] < level and cur["close"] < cur["open"]
        if swept and rejected:
            tag = "equal highs" if eq_high and abs(eq_high - level) <= tol else "swing high"
            return LiquiditySweep(
                kind=SweepKind.BEARISH,
                level=level,
                sweep_extreme=max(cur["high"], prev["high"]),
                reason=f"Likvidlik sweep ({tag} {level:.2f}) → SELL",
            )

    # Bullish: asta stop alindi, kerei koterildi
    if liq_low is not None:
        level = liq_low
        swept = cur["low"] < level - wick_min * 0.2 or prev["low"] < level - wick_min * 0.2
        rejected = cur["close"] > level and cur["close"] > cur["open"]
        if swept and rejected:
            tag = "equal lows" if eq_low and abs(eq_low - level) <= tol else "swing low"
            return LiquiditySweep(
                kind=SweepKind.BULLISH,
                level=level,
                sweep_extreme=min(cur["low"], prev["low"]),
                reason=f"Likvidlik sweep ({tag} {level:.2f}) → BUY",
            )

    return None


def sweep_to_side(sweep: LiquiditySweep) -> Side:
    return Side.BUY if sweep.kind == SweepKind.BULLISH else Side.SELL
