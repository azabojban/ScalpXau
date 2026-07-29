"""XAUUSD скальпинг конфиг."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from xau_scalp.session import parse_kill_zones

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class XauScalpSettings:
    symbol: str
    symbol_fallbacks: tuple[str, ...]
    magic: int
    lot: float
    poll_seconds: int
    sl_points: float
    tp_points: float
    min_rr: float
    max_spread_points: float
    max_trades_day: int
    cooldown_sec: int
    kill_zones: list[tuple[int, int]]
    kill_zone_local: bool
    max_hold_min: int
    breakeven_r: float
    trail_start_r: float
    trail_points: float
    rsi_period: int
    swing_lookback: int
    sweep_wick_min_points: float
    sl_buffer_points: float
    use_rsi_filter: bool
    use_ema_filter: bool
    use_h1_trend: bool
    use_atr_sltp: bool
    atr_period: int
    atr_sl_mult: float
    atr_tp_mult: float
    use_news_filter: bool
    news_block_minutes: int
    news_block_medium: bool
    use_web_signals: bool
    web_source: str
    web_timeframe: str
    web_cache_sec: int
    web_require_signal: bool
    web_block_neutral: bool
    web_min_strength: str
    investing_pair_id: str
    invert_signals: bool
    adaptive_mode: bool
    adaptive_min_trades: int
    skip_losing_slots: bool
    slot_min_trades: int
    use_drawdown_guard: bool
    max_daily_loss: float
    recovery_mode: bool
    recovery_min_confidence: int
    min_confidence: int
    confidence_lot_high: int
    lot_high: float
    use_dxy_filter: bool
    dxy_symbol: str
    dxy_symbol_fallbacks: tuple[str, ...]
    use_partial_tp: bool
    partial_tp_r: float
    partial_tp_pct: int
    smart_indicators: bool
    indicator_min_trades: int
    indicator_min_weight: float
    consensus_entry: bool
    consensus_min_confidence: int
    dry_run: bool
    state_file: Path
    journal_file: Path

    @classmethod
    def load(cls) -> "XauScalpSettings":
        data_dir = _ROOT / "data"
        data_dir.mkdir(exist_ok=True)
        kill_raw = os.getenv("XAU_SCALP_KILL_ZONES", "7-10,13-17").strip()
        zones = parse_kill_zones(kill_raw)
        if not zones:
            start = int(os.getenv("XAU_SCALP_SESSION_START_UTC", "7"))
            end = int(os.getenv("XAU_SCALP_SESSION_END_UTC", "10"))
            zones = [(start, end)]

        return cls(
            symbol=os.getenv("XAU_SCALP_SYMBOL", os.getenv("MT5_SYMBOL_GOLD", "XAUUSD")).strip(),
            symbol_fallbacks=tuple(
                s.strip()
                for s in os.getenv(
                    "XAU_SCALP_SYMBOL_FALLBACKS",
                    "XAUUSD.m,XAUUSD.,GOLD",
                ).split(",")
                if s.strip()
            ),
            magic=int(os.getenv("XAU_SCALP_MAGIC", "202609")),
            lot=float(os.getenv("XAU_SCALP_LOT", os.getenv("DEFAULT_LOT", "0.01"))),
            poll_seconds=int(os.getenv("XAU_SCALP_POLL_SECONDS", "15")),
            sl_points=float(os.getenv("XAU_SCALP_SL_POINTS", "250")),
            tp_points=float(os.getenv("XAU_SCALP_TP_POINTS", "375")),
            min_rr=float(os.getenv("XAU_SCALP_MIN_RR", "1.3")),
            max_spread_points=float(os.getenv("XAU_SCALP_MAX_SPREAD_POINTS", "35")),
            max_trades_day=int(os.getenv("XAU_SCALP_MAX_TRADES_DAY", "5")),
            cooldown_sec=int(os.getenv("XAU_SCALP_COOLDOWN_SEC", "300")),
            kill_zones=zones,
            kill_zone_local=_bool("XAU_SCALP_KILL_ZONE_LOCAL", False),
            max_hold_min=int(os.getenv("XAU_SCALP_MAX_HOLD_MIN", "30")),
            breakeven_r=float(os.getenv("XAU_SCALP_BREAKEVEN_R", "0.7")),
            trail_start_r=float(os.getenv("XAU_SCALP_TRAIL_START_R", "1.0")),
            trail_points=float(os.getenv("XAU_SCALP_TRAIL_POINTS", "150")),
            rsi_period=int(os.getenv("XAU_SCALP_RSI_PERIOD", "7")),
            swing_lookback=int(os.getenv("XAU_SCALP_SWING_LOOKBACK", "3")),
            sweep_wick_min_points=float(os.getenv("XAU_SCALP_SWEEP_WICK_POINTS", "30")),
            sl_buffer_points=float(os.getenv("XAU_SCALP_SL_BUFFER_POINTS", "40")),
            use_rsi_filter=_bool("XAU_SCALP_USE_RSI_FILTER", True),
            use_ema_filter=_bool("XAU_SCALP_USE_EMA_FILTER", True),
            use_h1_trend=_bool("XAU_SCALP_USE_H1_TREND", True),
            use_atr_sltp=_bool("XAU_SCALP_USE_ATR_SLTP", True),
            atr_period=int(os.getenv("XAU_SCALP_ATR_PERIOD", "14")),
            atr_sl_mult=float(os.getenv("XAU_SCALP_ATR_SL_MULT", "1.2")),
            atr_tp_mult=float(os.getenv("XAU_SCALP_ATR_TP_MULT", "1.8")),
            use_news_filter=_bool("XAU_SCALP_USE_NEWS_FILTER", True),
            news_block_minutes=int(os.getenv("XAU_SCALP_NEWS_BLOCK_MIN", "15")),
            news_block_medium=_bool("XAU_SCALP_NEWS_BLOCK_MEDIUM", False),
            use_web_signals=_bool("XAU_SCALP_USE_WEB_SIGNALS", True),
            web_source=os.getenv("XAU_SCALP_WEB_SOURCE", "investing").strip().lower(),
            web_timeframe=os.getenv("XAU_SCALP_WEB_TIMEFRAME", "15min").strip().lower(),
            web_cache_sec=int(os.getenv("XAU_SCALP_WEB_CACHE_SEC", "120")),
            web_require_signal=_bool("XAU_SCALP_WEB_REQUIRE", False),
            web_block_neutral=_bool("XAU_SCALP_WEB_BLOCK_NEUTRAL", True),
            web_min_strength=os.getenv("XAU_SCALP_WEB_MIN_STRENGTH", "strong").strip().lower(),
            investing_pair_id=os.getenv("XAU_SCALP_INVESTING_PAIR_ID", "8830").strip(),
            invert_signals=_bool("XAU_SCALP_INVERT_SIGNALS", False),
            adaptive_mode=_bool("XAU_SCALP_ADAPTIVE", True),
            adaptive_min_trades=int(os.getenv("XAU_SCALP_ADAPTIVE_MIN_TRADES", "2")),
            skip_losing_slots=_bool("XAU_SCALP_SKIP_LOSING_SLOTS", True),
            slot_min_trades=int(os.getenv("XAU_SCALP_SLOT_MIN_TRADES", "4")),
            use_drawdown_guard=_bool("XAU_SCALP_DRAWDOWN_GUARD", True),
            max_daily_loss=float(os.getenv("XAU_SCALP_MAX_DAILY_LOSS", "15")),
            recovery_mode=_bool("XAU_SCALP_RECOVERY_MODE", True),
            recovery_min_confidence=int(os.getenv("XAU_SCALP_RECOVERY_MIN_SCORE", "80")),
            min_confidence=int(os.getenv("XAU_SCALP_MIN_CONFIDENCE", "60")),
            confidence_lot_high=int(os.getenv("XAU_SCALP_CONFIDENCE_LOT_HIGH", "80")),
            lot_high=float(os.getenv("XAU_SCALP_LOT_HIGH", "0.02")),
            use_dxy_filter=_bool("XAU_SCALP_USE_DXY_FILTER", True),
            dxy_symbol=os.getenv("XAU_SCALP_DXY_SYMBOL", "").strip(),
            dxy_symbol_fallbacks=tuple(
                s.strip()
                for s in os.getenv(
                    "XAU_SCALP_DXY_FALLBACKS",
                    "USDX,DXY,USDIndex,Dollar Index",
                ).split(",")
                if s.strip()
            ),
            use_partial_tp=_bool("XAU_SCALP_PARTIAL_TP", True),
            partial_tp_r=float(os.getenv("XAU_SCALP_PARTIAL_TP_R", "1.0")),
            partial_tp_pct=int(os.getenv("XAU_SCALP_PARTIAL_TP_PCT", "50")),
            smart_indicators=_bool("XAU_SCALP_SMART_INDICATORS", False),
            indicator_min_trades=int(os.getenv("XAU_SCALP_INDICATOR_MIN_TRADES", "3")),
            indicator_min_weight=float(os.getenv("XAU_SCALP_INDICATOR_MIN_WEIGHT", "3")),
            consensus_entry=_bool("XAU_SCALP_CONSENSUS_ENTRY", False),
            consensus_min_confidence=int(os.getenv("XAU_SCALP_CONSENSUS_MIN_CONFIDENCE", "70")),
            dry_run=_bool("DRY_RUN", True),
            state_file=data_dir / "xau_scalp_state.json",
            journal_file=data_dir / "xau_scalp_journal.jsonl",
        )
