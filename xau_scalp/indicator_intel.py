"""Индикатор × follow/invert талдау — әр индикатор жеке оқылады."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from side import Side
from xau_scalp.analytics import ModeBucket, _pick_mode
from xau_scalp.config import XauScalpSettings
from xau_scalp.dxy_filter import dxy_trend
from xau_scalp.indicators import ema_last, rsi
from xau_scalp.journal import _load_journal
from xau_scalp.liquidity import LiquiditySweep, sweep_to_side
from xau_scalp.web_signals import WebBias
from mt5_trade import MT5Trader

logger = logging.getLogger(__name__)

INDICATOR_NAMES: tuple[str, ...] = ("sweep", "web", "rsi", "ema", "h1", "dxy")
_SWEEP_WEIGHT = 2


@dataclass
class IndicatorProfile:
    name: str
    follow: ModeBucket = field(default_factory=ModeBucket)
    invert: ModeBucket = field(default_factory=ModeBucket)

    @property
    def label(self) -> str:
        labels = {
            "sweep": "Liquidity sweep",
            "web": "Investing.com",
            "rsi": f"RSI",
            "ema": "EMA M5",
            "h1": "EMA H1",
            "dxy": "DXY inverse",
        }
        return labels.get(self.name, self.name)


@dataclass(frozen=True)
class IndicatorVote:
    name: str
    raw: Side
    effective: Side
    invert: bool
    reason: str


@dataclass(frozen=True)
class VoteResult:
    side: Side | None
    votes: tuple[IndicatorVote, ...]
    summary: str
    raw_map: dict[str, str]


def _parse_side(raw: Any) -> Side | None:
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if text == "BUY":
        return Side.BUY
    if text == "SELL":
        return Side.SELL
    return None


def _flip(side: Side) -> Side:
    return Side.BUY if side == Side.SELL else Side.SELL


def side_from_rsi(closes: list[float], period: int) -> Side | None:
    val = rsi(closes, period)
    if val < 50:
        return Side.BUY
    if val > 50:
        return Side.SELL
    return None


def side_from_ema(closes: list[float]) -> Side | None:
    if len(closes) < 22:
        return None
    ema9 = ema_last(closes, 9)
    ema21 = ema_last(closes, 21)
    if ema9 >= ema21:
        return Side.BUY
    return Side.SELL


def dxy_gold_side(trend: str | None) -> Side | None:
    if trend == "bear":
        return Side.BUY
    if trend == "bull":
        return Side.SELL
    return None


def collect_raw_indicators(
    *,
    sweep: LiquiditySweep | None,
    web_bias: WebBias | None,
    m1_closes: list[float],
    m5_closes: list[float],
    h1_closes: list[float],
    rsi_period: int,
    dxy_trend_val: str | None,
    use_web: bool,
    use_rsi: bool,
    use_ema: bool,
    use_h1: bool,
    use_dxy: bool,
) -> dict[str, Side | None]:
    raw: dict[str, Side | None] = {}
    if sweep is not None:
        raw["sweep"] = sweep_to_side(sweep)
    if use_web and web_bias is not None:
        raw["web"] = web_bias.side
    if use_rsi:
        raw["rsi"] = side_from_rsi(m1_closes, rsi_period)
    if use_ema:
        raw["ema"] = side_from_ema(m5_closes)
    if use_h1:
        raw["h1"] = side_from_ema(h1_closes)
    if use_dxy:
        raw["dxy"] = dxy_gold_side(dxy_trend_val)
    return raw


def build_indicator_profiles(journal_file: Path) -> dict[str, IndicatorProfile]:
    profiles: dict[str, IndicatorProfile] = {
        name: IndicatorProfile(name=name) for name in INDICATOR_NAMES
    }
    opens: dict[int, dict[str, Any]] = {}

    for row in _load_journal(journal_file):
        action = row.get("action")
        ticket = row.get("ticket")
        if ticket is None:
            continue
        ticket = int(ticket)
        if action == "OPEN":
            opens[ticket] = row
        elif action == "CLOSE" and ticket in opens:
            open_row = opens[ticket]
            trade_side = _parse_side(open_row.get("side"))
            if trade_side is None:
                continue
            profit = float(row.get("profit", 0.0))
            indicators = open_row.get("indicators") or {}
            if not indicators:
                continue
            for name, raw_val in indicators.items():
                if name not in profiles:
                    continue
                raw = _parse_side(raw_val)
                if raw is None:
                    continue
                bucket = profiles[name].follow if trade_side == raw else profiles[name].invert
                bucket.trades += 1
                bucket.profit += profit
                if profit > 0:
                    bucket.wins += 1
                elif profit < 0:
                    bucket.losses += 1
    return profiles


def resolve_indicator_invert(
    profile: IndicatorProfile,
    min_trades: int,
) -> tuple[bool, str]:
    """True = индикаторды инверттеу керек (Sell→Buy)."""
    choice, kind = _pick_mode(profile.follow, profile.invert, min_trades)
    if choice is None:
        return False, "learn"
    if choice:
        return True, kind
    return False, kind


def resolve_trade_from_votes(
    settings: XauScalpSettings,
    raw_map: dict[str, Side | None],
    *,
    min_weight: float | None = None,
) -> VoteResult:
    profiles = build_indicator_profiles(settings.journal_file)
    votes: list[IndicatorVote] = []
    buy_w = 0.0
    sell_w = 0.0

    for name in INDICATOR_NAMES:
        raw = raw_map.get(name)
        if raw is None:
            continue
        profile = profiles.get(name, IndicatorProfile(name=name))
        invert, kind = resolve_indicator_invert(profile, settings.indicator_min_trades)
        effective = _flip(raw) if invert else raw
        reason = f"{kind}"
        if invert:
            reason = f"inv:{kind}"
        votes.append(
            IndicatorVote(
                name=name,
                raw=raw,
                effective=effective,
                invert=invert,
                reason=reason,
            )
        )
        weight = _SWEEP_WEIGHT if name == "sweep" else 1.0
        if effective == Side.BUY:
            buy_w += weight
        else:
            sell_w += weight

    active = len(votes)
    if active == 0:
        return VoteResult(None, tuple(votes), "no votes", _raw_to_str(raw_map))

    min_w = min_weight if min_weight is not None else settings.indicator_min_weight
    if buy_w == sell_w:
        return VoteResult(None, tuple(votes), f"tie buy={buy_w:.0f} sell={sell_w:.0f}", _raw_to_str(raw_map))
    if max(buy_w, sell_w) < min_w:
        return VoteResult(
            None,
            tuple(votes),
            f"weak consensus buy={buy_w:.0f} sell={sell_w:.0f} need≥{min_w:.0f}",
            _raw_to_str(raw_map),
        )

    side = Side.BUY if buy_w > sell_w else Side.SELL
    parts = []
    for v in votes:
        tag = "inv" if v.invert else "ok"
        parts.append(f"{v.name}:{v.raw.value}→{v.effective.value}({tag})")
    summary = f"vote {side.value} buy={buy_w:.0f} sell={sell_w:.0f} | " + ", ".join(parts)
    return VoteResult(side, tuple(votes), summary, _raw_to_str(raw_map))


def _raw_to_str(raw_map: dict[str, Side | None]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, side in raw_map.items():
        if side is not None:
            out[name] = side.value
    return out


def build_indicator_brain(
    profiles: dict[str, IndicatorProfile],
    min_trades: int,
) -> dict[str, Any]:
    brain: dict[str, Any] = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "indicators": {},
    }
    for name in INDICATOR_NAMES:
        profile = profiles.get(name, IndicatorProfile(name=name))
        invert, kind = resolve_indicator_invert(profile, min_trades)
        best = "invert" if invert else "normal"
        if profile.follow.trades < min_trades and profile.invert.trades < min_trades:
            best = "learn"
        brain["indicators"][name] = {
            "label": profile.label,
            "best_mode": best,
            "reason": kind,
            "follow": {
                "trades": profile.follow.trades,
                "profit": round(profile.follow.profit, 2),
                "avg": round(profile.follow.avg_profit, 2),
            },
            "invert": {
                "trades": profile.invert.trades,
                "profit": round(profile.invert.profit, 2),
                "avg": round(profile.invert.avg_profit, 2),
            },
        }
    return brain


def save_indicator_brain(settings: XauScalpSettings) -> None:
    profiles = build_indicator_profiles(settings.journal_file)
    if not any(p.follow.trades or p.invert.trades for p in profiles.values()):
        return
    brain = build_indicator_brain(profiles, settings.indicator_min_trades)
    path = settings.journal_file.parent / "xau_scalp_indicator_brain.json"
    path.write_text(json.dumps(brain, indent=2, ensure_ascii=False), encoding="utf-8")


def format_indicator_report(settings: XauScalpSettings) -> str:
    profiles = build_indicator_profiles(settings.journal_file)
    if not any(p.follow.trades or p.invert.trades for p in profiles.values()):
        return ""
    lines = ["Индикатор | follow | invert | brain"]
    brain = build_indicator_brain(profiles, settings.indicator_min_trades)
    for name in INDICATOR_NAMES:
        p = profiles[name]
        b = brain["indicators"][name]
        lines.append(
            f"{p.label:16} | {p.follow.profit:+.2f}({p.follow.trades}) | "
            f"{p.invert.profit:+.2f}({p.invert.trades}) | {b['best_mode']}"
        )
    return "\n".join(lines)


def fetch_dxy_trend(trader: MT5Trader, settings: XauScalpSettings) -> str | None:
    if not settings.use_dxy_filter:
        return None
    sym = settings.dxy_symbol or None
    return dxy_trend(trader, sym, settings.dxy_symbol_fallbacks)


def try_consensus_entry(
    settings: XauScalpSettings,
    *,
    web_bias: WebBias | None,
    m1_closes: list[float],
    m5_closes: list[float],
    h1_closes: list[float],
    rsi_period: int,
    dxy_trend_val: str | None,
) -> VoteResult | None:
    """Sweep жоқ — web Strong + EMA/H1 consensus + smart vote."""
    from xau_scalp.web_signals import _meets_min_strength

    if not settings.consensus_entry:
        return None
    if web_bias is None or web_bias.side is None:
        return None
    if not _meets_min_strength(web_bias.strength, settings.web_min_strength):
        return None

    if settings.use_ema_filter:
        ema_side = side_from_ema(m5_closes)
        if ema_side is None or ema_side != web_bias.side:
            return None

    if settings.use_h1_trend and len(h1_closes) >= 25:
        h1_side = side_from_ema(h1_closes)
        if h1_side is None or h1_side != web_bias.side:
            return None

    raw_map = collect_raw_indicators(
        sweep=None,
        web_bias=web_bias,
        m1_closes=m1_closes,
        m5_closes=m5_closes,
        h1_closes=h1_closes,
        rsi_period=rsi_period,
        dxy_trend_val=dxy_trend_val,
        use_web=settings.use_web_signals,
        use_rsi=settings.use_rsi_filter,
        use_ema=settings.use_ema_filter,
        use_h1=settings.use_h1_trend,
        use_dxy=settings.use_dxy_filter,
    )
    vote = resolve_trade_from_votes(settings, raw_map, min_weight=2.0)
    if vote.side is None or vote.side != web_bias.side:
        return None
    return vote
