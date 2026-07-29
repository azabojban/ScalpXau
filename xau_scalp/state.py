"""Күндік лимит + cooldown."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from xau_scalp.session import utc_today


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"date": utc_today(), "trades": 0, "last_trade_ts": 0.0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"date": utc_today(), "trades": 0, "last_trade_ts": 0.0}


def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def can_trade(path: Path, max_day: int, cooldown_sec: int) -> tuple[bool, str]:
    state = _load(path)
    today = utc_today()
    if state.get("date") != today:
        state = {"date": today, "trades": 0, "last_trade_ts": 0.0}
        _save(path, state)

    if int(state.get("trades", 0)) >= max_day:
        return False, f"күндік лимит {max_day}"

    last = float(state.get("last_trade_ts", 0.0))
    wait = cooldown_sec - (time.time() - last)
    if wait > 0:
        return False, f"cooldown {int(wait)} сек"

    return True, ""


def mark_trade(path: Path) -> None:
    state = _load(path)
    today = utc_today()
    if state.get("date") != today:
        state = {"date": today, "trades": 0, "last_trade_ts": 0.0}
    state["trades"] = int(state.get("trades", 0)) + 1
    state["last_trade_ts"] = time.time()
    _save(path, state)
