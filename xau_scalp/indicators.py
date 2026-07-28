"""MT5 OHLC техникалық индикаторлар."""

from __future__ import annotations


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def ema_last(values: list[float], period: int) -> float:
    series = ema(values, period)
    return series[-1] if series else 0.0


def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(bars: list[dict[str, float]], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(bars)):
        h = bars[i]["high"]
        l = bars[i]["low"]
        prev_c = bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    if not trs:
        return 0.0
    window = trs[-period:] if len(trs) >= period else trs
    return sum(window) / len(window)


def swing_highs(highs: list[float], lookback: int = 5, count: int = 3) -> list[float]:
    if not highs:
        return []
    window = highs[-lookback * 4 :] if len(highs) > lookback * 4 else highs
    uniq = sorted(set(window), reverse=True)
    return uniq[:count]


def swing_lows(lows: list[float], lookback: int = 5, count: int = 3) -> list[float]:
    if not lows:
        return []
    window = lows[-lookback * 4 :] if len(lows) > lookback * 4 else lows
    uniq = sorted(set(window))
    return uniq[:count]


def trend_label(price: float, ema20: float, ema50: float, ema200: float) -> str:
    if ema200 <= 0:
        return "Mixed"
    bull = price > ema200 and ema20 > ema50
    bear = price < ema200 and ema20 < ema50
    if bull:
        return "Bull"
    if bear:
        return "Bear"
    return "Mixed"
