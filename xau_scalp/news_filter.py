"""USD high-impact жаңалық фильтрі — ForexFactory JSON."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_cache: tuple[float, list[dict[str, Any]]] | None = None


def _parse_event_time(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


def _fetch_calendar(force: bool = False) -> list[dict[str, Any]]:
    global _cache
    now = time.time()
    if not force and _cache is not None and now - _cache[0] < 3600:
        return _cache[1]

    req = Request(_CALENDAR_URL, headers={"User-Agent": "ScalpXau/1.0"})
    try:
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, list):
            data = []
    except Exception as exc:
        logger.warning("Calendar JSON алу сәтсіз: %s", exc)
        return _cache[1] if _cache else []

    _cache = (now, data)
    return data


def blocking_news(
    *,
    block_minutes: int = 15,
    currencies: tuple[str, ...] = ("USD",),
    block_high: bool = True,
    block_medium: bool = False,
    now: datetime | None = None,
) -> tuple[bool, list[str]]:
    """
    Жақын уақытта блоктаушы жаңалық бар ма.
    Returns: (blocked, titles)
    """
    dt_now = now or datetime.now(timezone.utc)
    window = timedelta(minutes=max(1, block_minutes))
    allowed_impacts = set()
    if block_high:
        allowed_impacts.add("High")
    if block_medium:
        allowed_impacts.update({"Medium", "Moderate"})

    notes: list[str] = []
    for ev in _fetch_calendar():
        impact = str(ev.get("impact", "")).strip()
        if impact not in allowed_impacts:
            continue
        country = str(ev.get("country", "")).upper()
        if country not in currencies:
            continue
        event_time = _parse_event_time(str(ev.get("date", "")))
        if event_time is None:
            continue
        if abs(event_time - dt_now) <= window:
            title = str(ev.get("title", "News")).strip()
            notes.append(f"{country} {impact}: {title}")

    return bool(notes), notes
