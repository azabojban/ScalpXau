"""Уақыт бойынша normal/invert — analytics wrapper."""

from __future__ import annotations

import logging

from xau_scalp.analytics import (
    format_monthly_losses,
    format_slot_report,
    resolve_slot_mode,
    save_reports,
    should_skip_slot,
)
from xau_scalp.config import XauScalpSettings
from xau_scalp.indicator_intel import format_indicator_report, save_indicator_brain

logger = logging.getLogger(__name__)

_last_log_key: str = ""


def resolve_invert_mode(
    settings: XauScalpSettings,
    now=None,
) -> tuple[bool, str]:
    invert, reason, _label = resolve_slot_mode(settings, now)
    return invert, reason


def log_mode_if_changed(settings: XauScalpSettings, invert: bool, reason: str) -> None:
    global _last_log_key
    key = f"{invert}:{reason}"
    if _last_log_key == key:
        return
    _last_log_key = key
    logger.info(
        "Adaptive → %s | %s",
        "INVERT" if invert else "NORMAL",
        reason,
    )


def log_analytics(settings: XauScalpSettings) -> None:
    if settings.adaptive_mode:
        slot_report = format_slot_report(settings)
        if slot_report:
            logger.info("Slot report:\n%s", slot_report)
    if settings.smart_indicators:
        ind_report = format_indicator_report(settings)
        if ind_report:
            logger.info("Indicator brain:\n%s", ind_report)
        save_indicator_brain(settings)
    loss_report = format_monthly_losses(settings)
    if loss_report:
        logger.info("%s", loss_report)
    save_reports(settings)


def hourly_report(journal_file):  # noqa: ARG001 — backward compat
    return []


def save_adaptive_report(settings: XauScalpSettings) -> None:
    save_reports(settings)
