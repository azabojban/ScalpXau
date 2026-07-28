"""Сауда журналы — append-only, ешқашан автоматты өшпейді."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Журнал тек қосылады (append). Бот файлды ешқашан тазаламайды.
# Талдау: data/xau_scalp_brain.json, data/reports/monthly_YYYY-MM.json


def _load_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    logger.info("Journal: %s", event.get("action", "event"))


def log_open(
    path: Path,
    *,
    ticket: int,
    side: str,
    symbol: str,
    lot: float,
    entry: float,
    sl: float,
    tp: float,
    reason: str,
    spread_points: float,
    mode: str = "normal",
    utc_hour: int | None = None,
    dow: int | None = None,
    session: str = "",
    indicators: dict[str, str] | None = None,
) -> None:
    if utc_hour is None:
        utc_hour = datetime.now(timezone.utc).hour
    if dow is None:
        dow = datetime.now(timezone.utc).weekday()
    append_event(
        path,
        {
            "action": "OPEN",
            "ticket": ticket,
            "side": side,
            "symbol": symbol,
            "lot": lot,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "reason": reason,
            "spread_points": spread_points,
            "mode": mode,
            "utc_hour": utc_hour,
            "dow": dow,
            "session": session,
            "indicators": indicators or {},
        },
    )


def log_close(
    path: Path,
    *,
    ticket: int,
    side: str,
    symbol: str,
    profit: float,
    reason: str,
    mode: str = "",
    dow: int | None = None,
    session: str = "",
    utc_hour: int | None = None,
) -> None:
    append_event(
        path,
        {
            "action": "CLOSE",
            "ticket": ticket,
            "side": side,
            "symbol": symbol,
            "profit": profit,
            "result": "WIN" if profit > 0 else ("LOSS" if profit < 0 else "BE"),
            "reason": reason,
            "mode": mode,
            "dow": dow,
            "session": session,
            "utc_hour": utc_hour,
        },
    )


def slot_stats_report(settings: "XauScalpSettings") -> str:
    from xau_scalp.analytics import format_slot_report

    return format_slot_report(settings)


def today_stats(path: Path) -> dict[str, Any]:
    today = str(date.today())
    rows = _load_journal(path)
    opens = 0
    wins = 0
    losses = 0
    profit = 0.0
    for row in rows:
        if not str(row.get("ts", "")).startswith(today):
            continue
        if row.get("action") == "OPEN":
            opens += 1
        elif row.get("action") == "CLOSE":
            p = float(row.get("profit", 0.0))
            profit += p
            if p > 0:
                wins += 1
            elif p < 0:
                losses += 1
    closed = wins + losses
    win_rate = (wins / closed * 100.0) if closed else 0.0
    return {
        "opens": opens,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "profit": round(profit, 2),
        "win_rate": round(win_rate, 1),
    }
