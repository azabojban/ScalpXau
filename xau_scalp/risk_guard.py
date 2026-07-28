"""Күндік drawdown + recovery режим."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from xau_scalp.config import XauScalpSettings
from xau_scalp.journal import _load_journal, today_stats


def _profit_on_date(journal_file: Path, day: str) -> float:
    profit = 0.0
    for row in _load_journal(journal_file):
        if row.get("action") != "CLOSE":
            continue
        if not str(row.get("ts", "")).startswith(day):
            continue
        profit += float(row.get("profit", 0.0))
    return profit


def drawdown_blocks(settings: XauScalpSettings) -> tuple[bool, str]:
    if not settings.use_drawdown_guard:
        return False, ""
    stats = today_stats(settings.journal_file)
    loss = -stats["profit"]
    if stats["profit"] <= -settings.max_daily_loss:
        return True, f"күндік loss {stats['profit']:.2f} (limit -{settings.max_daily_loss:.2f})"
    return False, ""


def required_min_confidence(settings: XauScalpSettings) -> int:
    if not settings.recovery_mode:
        return settings.min_confidence
    yesterday = str(date.today() - timedelta(days=1))
    y_profit = _profit_on_date(settings.journal_file, yesterday)
    if y_profit < 0:
        return settings.recovery_min_confidence
    return settings.min_confidence
