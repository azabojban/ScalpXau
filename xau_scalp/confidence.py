"""Сигнал confidence score (0–100) + lot таңдау."""

from __future__ import annotations

from dataclasses import dataclass

from side import Side
from xau_scalp.analytics import resolve_slot_mode
from xau_scalp.config import XauScalpSettings
from xau_scalp.liquidity import LiquiditySweep
from xau_scalp.web_signals import WebBias, WebSignalStrength


@dataclass
class ConfidenceResult:
    score: int
    lot: float
    notes: list[str]


def _web_points(bias: WebBias | None, ind_side: Side) -> tuple[int, str]:
    if bias is None or bias.side is None:
        return 5, "web:none"
    if bias.side != ind_side:
        return 0, "web:conflict"
    if bias.strength in (WebSignalStrength.STRONG_BUY, WebSignalStrength.STRONG_SELL):
        return 20, "web:strong"
    return 14, "web:ok"


def compute_confidence(
    settings: XauScalpSettings,
    *,
    trade_side: Side,
    ind_side: Side,
    sweep: LiquiditySweep | None,
    spread_pts: float,
    web_bias: WebBias | None,
    h1_ok: bool,
    ema_ok: bool,
    rsi_ok: bool,
    rr: float,
    dxy_ok: bool,
    invert_mode: bool,
    consensus: bool = False,
) -> ConfidenceResult:
    score = 0
    notes: list[str] = []

    if spread_pts <= settings.max_spread_points * 0.5:
        score += 10
        notes.append("spread:tight")
    elif spread_pts <= settings.max_spread_points * 0.8:
        score += 7
        notes.append("spread:ok")
    else:
        score += 4
        notes.append("spread:wide")

    if sweep is not None:
        reason_l = sweep.reason.lower()
        if "equal" in reason_l:
            score += 15
            notes.append("sweep:equal")
        else:
            score += 10
            notes.append("sweep:swing")
    elif consensus:
        score += 14
        notes.append("consensus:web_ema")
    else:
        score += 5
        notes.append("sweep:none")

    wp, wn = _web_points(web_bias, trade_side if consensus else ind_side)
    score += wp
    notes.append(wn)

    if h1_ok:
        score += 15
        notes.append("h1:ok")
    if ema_ok:
        score += 10
        notes.append("ema:ok")
    if rsi_ok:
        score += 10
        notes.append("rsi:ok")

    if dxy_ok:
        score += 10
        notes.append("dxy:ok")
    else:
        notes.append("dxy:against")

    if rr >= 1.8:
        score += 10
        notes.append(f"rr:{rr:.1f}")
    elif rr >= settings.min_rr:
        score += 6
        notes.append(f"rr:{rr:.1f}")

    if settings.adaptive_mode:
        want_inv, _, _ = resolve_slot_mode(settings)
        if want_inv == invert_mode:
            score += 10
            notes.append("brain:match")
        else:
            notes.append("brain:explore")

    if settings.smart_indicators and h1_ok and ema_ok and rsi_ok:
        score += 5
        notes.append("smart:filters_ok")

    score = max(0, min(100, score))

    lot = settings.lot
    if score >= settings.confidence_lot_high:
        lot = settings.lot_high
    elif score < settings.min_confidence:
        lot = 0.0

    return ConfidenceResult(score=score, lot=lot, notes=notes)
