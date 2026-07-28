"""Журнал талдау: күн×session×mode, айлық қорытынды, brain."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from xau_scalp.config import XauScalpSettings
from xau_scalp.journal import _load_journal
from xau_scalp.session import dow_label, session_name

logger = logging.getLogger(__name__)

_DOW_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


@dataclass
class ModeBucket:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    profit: float = 0.0

    @property
    def avg_profit(self) -> float:
        return self.profit / self.trades if self.trades else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0


@dataclass
class SlotProfile:
    dow: int
    session: str
    normal: ModeBucket = field(default_factory=ModeBucket)
    invert: ModeBucket = field(default_factory=ModeBucket)

    @property
    def slot_key(self) -> str:
        return f"{self.dow}:{self.session}"

    @property
    def label(self) -> str:
        return f"{dow_label(self.dow)} {self.session.upper()}"


@dataclass
class ClosedTrade:
    ticket: int
    opened_at: datetime
    closed_at: datetime
    dow: int
    utc_hour: int
    session: str
    mode: str
    side: str
    profit: float
    month: str
    reason: str = ""


def _parse_ts(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _trade_mode(reason: str, explicit: str | None = None) -> str:
    if explicit in ("normal", "invert"):
        return explicit
    return "invert" if "INVERT" in (reason or "").upper() else "normal"


def iter_closed_trades(
    journal_file: Path,
    zones: list[tuple[int, int]] | None = None,
) -> Iterator[ClosedTrade]:
    rows = _load_journal(journal_file)
    opens: dict[int, dict[str, Any]] = {}

    for row in rows:
        action = row.get("action")
        ticket = row.get("ticket")
        if ticket is None:
            continue
        ticket = int(ticket)
        if action == "OPEN":
            opens[ticket] = row
        elif action == "CLOSE" and ticket in opens:
            open_row = opens[ticket]
            opened = _parse_ts(str(open_row.get("ts", "")))
            closed = _parse_ts(str(row.get("ts", "")))
            if opened is None:
                continue
            if closed is None:
                closed = datetime.now(timezone.utc)
            hour = int(open_row.get("utc_hour", opened.hour))
            dow = int(open_row.get("dow", opened.weekday()))
            sess = str(open_row.get("session", session_name(hour, zones)))
            mode = _trade_mode(str(open_row.get("reason", "")), open_row.get("mode"))
            yield ClosedTrade(
                ticket=ticket,
                opened_at=opened,
                closed_at=closed,
                dow=dow,
                utc_hour=hour,
                session=sess,
                mode=mode,
                side=str(open_row.get("side", "")),
                profit=float(row.get("profit", 0.0)),
                month=opened.strftime("%Y-%m"),
                reason=str(open_row.get("reason", "")),
            )


def build_slot_profiles(
    journal_file: Path,
    zones: list[tuple[int, int]] | None = None,
) -> dict[str, SlotProfile]:
    profiles: dict[str, SlotProfile] = {}
    for trade in iter_closed_trades(journal_file, zones):
        key = f"{trade.dow}:{trade.session}"
        profile = profiles.setdefault(key, SlotProfile(dow=trade.dow, session=trade.session))
        bucket = profile.invert if trade.mode == "invert" else profile.normal
        bucket.trades += 1
        bucket.profit += trade.profit
        if trade.profit > 0:
            bucket.wins += 1
        elif trade.profit < 0:
            bucket.losses += 1
    return profiles


def _pick_mode(n: ModeBucket, inv: ModeBucket, min_tr: int) -> tuple[bool | None, str]:
    """None = explore керек."""
    if n.trades < min_tr and inv.trades < min_tr:
        return None, "explore"
    if n.trades < min_tr:
        return False, "explore_normal"
    if inv.trades < min_tr:
        return True, "explore_invert"
    if inv.avg_profit > n.avg_profit:
        return True, "learn_invert"
    if n.avg_profit > inv.avg_profit:
        return False, "learn_normal"
    if inv.win_rate > n.win_rate:
        return True, "learn_invert_wr"
    return False, "learn_normal_wr"


def resolve_slot_mode(
    settings: XauScalpSettings,
    now: datetime | None = None,
) -> tuple[bool, str, str]:
    """
    (invert?, reason, slot_label)
    """
    if not settings.adaptive_mode:
        return settings.invert_signals, "env", "-"

    dt = now or datetime.now(timezone.utc)
    dow = dt.weekday()
    hour = dt.hour
    sess = session_name(hour, settings.kill_zones)
    key = f"{dow}:{sess}"
    profiles = build_slot_profiles(settings.journal_file, settings.kill_zones)
    profile = profiles.get(key, SlotProfile(dow=dow, session=sess))
    min_tr = settings.adaptive_min_trades

    choice, kind = _pick_mode(profile.normal, profile.invert, min_tr)
    label = profile.label

    if choice is None:
        explore = (dow + hour) % 2 == 1
        return explore, f"{kind} {label}", label

    n, inv = profile.normal, profile.invert
    if kind == "explore_normal":
        return False, f"test normal {label} (inv avg={inv.avg_profit:.2f})", label
    if kind == "explore_invert":
        return True, f"test invert {label} (norm avg={n.avg_profit:.2f})", label
    if choice:
        return True, (
            f"{kind} {label}: invert avg={inv.avg_profit:.2f} "
            f"({inv.trades}tr) vs normal avg={n.avg_profit:.2f}"
        ), label
    return False, (
        f"{kind} {label}: normal avg={n.avg_profit:.2f} "
        f"({n.trades}tr) vs invert avg={inv.avg_profit:.2f}"
    ), label


def should_skip_slot(settings: XauScalpSettings, now: datetime | None = None) -> tuple[bool, str]:
    if not settings.skip_losing_slots:
        return False, ""
    dt = now or datetime.now(timezone.utc)
    key = f"{dt.weekday()}:{session_name(dt.hour, settings.kill_zones)}"
    profiles = build_slot_profiles(settings.journal_file, settings.kill_zones)
    profile = profiles.get(key)
    if profile is None:
        return False, ""

    min_total = settings.slot_min_trades
    total = profile.normal.trades + profile.invert.trades
    if total < min_total:
        return False, ""

    if profile.normal.profit < 0 and profile.invert.profit < 0:
        if profile.normal.trades >= settings.adaptive_min_trades and profile.invert.trades >= settings.adaptive_min_trades:
            return True, (
                f"skip {profile.label}: normal={profile.normal.profit:.2f} "
                f"invert={profile.invert.profit:.2f}"
            )
    return False, ""


def build_monthly_summary(journal_file: Path, zones: list[tuple[int, int]] | None = None) -> dict[str, Any]:
    months: dict[str, dict[str, Any]] = {}
    for trade in iter_closed_trades(journal_file, zones):
        month = months.setdefault(
            trade.month,
            {
                "month": trade.month,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "profit": 0.0,
                "slots": {},
                "losing_slots": [],
                "best_slots": [],
            },
        )
        month["trades"] += 1
        month["profit"] += trade.profit
        if trade.profit > 0:
            month["wins"] += 1
        elif trade.profit < 0:
            month["losses"] += 1

        slot_key = f"{trade.dow}:{trade.session}:{trade.mode}"
        slot = month["slots"].setdefault(
            slot_key,
            {
                "dow": trade.dow,
                "dow_name": _DOW_NAMES[trade.dow],
                "dow_kz": dow_label(trade.dow),
                "session": trade.session,
                "mode": trade.mode,
                "trades": 0,
                "profit": 0.0,
                "wins": 0,
                "losses": 0,
            },
        )
        slot["trades"] += 1
        slot["profit"] = round(slot["profit"] + trade.profit, 2)
        if trade.profit > 0:
            slot["wins"] += 1
        elif trade.profit < 0:
            slot["losses"] += 1

    for month_data in months.values():
        month_data["profit"] = round(month_data["profit"], 2)
        slots_list = list(month_data["slots"].values())
        month_data["losing_slots"] = [
            s for s in slots_list if s["profit"] < 0 and s["trades"] >= 2
        ]
        month_data["best_slots"] = sorted(
            [s for s in slots_list if s["profit"] > 0],
            key=lambda x: x["profit"],
            reverse=True,
        )[:5]
        month_data["slots"] = sorted(
            slots_list,
            key=lambda x: (x["dow"], x["session"], x["mode"]),
        )

    return {"months": sorted(months.values(), key=lambda m: m["month"])}


def build_brain(profiles: dict[str, SlotProfile], min_tr: int) -> dict[str, Any]:
    brain: dict[str, Any] = {"updated": datetime.now(timezone.utc).isoformat(), "slots": {}}
    for key, profile in sorted(profiles.items()):
        invert, kind = _pick_mode(profile.normal, profile.invert, min_tr)
        if invert is None:
            best = "explore"
        else:
            best = "invert" if invert else "normal"
        brain["slots"][key] = {
            "label": profile.label,
            "dow": profile.dow,
            "dow_kz": dow_label(profile.dow),
            "session": profile.session,
            "best_mode": best,
            "reason": kind,
            "normal": {
                "trades": profile.normal.trades,
                "profit": round(profile.normal.profit, 2),
                "avg": round(profile.normal.avg_profit, 2),
            },
            "invert": {
                "trades": profile.invert.trades,
                "profit": round(profile.invert.profit, 2),
                "avg": round(profile.invert.avg_profit, 2),
            },
            "skip": (
                profile.normal.profit < 0
                and profile.invert.profit < 0
                and profile.normal.trades >= min_tr
                and profile.invert.trades >= min_tr
            ),
        }
    return brain


def save_reports(settings: XauScalpSettings) -> None:
    data_dir = settings.journal_file.parent
    reports_dir = data_dir / "reports"
    reports_dir.mkdir(exist_ok=True)

    profiles = build_slot_profiles(settings.journal_file, settings.kill_zones)
    if profiles:
        brain = build_brain(profiles, settings.adaptive_min_trades)
        brain_path = data_dir / "xau_scalp_brain.json"
        brain_path.write_text(json.dumps(brain, indent=2, ensure_ascii=False), encoding="utf-8")

        slot_rows = []
        for key in sorted(profiles):
            p = profiles[key]
            slot_rows.append(
                {
                    "slot": p.label,
                    "key": key,
                    "normal_profit": round(p.normal.profit, 2),
                    "normal_trades": p.normal.trades,
                    "invert_profit": round(p.invert.profit, 2),
                    "invert_trades": p.invert.trades,
                    "brain": brain["slots"].get(key, {}).get("best_mode", "?"),
                }
            )
        (data_dir / "xau_scalp_slots.json").write_text(
            json.dumps(slot_rows, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    summary = build_monthly_summary(settings.journal_file, settings.kill_zones)
    if summary["months"]:
        all_path = reports_dir / "monthly_all.json"
        all_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        for month_data in summary["months"]:
            month_path = reports_dir / f"monthly_{month_data['month']}.json"
            month_path.write_text(
                json.dumps(month_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )


def format_slot_report(settings: XauScalpSettings) -> str:
    profiles = build_slot_profiles(settings.journal_file, settings.kill_zones)
    if not profiles:
        return ""
    lines = ["Слот (күн×session) | normal | invert | brain"]
    brain = build_brain(profiles, settings.adaptive_min_trades)
    for key in sorted(profiles):
        p = profiles[key]
        b = brain["slots"].get(key, {})
        lines.append(
            f"{p.label:14} | {p.normal.profit:+.2f}({p.normal.trades}) | "
            f"{p.invert.profit:+.2f}({p.invert.trades}) | {b.get('best_mode', '?')}"
        )
    return "\n".join(lines)


def format_monthly_losses(settings: XauScalpSettings) -> str:
    summary = build_monthly_summary(settings.journal_file, settings.kill_zones)
    if not summary["months"]:
        return ""
    lines = ["Айлық минус слоттар:"]
    for month_data in summary["months"][-3:]:
        lines.append(f"  {month_data['month']}: profit={month_data['profit']:+.2f}")
        for slot in month_data.get("losing_slots", [])[:8]:
            lines.append(
                f"    - {slot['dow_kz']} {slot['session']} {slot['mode']}: "
                f"{slot['profit']:+.2f} ({slot['trades']}tr)"
            )
    return "\n".join(lines)
