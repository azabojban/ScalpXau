"""Интернеттен XAUUSD скальп сигнал — Investing.com + MT5 local fallback."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import cloudscraper

from side import Side
from xau_scalp.config import XauScalpSettings
from xau_scalp.indicators import ema_last, rsi

logger = logging.getLogger(__name__)

_INVESTING_URL = "https://aappapi.investing.com/get_screen.php"
_RAPIDAPI_URL = (
    "https://trend-and-strength-api-for-forex-gold-xauusd.p.rapidapi.com"
)

_TIMEFRAME_SECONDS = {
    "5min": "300",
    "15min": "900",
    "30min": "1800",
    "hourly": "3600",
}

_scraper: cloudscraper.CloudScraper | None = None
_cache: tuple[float, "WebBias | None"] | None = None
_last_good: tuple[float, "WebBias"] | None = None


class WebSignalStrength(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


@dataclass(frozen=True)
class WebBias:
    side: Side | None
    strength: WebSignalStrength
    source: str
    summary: str
    timeframe: str
    ma_summary: str = ""
    ti_summary: str = ""
    trend_strength: int | None = None


def _new_scraper() -> cloudscraper.CloudScraper:
    global _scraper
    _scraper = cloudscraper.create_scraper()
    return _scraper


def _get_scraper() -> cloudscraper.CloudScraper:
    if _scraper is None:
        return _new_scraper()
    return _scraper


def _investing_headers() -> dict[str, str]:
    return {"x-meta-ver": "14"}


def _parse_investing_text(text: str) -> tuple[Side | None, WebSignalStrength]:
    raw = (text or "").strip().lower().replace("_", " ")
    if "strong buy" in raw:
        return Side.BUY, WebSignalStrength.STRONG_BUY
    if raw == "buy" or raw.endswith(" buy"):
        return Side.BUY, WebSignalStrength.BUY
    if "strong sell" in raw:
        return Side.SELL, WebSignalStrength.STRONG_SELL
    if raw == "sell" or raw.endswith(" sell"):
        return Side.SELL, WebSignalStrength.SELL
    return None, WebSignalStrength.NEUTRAL


def _meets_min_strength(strength: WebSignalStrength, min_level: str) -> bool:
    if min_level == "strong":
        return strength in (
            WebSignalStrength.STRONG_BUY,
            WebSignalStrength.STRONG_SELL,
        )
    if min_level == "buy_sell":
        return strength in (
            WebSignalStrength.STRONG_BUY,
            WebSignalStrength.BUY,
            WebSignalStrength.STRONG_SELL,
            WebSignalStrength.SELL,
        )
    if min_level == "any":
        return strength != WebSignalStrength.NEUTRAL
    return True


def bias_from_mt5_bars(
    m5: list[dict[str, Any]],
    m15: list[dict[str, Any]],
    *,
    timeframe: str = "15min",
) -> WebBias | None:
    """Investing.com блокталса — MT5 M15/M5 техникалық summary."""
    bars = m15 if len(m15) >= 25 else m5
    if len(bars) < 25:
        return None

    closes = [float(b["close"]) for b in bars]
    ema9 = ema_last(closes, 9)
    ema21 = ema_last(closes, 21)
    rsi_val = rsi(closes, 14)

    if ema9 > ema21 * 1.002 and rsi_val >= 58:
        side, strength, summary = Side.BUY, WebSignalStrength.STRONG_BUY, "Strong Buy"
    elif ema9 > ema21 and rsi_val >= 52:
        side, strength, summary = Side.BUY, WebSignalStrength.BUY, "Buy"
    elif ema9 < ema21 * 0.998 and rsi_val <= 42:
        side, strength, summary = Side.SELL, WebSignalStrength.STRONG_SELL, "Strong Sell"
    elif ema9 < ema21 and rsi_val <= 48:
        side, strength, summary = Side.SELL, WebSignalStrength.SELL, "Sell"
    else:
        side, strength, summary = None, WebSignalStrength.NEUTRAL, "Neutral"

    return WebBias(
        side=side,
        strength=strength,
        source="mt5-local",
        summary=summary,
        timeframe=timeframe,
        ma_summary="Bull" if ema9 > ema21 else ("Bear" if ema9 < ema21 else "Flat"),
        ti_summary=f"RSI={rsi_val:.1f}",
    )


def _fetch_investing(pair_id: str, interval: str) -> WebBias | None:
    tf = _TIMEFRAME_SECONDS.get(interval)
    if not tf:
        return None

    params = {
        "screen_ID": 25,
        "pair_ID": pair_id,
        "lang_ID": 1,
        "additionalTimeframes": "Yes",
    }
    payload = None

    for attempt in range(5):
        scraper = _new_scraper() if attempt else _get_scraper()
        try:
            resp = scraper.get(
                _INVESTING_URL,
                params=params,
                headers=_investing_headers(),
                timeout=20,
            )
            if resp.status_code == 403:
                time.sleep(1.0 + attempt * 0.8)
                continue
            resp.raise_for_status()
            payload = resp.json()
            break
        except Exception:
            if attempt >= 4:
                return None
            time.sleep(1.0 + attempt * 0.8)

    if payload is None:
        return None

    for item in payload.get("data", []):
        screen_data = item.get("screen_data", {})
        for tech in screen_data.get("technical_data", []):
            if str(tech.get("timeframe")) != tf:
                continue
            main = tech.get("main_summary", {})
            summary = (main.get("text") or "").strip()
            side, strength = _parse_investing_text(summary)
            ma_text = (tech.get("ma_summary", {}) or {}).get("ma_text", "")
            ti_text = (tech.get("ti_summary", {}) or {}).get("ti_text", "")
            return WebBias(
                side=side,
                strength=strength,
                source="investing.com",
                summary=summary or "Neutral",
                timeframe=interval,
                ma_summary=str(ma_text or ""),
                ti_summary=str(ti_text or ""),
            )
    return None


def _fetch_rapidapi(timeframe: str) -> WebBias | None:
    api_key = os.getenv("RAPIDAPI_KEY", "").strip()
    if not api_key:
        return None

    tf_map = {"5min": "M5", "15min": "M15", "30min": "M30", "hourly": "H1"}
    tf = tf_map.get(timeframe, "M5")
    try:
        resp = _get_scraper().get(
            f"{_RAPIDAPI_URL}/{tf}",
            headers={
                "x-rapidapi-key": api_key,
                "x-rapidapi-host": "trend-and-strength-api-for-forex-gold-xauusd.p.rapidapi.com",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    trend = str(data.get("trend", "neutral")).lower()
    raw_strength = data.get("strength")
    try:
        trend_strength = int(float(raw_strength))
    except (TypeError, ValueError):
        trend_strength = None

    if trend == "positive":
        if trend_strength and trend_strength >= 76:
            return WebBias(Side.BUY, WebSignalStrength.STRONG_BUY, "rapidapi", "Strong Buy", timeframe, trend_strength=trend_strength)
        return WebBias(Side.BUY, WebSignalStrength.BUY, "rapidapi", "Buy", timeframe, trend_strength=trend_strength)
    if trend == "negative":
        if trend_strength and trend_strength >= 76:
            return WebBias(Side.SELL, WebSignalStrength.STRONG_SELL, "rapidapi", "Strong Sell", timeframe, trend_strength=trend_strength)
        return WebBias(Side.SELL, WebSignalStrength.SELL, "rapidapi", "Sell", timeframe, trend_strength=trend_strength)
    return WebBias(None, WebSignalStrength.NEUTRAL, "rapidapi", "Neutral", timeframe, trend_strength=trend_strength)


def fetch_web_bias(
    settings: XauScalpSettings,
    *,
    m5: list[dict[str, Any]] | None = None,
    m15: list[dict[str, Any]] | None = None,
) -> WebBias | None:
    if not settings.use_web_signals:
        return None

    bias: WebBias | None = None

    if settings.web_source in ("investing", "both"):
        bias = _fetch_investing(settings.investing_pair_id, settings.web_timeframe)
    elif settings.web_source == "rapidapi":
        bias = _fetch_rapidapi(settings.web_timeframe)
    elif settings.web_source == "tradingview":
        bias = None

    if settings.web_source == "both" and bias is None:
        bias = _fetch_rapidapi(settings.web_timeframe)

    if bias is None and m5 and m15:
        bias = bias_from_mt5_bars(m5, m15, timeframe=settings.web_timeframe)
        if bias is not None:
            logger.info(
                "Investing.com блок → MT5 local: %s (RSI=%s)",
                bias.summary,
                bias.ti_summary,
            )

    return bias


def _log_bias(bias: WebBias, note: str = "") -> None:
    extra = ""
    if bias.ma_summary:
        extra += f", MA={bias.ma_summary}"
    if bias.ti_summary:
        extra += f", TI={bias.ti_summary}"
    if bias.trend_strength is not None:
        extra += f", strength={bias.trend_strength}"
    prefix = f"{note} " if note else ""
    logger.info(
        "%sWeb сигнал [%s/%s]: %s%s",
        prefix,
        bias.source,
        bias.timeframe,
        bias.summary,
        extra,
    )


def get_cached_web_bias(
    settings: XauScalpSettings,
    force: bool = False,
    *,
    m5: list[dict[str, Any]] | None = None,
    m15: list[dict[str, Any]] | None = None,
) -> WebBias | None:
    global _cache, _last_good
    if not settings.use_web_signals:
        return None

    now = time.time()
    if not force and _cache is not None:
        cached_at, cached_bias = _cache
        if now - cached_at < settings.web_cache_sec:
            return cached_bias

    bias = fetch_web_bias(settings, m5=m5, m15=m15)
    stale_sec = max(settings.web_cache_sec * 3, 600)

    if bias is not None:
        _last_good = (now, bias)
        _cache = (now, bias)
        _log_bias(bias)
        return bias

    if _last_good is not None and now - _last_good[0] < stale_sec:
        stale = _last_good[1]
        _cache = (now, stale)
        _log_bias(stale, note="(кеш)")
        return stale

    _cache = (now, None)
    logger.info("Web сигнал жоқ — sweep фильтрсіз")
    return None


def web_allows_side(bias: WebBias | None, side: Side, settings: XauScalpSettings) -> bool:
    if not settings.use_web_signals:
        return True
    if bias is None:
        return not settings.web_require_signal

    if bias.side is None:
        return not settings.web_block_neutral

    if bias.side != side:
        return False

    return _meets_min_strength(bias.strength, settings.web_min_strength)
