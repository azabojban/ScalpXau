"""Kill zone + session анықтау."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_today() -> str:
    """Күндік лимиттер — UTC (demo/prop бірдей)."""
    return str(datetime.now(timezone.utc).date())

_DOW_KZ = ("Дс", "Сс", "Ср", "Бс", "Жм", "Сн", "Жс")


def parse_kill_zones(raw: str) -> list[tuple[int, int]]:
    zones: list[tuple[int, int]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part or "-" not in part:
            continue
        start_s, end_s = part.split("-", 1)
        try:
            start = int(start_s.strip())
            end = int(end_s.strip())
        except ValueError:
            continue
        if 0 <= start < end <= 24:
            zones.append((start, end))
    return zones


def in_kill_zone(
    zones: list[tuple[int, int]],
    now: datetime | None = None,
    *,
    use_local: bool = False,
) -> bool:
    if not zones:
        return True
    if use_local:
        dt = now or datetime.now().astimezone()
        hour = dt.hour
    else:
        dt = now or datetime.now(timezone.utc)
        hour = dt.hour
    return any(start <= hour < end for start, end in zones)


def kill_zone_clock_label(use_local: bool) -> str:
    return "local" if use_local else "UTC"


def session_name(
    hour: int,
    zones: list[tuple[int, int]] | None = None,
) -> str:
    """London / NY / other."""
    if zones:
        for start, end in zones:
            if start <= hour < end:
                if start == 7 and end == 10:
                    return "london"
                if start == 13 and end == 17:
                    return "ny"
                return f"zone_{start}_{end}"
    if 7 <= hour < 10:
        return "london"
    if 13 <= hour < 17:
        return "ny"
    return "other"


def dow_label(dow: int) -> str:
    if 0 <= dow <= 6:
        return _DOW_KZ[dow]
    return str(dow)
